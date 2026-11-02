// POST /api/v1/analytics/batch 的 Go 侧实现（N8 批 1，analytics 域）。
//
// 这个端点的 400 体是切流风险最高的一段：FastAPI 把**请求原文**回显在 data[].input 里，
// 而那串字节是 `json.loads(json.dumps(errors))` + `JSONResponse(ensure_ascii=False,
// separators=(",",":"))` 的产物。所以校验走 pyjson.go（CPython 的值语义）而不是
// encoding/json，日期走 pyspeedate.go（speedate 的错误词），两者由
// go-core/testdata/n8_contract.json 逐字节回放。
//
// 依赖顺序也是契约的一部分：FastAPI 先解 get_current_user（鉴权失败⇒401 而非 400），
// 再校验 body，最后才进 service（读 users.analytics_consent）。
package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"sync"
	"unicode/utf8"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/posthog"
)

// maxAnalyticsEvents 是 schemas.py:689 `Field(..., max_length=100)` 的那个 100。
const maxAnalyticsEvents = 100

// maxAnalyticsEventIDLength 是 analytics_service.py::MAX_EVENT_ID_LENGTH（=128）。
const maxAnalyticsEventIDLength = 128

type analyticsEvent struct {
	name       string
	properties *pyValue
	occurredAt pyDatetime
	// eventID 是客户端幂等键（`event_id: Any = None`）。nil＝请求里没给/给了 null，
	// 两种在 Python 侧都是 None，不触发逐事件确认形状。
	eventID *pyValue
}

func (s *Server) handleAnalyticsBatch(w http.ResponseWriter, r *http.Request) {
	userID, apiErr := s.middleware.RequireBearer(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	if s.batch1.Gate.Delegate(w, r, "analytics", userID) {
		return
	}
	raw, err := readBody(r)
	if err != nil {
		writeJSONError(w, r, asAPIError(err))
		return
	}
	events, entries := validateAnalyticsBody(raw, r.Header.Get("Content-Type"))
	if len(entries) > 0 {
		writePyInvalid(w, r, entries)
		return
	}

	profile, err := s.batch1.Users.UserProfile(r.Context(), userID)
	if err != nil {
		writeDBError(w, r)
		return
	}
	if analyticsAckMode(events) {
		s.writeAnalyticsAck(w, r, userID, profile, events)
		return
	}
	rc := fromContext(r.Context())
	// 未同意 / 用户不存在（含 status≠active）⇒ 整批丢弃，但仍回 200 + 计数（Python 同）。
	if !profile.Found || !profile.AnalyticsConsent {
		WriteSuccess(w, batchResult{Relayed: 0, Dropped: len(events)}, rc.traceID, rc.userID)
		return
	}
	relayed := s.relayEvents(r, userID, events)
	// 计数只认真正送出去的那几条（D-G18）：relayed + dropped == len(events)。
	WriteSuccess(w, batchResult{Relayed: relayed, Dropped: len(events) - relayed},
		rc.traceID, rc.userID)
}

// analyticsAckMode 与 Python 同一条判据：任一条 event_id 不是 None ⇒ 新形状。
// 旧客户端整批不带该键，所以 N8 冻结夹具仍走 {relayed,dropped} 的字节。
func analyticsAckMode(events []analyticsEvent) bool {
	for _, ev := range events {
		if ev.eventID != nil {
			return true
		}
	}
	return false
}

// batchResult 的键序即 Python 返回 dict 的键序（relayed 先、dropped 后）。
type batchResult struct {
	Relayed int `json:"relayed"`
	Dropped int `json:"dropped"`
}

// analyticsAckResult 逐事件结果：键序 event_id → status → reason（reason 空则不写），
// 与 analytics_service.py::_ack_item 一致。event_id 回显**请求原文**（Any 字段），
// 非法值也要原样带回，前端才认得出是哪一条被永久拒绝。
type analyticsAckResult struct {
	EventID analyticsJSONValue `json:"event_id"`
	Status  string             `json:"status"`
	Reason  string             `json:"reason,omitempty"`
}

// analyticsAckResponse 的键序即 Python `_ack_response` 的 dict 顺序。
// 不变量：accepted + duplicate + dropped == len(events)，其中 dropped = retryable + permanent。
type analyticsAckResponse struct {
	Relayed   int                 `json:"relayed"`
	Dropped   int                 `json:"dropped"`
	Accepted  int                 `json:"accepted"`
	Duplicate int                 `json:"duplicate"`
	Retryable int                 `json:"retryable"`
	Permanent int                 `json:"permanent"`
	Results   []analyticsAckResult `json:"results"`
}

// analyticsJSONValue 让 pyValue 原文字节直接进 encoding/json（不经过 float64）。
type analyticsJSONValue struct {
	value *pyValue
}

func (v analyticsJSONValue) MarshalJSON() ([]byte, error) {
	if v.value == nil {
		return []byte("null"), nil
	}
	encoded, err := v.value.json()
	if err != nil {
		return nil, err
	}
	return []byte(encoded), nil
}

func analyticsAckItem(ev analyticsEvent, status, reason string) analyticsAckResult {
	return analyticsAckResult{
		EventID: analyticsJSONValue{value: ev.eventID},
		Status:  status,
		Reason:  reason,
	}
}

// summarizeAck 逐条计数。accepted 才算 relayed；retryable+permanent 合计 dropped。
func summarizeAck(results []analyticsAckResult) analyticsAckResponse {
	out := analyticsAckResponse{Results: results}
	for _, item := range results {
		switch item.Status {
		case "accepted":
			out.Accepted++
		case "duplicate":
			out.Duplicate++
		case "retryable":
			out.Retryable++
		case "permanent":
			out.Permanent++
		}
	}
	out.Relayed = out.Accepted
	out.Dropped = out.Retryable + out.Permanent
	return out
}

// validAnalyticsEventID 是 analytics_service.py::_valid_event_id 的字面等价：
// 非空字符串且长度（按字符，不是字节）不超过 128。
func validAnalyticsEventID(raw *pyValue) (string, bool) {
	if raw == nil || raw.kind != pyString || raw.str == "" {
		return "", false
	}
	if utf8.RuneCountInString(raw.str) > maxAnalyticsEventIDLength {
		return "", false
	}
	return raw.str, true
}

// writeAnalyticsAck 处理带 event_id 的批次：认领 → 中继 → 落账 → 逐事件回报。
//
// 与 Python 的差别只有并发：认领仍按请求顺序串行（同批重复、prior 状态都靠这个顺序），
// 真正外发沿用 legacy 的并发派发，再按每条的结果写台账。台账读写在数据库出错时
// 释放本批仍为 pending 的认领，避免一次 500 留下永远 in_flight 的行。
func (s *Server) writeAnalyticsAck(w http.ResponseWriter, r *http.Request, userID string,
	profile domain.UserProfile, events []analyticsEvent) {
	rc := fromContext(r.Context())
	if !profile.Found || !profile.AnalyticsConsent {
		reason := "consent_off"
		if !profile.Found {
			reason = "user_not_found"
		}
		results := make([]analyticsAckResult, len(events))
		for i, ev := range events {
			results[i] = analyticsAckItem(ev, "permanent", reason)
		}
		WriteSuccess(w, summarizeAck(results), rc.traceID, rc.userID)
		return
	}
	if s.batch1.Receipts == nil {
		writeJSONError(w, r, apierr.New(apierr.CodeInternalError,
			"服务器内部错误 / Internal server error"))
		return
	}

	results := make([]analyticsAckResult, len(events))
	claimed := make([]int, 0, len(events))
	claimIDs := make([]string, 0, len(events))
	seen := make(map[string]bool, len(events))
	for i, ev := range events {
		eventID, ok := validAnalyticsEventID(ev.eventID)
		if !ok {
			results[i] = analyticsAckItem(ev, "permanent", "invalid_event_id")
			continue
		}
		if seen[eventID] {
			results[i] = analyticsAckItem(ev, "duplicate", "")
			continue
		}
		seen[eventID] = true
		got, prior, err := s.batch1.Receipts.Claim(r.Context(), userID, eventID)
		if err != nil {
			s.releaseReceipts(r.Context(), userID, claimIDs)
			writeDBError(w, r)
			return
		}
		if !got {
			if prior == "accepted" {
				results[i] = analyticsAckItem(ev, "duplicate", "")
			} else {
				// pending 或并发下读不到行：都不能再外发，让前端退避重试。
				results[i] = analyticsAckItem(ev, "retryable", "in_flight")
			}
			continue
		}
		claimed = append(claimed, i)
		claimIDs = append(claimIDs, eventID)
	}

	delivered := s.relaySelected(r, userID, events, claimed)
	for j, idx := range claimed {
		ev := events[idx]
		eventID := claimIDs[j]
		if delivered[j] {
			if err := s.batch1.Receipts.MarkAccepted(r.Context(), userID, eventID); err != nil {
				s.releaseReceipts(r.Context(), userID, claimIDs[j:])
				writeDBError(w, r)
				return
			}
			results[idx] = analyticsAckItem(ev, "accepted", "")
			continue
		}
		// 没送到就不许留下 accepted：删掉认领行，下一次能重新进来。
		if err := s.batch1.Receipts.Release(r.Context(), userID, eventID); err != nil {
			s.releaseReceipts(r.Context(), userID, claimIDs[j+1:])
			writeDBError(w, r)
			return
		}
		results[idx] = analyticsAckItem(ev, "retryable", "relay_failed")
	}
	WriteSuccess(w, summarizeAck(results), rc.traceID, rc.userID)
}

// releaseReceipts 是错误路径的收尾：已认领但还没落账的行删掉，下一次重试能重新认领。
func (s *Server) releaseReceipts(ctx context.Context, userID string, eventIDs []string) {
	for _, eventID := range eventIDs {
		if err := s.batch1.Receipts.Release(ctx, userID, eventID); err != nil {
			_, _ = fmt.Fprintf(os.Stderr, "analytics: release receipt failed: %v\n", err)
		}
	}
}


// relayEvents 并发外发并等总时限到点，返回**真的送出去了几条**。
//
// 「计数看发送结果」是 D-G18（2026-10-17）改的口径，从前这里无条件把整批算成 relayed：
// Relay 为 nil（＝没有中继通道，Python 那侧对应 posthog 没装或没配 key）与 Capture 报错
// 都是「一条没出门」，却回 200 宣称这批已经落到分析平台。
// 红线没动——外发失败只记日志，绝不因分析平台抖动让业务请求失败；改的只是「没做的事
// 不许算成已中继」。与 backend/services/analytics_service.py::relay_batch 同批同形。
func (s *Server) relayEvents(r *http.Request, userID string, events []analyticsEvent) int {
	indexes := make([]int, len(events))
	for i := range events {
		indexes[i] = i
	}
	relayed := 0
	for _, ok := range s.relaySelected(r, userID, events, indexes) {
		if ok {
			relayed++
		}
	}
	return relayed
}

// relaySelected 并发外发 events[indexes]，返回与 indexes 对齐的「真的送出去了没有」。
// 逐事件确认形状要靠这份结果写台账；legacy 计数只是把它数一遍（同一条判据）。
func (s *Server) relaySelected(r *http.Request, userID string, events []analyticsEvent,
	indexes []int) []bool {
	delivered := make([]bool, len(indexes))
	if s.batch1.Relay == nil || len(indexes) == 0 {
		return delivered
	}
	ctx, cancel := context.WithTimeout(context.WithoutCancel(r.Context()), posthog.Timeout)
	defer cancel()

	var wg sync.WaitGroup
	for j, idx := range indexes {
		ev := events[idx]
		payload := posthog.Event{
			Event:      ev.name,
			DistinctID: userID,
			Properties: anonymizeProperties(ev.properties),
			Timestamp:  ev.occurredAt.Isoformat(),
		}
		wg.Add(1)
		go func(j int) {
			defer wg.Done()
			if err := s.batch1.Relay.Capture(ctx, payload); err != nil {
				_, _ = fmt.Fprintf(os.Stderr, "analytics: posthog relay failed: %v\n", err)
				return
			}
			delivered[j] = true
		}(j)
	}
	wg.Wait()
	return delivered
}

// anonymizeProperties 先按白名单+空值过滤，再把留下的值转成**原文字节**送出去：
// 用 pyValue 的再编码而不是 Go 的 encoding/json，10**30 这类超出 float64 的数才不会变形。
func anonymizeProperties(properties *pyValue) map[string]json.RawMessage {
	out := map[string]json.RawMessage{}
	if properties == nil || properties.kind != pyObject {
		return out
	}
	for _, member := range properties.members {
		if !domain.AllowedPropertyKeys[member.key] || member.val.isNullish() {
			continue
		}
		encoded, err := member.val.json()
		if err != nil {
			continue // NaN/Infinity：CPython 侧同样编不出来
		}
		out[member.key] = json.RawMessage(encoded)
	}
	return out
}

// validateAnalyticsBody 返回通过校验的事件，或 pydantic 的错误列表（非空表示拒绝）。
//
// 错误的**产生顺序**就是回显顺序：顶层 → events 字段 → 逐条 item（按 item 下标、
// 字段声明序 event/properties/occurred_at）→ 列表长度约束。最后一条与 item 错误的
// 相对次序由契约夹具 batch-101-items-with-bad-item 钉住，不是推的。
// contentType 取自请求头；判据不是「像不像 JSON」而是 FastAPI 的
// routing.py:428-446：body 非空 ⇒ 只有 email.message 解析出的主类型 application
// 且子类型 json / *+json 才走 json.loads，否则把 **原始 bytes** 当 body 交给 pydantic。
func validateAnalyticsBody(raw []byte, contentType string) ([]analyticsEvent, []pyFieldError) {
	if len(raw) == 0 {
		// `if body_bytes:` 不 strip，所以空串才是 missing；三个空格会进 JSON 解析、
		// 报 json_invalid at char 3（夹具 batch-body-whitespace-only）。
		return nil, []pyFieldError{missingBody()}
	}
	if !isJSONContentType(contentType) {
		return nil, []pyFieldError{{
			kind:  "model_attributes_type",
			loc:   []any{"body"},
			msg:   "Input should be a valid dictionary or object to extract fields from",
			input: pyStringValue(pyBytesRepr(raw)),
		}}
	}
	body, err := pyJSONLoads(raw)
	if err != nil {
		return nil, []pyFieldError{jsonInvalidPyEntry(err)}
	}
	switch body.kind {
	case pyNull:
		// FastAPI 把空 body 与 JSON null 都当成「没给 body」⇒ missing，而不是类型错误。
		return nil, []pyFieldError{missingBody()}
	case pyObject:
	default:
		return nil, []pyFieldError{{
			kind:  "model_attributes_type",
			loc:   []any{"body"},
			msg:   "Input should be a valid dictionary or object to extract fields from",
			input: body,
		}}
	}

	eventsValue, present := body.get("events")
	if !present {
		return nil, []pyFieldError{{
			kind: "missing", loc: []any{"body", "events"},
			msg: "Field required", input: body,
		}}
	}
	if eventsValue.kind != pyArray {
		return nil, []pyFieldError{{
			kind: "list_type", loc: []any{"body", "events"},
			msg: "Input should be a valid list", input: eventsValue,
		}}
	}

	if len(eventsValue.items) > maxAnalyticsEvents {
		// 实测（batch-101-items-with-bad-item）：越界时 pydantic **不再**逐条校验，
		// data 里只有这一条 too_long，actual_length 数的是原文条数。
		return nil, []pyFieldError{{
			kind: "too_long", loc: []any{"body", "events"},
			msg: fmt.Sprintf("List should have at most %d items after validation, not %d",
				maxAnalyticsEvents, len(eventsValue.items)),
			input: eventsValue,
			ctx: pyObjectOf(
				"field_type", pyStringValue("List"),
				"max_length", pyIntValue(maxAnalyticsEvents),
				"actual_length", pyIntValue(len(eventsValue.items)),
			),
		}}
	}

	var entries []pyFieldError
	var out []analyticsEvent
	for i, item := range eventsValue.items {
		event, itemEntries := validateAnalyticsEvent(item, i)
		if len(itemEntries) > 0 {
			entries = append(entries, itemEntries...)
			continue
		}
		out = append(out, event)
	}
	if len(entries) > 0 {
		return nil, entries
	}
	return out, nil
}

func locAt(base []any, field string) []any {
	out := make([]any, 0, len(base)+1)
	out = append(out, base...)
	return append(out, field)
}

func missingBody() pyFieldError {
	return pyFieldError{kind: "missing", loc: []any{"body"},
		msg: "Field required", input: pyNullValue()}
}

// validateAnalyticsEvent 校验列表里的一个 item，字段按 schemas.py:675-684 的声明序。
func validateAnalyticsEvent(item *pyValue, index int) (analyticsEvent, []pyFieldError) {
	if item.kind != pyObject {
		return analyticsEvent{}, []pyFieldError{{
			kind: "model_attributes_type", loc: []any{"body", "events", index},
			msg:   "Input should be a valid dictionary or object to extract fields from",
			input: item,
		}}
	}
	base := []any{"body", "events", index}
	// locAt 复制 base 再接字段名：三处共用同一个 base，就地 append 会互相覆盖。
	var entries []pyFieldError
	out := analyticsEvent{}

	name, present := item.get("event")
	switch {
	case !present:
		entries = append(entries, pyFieldError{kind: "missing",
			loc: locAt(base, "event"), msg: "Field required", input: item})
	case name.kind != pyString:
		entries = append(entries, pyFieldError{kind: "string_type",
			loc: locAt(base, "event"),
			msg: "Input should be a valid string", input: name})
	default:
		out.name = name.str
	}

	props, present := item.get("properties")
	switch {
	case !present, props.isNullish(): // Optional[Dict] = None
	case props.kind != pyObject:
		entries = append(entries, pyFieldError{kind: "dict_type",
			loc: locAt(base, "properties"),
			msg: "Input should be a valid dictionary", input: props})
	default:
		out.properties = props
	}

	at, present := item.get("occurred_at")
	if !present {
		entries = append(entries, pyFieldError{kind: "missing",
			loc: locAt(base, "occurred_at"), msg: "Field required", input: item})
	} else {
		parsed, e := ParseOccurredAt(at)
		if e != nil {
			entries = append(entries, e.fieldError(locAt(base, "occurred_at"), at))
		} else {
			out.occurredAt = parsed
		}
	}
	// `event_id: Any = None`：不参与校验，只决定响应形状。null 与缺省同义（都是 None），
	// 但原文要留着——非字符串的非法值在 ack 结果里必须原样回显。
	if id, present := item.get("event_id"); present && !id.isNullish() {
		out.eventID = id
	}
	if len(entries) > 0 {
		return analyticsEvent{}, entries
	}
	return out, nil
}

// isJSONContentType 是 FastAPI 那段 email.message 判定的等价实现：
// 取第一个 ';' 前的段，主类型必须 application，子类型 json 或以 +json 结尾。
// 没有头 / 值为空 / 形状非法（email 库此时回 text/plain）都不算 JSON。
func isJSONContentType(contentType string) bool {
	if contentType == "" {
		return false
	}
	first, _, _ := strings.Cut(contentType, ";")
	main, sub, found := strings.Cut(strings.TrimSpace(first), "/")
	if !found {
		return false
	}
	sub = strings.ToLower(strings.TrimSpace(sub))
	main = strings.ToLower(strings.TrimSpace(main))
	return main == "application" && (sub == "json" || strings.HasSuffix(sub, "+json"))
}

// pyBytesRepr 是 Python 的 repr(bytes)。非 JSON 请求体走 bytes 分支后，
// 这个字符串就是 400 体里 input 的值（实测 "b'{\"events\": []}'"）。
// bytes 的 repr 永远是 ASCII：>=0x7f 的字节写 \xNN。
func pyBytesRepr(raw []byte) string {
	quote := byte('\'')
	if bytes.ContainsRune(raw, '\'') && !bytes.ContainsRune(raw, '"') {
		quote = '"'
	}
	var b strings.Builder
	b.WriteString("b")
	b.WriteByte(quote)
	for _, c := range raw {
		switch {
		case c == '\\':
			b.WriteString(`\\`)
		case c == quote:
			b.WriteByte('\\')
			b.WriteByte(quote)
		case c == '\n':
			b.WriteString(`\n`)
		case c == '\r':
			b.WriteString(`\r`)
		case c == '\t':
			b.WriteString(`\t`)
		case c < 0x20 || c >= 0x7f:
			fmt.Fprintf(&b, `\x%02x`, c)
		default:
			b.WriteByte(c)
		}
	}
	b.WriteByte(quote)
	return b.String()
}

// ── pydantic 错误面的 400 体 ─────────────────────────────────────────────────

// pyFieldError 是一条 pydantic-core 校验错误；键序 type/loc/msg/input/ctx 是实测顺序。
type pyFieldError struct {
	kind  string
	loc   []any
	msg   string
	input *pyValue
	ctx   *pyValue // nil ⇒ 不输出 ctx（datetime_type 就是这一格）
}

func (e pyFieldError) value() *pyValue {
	v := &pyValue{kind: pyObject}
	v.set("type", pyStringValue(e.kind))
	v.set("loc", pyLocValue(e.loc))
	v.set("msg", pyStringValue(e.msg))
	input := e.input
	if input == nil {
		input = pyNullValue()
	}
	v.set("input", input)
	if e.ctx != nil {
		v.set("ctx", e.ctx)
	}
	return v
}

func pyLocValue(loc []any) *pyValue {
	items := make([]*pyValue, 0, len(loc))
	for _, part := range loc {
		switch p := part.(type) {
		case string:
			items = append(items, pyStringValue(p))
		case int:
			items = append(items, pyIntValue(p))
		}
	}
	return &pyValue{kind: pyArray, items: items}
}

func pyObjectOf(pairs ...any) *pyValue {
	v := &pyValue{kind: pyObject}
	for i := 0; i+1 < len(pairs); i += 2 {
		key, _ := pairs[i].(string)
		val, _ := pairs[i+1].(*pyValue)
		v.set(key, val)
	}
	return v
}

// jsonInvalidPyEntry 对应 starlette 解析失败后 pydantic 包的 json_invalid：
// ctx.error 是 CPython json 的报错文案，loc 末位是**字符**下标。
func jsonInvalidPyEntry(e *pyJSONError) pyFieldError {
	return pyFieldError{
		kind:  "json_invalid",
		loc:   []any{"body", e.pos},
		msg:   "JSON decode error",
		input: pyObjectOf(),
		ctx:   pyObjectOf("error", pyStringValue(e.msg)),
	}
}

// fieldError 把 speedate 的失败映射成 400 体里的一条：kind 决定 type 与 msg 前缀，
// datetime_type 那一格**没有 ctx**（实测）。
func (e *speedateError) fieldError(loc []any, input *pyValue) pyFieldError {
	entry := pyFieldError{
		kind:  e.kind.String(),
		loc:   loc,
		msg:   e.kind.Message(e.word),
		input: input,
	}
	if e.word != "" {
		entry.ctx = pyObjectOf("error", pyStringValue(e.word))
	}
	return entry
}

// pyInvalidMessage 复刻 backend/core/errors.py 对 RequestValidationError 的文案：
// 只取**第一条**错误，loc 去掉 "body" 后用 '.' 连接。
func pyInvalidMessage(entries []pyFieldError) string {
	first := entries[0]
	parts := make([]string, 0, len(first.loc))
	for _, part := range first.loc {
		if s, ok := part.(string); ok && s == "body" {
			continue
		}
		parts = append(parts, fmt.Sprint(part))
	}
	loc := strings.Join(parts, ".")
	if loc == "" {
		return fmt.Sprintf("参数错误: %s / Invalid parameters", first.msg)
	}
	return fmt.Sprintf("参数错误: %s %s / Invalid parameters", loc, first.msg)
}

func writePyInvalid(w http.ResponseWriter, r *http.Request, entries []pyFieldError) {
	items := make([]*pyValue, 0, len(entries))
	for _, entry := range entries {
		items = append(items, entry.value())
	}
	rc := fromContext(r.Context())
	body := pyObjectOf(
		"code", pyIntValue(apierr.CodeInvalidParams),
		"message", pyStringValue(pyInvalidMessage(entries)),
		"data", &pyValue{kind: pyArray, items: items},
		"trace_id", pyStringValue(rc.traceID),
		"user_id", pyStringValue(rc.userID),
	)
	encoded, err := body.json()
	if err != nil {
		// 回显里带 NaN/Infinity ⇒ CPython 侧 allow_nan=False 同样编不出来，落 500。
		writeJSONError(w, r, apierr.New(apierr.CodeInternalError,
			"服务器内部错误 / Internal server error"))
		return
	}
	writeRawJSON(w, http.StatusBadRequest, encoded)
}

func writeRawJSON(w http.ResponseWriter, status int, body string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write([]byte(body))
}

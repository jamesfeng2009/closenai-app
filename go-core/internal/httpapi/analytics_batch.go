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
	"sync/atomic"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/posthog"
)

// maxAnalyticsEvents 是 schemas.py:689 `Field(..., max_length=100)` 的那个 100。
const maxAnalyticsEvents = 100

type analyticsEvent struct {
	name       string
	properties *pyValue
	occurredAt pyDatetime
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

// batchResult 的键序即 Python 返回 dict 的键序（relayed 先、dropped 后）。
type batchResult struct {
	Relayed int `json:"relayed"`
	Dropped int `json:"dropped"`
}

// relayEvents 并发外发并等总时限到点，返回**真的送出去了几条**。
//
// 「计数看发送结果」是 D-G18（2026-10-17）改的口径，从前这里无条件把整批算成 relayed：
// Relay 为 nil（＝没有中继通道，Python 那侧对应 posthog 没装或没配 key）与 Capture 报错
// 都是「一条没出门」，却回 200 宣称这批已经落到分析平台。
// 红线没动——外发失败只记日志，绝不因分析平台抖动让业务请求失败；改的只是「没做的事
// 不许算成已中继」。与 backend/services/analytics_service.py::relay_batch 同批同形。
func (s *Server) relayEvents(r *http.Request, userID string, events []analyticsEvent) int {
	if s.batch1.Relay == nil || len(events) == 0 {
		return 0
	}
	ctx, cancel := context.WithTimeout(context.WithoutCancel(r.Context()), posthog.Timeout)
	defer cancel()

	var wg sync.WaitGroup
	var relayed atomic.Int64
	for _, ev := range events {
		payload := posthog.Event{
			Event:      ev.name,
			DistinctID: userID,
			Properties: anonymizeProperties(ev.properties),
			Timestamp:  ev.occurredAt.Isoformat(),
		}
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := s.batch1.Relay.Capture(ctx, payload); err != nil {
				_, _ = fmt.Fprintf(os.Stderr, "analytics: posthog relay failed: %v\n", err)
				return
			}
			relayed.Add(1)
		}()
	}
	wg.Wait()
	return int(relayed.Load())
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

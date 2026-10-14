// N8 批 3 在 HTTP 层的单元闸：预写次序、四个静默提前返回、文本列直通与 acknowledge 的归属面。
//
// 与 n8_replay_test.go 的分工沿用批 2：那边比的是**响应字节**（真库、真 FastAPI 录的基线），
// 这里钉的是字节测看不见结构性事实：
//  1. **调用次序**：Python 的 list_events 是「预写 ⇒ 列表 ⇒ 未读数」三步，
//     而且预写内部还有四个提前返回（用户读不到／没有基准日／…）。
//     把「用户读不到」升成 40401 或把 count 挪到预写之前，**现网数据可能测不出来**：
//     注销用户若没有历史行，两条路径都是 `{"events":[],"unseen_count":0}`。
//     所以这里用「注销用户**有**行」的假件直接钉序列。
//  2. **文本列直通**：journey 的 payload/memory_refs 走 parse_json（无 isinstance 检查），
//     与批 2 课程卡的 _payload/_refs（强制 dict/list）**不是同一个函数**。
//     列里存数组时这边出数组、那边出空对象——照抄批 2 会静默改契约。
//  3. **写库文本**的分隔符：prefill 落进 payload 列的文本是 CPython json.dumps 的
//     **默认**分隔符（`", "`/`": "`），不是响应面那套紧凑形。
//  4. acknowledge 的响应回显**请求里的** event_id（routers/journey.py:41），
//     而批 2 的 read 回的是 event.id 列值——两条端点各自的出处不同，这里钉住本域那条。
package httpapi

import (
	"context"
	"errors"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// ── 假件 ──────────────────────────────────────────────────────────────────────

type journeyFakeStore struct {
	t     *testing.T
	calls *[]string

	user    domain.JourneyUser
	base    time.Time
	hasBase bool
	userErr error
	baseErr error

	writes []domain.JourneyPrefill
	preErr error

	rows      []domain.JourneyEventRow
	listErr   error
	unseen    int
	unseenErr error
	owner     domain.JourneyOwner
	ownerErr  error
	ackErr    error
}

func (f *journeyFakeStore) JourneyUser(_ context.Context, userID string) (domain.JourneyUser, error) {
	*f.calls = append(*f.calls, "JourneyUser("+userID+")")
	if f.userErr != nil {
		return domain.JourneyUser{}, f.userErr
	}
	out := f.user
	out.ID = userID
	return out, nil
}

func (f *journeyFakeStore) OnboardingBase(_ context.Context, userID string) (time.Time, bool, error) {
	*f.calls = append(*f.calls, "OnboardingBase("+userID+")")
	if f.baseErr != nil {
		return time.Time{}, false, f.baseErr
	}
	return f.base, f.hasBase, nil
}

func (f *journeyFakeStore) PrefillJourney(_ context.Context, userID string,
	rows []domain.JourneyPrefill) error {
	names := make([]string, 0, len(rows))
	for _, row := range rows {
		names = append(names, row.Kind)
	}
	*f.calls = append(*f.calls, "PrefillJourney("+userID+","+strings.Join(names, "|")+")")
	f.writes = rows
	return f.preErr
}

func (f *journeyFakeStore) JourneyEvents(_ context.Context, userID string,
	limit int) ([]domain.JourneyEventRow, error) {
	*f.calls = append(*f.calls, "JourneyEvents("+userID+")")
	if limit != domain.JourneyListLimit {
		f.t.Errorf("limit = %d，期望 %d（list_for_user 的默认值就是契约）", limit, domain.JourneyListLimit)
	}
	if f.listErr != nil {
		return nil, f.listErr
	}
	return f.rows, nil
}

func (f *journeyFakeStore) JourneyUnseenCount(_ context.Context, userID string) (int, error) {
	*f.calls = append(*f.calls, "JourneyUnseenCount("+userID+")")
	if f.unseenErr != nil {
		return 0, f.unseenErr
	}
	return f.unseen, nil
}

func (f *journeyFakeStore) JourneyOwner(_ context.Context, eventID string) (domain.JourneyOwner, error) {
	*f.calls = append(*f.calls, "JourneyOwner("+eventID+")")
	if f.ownerErr != nil {
		return domain.JourneyOwner{}, f.ownerErr
	}
	return f.owner, nil
}

func (f *journeyFakeStore) AcknowledgeJourneyEvent(_ context.Context, eventID string) error {
	*f.calls = append(*f.calls, "AcknowledgeJourneyEvent("+eventID+")")
	return f.ackErr
}

func journeyRequest(t *testing.T, handler http.Handler, method, path, userID string,
	body string, contentType string) *httptest.ResponseRecorder {
	t.Helper()
	var reader *strings.Reader
	if body == "" {
		reader = strings.NewReader("")
	} else {
		reader = strings.NewReader(body)
	}
	req := httptest.NewRequest(method, path, reader)
	req.Header.Set("X-User-Id", userID)
	req.Header.Set(TraceHeader, "t-journey")
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	return rec
}

// prefilledFake 是「已过预写」的默认假件：active 用户 + 有 created_at + 零行。
func prefilledFake(t *testing.T) *journeyFakeStore {
	t.Helper()
	created := time.Date(2026, 10, 10, 3, 0, 0, 0, time.UTC)
	return &journeyFakeStore{t: t, calls: &[]string{},
		user: domain.JourneyUser{Found: true, Timezone: "UTC", Language: "en",
			PushTimeRaw: "09:00", CreatedAt: created, HasCreatedAt: true}}
}

func newJourneyServer(t *testing.T, fake *journeyFakeStore) *Server {
	t.Helper()
	if fake == nil {
		fake = prefilledFake(t)
	}
	srv := newN8Server(t, n8ForbiddenStore{t: t}, n8ForbiddenStore{t: t},
		&n8RecordingRelay{t: t}, nil, &Batch3{Store: fake, Gate: nil}, nil)
	srv.now = func() time.Time { return time.Date(2026, 10, 14, 14, 45, 30, 0, time.UTC) }
	return srv
}

// ── 1. 次序与提前返回 ─────────────────────────────────────────────────────────

func TestJourneyEventsCallOrderMatchesListEvents(t *testing.T) {
	srv := newJourneyServer(t, nil)
	rec := journeyRequest(t, srv.Handler(), http.MethodGet, "/api/v1/journey/events", "u-1", "", "")
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	fake := srv.batch3.Store.(*journeyFakeStore)
	want := []string{"JourneyUser(u-1)", "OnboardingBase(u-1)",
		"PrefillJourney(u-1,day0_read|day1_checkin_echo|day2_first_outreach|day3_weak_assert|day5_stat|day7_report)",
		"JourneyEvents(u-1)", "JourneyUnseenCount(u-1)"}
	if got := *fake.calls; strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("调用序列 = %v，期望 %v", got, want)
	}
	// 空列表也出 []：Python 的 events=[] 是空数组而不是 null。
	if body := rec.Body.String(); !strings.Contains(body, `"events":[]`) {
		t.Errorf("响应里没有 \"events\":[]：%s", body)
	}
}

func TestJourneyEventsDeletedUserStillListsRows(t *testing.T) {
	// 本批最容易「顺手修好」的一格：Python 的 `if user is None: return` 是**静默**返回，
	// 之后 list_for_user 照查。注销用户有历史行时契约是 200 + 那些行。
	fake := prefilledFake(t)
	fake.user = domain.JourneyUser{} // Found=false
	fake.rows = []domain.JourneyEventRow{{ID: "e-1", DayNo: 0, Kind: "day0_read",
		Status: "sent", ScheduledAt: time.Date(2026, 10, 10, 9, 0, 0, 0, time.UTC)}}
	srv := newJourneyServer(t, fake)
	rec := journeyRequest(t, srv.Handler(), http.MethodGet, "/api/v1/journey/events", "u-del", "", "")
	if rec.Code != http.StatusOK {
		t.Fatalf("注销用户应回 200（Python 不报 40401），得到 %d %s", rec.Code, rec.Body.String())
	}
	// 用户读不到 ⇒ OnboardingBase 与 Prefill 都不该发生（Python 在第一条 return）。
	for _, call := range *fake.calls {
		if strings.HasPrefix(call, "OnboardingBase") || strings.HasPrefix(call, "PrefillJourney") {
			t.Errorf("用户不存在时不该走基准日与预写，序列 = %v", *fake.calls)
		}
	}
}

func TestJourneyEventsWithoutAnyBaseDateSkipsPrefill(t *testing.T) {
	// `if base is None: return`：没有 onboarding 行**且** users.created_at 为 NULL 才走这支。
	fake := prefilledFake(t)
	fake.hasBase = false
	fake.user.HasCreatedAt = false
	srv := newJourneyServer(t, fake)
	rec := journeyRequest(t, srv.Handler(), http.MethodGet, "/api/v1/journey/events", "u-2", "", "")
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d %s", rec.Code, rec.Body.String())
	}
	want := []string{"JourneyUser(u-2)", "OnboardingBase(u-2)", "JourneyEvents(u-2)",
		"JourneyUnseenCount(u-2)"}
	if got := *fake.calls; strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("调用序列 = %v，期望 %v", got, want)
	}
}

func TestJourneyEventsUsesProfileBaseWhenPresent(t *testing.T) {
	// profile.created_at 优先于 user.created_at（Python 的三元表达式第一支）。
	fake := prefilledFake(t)
	fake.hasBase = true
	fake.base = time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC) // 比 user.created_at 更早
	srv := newJourneyServer(t, fake)
	journeyRequest(t, srv.Handler(), http.MethodGet, "/api/v1/journey/events", "u-3", "", "")
	first := srv.batch3.Store.(*journeyFakeStore).writes[0]
	// 基准日 2026-09-01 的 D0 ⇒ 本地 09:00（UTC）= 当天 09:00Z；
	// 若错拿 user.created_at(10-10) 就是 10-10T09:00Z。
	if !first.ScheduledAt.Equal(time.Date(2026, 9, 1, 9, 0, 0, 0, time.UTC)) {
		t.Errorf("D0 = %s，期望 2026-09-01T09:00:00Z（基准日取 onboarding 行）",
			first.ScheduledAt.Format(time.RFC3339))
	}
	if !first.Past {
		t.Error("2026-09-01 的 D0 相对 today=10-14 必须是 Past")
	}
}

// ── 2. 预写的文本与形状 ────────────────────────────────────────────────────────

func TestJourneyPrefillPayloadTextUsesCPythonDefaultSeparators(t *testing.T) {
	fake := prefilledFake(t)
	srv := newJourneyServer(t, fake)
	journeyRequest(t, srv.Handler(), http.MethodGet, "/api/v1/journey/events", "u-4", "", "")
	writes := srv.batch3.Store.(*journeyFakeStore).writes
	if len(writes) != len(domain.ScriptEvents) {
		t.Fatalf("预写 %d 行，期望 %d 行", len(writes), len(domain.ScriptEvents))
	}
	// 逐字对 journey_service.py:182 的 dict 字面量经 json.dumps(ensure_ascii=False)
	// 默认分隔符后的形态：键序 kind/day_no/language，**冒号后与逗号后各一个空格**。
	if got, want := writes[0].Payload,
		`{"kind": "day0_read", "day_no": 0, "language": "en"}`; got != want {
		t.Errorf("payload 列文本 = %s，期望 %s", got, want)
	}
	// language 走 `user.language or "en"`：空列要落 en，不能落空串。
	fake2 := prefilledFake(t)
	fake2.user.Language = ""
	srv2 := newJourneyServer(t, fake2)
	journeyRequest(t, srv2.Handler(), http.MethodGet, "/api/v1/journey/events", "u-5", "", "")
	if got, want := srv2.batch3.Store.(*journeyFakeStore).writes[2].Payload,
		`{"kind": "day2_first_outreach", "day_no": 2, "language": "en"}`; got != want {
		t.Errorf("空 language 的 payload = %s，期望 %s", got, want)
	}
}

func TestPyDumpsSpacedShapes(t *testing.T) {
	// 默认分隔符只在容器层加空格；叶子与紧凑形同源（转义集、float repr）。
	nested := pyObjectOf("a", pyArrayValue(pyIntValue(1), pyStringValue("中")),
		"b", pyObjectOf("k", pyBoolValue(true)))
	got, err := pyDumpsSpaced(nested)
	if err != nil {
		t.Fatal(err)
	}
	if want := `{"a": [1, "中"], "b": {"k": true}}`; got != want {
		t.Errorf("pyDumpsSpaced = %s，期望 %s", got, want)
	}
	// 空容器：`[]` / `{}`（Python 不会写成 `[ ]`）。
	for v, want := range map[*pyValue]string{pyArrayValue(): "[]", pyObjectValue(): "{}"} {
		if got, err := pyDumpsSpaced(v); err != nil || got != want {
			t.Errorf("pyDumpsSpaced(%v) = (%s, %v)，期望 %s", v, got, err, want)
		}
	}
	// 非 ASCII 原样（ensure_ascii=False），控制字符仍走 \uXXXX。
	if got, err := pyDumpsSpaced(pyStringValue("a\tb\x01c")); err != nil ||
		got != `"a\tb\u0001c"` {
		t.Errorf("字符串转义 = %s (%v)，期望 \"a\\tb\\u0001c\"", got, err)
	}
	// NaN 走的是与响应面同一条失败路（prefill 的三键不可能命中，留着是为了不静默写半份文本）。
	if _, err := pyDumpsSpaced(pyArrayValue(
		&pyValue{kind: pyFloat, float: math.NaN()})); err == nil {
		t.Error("NaN 必须编不出来，而不是写出非法 JSON 入库")
	}
}

// ── 3. 文本列直通 ─────────────────────────────────────────────────────────────

func TestJourneyEventsPassTextColumnsThroughWithoutKindCheck(t *testing.T) {
	// parse_json 只兜「空文本」与「坏 JSON」两种；**不做** isinstance（批 2 的 _payload 做了）。
	// 列里存 5 就回 5、存数组就回数组。把批 2 的回退函数抄过来会静默改契约。
	fake := prefilledFake(t)
	fake.rows = []domain.JourneyEventRow{
		{ID: "e-num", DayNo: 0, Kind: "day0_read", Status: "sent",
			ScheduledAt: time.Date(2026, 10, 10, 9, 0, 0, 0, time.UTC),
			Payload:     "5", MemoryRefs: "[1,2]"},
		{ID: "e-bad", DayNo: 1, Kind: "day1_checkin_echo", Status: "pending",
			ScheduledAt: time.Date(2026, 10, 11, 9, 0, 0, 0, time.UTC),
			Payload:     "{oops", MemoryRefs: ""},
	}
	srv := newJourneyServer(t, fake)
	rec := journeyRequest(t, srv.Handler(), http.MethodGet, "/api/v1/journey/events", "u-6", "", "")
	body := rec.Body.String()
	want := `{"code":200,"message":"success","data":{"events":[` +
		`{"id":"e-num","day_no":0,"event_kind":"day0_read","status":"sent",` +
		`"scheduled_at":"2026-10-10T09:00:00+00:00","sent_at":null,"acknowledged":false,` +
		`"payload":5,"memory_refs":[1,2]},` +
		`{"id":"e-bad","day_no":1,"event_kind":"day1_checkin_echo","status":"pending",` +
		`"scheduled_at":"2026-10-11T09:00:00+00:00","sent_at":null,"acknowledged":false,` +
		`"payload":{},"memory_refs":[]}],` +
		`"unseen_count":0},"trace_id":"t-journey","user_id":"u-6"}`
	if body != want {
		t.Errorf("逐字不符：\n got %s\nwant %s", body, want)
	}
}

func TestJourneyEventsDatetimeISOHasNoZAndKeepsSixMicroDigits(t *testing.T) {
	// 这条端点没有 response_model ⇒ 走 jsonable_encoder ⇒ datetime.isoformat()：
	// UTC 写 "+00:00"（批 1 的 discovery 有 pydantic 模型，那边才是 "Z"），
	// 微秒非零固定 6 位（.001000 而不是 .001），为零整段省略。
	fake := prefilledFake(t)
	fake.rows = []domain.JourneyEventRow{
		{ID: "e-1", DayNo: 0, Kind: "day0_read", Status: "sent",
			// time.Date 第 7 个参数是**纳秒**，Python 的 datetime 只到微秒：
			// 1_000_000 ns = µs=1000，1000 ns = µs=1。
			ScheduledAt: time.Date(2026, 10, 10, 9, 0, 0, 1000000, time.UTC), // µs=1000
			SentAt:      time.Date(2026, 10, 10, 9, 0, 0, 0, time.UTC), HasSentAt: true},
		{ID: "e-2", DayNo: 1, Kind: "day1_checkin_echo", Status: "pending",
			ScheduledAt: time.Date(2026, 10, 11, 9, 0, 0, 1000, time.UTC), // µs=1
			SentAt:      time.Date(2026, 10, 11, 9, 5, 0, 999000, time.UTC), HasSentAt: true},
	}
	srv := newJourneyServer(t, fake)
	body := journeyRequest(t, srv.Handler(), http.MethodGet, "/api/v1/journey/events", "u-7", "", "").Body.String()
	for _, want := range []string{
		`"scheduled_at":"2026-10-10T09:00:00.001000+00:00"`, // µs=1000 ⇒ 固定 6 位，不是 .001
		`"sent_at":"2026-10-10T09:00:00+00:00"`,             // µs=0 ⇒ 整段省略（绝不会出 .000000）
		`"scheduled_at":"2026-10-11T09:00:00.000001+00:00"`,
		`"sent_at":"2026-10-11T09:05:00.000999+00:00"`,
	} {
		if !strings.Contains(body, want) {
			t.Errorf("响应里没有 %s：%s", want, body)
		}
	}
	if strings.Contains(body, "Z\",") || strings.Contains(body, "+00:00Z") {
		t.Errorf("这条端点不该出现 Z 形态：%s", body)
	}
}

func TestJourneyEventsNaNPayloadFailsLikePython(t *testing.T) {
	// 列里 {"a": NaN}：Python 的 json.loads 认 NaN，但 JSONResponse(allow_nan=False) 抛
	// ⇒ 未处理异常 500；Go 同状态、信封码 50003（与批 2 的发散 ② 同形）。
	fake := prefilledFake(t)
	fake.rows = []domain.JourneyEventRow{{ID: "e-nan", DayNo: 0, Kind: "day0_read",
		Status: "sent", ScheduledAt: time.Date(2026, 10, 10, 9, 0, 0, 0, time.UTC),
		Payload: "{\"a\": NaN}"}}
	srv := newJourneyServer(t, fake)
	rec := journeyRequest(t, srv.Handler(), http.MethodGet, "/api/v1/journey/events", "u-8", "", "")
	if rec.Code != http.StatusInternalServerError ||
		!strings.Contains(rec.Body.String(), `"code":50003`) {
		t.Fatalf("NaN 负载应落 500/50003，得到 %d %s", rec.Code, rec.Body.String())
	}
}

// ── 4. acknowledge ────────────────────────────────────────────────────────────

func TestJourneyAckBodyValidationBytes(t *testing.T) {
	cases := []struct {
		name        string
		body        string
		contentType string
		want        string // 400 体逐字；空串表示期望 200
	}{
		{"no-body", "", "application/json",
			`{"code":40001,"message":"参数错误: Field required / Invalid parameters","data":[` +
				`{"type":"missing","loc":["body"],"msg":"Field required","input":null}],` +
				`"trace_id":"t-journey","user_id":"u-1"}`},
		{"empty-object", `{}`, "application/json",
			`{"code":40001,"message":"参数错误: event_id Field required / Invalid parameters","data":[` +
				`{"type":"missing","loc":["body","event_id"],"msg":"Field required","input":{}}],` +
				`"trace_id":"t-journey","user_id":"u-1"}`},
		{"null-body", `null`, "application/json",
			`{"code":40001,"message":"参数错误: Field required / Invalid parameters","data":[` +
				`{"type":"missing","loc":["body"],"msg":"Field required","input":null}],` +
				`"trace_id":"t-journey","user_id":"u-1"}`},
		{"array-body", `[1]`, "application/json",
			`{"code":40001,"message":"参数错误: Input should be a valid dictionary or object ` +
				`to extract fields from / Invalid parameters","data":[` +
				`{"type":"model_attributes_type","loc":["body"],` +
				`"msg":"Input should be a valid dictionary or object to extract fields from",` +
				`"input":[1]}],"trace_id":"t-journey","user_id":"u-1"}`},
		{"null-field", `{"event_id":null}`, "application/json",
			`{"code":40001,"message":"参数错误: event_id Input should be a valid string ` +
				`/ Invalid parameters","data":[` +
				`{"type":"string_type","loc":["body","event_id"],` +
				`"msg":"Input should be a valid string","input":null}],` +
				`"trace_id":"t-journey","user_id":"u-1"}`},
		{"int-field", `{"event_id":5}`, "application/json",
			`{"code":40001,"message":"参数错误: event_id Input should be a valid string ` +
				`/ Invalid parameters","data":[` +
				`{"type":"string_type","loc":["body","event_id"],` +
				`"msg":"Input should be a valid string","input":5}],` +
				`"trace_id":"t-journey","user_id":"u-1"}`},
		// ctx **只有** max_length：列表的 too_long 还带 field_type/actual_length，
		// 字符串这条没有（2026-10-15 用同版本 pydantic 2.13.4 实测）。
		{"too-long", `{"event_id":"` + strings.Repeat("x", 37) + `"}`, "application/json",
			`{"code":40001,"message":"参数错误: event_id String should have at most 36 characters ` +
				`/ Invalid parameters","data":[` +
				`{"type":"string_too_long","loc":["body","event_id"],` +
				`"msg":"String should have at most 36 characters","input":"` +
				strings.Repeat("x", 37) + `","ctx":{"max_length":36}}],` +
				`"trace_id":"t-journey","user_id":"u-1"}`},
		// 长度按码点数：20 个「你好」是 40 个字符⇒越界，而它的 UTF-8 是 60 字节。
		{"too-long-nonascii", `{"event_id":"` + strings.Repeat("你好", 20) + `"}`, "application/json",
			`{"code":40001,"message":"参数错误: event_id String should have at most 36 characters ` +
				`/ Invalid parameters","data":[` +
				`{"type":"string_too_long","loc":["body","event_id"],` +
				`"msg":"String should have at most 36 characters","input":"` +
				strings.Repeat("你好", 20) + `","ctx":{"max_length":36}}],` +
				`"trace_id":"t-journey","user_id":"u-1"}`},
		{"bad-json", `{"event_id":}`, "application/json",
			// loc 的第二个元素是 pydantic 的字符偏移，且它会**同时**进 message
			// （backend/core/middleware.py:75 只把 "body" 剔掉，数字留下）。
			// 偏移 12 是 '}' 的下标：pydantic-core 报「出错处」而非「出错后一位」
			// （2026-10-15 用 pydantic 2.13.4 实测，见下方注释）。
			`{"code":40001,"message":"参数错误: 12 JSON decode error / Invalid parameters","data":[` +
				`{"type":"json_invalid","loc":["body",12],"msg":"JSON decode error","input":{},` +
				`"ctx":{"error":"Expecting value"}}],"trace_id":"t-journey","user_id":"u-1"}`},
		// 非 JSON 的 Content-Type：pydantic 拿到的是**原始 bytes** 的 repr。
		{"text-plain", `raw:1`, "text/plain",
			`{"code":40001,"message":"参数错误: Input should be a valid dictionary or object ` +
				`to extract fields from / Invalid parameters","data":[` +
				`{"type":"model_attributes_type","loc":["body"],` +
				`"msg":"Input should be a valid dictionary or object to extract fields from",` +
				`"input":"b'raw:1'"}],"trace_id":"t-journey","user_id":"u-1"}`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newJourneyServer(t, nil)
			rec := journeyRequest(t, srv.Handler(), http.MethodPost,
				"/api/v1/journey/events/acknowledge", "u-1", tc.body, tc.contentType)
			if rec.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
			}
			if got := rec.Body.String(); got != tc.want {
				t.Errorf("逐字不符：\n got %s\nwant %s", got, tc.want)
			}
			// 校验失败绝不碰库（Python 的 body 校验在依赖求解阶段，进不了 service）。
			if calls := *srv.batch3.Store.(*journeyFakeStore).calls; len(calls) != 0 {
				t.Errorf("校验失败仍打了库：%v", calls)
			}
		})
	}
}

func TestJourneyAckSuccessAndOwnership(t *testing.T) {
	// 36 字符恰好合法（<=），多余键被忽略（pydantic 默认 extra='ignore'）。
	fake := prefilledFake(t)
	fake.owner = domain.JourneyOwner{ID: "ignored-column-value", UserID: "u-1", Found: true}
	srv := newJourneyServer(t, fake)
	rec := journeyRequest(t, srv.Handler(), http.MethodPost, "/api/v1/journey/events/acknowledge",
		"u-1", `{"event_id":"e-1","other":1}`, "application/json")
	want := `{"code":200,"message":"success","data":{"acknowledged":"e-1"},` +
		`"trace_id":"t-journey","user_id":"u-1"}`
	if rec.Code != http.StatusOK || rec.Body.String() != want {
		t.Fatalf("status = %d body = %s，期望 200 %s", rec.Code, rec.Body.String(), want)
	}
	// **不**做预写：acknowledge 不调 ensure_prefilled（批 3 里只有 GET 会写）。
	for _, call := range *fake.calls {
		if strings.HasPrefix(call, "JourneyUser") || strings.HasPrefix(call, "Prefill") {
			t.Errorf("acknowledge 不该预写：%v", *fake.calls)
		}
	}
	// 回显的是**请求里的** event_id（routers/journey.py:41），不是 event.id 列值。
	// 假件故意把列值设成另一个串，就是为了这条断言能打红「改成取列值」的做法。
	if got := (*fake.calls)[1]; got != "AcknowledgeJourneyEvent(e-1)" {
		t.Errorf("UPDATE 的入参 = %s，期望用请求原文 e-1", got)
	}
}

func TestJourneyAckLengthCountsCodePointsNotBytes(t *testing.T) {
	// max_length=36 数的是**码点**（Python 的 len(str)），不是 UTF-8 字节。
	// 这条是「按字节判长」唯一可证的形态：26 个码点＝39 个字节，
	// 落在「码点合法 / 字节越界」的差集里；只测 37 个 ASCII 或 40 个汉字都证不出来
	// （两种口径在那两侧给出同一个结论）。
	fake := prefilledFake(t)
	fake.owner = domain.JourneyOwner{ID: "x", UserID: "u-1", Found: true}
	srv := newJourneyServer(t, fake)
	ok := journeyRequest(t, srv.Handler(), http.MethodPost,
		"/api/v1/journey/events/acknowledge", "u-1",
		`{"event_id":"`+strings.Repeat("你好", 13)+`"}`, "application/json")
	want := `{"code":200,"message":"success","data":{"acknowledged":"` +
		strings.Repeat("你好", 13) + `"},"trace_id":"t-journey","user_id":"u-1"}`
	if ok.Code != http.StatusOK || ok.Body.String() != want {
		t.Fatalf("26 码点/39 字节被拒：status = %d body = %s", ok.Code, ok.Body.String())
	}

	long := journeyRequest(t, srv.Handler(), http.MethodPost,
		"/api/v1/journey/events/acknowledge", "u-1",
		`{"event_id":"`+strings.Repeat("你好", 19)+`"}`, "application/json")
	if long.Code != http.StatusBadRequest ||
		!strings.Contains(long.Body.String(), `"type":"string_too_long"`) {
		t.Errorf("38 码点必须越界，得到 %d %s", long.Code, long.Body.String())
	}
}

func TestJourneyAckMissingAndForeignAreIdentical(t *testing.T) {
	// 「不存在」与「别人的事件」必须逐字同形（同码同文案），否则这条端点变成 id 枚举 oracle。
	srv := newJourneyServer(t, nil)
	absent := journeyRequest(t, srv.Handler(), http.MethodPost,
		"/api/v1/journey/events/acknowledge", "u-1", `{"event_id":"e-x"}`, "application/json")

	fake := prefilledFake(t)
	fake.owner = domain.JourneyOwner{ID: "e-y", UserID: "u-2", Found: true} // 别人的
	other := newJourneyServer(t, fake)
	foreign := journeyRequest(t, other.Handler(), http.MethodPost,
		"/api/v1/journey/events/acknowledge", "u-1", `{"event_id":"e-y"}`, "application/json")

	want := `{"code":40402,"message":"事件不存在 / Event not found","data":null,` +
		`"trace_id":"t-journey","user_id":"u-1"}`
	for name, rec := range map[string]*httptest.ResponseRecorder{"absent": absent, "foreign": foreign} {
		if rec.Code != http.StatusNotFound || rec.Body.String() != want {
			t.Errorf("%s：status = %d body = %s，期望 404 %s", name, rec.Code, rec.Body.String(), want)
		}
	}
	// 两种情形都不许走到 UPDATE。
	if calls := *fake.calls; len(calls) != 1 {
		t.Errorf("越权后仍写了库：%v", calls)
	}
}

func TestJourneyAckStoreFailuresUseTheExistingCodes(t *testing.T) {
	// 读失败与写失败都落已登记的 50003，不新造码。
	fake := prefilledFake(t)
	fake.ownerErr = errors.New("boom")
	srv := newJourneyServer(t, fake)
	rec := journeyRequest(t, srv.Handler(), http.MethodPost,
		"/api/v1/journey/events/acknowledge", "u-1", `{"event_id":"e-1"}`, "application/json")
	if rec.Code != http.StatusInternalServerError ||
		!strings.Contains(rec.Body.String(), `"code":50003`) {
		t.Errorf("读失败应 500/50003，得到 %d %s", rec.Code, rec.Body.String())
	}

	fake2 := prefilledFake(t)
	fake2.owner = domain.JourneyOwner{ID: "e-1", UserID: "u-1", Found: true}
	fake2.ackErr = errors.New("boom")
	srv2 := newJourneyServer(t, fake2)
	rec2 := journeyRequest(t, srv2.Handler(), http.MethodPost,
		"/api/v1/journey/events/acknowledge", "u-1", `{"event_id":"e-1"}`, "application/json")
	if rec2.Code != http.StatusInternalServerError ||
		!strings.Contains(rec2.Body.String(), `"code":50003`) {
		t.Errorf("写失败应 500/50003，得到 %d %s", rec2.Code, rec2.Body.String())
	}
}

func TestJourneyEventsPrefillFailureDoesNotReadOrLeakPartialState(t *testing.T) {
	// 预写失败⇒信封 50003，且**不再**读列表（Python 的异常同样让整批写回滚，
	// 读下去只会拿到半批行；store 侧的事务口径见 legacy_batch3.go 的 PrefillJourney）。
	fake := prefilledFake(t)
	fake.preErr = errors.New("boom")
	srv := newJourneyServer(t, fake)
	rec := journeyRequest(t, srv.Handler(), http.MethodGet, "/api/v1/journey/events", "u-1", "", "")
	if rec.Code != http.StatusInternalServerError ||
		!strings.Contains(rec.Body.String(), `"code":50003`) {
		t.Fatalf("预写失败应 500/50003，得到 %d %s", rec.Code, rec.Body.String())
	}
	if got := *fake.calls; len(got) != 3 {
		t.Errorf("调用序列 = %v，期望在读列表之前停下", got)
	}
}

// ── 5. 路由接线 ───────────────────────────────────────────────────────────────

func TestJourneyRoutesAbsentWithoutBatch3(t *testing.T) {
	// 机制没接线（Batch3==nil）时 Go 一条都不答：与未注册路径逐字同形。
	srv := newN8Server(t, n8ForbiddenStore{t: t}, n8ForbiddenStore{t: t},
		&n8RecordingRelay{t: t}, nil, nil, nil)
	handler := srv.Handler()
	for _, pair := range [][2]string{
		{http.MethodGet, "/api/v1/journey/events"},
		{http.MethodPost, "/api/v1/journey/events/acknowledge"},
	} {
		rec := journeyRequest(t, handler, pair[0], pair[1], "u-5", `{"event_id":"e-1"}`, "application/json")
		got := rec.Body.String()
		want := `{"code":404,"message":"Not Found","data":null,"trace_id":"t-journey","user_id":"u-5"}`
		if rec.Code != http.StatusNotFound || got != want {
			t.Errorf("%s %s：status = %d body = %s，期望 404 %s",
				pair[0], pair[1], rec.Code, got, want)
		}
	}
}

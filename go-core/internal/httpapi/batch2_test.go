// N8 批 2 在 HTTP 层的单元闸：依赖顺序、真值判定与两个 JSON 回退分支。
//
// 与 n8_replay_test.go 的分工：那边 28 条用例比的是**响应字节**（真库、真 FastAPI 录的基线），
// 它能发现「结果不一样」，发现不了「为什么不一样」。这里钉的是三件结构性事实：
//  1. **调用次序**：两道同意门（两条同样的 SELECT）都在碰 journey_events 之前。
//     Python 侧 require_ai_consent 与 require_user 各查一次 users，
//     合并成一次或颠倒顺序，「已注销」与「越权」谁先报错就会换侧——字节测要恰好
//     有两种用户同时命中才看得见，这里一次就钉住。
//  2. **真值判定**：`bool(payload.get("read_at"))` 不是「非 null」。空串、0、0.0、
//     空数组、空对象在 Python 那边都是「未读」，用判空实现会让这些卡永远计进 unread。
//  3. **回退分支**：坏 payload/坏 refs 必须回退成 {} / []，且 [] 不能是 nil（nil 出 null）。
//
// 外加一条路由闸：Batch2 没接线时这两条路**不存在**（Ingress 一刀之前不许抢先答）。
package httpapi

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// ── 假件：记次序 ──────────────────────────────────────────────────────────────

type batch2FakeStore struct {
	t        *testing.T
	calls    *[]string
	user     domain.CurriculumUser
	cards    []domain.CurriculumCard
	cardsErr error
	event    domain.CurriculumEvent
	eventErr error
	setErr   error
	setText  *string
}

func (f *batch2FakeStore) CurriculumUser(_ context.Context, userID string) (domain.CurriculumUser, error) {
	*f.calls = append(*f.calls, "CurriculumUser("+userID+")")
	out := f.user
	out.ID = userID
	return out, nil
}

func (f *batch2FakeStore) CurriculumCards(_ context.Context, userID, kind string,
	_, _ time.Time, limit int) ([]domain.CurriculumCard, error) {
	*f.calls = append(*f.calls, "CurriculumCards("+userID+","+kind+")")
	if f.cardsErr != nil {
		return nil, f.cardsErr
	}
	if limit != domain.CurriculumListLimit {
		f.t.Errorf("limit = %d，期望 %d（周窗行数上限是契约的一部分）",
			limit, domain.CurriculumListLimit)
	}
	return f.cards, nil
}

func (f *batch2FakeStore) JourneyEventOwner(_ context.Context, eventID string) (domain.CurriculumEvent, error) {
	*f.calls = append(*f.calls, "JourneyEventOwner("+eventID+")")
	return f.event, f.eventErr
}

func (f *batch2FakeStore) SetJourneyPayload(_ context.Context, eventID, payload string) error {
	*f.calls = append(*f.calls, "SetJourneyPayload("+eventID+")")
	f.setText = &payload
	return f.setErr
}

func batch2Request(t *testing.T, handler http.Handler, method, path, userID string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, nil)
	// 与夹具同一条身份通道：X-User-Id（开发态中间件接受它，见 middleware.go 的 RequireBearer）。
	req.Header.Set("X-User-Id", userID)
	// 固定 trace_id：不钉住的话，「两个响应逐字相同」比的是每请求一个的 UUID。
	req.Header.Set(TraceHeader, "t-batch2")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	return rec
}

// consentingFake 是「已过两道同意门」的默认假件：各用例只改自己要的那一列。
func consentingFake(t *testing.T) *batch2FakeStore {
	t.Helper()
	return &batch2FakeStore{t: t, calls: &[]string{},
		user: domain.CurriculumUser{Found: true, AIConsent: true, Timezone: "UTC", Language: "en"}}
}

func newBatch2Server(t *testing.T, fake *batch2FakeStore) *Server {
	t.Helper()
	if fake == nil {
		fake = consentingFake(t)
	}
	srv := newN8Server(t, n8ForbiddenStore{t: t}, n8ForbiddenStore{t: t},
		&n8RecordingRelay{t: t}, &Batch2{Store: fake, Gate: nil}, nil, nil)
	srv.now = func() time.Time { return time.Date(2026, 10, 14, 14, 45, 30, 0, time.UTC) }
	return srv
}

// ── 1. 调用次序 ───────────────────────────────────────────────────────────────

func TestCurriculumWeekReadsConsentBeforeCards(t *testing.T) {
	srv := newBatch2Server(t, nil)
	rec := batch2Request(t, srv.Handler(), http.MethodGet, "/api/v1/curriculum/week", "u-1")
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	fake := srv.batch2.Store.(*batch2FakeStore)
	// 两条同样的 SELECT 在前，卡表读在后：次序就是 Python 那两行 await。
	want := []string{"CurriculumUser(u-1)", "CurriculumUser(u-1)", "CurriculumCards(u-1,curriculum)"}
	if len(*fake.calls) != len(want) {
		t.Fatalf("调用序列 = %v，期望 %v", *fake.calls, want)
	}
	for i, c := range *fake.calls {
		if c != want[i] {
			t.Fatalf("调用序列 = %v，期望 %v", *fake.calls, want)
		}
	}
	// 空卡表也出 []：Python 的 days=[] 是空数组。
	body := rec.Body.String()
	if want := `"days":[]`; !strings.Contains(body, want) {
		t.Errorf("响应里没有 %s：%s", want, body)
	}
}

func TestCurriculumConsentFailuresStopBeforeTheEventTable(t *testing.T) {
	for _, c := range []struct {
		name  string
		user  domain.CurriculumUser
		want  string
		calls int
	}{
		{
			name: "无行⇒40401",
			user: domain.CurriculumUser{Found: false},
			want: `"code":40401`, calls: 1,
		},
		{
			name: "未同意⇒40303",
			user: domain.CurriculumUser{Found: true, AIConsent: false},
			want: `"code":40303`, calls: 1,
		},
	} {
		t.Run(c.name, func(t *testing.T) {
			fake := &batch2FakeStore{t: t, calls: &[]string{}, user: c.user}
			srv := newBatch2Server(t, fake)
			rec := batch2Request(t, srv.Handler(), http.MethodGet, "/api/v1/curriculum/week", "u-2")
			if !strings.Contains(rec.Body.String(), c.want) {
				t.Errorf("响应体 = %s，期望含 %s", rec.Body.String(), c.want)
			}
			if len(*fake.calls) != c.calls {
				t.Errorf("调用 = %v，期望恰好 %d 次（第二趟 SELECT 只在两关都过时才打）",
					*fake.calls, c.calls)
			}
			for _, call := range *fake.calls {
				if strings.Contains(call, "CurriculumCards") {
					t.Error("同意门没过还去读了卡表：Python 侧这里直接抛异常")
				}
			}
		})
	}
}

func TestCurriculumReadForbiddenIsOneMergedCode(t *testing.T) {
	// 三种情形必须合成同一条 403：无行 / 别人的 / 别的 kind。
	// 分开出码＝这个端点变成 id 枚举 oracle（能区分「不存在」与「无权」）。
	for _, c := range []struct {
		name  string
		event domain.CurriculumEvent
	}{
		{"无行", domain.CurriculumEvent{Found: false}},
		{"别人的卡", domain.CurriculumEvent{Found: true, UserID: "someone-else",
			Kind: domain.CurriculumKind}},
		{"别的 kind", domain.CurriculumEvent{Found: true, UserID: "u-3", Kind: "journey"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			fake := consentingFake(t)
			fake.event = c.event
			srv := newBatch2Server(t, fake)
			rec := batch2Request(t, srv.Handler(), http.MethodPost,
				"/api/v1/curriculum/ev-1/read", "u-3")
			if rec.Code != http.StatusForbidden {
				t.Errorf("status = %d, body = %s", rec.Code, rec.Body.String())
			}
			if want := `{"code":403,"message":"无权访问该主题卡"`; !strings.Contains(rec.Body.String(), want) {
				t.Errorf("响应体 = %s，期望以 %s 开头", rec.Body.String(), want)
			}
			for _, call := range *fake.calls {
				if strings.Contains(call, "SetJourneyPayload") {
					t.Error("越权分支写库了：幂等与越权两条路都不能触发 UPDATE")
				}
			}
		})
	}
}

func TestCurriculumReadIsIdempotentInStoreCalls(t *testing.T) {
	// read_at 已有真值 ⇒ 第二次调用**不写库**，回同样的字节（Python: if not payload.get(...)）。
	// 假件里的 id 故意与路径参数不同：Python 响应中的 id 来自 event.id（列值），
	// handler 若改成贴 PathValue 就是回显客户端输入，这里立刻红。
	event := domain.CurriculumEvent{Found: true, ID: "ev-4-from-column", UserID: "u-4",
		Kind:    domain.CurriculumKind,
		Payload: `{"title":"T","read_at":"2026-10-13T01:02:03.004005+00:00"}`}
	fake := consentingFake(t)
	fake.event = event
	srv := newBatch2Server(t, fake)
	rec := batch2Request(t, srv.Handler(), http.MethodPost, "/api/v1/curriculum/ev-4/read", "u-4")
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if want := `{"id":"ev-4-from-column","read":true}`; !strings.Contains(rec.Body.String(), want) {
		t.Errorf("响应体 = %s，期望含 %s", rec.Body.String(), want)
	}
	for _, call := range *fake.calls {
		if strings.Contains(call, "SetJourneyPayload") {
			t.Fatalf("已读卡又写了一次库：%v", *fake.calls)
		}
	}

	// 未读则恰好一次 UPDATE，且写回的是**整份** payload（其余键位置不动）。
	fake.event.Payload = `{"title":"T","read_at":null}`
	fake.calls = &[]string{}
	rec = batch2Request(t, srv.Handler(), http.MethodPost, "/api/v1/curriculum/ev-4/read", "u-4")
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	writes := 0
	for _, call := range *fake.calls {
		if strings.Contains(call, "SetJourneyPayload") {
			writes++
		}
	}
	if writes != 1 {
		t.Fatalf("UPDATE 次数 = %d，期望 1：%v", writes, *fake.calls)
	}
	if fake.setText == nil {
		t.Fatal("没有写回文本")
	}
	if !strings.Contains(*fake.setText, `"title":"T"`) || !strings.Contains(*fake.setText, `"read_at":`) {
		t.Errorf("写回的 payload 丢了原有键：%s", *fake.setText)
	}
	// read_at 的格式是 Python 的 datetime.isoformat()（+00:00），不是 pydantic 的 Z。
	if !strings.Contains(*fake.setText, `+00:00"`) {
		t.Errorf("read_at 不是 isoformat 形状：%s", *fake.setText)
	}
	// 已知发散（2026-10-15 登记在 tasks.md）：Python 侧 set_payload 用 `json.dumps(…,
	// ensure_ascii=False)` 的**默认分隔符**（journey_repository.py:202，即 `", "`/`": "`），
	// Go 复用响应面那套紧凑形（Starlette 的 JSONResponse 才是紧凑的）。
	// 把当前形态钉成断言，免得日后有人拿「两侧库内文本逐字相同」当既成事实：
	// 差的只是空白，两侧读回都过 json.loads，响应字节另由 28 条回放用例证。
	if !strings.HasPrefix(*fake.setText, `{"title":"T","read_at":"`) {
		t.Errorf("写回文本的紧凑分隔符形态变了（这一条测的是发散本身，不是等价）：%s",
			*fake.setText)
	}
}

func TestCurriculumRoutesAbsentWithoutBatch2(t *testing.T) {
	// 机制没接线（Batch2==nil）时 Go 一条都不答：与未注册路径逐字同形。
	srv := newN8Server(t, n8ForbiddenStore{t: t}, n8ForbiddenStore{t: t},
		&n8RecordingRelay{t: t}, nil, nil, nil)
	handler := srv.Handler()
	for _, path := range []string{"/api/v1/curriculum/week", "/api/v1/curriculum/ev-1/read"} {
		got := batch2Request(t, handler, http.MethodGet, path, "u-5")
		want := batch2Request(t, handler, http.MethodGet, "/api/v1/no-such-domain/thing", "u-5")
		if got.Code != want.Code || got.Body.String() != want.Body.String() {
			t.Errorf("%s 与未注册路径不同形：%d %s vs %d %s", path,
				got.Code, got.Body.String(), want.Code, want.Body.String())
		}
	}
	// 接线后同一请求必须**不像**未注册路径（证明注册真的发生了）。
	wired := newBatch2Server(t, nil)
	rec := batch2Request(t, wired.Handler(), http.MethodGet, "/api/v1/curriculum/week", "u-5")
	if rec.Code != http.StatusOK {
		t.Errorf("接线后应由 Go 答，实为 %d %s", rec.Code, rec.Body.String())
	}
}

// ── 2. 真值判定 ───────────────────────────────────────────────────────────────

func TestPyTruthyIsPythonBoolNotNullCheck(t *testing.T) {
	for _, c := range []struct {
		json string
		want bool
	}{
		{`{"v":null}`, false}, {`{"v":false}`, false}, {`{"v":0}`, false},
		{`{"v":0.0}`, false}, {`{"v":""}`, false}, {`{"v":[]}`, false},
		{`{"v":{}}`, false},
		// 这一侧全是「非 null 但为假」的值：判空实现会在这里全错。
		{`{"v":true}`, true}, {`{"v":1}`, true}, {`{"v":-1}`, true},
		{`{"v":0.5}`, true}, {`{"v":"2026-10-13"}`, true}, {`{"v":[0]}`, true},
		{`{"v":{"k":null}}`, true},
	} {
		t.Run(c.json, func(t *testing.T) {
			v, err := pyJSONLoads([]byte(c.json))
			if err != nil {
				t.Fatalf("pyJSONLoads: %v", err)
			}
			if got := pyTruthy(payloadGet(v, "v")); got != c.want {
				t.Errorf("pyTruthy = %v，期望 %v", got, c.want)
			}
		})
	}
	// 缺键＝None＝假。响应里这个键仍然出现（null 而不是省略）由 pyjson 的
	// payloadGet + set 保证，那部分在 28 条回放用例的 week-falsy 里逐字比过。
	v, err := pyJSONLoads([]byte(`{"other":1}`))
	if err != nil {
		t.Fatalf("pyJSONLoads: %v", err)
	}
	if pyTruthy(payloadGet(v, "read_at")) {
		t.Error("缺键必须判假")
	}
	text, jerr := v.json()
	if jerr != nil || text != `{"other":1}` {
		t.Errorf("对象重排/改写了合法输入：%q %v", text, jerr)
	}
}

// ── 3. 两个回退分支 ───────────────────────────────────────────────────────────

func TestPayloadObjectFallsBackToEmptyObject(t *testing.T) {
	for _, raw := range []string{
		"", "   ", "{", "not json", `[1,2]`, `"文本"`, `123`, `null`, `true`,
	} {
		v := payloadObject(raw)
		if v.kind != pyObject || len(v.members) != 0 {
			t.Errorf("payloadObject(%q) = %+v，期望空对象", raw, v)
		}
		text, err := v.json()
		if err != nil || text != "{}" {
			t.Errorf("payloadObject(%q).json() = %q, %v", raw, text, err)
		}
	}
	// 合法对象原样保留（键序与值都由 pyjson 负责）。
	v := payloadObject(`{"b":1,"a":[2,3]}`)
	text, err := v.json()
	if err != nil || text != `{"b":1,"a":[2,3]}` {
		t.Errorf("合法 payload 被改写：%q %v", text, err)
	}
}

func TestRefsArrayFallsBackToEmptyArrayNotNull(t *testing.T) {
	for _, raw := range []string{"", "broken", `{"a":1}`, `"s"`, `3`, `null`, `true`} {
		v := refsArray(raw)
		if v.kind != pyArray {
			t.Errorf("refsArray(%q) kind = %v，期望数组", raw, v.kind)
			continue
		}
		text, err := v.json()
		if err != nil || text != "[]" {
			t.Errorf("refsArray(%q).json() = %q, %v（nil 会出 null，Python 那边是 []）", raw, text, err)
		}
	}
	v := refsArray(`["m1", 2, null]`)
	text, err := v.json()
	if err != nil || text != `["m1",2,null]` {
		t.Errorf("合法 refs 被改写：%q %v", text, err)
	}
}

func TestStoreErrorBecomesDatabaseEnvelope(t *testing.T) {
	fake := consentingFake(t)
	fake.cardsErr = errors.New("boom")
	srv := newBatch2Server(t, fake)
	rec := batch2Request(t, srv.Handler(), http.MethodGet, "/api/v1/curriculum/week", "u-6")
	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if !strings.Contains(rec.Body.String(), `"code":50003`) {
		t.Errorf("响应体 = %s，期望 50003 信封", rec.Body.String())
	}
}

// ── 4. 两个 isoformat 的分工 ─────────────────────────────────────────────────

func TestPyISOOffsetAndPyISOAreDifferentFormats(t *testing.T) {
	// 同一个瞬间：响应里的 datetime 走 pydantic 序列化器（Z 结尾），
	// 而 payload.read_at 是 Python 自己 isoformat() 后塞进文本的（+00:00）。
	// 混用会让 read_at 与 week 视图里的 datetime 长成两种样子。
	at := time.Date(2026, 10, 14, 14, 45, 30, 123456000, time.UTC)
	if got, want := pyISO(at), "2026-10-14T14:45:30.123456Z"; got != want {
		t.Errorf("pyISO = %s，期望 %s", got, want)
	}
	if got, want := pyISOOffset(at), "2026-10-14T14:45:30.123456+00:00"; got != want {
		t.Errorf("pyISOOffset = %s，期望 %s", got, want)
	}
	// µs==0 时整段省略，两个格式同一条规则。
	whole := time.Date(2026, 10, 14, 14, 45, 30, 0, time.UTC)
	if got, want := pyISO(whole), "2026-10-14T14:45:30Z"; got != want {
		t.Errorf("pyISO(整秒) = %s，期望 %s", got, want)
	}
	if got, want := pyISOOffset(whole), "2026-10-14T14:45:30+00:00"; got != want {
		t.Errorf("pyISOOffset(整秒) = %s，期望 %s", got, want)
	}
	// 前三条字节去掉尾部的 Z/+00:00 必须完全一致：只差后缀，不许差秒。
	if pyISO(at)[:len(pyISO(at))-1] != pyISOOffset(at)[:len(pyISOOffset(at))-6] {
		t.Error("两个格式的日期部分不一致")
	}
}

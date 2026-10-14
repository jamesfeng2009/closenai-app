// N8 批 4 在 HTTP 层的单元闸：三处写的条件不同源、卡片键序、restore 的四种出口。
//
// 与 n8_replay_test.go 的分工沿用批 2/批 3：那边比**响应字节**（真库、真 FastAPI 录的基线），
// 这里钉字节测看不见的三件事：
//  1. **调用次序与条件分支**：upsert 无条件、推荐码与 prefill 有条件（batch4.go 头注释）。
//     「账号不存在」时两侧都得回 200 并留下一行 profile，而夹具里造一个 status≠active
//     又同时能通过鉴权的账号代价极大（GET 不带写、POST 只写 profile），所以这一支在
//     Go 侧用假件直接钉序列。
//  2. **share_url 的同源性**：响应里那串与 first_read 列文本里那串必须逐字相同
//     （restore 之后按列复现、不再回查），夹具能测出「不同源」但测不出「为什么不同源」，
//     所以这里同时断言两处文本。
//  3. **restore 的四种出口**：两种 40402、两种 500。库内损坏不能伪装成「没生成过」。
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

const (
	firstReadPath   = "/api/v1/onboarding/first-read"
	testSiteURL     = "https://closenai.test"
	testAppStoreURL = "https://apps.apple.com/app/closenai/idTEST"
)

// ── 假件 ──────────────────────────────────────────────────────────────────────

type onboardingFakeStore struct {
	t     *testing.T
	calls *[]string

	user    domain.JourneyUser
	code    string
	submit  domain.OnboardingWrite
	profile domain.OnboardingProfile
	profErr error
	subErr  error

	// buildErr 让回调本身失败，用来钉「build 报错时事务不提交」（三处写一起回滚的前提）。
	buildErr error
}

func (f *onboardingFakeStore) SubmitOnboarding(_ context.Context, userID string,
	build domain.OnboardingSubmitBuilder) (string, error) {
	*f.calls = append(*f.calls, "SubmitOnboarding("+userID+")")
	if f.subErr != nil {
		return "", f.subErr
	}
	in := domain.OnboardingSubmitInput{User: f.user, Code: f.code}
	write, err := build(in)
	if err != nil {
		return "", err
	}
	f.submit = write
	if f.profile.Found {
		return f.profile.ID, nil
	}
	return "00000000-0000-4000-8000-000000000001", nil
}

func (f *onboardingFakeStore) OnboardingProfile(_ context.Context,
	userID string) (domain.OnboardingProfile, error) {
	*f.calls = append(*f.calls, "OnboardingProfile("+userID+")")
	if f.profErr != nil {
		return domain.OnboardingProfile{}, f.profErr
	}
	return f.profile, nil
}

func newOnboardingServer(t *testing.T, fake *onboardingFakeStore) *Server {
	t.Helper()
	if fake == nil {
		fake = &onboardingFakeStore{t: t, calls: &[]string{},
			user: domain.JourneyUser{Found: true, Timezone: "UTC", Language: "en",
				PushTimeRaw: "09:00"},
			code: "AB7CDEFG"}
	}
	srv := newN8Server(t, n8ForbiddenStore{t: t}, n8ForbiddenStore{t: t},
		&n8NoRelayStore{t: t}, nil, nil, &Batch4{Store: fake, Gate: nil})
	srv.cfg.SiteURL = testSiteURL
	srv.cfg.AppStoreURL = testAppStoreURL
	srv.now = func() time.Time { return time.Date(2026, 10, 14, 14, 45, 30, 0, time.UTC) }
	return srv
}

func firstReadRequest(t *testing.T, handler http.Handler, method, userID, body string,
	contentType string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, firstReadPath, strings.NewReader(body))
	req.Header.Set("X-User-Id", userID)
	req.Header.Set(TraceHeader, "t-batch4")
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	return rec
}

// ── 1. 响应形状：九键顺序 ＋ id 追加 ──────────────────────────────────────────

func TestFirstReadSubmitKeyOrderAndNulls(t *testing.T) {
	srv := newOnboardingServer(t, nil)
	rec := firstReadRequest(t, srv.Handler(), http.MethodPost, "u-1",
		`{"answers":[{"q":"conflict","option":"speak_now"}],"language":"zh"}`,
		"application/json")
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	body := rec.Body.String()
	// data 里的键序就是 build_first_read 那个 dict 字面量的顺序（:154-164），id 追加在最后。
	want := []string{
		`"card_type":"first_read"`, `"language":"zh"`, `"persona_type":null`,
		`"assertions":[`, `"summary":""`, `"closer":"`, `"answer_count":2`,
		`"share_url":"https://closenai.test/r/AB7CDEFG"`,
		`"generated_at":"2026-10-14T14:45:30+00:00"`, `"id":"00000000`,
	}
	at := -1
	for _, frag := range want {
		i := strings.Index(body, frag)
		if i < 0 {
			t.Fatalf("响应缺少 %s：%s", frag, body)
		}
		if i < at {
			t.Fatalf("键序漂移：%s 出现在前一个键之前：%s", frag, body)
		}
		at = i
	}
	if !strings.HasSuffix(body, "}\n") && strings.HasSuffix(body, "\n") {
		t.Error("响应体带尾随换行：Starlette 的 JSONResponse 不写换行")
	}
}

// TestFirstReadSubmitGeneratedAtOffset 「+00:00」而不是「Z」：这条键是 service 自己
// 折好的**字符串**（utc_now().isoformat()），不是 datetime 过 pydantic。
func TestFirstReadSubmitGeneratedAtOffset(t *testing.T) {
	srv := newOnboardingServer(t, nil)
	rec := firstReadRequest(t, srv.Handler(), http.MethodPost, "u-1", `{}`, "application/json")
	body := rec.Body.String()
	if !strings.Contains(body, `+00:00"`) || strings.Contains(body, `"Z"`) {
		t.Errorf("generated_at 形状不对：%s", body)
	}
}

// ── 2. 三处写的条件不同源 ＋ share_url 同源 ────────────────────────────────────

func TestFirstReadSubmitWritesShareURLIntoColumn(t *testing.T) {
	srv := newOnboardingServer(t, nil)
	rec := firstReadRequest(t, srv.Handler(), http.MethodPost, "u-1",
		`{"answers":[{"q":"night","option":"replay"}],"persona_type":"intj"}`,
		"application/json")
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	fake := srv.batch4.Store.(*onboardingFakeStore)
	got := *fake.calls
	if len(got) != 1 || got[0] != "SubmitOnboarding(u-1)" {
		t.Fatalf("调用序列 = %v", got)
	}
	// 响应与列文本里的 share_url 必须逐字相同（restore 按列复现，事后改不落盘）。
	if !strings.Contains(fake.submit.FirstReadText,
		`"share_url": "https://closenai.test/r/AB7CDEFG"`) {
		t.Errorf("first_read 列文本里没有同一个 share_url：%s", fake.submit.FirstReadText)
	}
	if !strings.Contains(rec.Body.String(), `"share_url":"https://closenai.test/r/AB7CDEFG"`) {
		t.Errorf("响应里的 share_url 与列不同源：%s", rec.Body.String())
	}
	// persona_type 归一后进列（校验层已 upper），语言用**请求原文**。
	if fake.submit.PersonaType != "INTJ" {
		t.Errorf("PersonaType = %q，期望 INTJ", fake.submit.PersonaType)
	}
	if fake.submit.Language != "en" {
		t.Errorf("Language = %q，期望缺键时的默认 en", fake.submit.Language)
	}
	// 列文本走 CPython json.dumps 的**默认**分隔符（带空格），响应面走紧凑形。
	if !strings.Contains(fake.submit.FirstReadText, `"card_payload": {`) ||
		strings.Contains(fake.submit.FirstReadText, `"card_payload":{`) {
		t.Errorf("first_read 列文本的分隔符不是 CPython 默认形：%s", fake.submit.FirstReadText)
	}
	if !strings.Contains(fake.submit.AnswersText, `{"q": "night", "option": "replay"}`) {
		t.Errorf("answers 列文本 = %s", fake.submit.AnswersText)
	}
	// POST 的 base_day 是「用户本地今天」⇒ 六行全不 Past ⇒ 一行都不会被置 skipped。
	if len(fake.submit.JourneyRows) != len(domain.ScriptEvents) {
		t.Fatalf("剧本行数 = %d，期望 %d", len(fake.submit.JourneyRows), len(domain.ScriptEvents))
	}
	for _, row := range fake.submit.JourneyRows {
		if row.Past {
			t.Errorf("%s 的 Past = true，POST 当天不该有过去的剧本日", row.Kind)
		}
	}
}

// TestFirstReadSubmitWithoutUser 是本批最容易被「顺手统一条件」改掉的一格：
// 账号读不到（不存在/已注销）时 **profile 照样写**、剧本照样不写、
// share_url 用 app_store_url 原文。三处写各有各的条件。
func TestFirstReadSubmitWithoutUser(t *testing.T) {
	fake := &onboardingFakeStore{t: t, calls: &[]string{},
		user: domain.JourneyUser{Found: false}}
	srv := newOnboardingServer(t, fake)
	rec := firstReadRequest(t, srv.Handler(), http.MethodPost, "u-gone", `{}`, "application/json")
	if rec.Code != http.StatusOK {
		t.Fatalf("账号读不到也要出 200（Python 的 upsert 不在 if 块里）：%d %s",
			rec.Code, rec.Body.String())
	}
	if fake.submit.JourneyRows != nil {
		t.Errorf("账号读不到却预写了剧本：%#v", fake.submit.JourneyRows)
	}
	if !strings.Contains(rec.Body.String(), `"share_url":"`+testAppStoreURL+`"`) {
		t.Errorf("share_url 没回退到 app_store_url：%s", rec.Body.String())
	}
	if !strings.Contains(fake.submit.FirstReadText, testAppStoreURL) {
		t.Errorf("列文本里的 share_url 与响应不同源：%s", fake.submit.FirstReadText)
	}
}

// TestFirstReadSubmitExistingUserWithExistingCode 钉「已有码原样复用」：
// 列里已有 referral_code 时不生成、不 UPDATE，而 share_url 用的就是那一列。
func TestFirstReadSubmitExistingUserWithExistingCode(t *testing.T) {
	fake := &onboardingFakeStore{t: t, calls: &[]string{},
		user: domain.JourneyUser{Found: true, Timezone: "UTC", Language: "en",
			PushTimeRaw: "09:00"},
		code: "ZZZ22222"}
	srv := newOnboardingServer(t, fake)
	rec := firstReadRequest(t, srv.Handler(), http.MethodPost, "u-1", `{}`, "application/json")
	if !strings.Contains(rec.Body.String(), `"share_url":"https://closenai.test/r/ZZZ22222"`) {
		t.Errorf("已有的码没被原样复用：%s", rec.Body.String())
	}
}

func TestFirstReadSubmitStoreErrorIsNotSilentOK(t *testing.T) {
	fake := &onboardingFakeStore{t: t, calls: &[]string{},
		subErr: errors.New("boom")}
	srv := newOnboardingServer(t, fake)
	rec := firstReadRequest(t, srv.Handler(), http.MethodPost, "u-1", `{}`, "application/json")
	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if !strings.Contains(rec.Body.String(), `"code":50003`) {
		t.Errorf("库错误没有落 50003：%s", rec.Body.String())
	}
}

// ── 3. restore 的四种出口 ─────────────────────────────────────────────────────

func TestFirstReadRestoreFourExits(t *testing.T) {
	notFound := `{"code":40402,"message":"尚未生成初读卡 / First-read card not generated yet"`
	for _, tc := range []struct {
		name    string
		profile domain.OnboardingProfile
		want    int
		frag    string
	}{
		{"no-row", domain.OnboardingProfile{Found: false}, http.StatusNotFound, notFound},
		{"broken-json", domain.OnboardingProfile{Found: true, ID: "p1", FirstRead: "{oops"},
			http.StatusNotFound, notFound},
		{"not-an-object", domain.OnboardingProfile{Found: true, ID: "p1", FirstRead: `5`},
			http.StatusInternalServerError, `"code":50003`},
		{"empty-object", domain.OnboardingProfile{Found: true, ID: "p1", FirstRead: `{}`},
			http.StatusOK, `"id":"p1"`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			fake := &onboardingFakeStore{t: t, calls: &[]string{}, profile: tc.profile}
			srv := newOnboardingServer(t, fake)
			rec := firstReadRequest(t, srv.Handler(), http.MethodGet, "u-1", "", "")
			if rec.Code != tc.want {
				t.Fatalf("status = %d，期望 %d，body = %s", rec.Code, tc.want, rec.Body.String())
			}
			if !strings.Contains(rec.Body.String(), tc.frag) {
				t.Fatalf("body = %s，期望含 %s", rec.Body.String(), tc.frag)
			}
		})
	}
}

// TestFirstReadRestoreIdStaysInPlace 是本层唯一会改 JSON 形状的地方：
// restore 走 `payload.update({...})` ⇒ **已有键就地换值、位置不动；缺的键追加在末尾**。
// POST 写出的 card_payload 里既没有 id 也没有 created_at，所以两者都追加（跟在
// generated_at 之后）；而 language/assertions/summary/closer 四个键原位被列值覆盖。
// 手工造的行若把 id 放进 card_payload，它就留在原位——两种形状都由同一支 set() 决定。
func TestFirstReadRestoreIdStaysInPlace(t *testing.T) {
	inner := `{"assertions":["外层甲"],"summary":"外层乙","closer":"外层丙","card_payload":{"card_type":"first_read","language":"en","persona_type":null,"assertions":["卡内甲"],"summary":"卡内乙","closer":"卡内丙","answer_count":1,"share_url":"https://x/r/AAAA2222","generated_at":"2026-10-01T00:00:00+00:00"}}`
	fake := &onboardingFakeStore{t: t, calls: &[]string{},
		profile: domain.OnboardingProfile{Found: true, ID: "p-9", FirstRead: inner,
			Language: "zh", HasLanguage: true,
			CreatedAt: time.Date(2026, 10, 2, 3, 4, 5, 0, time.UTC), HasCreatedAt: true}}
	srv := newOnboardingServer(t, fake)
	rec := firstReadRequest(t, srv.Handler(), http.MethodGet, "u-1", "", "")
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	body := rec.Body.String()
	want := []string{
		`"card_type":"first_read"`, `"language":"zh"`, `"persona_type":null`,
		`"assertions":["外层甲"]`, `"summary":"外层乙"`, `"closer":"外层丙"`,
		`"answer_count":1`, `"share_url":"https://x/r/AAAA2222"`,
		`"generated_at":"2026-10-01T00:00:00+00:00"`, `"id":"p-9"`,
		`"created_at":"2026-10-02T03:04:05+00:00"`,
	}
	at := -1
	for _, frag := range want {
		i := strings.Index(body, frag)
		if i < 0 {
			t.Fatalf("恢复的响应缺少 %s：%s", frag, body)
		}
		if i < at {
			t.Fatalf("restore 的键序漂移到 %s 之前：%s", frag, body)
		}
		at = i
	}
	// assertions 取**外层** card 的那一份，不是 card_payload 里的（两份今天同源，
	// 但外层为空时会换成 []，这里用不同的字面量把这条判据钉住）。
	if strings.Contains(body, "卡内甲") {
		t.Errorf("响应里还留着 card_payload 的 assertions：%s", body)
	}
}

// TestFirstReadRestoreIdKeepsSlotWhenColumnHasIt 钉另一半：列里已带 id ⇒ 就地覆盖，不追加。
func TestFirstReadRestoreIdKeepsSlotWhenColumnHasIt(t *testing.T) {
	fake := &onboardingFakeStore{t: t, calls: &[]string{},
		profile: domain.OnboardingProfile{Found: true, ID: "p-7",
			FirstRead: `{"card_payload":{"id":"old","card_type":"first_read"}}`}}
	srv := newOnboardingServer(t, fake)
	rec := firstReadRequest(t, srv.Handler(), http.MethodGet, "u-1", "", "")
	body := rec.Body.String()
	if !strings.Contains(body, `"data":{"id":"p-7","card_type":"first_read"`) {
		t.Errorf("原位覆盖没生效（或追加到了末尾）：%s", body)
	}
	if strings.Contains(body, "old") {
		t.Errorf("列里的旧 id 还在：%s", body)
	}
}

// TestFirstReadRestoreNullColumns 「NULL 就是 null，键不省略」：language/created_at 两列
// 都可空，缺这一支会写出 ""/零值时间，与 Python 的 None 不同形。
func TestFirstReadRestoreNullColumns(t *testing.T) {
	fake := &onboardingFakeStore{t: t, calls: &[]string{},
		profile: domain.OnboardingProfile{Found: true, ID: "p-1", FirstRead: `{"card_payload":{"a":1}}`}}
	srv := newOnboardingServer(t, fake)
	rec := firstReadRequest(t, srv.Handler(), http.MethodGet, "u-1", "", "")
	body := rec.Body.String()
	if !strings.Contains(body, `"language":null`) || !strings.Contains(body, `"created_at":null`) {
		t.Errorf("可空列没写成 null：%s", body)
	}
	// card_payload 里的私有键原样保留（dict(card_payload) 是浅拷贝，不是白名单）。
	if !strings.Contains(body, `"a":1`) {
		t.Errorf("card_payload 的键被吞了：%s", body)
	}
}

func TestFirstReadRestoreProfileError(t *testing.T) {
	fake := &onboardingFakeStore{t: t, calls: &[]string{},
		profErr: errors.New("db down")}
	srv := newOnboardingServer(t, fake)
	rec := firstReadRequest(t, srv.Handler(), http.MethodGet, "u-1", "", "")
	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
}

// ── 4. 校验面的错误字节 ───────────────────────────────────────────────────────

func TestFirstReadValidationBytes(t *testing.T) {
	// 校验失败走的是本项目的信封：HTTP 400 ＋ code 40001，data 是 pydantic 的
	// loc/msg/type/ctx/input 列表（批 1 的 analytics 同形，不是 FastAPI 默认的 422）。
	for _, tc := range []struct{ name, body, want string }{
		{"empty-body", "", `"type":"missing"`},
		{"null-body", "null", `"type":"missing"`},
		{"scalar-body", `"x"`, `"type":"model_attributes_type"`},
		{"answers-not-list", `{"answers":"x"}`, `"type":"list_type"`},
		{"item-not-dict", `{"answers":["x"]}`, `"type":"model_attributes_type"`},
		{"both-missing", `{"answers":[{}]}`, `"loc":["body","answers",0,"q"]`},
		{"lang-null", `{"language":null}`, `"loc":["body","language"]`},
		{"persona-bad", `{"persona_type":"INTJJ"}`, "非法的性格类型: INTJJ"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := newOnboardingServer(t, nil)
			rec := firstReadRequest(t, srv.Handler(), http.MethodPost, "u-1",
				tc.body, "application/json")
			if rec.Code != http.StatusBadRequest {
				t.Fatalf("%s: status = %d, body = %s", tc.name, rec.Code, rec.Body.String())
			}
			if !strings.Contains(rec.Body.String(), tc.want) {
				t.Errorf("%s: body = %s，期望含 %s", tc.name, rec.Body.String(), tc.want)
			}
		})
	}
}

// TestFirstReadValidationFieldOrder 钉「三字段各判各的、按声明序累积」：
// 一支请求里三个字段全错 ⇒ 三条错误，顺序 answers→persona_type→language。
// 这是本域与批 1（只有一个字段，看不出短路与否）的分界。
func TestFirstReadValidationFieldOrder(t *testing.T) {
	srv := newOnboardingServer(t, nil)
	rec := firstReadRequest(t, srv.Handler(), http.MethodPost, "u-1",
		`{"answers":"x","persona_type":1,"language":2}`, "application/json")
	body := rec.Body.String()
	at := -1
	for _, frag := range []string{`"loc":["body","answers"]`,
		`"loc":["body","persona_type"]`, `"loc":["body","language"]`} {
		i := strings.Index(body, frag)
		if i < 0 {
			t.Fatalf("少了一条错误 %s：%s", frag, body)
		}
		if i < at {
			t.Fatalf("错误顺序不是声明序：%s", body)
		}
		at = i
	}
	if strings.Count(body, `"type":`) != 3 {
		t.Errorf("应当恰好三条错误：%s", body)
	}
}

// TestFirstReadPersonaStripAndUpperReachable 是本批两条**经 API 今天就能造出**的发散：
// \x1c 是 CPython 的空白、ß 大写展开成 SS。两条都出 200，用标准库会错成 422。
func TestFirstReadPersonaStripAndUpperReachable(t *testing.T) {
	// 请求原文里用 JSON 的 \u001c 转义（与客户端发过来的字节同形），
	// 第二列是它解码后的 Go 字符串。
	for _, tc := range []struct{ jsonLit, decoded, want string }{
		{`\u001cINTJ`, "\x1cINTJ", "INTJ"},
		{`ßß`, "ßß", "SSSS"},
		{`  intj  `, "  intj  ", "INTJ"},
		{`ﬁnt`, "ﬁnt", "FINT"},
	} {
		srv := newOnboardingServer(t, nil)
		body := `{"persona_type":"` + tc.jsonLit + `"}`
		rec := firstReadRequest(t, srv.Handler(), http.MethodPost, "u-1", body, "application/json")
		if rec.Code != http.StatusOK {
			t.Fatalf("persona_type=%q: status = %d, body = %s", tc.decoded, rec.Code, rec.Body.String())
		}
		fake := srv.batch4.Store.(*onboardingFakeStore)
		if fake.submit.PersonaType != tc.want {
			t.Errorf("persona_type=%q 归一成 %q，期望 %q", tc.decoded, fake.submit.PersonaType, tc.want)
		}
	}
}

// TestFirstReadAnswersTooLongIsSingleError 钉「越界即不逐条校验」：11 条只回一条 too_long。
func TestFirstReadAnswersTooLongIsSingleError(t *testing.T) {
	items := make([]string, 0, 11)
	for i := 0; i < 11; i++ {
		items = append(items, `{"q":"conflict","option":"speak_now"}`)
	}
	srv := newOnboardingServer(t, nil)
	rec := firstReadRequest(t, srv.Handler(), http.MethodPost, "u-1",
		`{"answers":[`+strings.Join(items, ",")+`]}`, "application/json")
	body := rec.Body.String()
	if strings.Count(body, `"type":"too_long"`) != 1 {
		t.Errorf("越界应只回一条 too_long：%s", body)
	}
	if !strings.Contains(body, `"actual_length":11`) {
		t.Errorf("too_long 的 ctx 少了 actual_length：%s", body)
	}
}

// ── 5. 深链拼装与路由注册 ─────────────────────────────────────────────────────

func TestReferralLinkRTrimSlash(t *testing.T) {
	for _, tc := range []struct{ site, want string }{
		{"https://a", "https://a/r/CODE"},
		{"https://a/", "https://a/r/CODE"},
		{"https://a///", "https://a/r/CODE"},
		{"", "/r/CODE"},
	} {
		if got := referralLink(tc.site, "CODE"); got != tc.want {
			t.Errorf("referralLink(%q) = %q，期望 %q", tc.site, got, tc.want)
		}
	}
}

func TestOnboardingRoutesAbsentWithoutBatch4(t *testing.T) {
	// 机制没接线（Batch4==nil）时 Go 一条都不答：与未注册路径逐字同形。
	srv := newN8Server(t, n8ForbiddenStore{t: t}, n8ForbiddenStore{t: t},
		&n8NoRelayStore{t: t}, nil, nil, nil)
	handler := srv.Handler()
	reference := firstReadRequest(t, handler, http.MethodGet, "u-1", "", "")
	for _, method := range []string{http.MethodGet, http.MethodPost} {
		got := firstReadRequest(t, handler, method, "u-1", "{}", "application/json")
		if got.Code != http.StatusNotFound ||
			!strings.Contains(got.Body.String(), `"code":404,"message":"Not Found"`) {
			t.Errorf("%s 未注册时应落 40404：%d %s", method, got.Code, got.Body.String())
		}
		if got.Body.String() != reference.Body.String() {
			t.Errorf("%s 与「Go 侧从来没有这个域」不同形：%s vs %s",
				method, got.Body.String(), reference.Body.String())
		}
	}
}

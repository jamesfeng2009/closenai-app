// T3-01／T3-02 的内部面形状闸（裁决七-10，工单 T3-01 §验收、T3-02 §验收）。
//
// 这一批断言的是**入口与门禁**，不是领域判据：六态矩阵由 `domain/service_outcome_test.go`
// 钉，「无证据不上线」的 SQL 语义由 `store/insight_browse_integration_test.go` 在真库上钉。
// 这里只回答三件事：
//  1. 这一面对**没有密钥的调用方**是否真的不存在（403/401，以及密钥未配置时整面不注册）；
//  2. 身份是否**只**从 `X-User-Id` 进来（ADR-002 的可证落点：query/body 里的 user_id 无效）；
//  3. 领域错误是否折成工单点名的那几个码（409／404／400），以及错误映射不许把
//     「不存在」与「别人的行」分开发言（那等于替旁人确认 id 为真）。
package httpapi_test

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/config"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/httpapi"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store"
)

const v3Token = "v3-service-token-v3-service-token"

func v3Headers(token, userID string) map[string]string {
	h := map[string]string{}
	if token != "" {
		h["X-Service-Token"] = token
	}
	if userID != "" {
		h["X-User-Id"] = userID
	}
	return h
}

// ── 两个替身：只搬数据，不做判断（判断有它自己的闸，见文件头）─────────────────

type stubBrowseReader struct {
	rows       []domain.InsightListItem
	err        error
	gotUser    string
	gotStatus  []string
	gotAfter   domain.InsightKey
	gotHasAft  bool
	gotLimit   int
	calledWith int
}

func (s *stubBrowseReader) ListInsightsForBrowsing(_ context.Context, userID string,
	statuses []string, after domain.InsightKey, hasAfter bool, limit int) ([]domain.InsightListItem, error) {
	s.gotUser, s.gotStatus, s.gotAfter = userID, statuses, after
	s.gotHasAft, s.gotLimit = hasAfter, limit
	s.calledWith++
	return s.rows, s.err
}

type stubFeedbackWriter struct {
	got     domain.FeedbackProposal
	receipt domain.FeedbackReceipt
	err     error
	calls   int
}

func (f *stubFeedbackWriter) RecordUserFeedback(_ context.Context,
	p domain.FeedbackProposal) (domain.FeedbackReceipt, error) {
	f.calls++
	f.got = p
	return f.receipt, f.err
}

type stubGoalReader struct {
	goals    []domain.GoalRecord
	actions  []domain.ActionRecord
	err      error
	gotUser  string
	gotStat  string
	gotLimit int
}

func (s *stubGoalReader) ListGoalRecords(_ context.Context, userID, status string,
	limit int) ([]domain.GoalRecord, error) {
	s.gotUser, s.gotStat, s.gotLimit = userID, status, limit
	return s.goals, s.err
}

func (s *stubGoalReader) ListActionRecords(_ context.Context, userID, status string,
	limit int) ([]domain.ActionRecord, error) {
	s.gotUser, s.gotStat, s.gotLimit = userID, status, limit
	return s.actions, s.err
}

// stubGoalWriter 记录**最后一次**调用的形状：四条入口各回什么码是一张映射表，
// 它的真身（转移表、幂等、留痕）由 domain/service_goal_test.go 与真库集成测各自钉住。
type stubGoalWriter struct {
	gotGoal    domain.GoalProposal
	gotAction  domain.ActionProposal
	gotTransID string
	gotVerb    domain.LifecycleVerb
	goalRec    domain.GoalReceipt
	actionRec  domain.ActionReceipt
	err        error
	calls      int
}

func (f *stubGoalWriter) CreateGoal(_ context.Context,
	p domain.GoalProposal) (domain.GoalReceipt, error) {
	f.calls++
	f.gotGoal = p
	return f.goalRec, f.err
}

func (f *stubGoalWriter) TransitionGoal(_ context.Context, _, id string,
	verb domain.LifecycleVerb) (domain.GoalReceipt, error) {
	f.calls++
	f.gotTransID, f.gotVerb = id, verb
	return f.goalRec, f.err
}

func (f *stubGoalWriter) CreateAction(_ context.Context,
	p domain.ActionProposal) (domain.ActionReceipt, error) {
	f.calls++
	f.gotAction = p
	return f.actionRec, f.err
}

func (f *stubGoalWriter) TransitionAction(_ context.Context, _, id string,
	verb domain.LifecycleVerb) (domain.ActionReceipt, error) {
	f.calls++
	f.gotTransID, f.gotVerb = id, verb
	return f.actionRec, f.err
}

// stubOutcomeWriter 记录最后一次 RecordActionOutcome 收到的提案：这一面的「四种 result
// 各回什么码／跨用户 404／同日不再写第二行」是一张映射表，真身在 domain 与真库测里。
type stubOutcomeWriter struct {
	got   domain.ActionOutcomeProposal
	rec   domain.ActionOutcomeReceipt
	err   error
	calls int
}

func (f *stubOutcomeWriter) RecordActionOutcome(_ context.Context,
	p domain.ActionOutcomeProposal) (domain.ActionOutcomeReceipt, error) {
	f.calls++
	f.got = p
	return f.rec, f.err
}

func newV3Server(t *testing.T, browser *domain.InsightBrowser,
	outcomes httpapi.FeedbackRecorder, token string) *httpapi.Server {
	t.Helper()
	return newV3ServerFull(t, browser, outcomes, nil, nil, nil, token)
}

// newV3ServerGoals 是目标/行动那批用例的装配点（结果面用默认桩）。
func newV3ServerGoals(t *testing.T, browser *domain.InsightBrowser,
	outcomes httpapi.FeedbackRecorder, read domain.GoalReader,
	writes httpapi.GoalWriter, token string) *httpapi.Server {
	t.Helper()
	return newV3ServerFull(t, browser, outcomes, read, writes, nil, token)
}

// newV3ServerFull 是唯一的真装配点：内部面五格依赖＋密钥一起判（v3.go 的
// internalEnabled），所以「goals 没装配」在这套测里不可能悄悄存在。
// newV3ServerFull 是五格依赖的常规装配点：nil 会被替成默认桩。
//
// 「逐格置 nil 会怎样」不能用这个函数问——它正好把 nil 兜掉了。那一格由
// newV3ServerWith（下面的最底层夹具）负责，见 v3_internal_enabled_test.go。
func newV3ServerFull(t *testing.T, browser *domain.InsightBrowser,
	outcomes httpapi.FeedbackRecorder, read domain.GoalReader,
	writes httpapi.GoalWriter, outcomeWrites httpapi.OutcomeWriter,
	token string) *httpapi.Server {
	t.Helper()
	if browser == nil {
		browser = domain.NewInsightBrowser(&stubBrowseReader{}, explainerForTest(t))
	}
	if read == nil {
		read = &stubGoalReader{}
	}
	if writes == nil {
		writes = &stubGoalWriter{}
	}
	if outcomeWrites == nil {
		outcomeWrites = &stubOutcomeWriter{}
	}
	return newV3ServerWith(t, &httpapi.V3{Insights: browser, Outcomes: outcomes,
		Goals: domain.NewGoalBrowser(read), GoalWrites: writes,
		OutcomeWrites: outcomeWrites}, token)
}

// newV3ServerWith 是唯一的 Server 装配口：V3 由调用方**逐格给好**，这里不兜 nil。
// internalEnabled 的判据要能被一格一格地拆开测，兜掉 nil 就等于替它做了决定。
func newV3ServerWith(t *testing.T, v3 *httpapi.V3, token string) *httpapi.Server {
	t.Helper()
	cfg := config.Config{AppName: "ClosenAI", Environment: "development",
		HTTPAddr: ":0", InsightEvidenceRead: "on", GoServiceToken: token}
	signer := auth.NewSigner("v3-test-secret-v3-test-secret", 30*time.Minute, 30*24*time.Hour)
	return httpapi.NewServer(cfg, signer,
		auth.NewService(store.NewFakeStore(), signer, 30*24*time.Hour),
		explainerForTest(t), nil, nil, nil, nil, nil, nil, v3)
}

func explainerForTest(t *testing.T) *domain.InsightExplainer {
	t.Helper()
	return domain.NewInsightExplainer(seedReader(),
		domain.Policy{Read: domain.ReadPolicy{EvidenceMaxItems: 20, PatternMaxItems: 6}})
}

func listServer(t *testing.T, browse domain.InsightBrowseReader,
	outcomes httpapi.FeedbackRecorder, token ...string) *httpapi.Server {
	t.Helper()
	tok := v3Token
	if len(token) > 0 {
		tok = token[0]
	}
	return newV3Server(t, domain.NewInsightBrowser(browse, nil), outcomes, tok)
}

func insightRow(id string, at time.Time, evidence int64) domain.InsightListItem {
	conf := 0.72
	return domain.InsightListItem{ID: id, Statement: "statement " + id,
		ClaimType: "pattern", Status: "published", Confidence: conf, HasConfidence: true,
		PublishedAt: at, EvidenceCount: evidence}
}

// ── 1. 这一面对没有密钥的调用方不存在 ─────────────────────────────────────────

func TestV3SurfaceIsNotRegisteredWithoutServiceToken(t *testing.T) {
	// 「没配密钥」必须是**整面不注册**，而不是注册后一律拒绝（取舍见 v3.go 头注释）。
	// 这一条是那条取舍唯一的证人：把它改成「注册但 403」，下面两个断言会立刻红。
	srv := newV3Server(t, domain.NewInsightBrowser(&stubBrowseReader{}, nil), &stubFeedbackWriter{}, "")
	h := srv.Handler()
	status, env := do(t, h, "GET", "/internal/v3/insights", "", v3Headers(v3Token, "u1"))
	if status != 404 || env.Code != apierr.CodeNotFound {
		t.Errorf("密钥为空时列表面 = %d/%d，期望 404 信封（这一面应与「从来没有它」同形）",
			status, env.Code)
	}
	status, env = do(t, h, "POST", "/internal/v3/insights/i1/feedback",
		`{"type":"confirm"}`, v3Headers(v3Token, "u1"))
	if status != 404 || env.Code != apierr.CodeNotFound {
		t.Errorf("密钥为空时反馈面 = %d/%d，期望 404 信封", status, env.Code)
	}
	// 反向证人：对外那十条切流域路由**不受这一格影响**——否则「没配内部密钥」会被读成
	// 「Go 挂了」，而注册判据只该管它自己那一面。
	status, _ = do(t, h, "GET", "/health", "", nil)
	if status != 200 {
		t.Errorf("/health = %d，期望 200 ⇒ 内部面的注册闸越界管到了对外面", status)
	}
}

func TestV3RejectsMissingOrWrongServiceToken(t *testing.T) {
	srv := listServer(t, &stubBrowseReader{}, &stubFeedbackWriter{}, v3Token)
	h := srv.Handler()
	for name, headers := range map[string]map[string]string{
		"缺头":   v3Headers("", "u1"),
		"错值":   v3Headers("not-the-token-not-the-token", "u1"),
		"空用户":  v3Headers(v3Token, ""),
		"占位身份": v3Headers(v3Token, "anonymous"),
	} {
		status, env := do(t, h, "GET", "/internal/v3/insights", "", headers)
		wantForbidden := name == "缺头" || name == "错值"
		if wantForbidden {
			if status != 403 || env.Code != apierr.CodeForbidden {
				t.Errorf("%s：%d/%d，期望 403（401 的语义是「请重新认证」，BFF 没这个动作）",
					name, status, env.Code)
			}
			continue
		}
		if status != 401 || env.Code != apierr.CodeUnauthorized {
			t.Errorf("%s：%d/%d，期望 401（身份缺失不是凭据错误）", name, status, env.Code)
		}
	}
}

// ── 2. 身份只从 X-User-Id 进来（ADR-002 的可证落点）───────────────────────────

func TestV3ListIgnoresUserIDFromQuery(t *testing.T) {
	browse := &stubBrowseReader{}
	srv := listServer(t, browse, &stubFeedbackWriter{}, v3Token)
	status, _ := do(t, srv.Handler(), "GET",
		"/internal/v3/insights?user_id=victim&limit=5", "", v3Headers(v3Token, "owner"))
	if status != 200 {
		t.Fatalf("status = %d", status)
	}
	if browse.gotUser != "owner" {
		t.Errorf("领域层收到 user_id=%q，期望 header 的 owner ⇒ query 里的身份被采信了", browse.gotUser)
	}
}

func TestV3FeedbackIgnoresUserIDInBody(t *testing.T) {
	// 这一条是 ADR-002「一律不信 body 里的 user_id」唯一的形状证人：
	// 只要解码不挡未知键，`{"user_id":"victim","type":"confirm"}` 就会静默多带一个字段，
	// 而客户端会以为自己的身份生效了。`calls==0` 也要断言——挡在解码层，不是挡在领域层。
	writer := &stubFeedbackWriter{receipt: domain.FeedbackReceipt{ID: "fb1", TargetStatus: "confirmed", Changed: true}}
	srv := listServer(t, &stubBrowseReader{}, writer, v3Token)
	status, env := do(t, srv.Handler(), "POST", "/internal/v3/insights/i1/feedback",
		`{"type":"confirm","user_id":"victim"}`, v3Headers(v3Token, "owner"))
	if status != 400 || env.Code != apierr.CodeInvalidParams {
		t.Errorf("body 带 user_id：%d/%d，期望 400（DisallowUnknownFields 是这条口径的落点）",
			status, env.Code)
	}
	if writer.calls != 0 {
		t.Errorf("领域写面被调用 %d 次 ⇒ 越权 body 仍然进到了写路径", writer.calls)
	}
}

// ── 3. 列表形状、翻页边界与显式失败 ───────────────────────────────────────────

func TestV3ListShapeAndPaginationBoundary(t *testing.T) {
	at := time.Date(2026, 10, 19, 8, 0, 0, 0, time.UTC)
	browse := &stubBrowseReader{rows: []domain.InsightListItem{
		insightRow("i1", at, 3), insightRow("i2", at.Add(-time.Hour), 1)}}
	srv := listServer(t, browse, &stubFeedbackWriter{}, v3Token)
	status, env := do(t, srv.Handler(), "GET", "/internal/v3/insights?limit=1",
		"", v3Headers(v3Token, "owner"))
	if status != 200 {
		t.Fatalf("status = %d body=%s", status, env.Message)
	}
	var page struct {
		Items []struct {
			ID            string    `json:"id"`
			Statement     string    `json:"statement"`
			ClaimType     string    `json:"claim_type"`
			Status        string    `json:"status"`
			Confidence    *float64  `json:"confidence"`
			PublishedAt   time.Time `json:"published_at"`
			EvidenceCount int64     `json:"evidence_count"`
		} `json:"items"`
		NextCursor string `json:"next_cursor"`
	}
	if err := json.Unmarshal(env.Data, &page); err != nil {
		t.Fatalf("data 不合形：%v", err)
	}
	if len(page.Items) != 1 || page.Items[0].ID != "i1" {
		t.Fatalf("items = %+v，期望恰好一条（limit=1 的边界）", page.Items)
	}
	if page.Items[0].EvidenceCount != 3 {
		t.Errorf("evidence_count = %d，期望 3 ⇒ 列表必须带这个数，否则前端只能逐颗洞察问一次（N+1）",
			page.Items[0].EvidenceCount)
	}
	if page.NextCursor == "" {
		t.Fatal("next_cursor 为空：多取一条没兑现成游标 ⇒ 翻页到此为止，用户看不到第二页")
	}
	// 游标必须指向**这一页的最后一行**，而不是多取回来的那条：错一格就跳行漏行。
	key, err := domain.DecodeInsightKey(page.NextCursor)
	if err != nil {
		t.Fatalf("next_cursor 解不开：%v", err)
	}
	if key.ID != "i1" || !key.PublishedAt.Equal(at) {
		t.Errorf("游标 = %+v，期望 (i1, %s) ⇒ 下一页会重复或跳过 i1", key, at)
	}
	// 领域层要的是 limit+1：只有「页外还有行」被证实，才许发游标（insight_browse.go 同口径）。
	if browse.gotLimit != 2 {
		t.Errorf("仓储被要求取 %d 行，期望 limit+1=2", browse.gotLimit)
	}
	if browse.gotHasAft {
		t.Errorf("第一页不该带 after=%+v ⇒ 「无游标」被当成「游标为零」", browse.gotAfter)
	}
}

func TestV3ListLastPageHasNoCursor(t *testing.T) {
	at := time.Date(2026, 10, 19, 8, 0, 0, 0, time.UTC)
	browse := &stubBrowseReader{rows: []domain.InsightListItem{insightRow("i1", at, 2)}}
	srv := listServer(t, browse, &stubFeedbackWriter{}, v3Token)
	status, env := do(t, srv.Handler(), "GET", "/internal/v3/insights?limit=1",
		"", v3Headers(v3Token, "owner"))
	if status != 200 {
		t.Fatalf("status = %d", status)
	}
	var page struct {
		NextCursor string `json:"next_cursor"`
	}
	if err := json.Unmarshal(env.Data, &page); err != nil {
		t.Fatal(err)
	}
	if page.NextCursor != "" {
		t.Errorf("尽头那页给了游标 %q ⇒ 调用方会多打一次空请求", page.NextCursor)
	}
}

func TestV3ListBadCursorIsExplicitFourHundred(t *testing.T) {
	// 反向证人＋正向证人：坏游标必须 400，且**不许**退化成「静默回第一页」。
	// 后者在日志里长得和成功一模一样，只有用户看得到少掉的那些页。
	browse := &stubBrowseReader{}
	srv := listServer(t, browse, &stubFeedbackWriter{}, v3Token)
	status, env := do(t, srv.Handler(), "GET", "/internal/v3/insights?cursor=not-base64-%E2%9D%97",
		"", v3Headers(v3Token, "owner"))
	if status != 400 || env.Code != apierr.CodeInvalidParams {
		t.Errorf("坏游标：%d/%d，期望 400/40001", status, env.Code)
	}
	if browse.calledWith != 0 {
		t.Errorf("坏游标仍然打到仓储 %d 次 ⇒ 「回第一页」的退化路径被实现了", browse.calledWith)
	}
}

func TestV3ListRejectsNonPositiveLimit(t *testing.T) {
	srv := listServer(t, &stubBrowseReader{}, &stubFeedbackWriter{}, v3Token)
	for _, raw := range []string{"abc", "0", "-3"} {
		status, env := do(t, srv.Handler(), "GET", "/internal/v3/insights?limit="+raw,
			"", v3Headers(v3Token, "owner"))
		if status != 400 || env.Code != apierr.CodeInvalidParams {
			t.Errorf("limit=%q：%d/%d，期望 400（把它当默认值＝调用方的拼写错误变成「分页变小了」）",
				raw, status, env.Code)
		}
	}
}

func TestV3ListStatusFilterCannotEscapeReturnableSet(t *testing.T) {
	browse := &stubBrowseReader{}
	srv := listServer(t, browse, &stubFeedbackWriter{}, v3Token)
	if _, env := do(t, srv.Handler(), "GET", "/internal/v3/insights?status=draft",
		"", v3Headers(v3Token, "owner")); env.Code != 200 {
		t.Fatalf("env = %+v", env)
	}
	// 交集语义（narrowToReturnable）：过滤器给什么就查什么，**不回落成全集**。
	// 回落成全集＝「过滤一个不该看的态」得到「看到全部」，那是授权判据的反面。
	if len(browse.gotStatus) != 1 || browse.gotStatus[0] != "draft" {
		t.Errorf("下发给 SQL 的状态集 = %v，期望 [draft]（查不到自然回空列表）", browse.gotStatus)
	}
	do(t, srv.Handler(), "GET", "/internal/v3/insights", "", v3Headers(v3Token, "owner"))
	if strings.Join(browse.gotStatus, ",") != strings.Join(domain.InsightReturnableStatuses, ",") {
		t.Errorf("默认状态集 = %v，期望 InsightReturnableStatuses %v",
			browse.gotStatus, domain.InsightReturnableStatuses)
	}
}

// ── 4. 详情：跨用户与不可见的形状 ─────────────────────────────────────────────

func TestV3DetailForeignOrInvisibleIsSingleFourOhFour(t *testing.T) {
	// 两种情形回**同一个** 40402，与 N9 那条端点逐字一致（handlers_evidence.go:111）。
	// 分成两格＝替旁人确认这颗 id 存在。这里用「读面报 NotFound」代表两支：
	// 归属与可见性都在 SQL/领域层判，本层只许映射成同一种回答。
	reader := &stubEvidenceReader{err: domain.ErrInsightNotFound}
	explainer := domain.NewInsightExplainer(reader,
		domain.Policy{Read: domain.ReadPolicy{EvidenceMaxItems: 20, PatternMaxItems: 6}})
	browser := domain.NewInsightBrowser(&stubBrowseReader{}, explainer)
	srv := newV3Server(t, browser, &stubFeedbackWriter{}, v3Token)
	status, env := do(t, srv.Handler(), "GET", "/internal/v3/insights/i-other",
		"", v3Headers(v3Token, "attacker"))
	if status != 404 || env.Code != apierr.CodeResourceNotFound {
		t.Errorf("详情跨用户：%d/%d，期望 404/40402", status, env.Code)
	}
}

func TestV3DetailCarriesEvidenceAndPatterns(t *testing.T) {
	browser := domain.NewInsightBrowser(&stubBrowseReader{}, domain.NewInsightExplainer(
		seedReader(), domain.Policy{Read: domain.ReadPolicy{
			EvidenceMaxItems: 20, PatternMaxItems: 6}}))
	srv := newV3Server(t, browser, &stubFeedbackWriter{}, v3Token)
	status, env := do(t, srv.Handler(), "GET", "/internal/v3/insights/i1",
		"", v3Headers(v3Token, "owner"))
	if status != 200 {
		t.Fatalf("status = %d env=%+v", status, env)
	}
	var detail struct {
		ID              string          `json:"id"`
		Statement       string          `json:"statement"`
		Status          string          `json:"status"`
		EvidenceSummary json.RawMessage `json:"evidence_summary"`
		Evidence        []struct {
			EvidenceID   string  `json:"evidence_id"`
			EvidenceType string  `json:"evidence_type"`
			Role         string  `json:"role"`
			Excerpt      *string `json:"excerpt"`
		} `json:"evidence"`
		Patterns []struct {
			ID            string `json:"id"`
			EvidenceCount int64  `json:"evidence_count"`
		} `json:"patterns"`
	}
	if err := json.Unmarshal(env.Data, &detail); err != nil {
		t.Fatalf("data 不合形：%v（%s）", err, env.Data)
	}
	if detail.ID != "i1" || len(detail.Evidence) == 0 || len(detail.Patterns) == 0 {
		t.Fatalf("详情缺依据或 pattern：%+v", detail)
	}
	if len(detail.EvidenceSummary) == 0 {
		t.Error("详情缺 evidence_summary ⇒ 同一颗洞察在两个面上给出不一致的依据条数")
	}
}

// TestV3DetailOpensTheUserFeedbackStatuses 是「详情面用 Returnable 而不是 N9 的三态」
// 这一格的**正向证人**（裁决七-10 T3-01-9，工单 §怎么做.5）。
//
// 为什么必须单独一条：`ExplainReturnable` 与 `Explain` 只差一个判据函数，
// 而上面几条详情的断言全用 `published`——把这一支换回 `isVisibleStatus`，
// 本包与 domain 包的既有闸一条都不会红，只有用户纠错之后打不开自己那条详情时才看得见。
// 反面那半（`retracted` 仍然不给）也钉在同一格：并集不是「放宽一切」。
func TestV3DetailOpensTheUserFeedbackStatuses(t *testing.T) {
	for _, c := range []struct {
		status     string
		wantStatus int
		wantCode   int
	}{
		{"corrected", 200, 200}, {"expired", 200, 200}, {"dismissed", 200, 200},
		{"retracted", 404, apierr.CodeResourceNotFound},
		{"draft", 404, apierr.CodeResourceNotFound},
	} {
		reader := seedReader()
		reader.insight.Status = c.status
		browser := domain.NewInsightBrowser(&stubBrowseReader{}, domain.NewInsightExplainer(
			reader, domain.Policy{Read: domain.ReadPolicy{
				EvidenceMaxItems: 20, PatternMaxItems: 6}}))
		srv := newV3Server(t, browser, &stubFeedbackWriter{}, v3Token)
		status, env := do(t, srv.Handler(), "GET", "/internal/v3/insights/i1",
			"", v3Headers(v3Token, "u1"))
		if status != c.wantStatus || env.Code != c.wantCode {
			t.Errorf("status=%q 的详情 = %d/%d，期望 %d/%d ⇒ 详情面与列表面用了两套可见集合",
				c.status, status, env.Code, c.wantStatus, c.wantCode)
		}
	}
}

// ── 5. 反馈：六种 type、错误映射与「纠错必须有正文」──────────────────────────
func TestV3FeedbackSixTypesAllReachDomainAndReturnReceipt(t *testing.T) {
	for _, ty := range []string{"confirm", "reject", "partially_confirm", "correct",
		"outdated", "forget_from_model"} {
		writer := &stubFeedbackWriter{receipt: domain.FeedbackReceipt{
			ID: "fb-" + ty, TargetStatus: "confirmed", Changed: ty != "partially_confirm",
			ModelVersion: 7, Detail: "说明串"}}
		srv := listServer(t, &stubBrowseReader{}, writer, v3Token)
		body := `{"type":"` + ty + `"}`
		if ty == "correct" {
			body = `{"type":"correct","correction_text":"其实是周五，不是周一"}`
		}
		status, env := do(t, srv.Handler(), "POST",
			"/internal/v3/insights/i42/feedback", body, v3Headers(v3Token, "owner"))
		if status != 200 || env.Code != 200 {
			t.Errorf("%s：%d/%d，期望 200（env=%+v）", ty, status, env.Code, env)
			continue
		}
		if writer.calls != 1 {
			t.Errorf("%s：领域写面调用 %d 次", ty, writer.calls)
		}
		if got := writer.got; string(got.Type) != ty || got.UserID != "owner" ||
			got.TargetID != "i42" || got.TargetType != domain.RefInsight {
			t.Errorf("%s：提案 = %+v，期望身份来自 header、目标来自 path、类型来自 body", ty, got)
		}
		var receipt struct {
			FeedbackID   string `json:"feedback_id"`
			TargetStatus string `json:"target_status"`
			Changed      bool   `json:"changed"`
			Detail       string `json:"detail"`
		}
		if err := json.Unmarshal(env.Data, &receipt); err != nil {
			t.Fatalf("%s：data 不合形 %v", ty, err)
		}
		if receipt.FeedbackID != "fb-"+ty || receipt.Detail == "" {
			t.Errorf("%s：回执 = %+v，期望带上领域给的 id 与说明串", ty, receipt)
		}
	}
}

func TestV3FeedbackErrorMapping(t *testing.T) {
	cases := []struct {
		name       string
		err        error
		wantStatus int
		wantCode   int
	}{
		{"重复同型", domain.ErrDuplicateFeedback, 409, apierr.CodeConflict},
		{"别人的行或不存在", domain.ErrInvalidOwnership, 404, apierr.CodeResourceNotFound},
	}
	for _, c := range cases {
		writer := &stubFeedbackWriter{err: c.err}
		srv := listServer(t, &stubBrowseReader{}, writer, v3Token)
		status, env := do(t, srv.Handler(), "POST", "/internal/v3/insights/i1/feedback",
			`{"type":"reject"}`, v3Headers(v3Token, "owner"))
		if status != c.wantStatus || env.Code != c.wantCode {
			t.Errorf("%s：%d/%d，期望 %d/%d", c.name, status, env.Code, c.wantStatus, c.wantCode)
		}
	}
}

func TestV3FeedbackRejectsUnknownTypeAndEmptyCorrection(t *testing.T) {
	cases := []struct {
		name string
		body string
	}{
		{"未知 type", `{"type":"meh"}`},
		{"缺 type", `{}`},
		{"correct 空正文", `{"type":"correct","correction_text":"   "}`},
		{"correct 缺正文", `{"type":"correct"}`},
		{"正文超长 501", `{"type":"correct","correction_text":"` + strings.Repeat("字", 501) + `"}`},
	}
	for _, c := range cases {
		writer := &stubFeedbackWriter{}
		srv := listServer(t, &stubBrowseReader{}, writer, v3Token)
		status, env := do(t, srv.Handler(), "POST", "/internal/v3/insights/i1/feedback",
			c.body, v3Headers(v3Token, "owner"))
		if status != 400 || env.Code != apierr.CodeInvalidParams {
			t.Errorf("%s：%d/%d，期望 400/40001", c.name, status, env.Code)
		}
		if writer.calls != 0 {
			t.Errorf("%s：校验没挡住，领域层被调用 ⇒ 会往权威表写行", c.name)
		}
	}
}

func TestV3FeedbackCarriesOperationIDIntoMetadata(t *testing.T) {
	writer := &stubFeedbackWriter{receipt: domain.FeedbackReceipt{ID: "fb1", TargetStatus: "confirmed"}}
	srv := listServer(t, &stubBrowseReader{}, writer, v3Token)
	status, _ := do(t, srv.Handler(), "POST", "/internal/v3/insights/i1/feedback",
		`{"type":"confirm","operation_id":"op-77"}`, v3Headers(v3Token, "owner"))
	if status != 200 {
		t.Fatalf("status = %d", status)
	}
	if writer.got.Metadata["operation_id"] != "op-77" {
		t.Errorf("metadata = %v，期望带 operation_id ⇒ 幂等键丢了就只能靠「最近一条同型」判重",
			writer.got.Metadata)
	}
}

// ── 6. G-02：内部面不得出现在客户端契约里 ────────────────────────────────────

func TestInternalRoutesAreAbsentFromClientContract(t *testing.T) {
	// 两侧不同源：这里拿**生成物**（FastAPI 导出的 openapi.json）与 Go 的声明表对账。
	// 只在 Go 侧自证「我的 path 前缀是 /internal」是恒真的；真正的风险是有人把某条
	// 内部 path 抄进契约，那会让它长出前端调用方，而这一面的鉴权是服务密钥、不是用户 JWT。
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "contracts", "openapi.json"))
	if err != nil {
		t.Fatalf("读 contracts/openapi.json 失败：%v ⇒ 这一条没有真的比过", err)
	}
	contract := string(raw)
	var internal []string
	for _, r := range httpapi.RoutePlan() {
		if !strings.HasPrefix(r.Path, "/internal/") {
			continue
		}
		internal = append(internal, r.Path)
		if strings.Contains(contract, r.Path) {
			t.Errorf("%s 同时出现在 Go 声明表与客户端契约里 ⇒ 内部面长出了客户端调用方", r.Path)
		}
		if r.Domain != "" {
			t.Errorf("%s 带切流域键 %q ⇒ 内部面被当成可切给客户端的路由（ADR-002 的反面）",
				r.Path, r.Domain)
		}
	}
	if len(internal) != 10 {
		// 3 条是 T3-01/02 的洞察面，6 条是 T4-02 的目标/行动面（三条目标 + 三条行动），
		// 1 条是 T4-03 的行动结果面（POST /internal/v3/actions/{id}/outcomes）。
		t.Errorf("声明表里的 /internal/ 路由 = %d 条 %v，期望 10 ⇒ 少一条就是这一面缺了入口",
			len(internal), internal)
	}
	if strings.Contains(contract, "/internal/") {
		t.Error("契约里出现了 /internal/ 前缀（不只是这几条 path）")
	}
	// G-02 的另一半（正向证人）：**每一条**声明的 path 都要落在三个已登记面之一。
	// 只查「内部面别漏进契约」是反向的——有人新加一条 `/api/v1/internal-x` 或
	// `/debug/vars`，它不会命中上面任何一条，却会真的注册到线上。
	for _, r := range httpapi.RoutePlan() {
		allowed := strings.HasPrefix(r.Path, "/api/v1/") ||
			strings.HasPrefix(r.Path, "/internal/v3/") ||
			r.Path == "/health" || r.Path == "/healthz" ||
			(r.Path == "/" && r.Method == "") // 兜底 404：不限方法那一条
		if !allowed {
			t.Errorf("声明表里 %q %s 不属于任何已登记面 ⇒ G-02：客户端面长出了没人登记的路由",
				r.Method, r.Path)
		}
	}
}

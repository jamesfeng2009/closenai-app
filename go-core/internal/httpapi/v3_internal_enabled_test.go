// T4-02 结案行登记的缺陷①在这里被真的补上：**逐格**的注册闸。
//
// 为什么必须有这张表：`internalEnabled()` 是六格依赖 AND 一个密钥（v3.go）。
// T4-02 那次翻车是把 `Goals` 忘在装配里，结果整个内部面 404，而**没有任何一条测会红**
// ——各面的用例各自只填自己那一格，兜底的夹具又把 nil 替成了默认桩。
// 「摘掉一格不可观察」正是这一族的死法：这一面缺入口时，BFF 看到的是 503/404 信封，
// 值班看到的是「Go 好像挂了」，而真相是少装配了一个域。
//
// 两侧不许同源（这条闸自己也一样）：断言用的是**路由清单的字面量**，
// 不是拿 `httpapi.RoutePlan()` 反推——后者会让「删掉一条路由」同时改掉判据本身。
// 清单与 RoutePlan 的一致性由 v3_test.go 的计数断言（11 条 /internal/）守着。
package httpapi_test

import (
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/httpapi"
)

// v3InternalRoutes 是这一面全部的 11 条 path×method（逐字，含 path 参数已展开的样子）。
var v3InternalRoutes = []struct {
	name   string
	method string
	path   string
	body   string
}{
	{"洞察列表", "GET", "/internal/v3/insights", ""},
	{"洞察详情", "GET", "/internal/v3/insights/i1", ""},
	{"洞察反馈", "POST", "/internal/v3/insights/i1/feedback", `{"type":"confirm"}`},
	{"目标创建", "POST", "/internal/v3/goals", `{"title":"t"}`},
	{"目标列表", "GET", "/internal/v3/goals", ""},
	{"目标转移", "PATCH", "/internal/v3/goals/g1", `{"action":"complete"}`},
	{"行动创建", "POST", "/internal/v3/goals/g1/actions", `{"title":"a"}`},
	{"行动列表", "GET", "/internal/v3/actions", ""},
	{"行动转移", "PATCH", "/internal/v3/actions/a1", `{"action":"skip"}`},
	{"行动结果", "POST", "/internal/v3/actions/a1/outcomes",
		`{"outcome_type":"commitment_completed","result":"better"}`},
	{"触达审计", "GET", "/internal/v3/proactives", ""},
}

func fullV3(t *testing.T) *httpapi.V3 {
	t.Helper()
	return &httpapi.V3{
		Insights:      domain.NewInsightBrowser(&stubBrowseReader{}, explainerForTest(t)),
		Outcomes:      &stubFeedbackWriter{},
		Goals:         domain.NewGoalBrowser(&stubGoalReader{}),
		GoalWrites:    &stubGoalWriter{},
		OutcomeWrites: &stubOutcomeWriter{},
		Proactives:    domain.NewProactiveAuditBrowser(&stubProactiveAuditReader{}),
	}
}

// assertSurfaceGone 期望 11 条**全部**回「这一面从来没有」那个形状的 404 信封。
func assertSurfaceGone(t *testing.T, v3 *httpapi.V3, token, why string) {
	t.Helper()
	h := newV3ServerWith(t, v3, token).Handler()
	for _, r := range v3InternalRoutes {
		status, env := do(t, h, r.method, r.path, r.body, goalHeaders())
		if status != 404 || env.Code != apierr.CodeNotFound {
			t.Errorf("%s：%s %s = %d/%d，期望 404 信封 ⇒ 少装配一格却只让部分路由消失，"+
				"BFF 会看到一片形状陌生的错误（v3.go 那条「整面不注册」的取舍作废）",
				why, r.method, r.path, status, env.Code)
		}
	}
	// 注册闸不许越界管到对外面：/health 必须照常。
	if s, _ := do(t, h, "GET", "/health", "", nil); s != 200 {
		t.Errorf("%s：/health = %d，期望 200 ⇒ 内部面的装配闸连累了探针", why, s)
	}
}

func TestV3SurfaceRegistersAllElevenRoutesWhenComplete(t *testing.T) {
	// 正向证人：没有这一格，上面那个「全部 404」在「十条路由从来都没注册」的世界里照样全绿。
	h := newV3ServerWith(t, fullV3(t), v3Token).Handler()
	for _, r := range v3InternalRoutes {
		status, env := do(t, h, r.method, r.path, r.body, goalHeaders())
		if status == 404 && env.Code == apierr.CodeNotFound {
			t.Errorf("%s：%s %s 回 404 信封 ⇒ 六格齐了也不该消失，这一面缺了入口",
				r.name, r.method, r.path)
		}
	}
}

func TestV3SurfaceVanishesWhenAnySingleDependencyIsMissing(t *testing.T) {
	// 每一格单独置 nil：这是 T4-02 缺陷①的直接靶。
	// 「Goals 忘了装配」在那一批里表现为 insights 正常、goals 那几条 404，
	// 与「从来没做过这个域」同形，没人会去查——所以这里要求**整面**一起消失。
	cases := []struct {
		why    string
		mutate func(v *httpapi.V3)
	}{
		{"Insights 为 nil", func(v *httpapi.V3) { v.Insights = nil }},
		{"Outcomes 为 nil", func(v *httpapi.V3) { v.Outcomes = nil }},
		{"Goals 为 nil", func(v *httpapi.V3) { v.Goals = nil }},
		{"GoalWrites 为 nil", func(v *httpapi.V3) { v.GoalWrites = nil }},
		{"OutcomeWrites 为 nil", func(v *httpapi.V3) { v.OutcomeWrites = nil }},
		{"Proactives 为 nil", func(v *httpapi.V3) { v.Proactives = nil }},
	}
	for _, c := range cases {
		v3 := fullV3(t)
		c.mutate(v3)
		assertSurfaceGone(t, v3, v3Token, c.why)
	}
	// 密钥为空同样整面不注册（这一格在 v3_test.go 只覆盖了洞察两条，这里补齐全 11 条）。
	assertSurfaceGone(t, fullV3(t), "", "密钥为空")
}

func TestV3SurfaceAbsentWhenV3ItselfIsNil(t *testing.T) {
	// internalEnabled 的第一格（s.v3 != nil）：NewServer 允许不传这一面，
	// 那必须是「没有这个域」而不是「每个 handler 都 panic」。
	assertSurfaceGone(t, nil, v3Token, "整个 V3 为 nil")
}

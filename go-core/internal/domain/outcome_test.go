package domain

import (
	"strings"
	"testing"
)

// N6 词表与生命周期行为的单测。
//
// 钉四件事：
//  1. 四套词表各自等于它的事实源（04:288-297 / 02:176-189 / 04:279-285 / 02:156-163），
//     改名或加值都要先改裁决文档，这里就是那道闸；
//  2. 非权威来源**不可能**产 recorded（不变量 5 的可执行形式）；
//  3. 生命周期转移是一张函数表，终态不接受改写（07 §3）；
//  4. 反例传播只降分数，且「未知」不会被当成 0 来削（O-4）。

func TestOutcomeVocabularyMatchesSourceOfTruth(t *testing.T) {
	types := []string{
		"goal_progress", "goal_completed", "commitment_completed", "behavior_change",
		"user_reported_change", "proactive_helpful", "proactive_not_helpful",
		"pattern_confirmed", "pattern_disconfirmed",
	}
	if len(OutcomeTypes) != len(types) {
		t.Fatalf("outcome_type 类数 = %d，04:288-297 只给 %d 类", len(OutcomeTypes), len(types))
	}
	for i, want := range types {
		if string(OutcomeTypes[i]) != want {
			t.Errorf("OutcomeTypes[%d] = %q，期望 %q（逐字 snake_case）", i, OutcomeTypes[i], want)
		}
	}
	statuses := []string{"candidate", "recorded", "corrected", "invalidated"}
	if strings.Join(castStatuses(), ",") != strings.Join(statuses, ",") {
		t.Errorf("outcomes.status 词表 = %v，期望 %v（02:176-180 + candidate 偏离见七-5 O-1）",
			castStatuses(), statuses)
	}
	results := []string{"better", "same", "worse", "mixed", "not_relevant", "unknown"}
	if strings.Join(castResults(), ",") != strings.Join(results, ",") {
		t.Errorf("outcomes.result 词表 = %v，期望 02:182-189 六值", castResults())
	}
	origins := []string{"user_statement", "action_completion", "product_event", "llm_inference"}
	if strings.Join(castOrigins(), ",") != strings.Join(origins, ",") {
		t.Errorf("outcomes.origin 词表 = %v，期望 04:279-285 三条权威来源 + llm_inference",
			castOrigins())
	}
	feedback := []string{"confirm", "reject", "partially_confirm", "correct", "outdated",
		"forget_from_model"}
	if strings.Join(castFeedbackTypes(), ",") != strings.Join(feedback, ",") {
		t.Errorf("feedback_type 词表 = %v，期望 02:156-163 六值（**不是** 04 §14 的响应态）",
			castFeedbackTypes())
	}
	// 不变量 5 的数据面：词表里根本没有「沉默」这一值。
	for _, o := range OutcomeOrigins {
		if strings.Contains(string(o), "silence") || strings.Contains(string(o), "ignored") {
			t.Fatalf("origin 词表出现了沉默类来源 %q（01:810 禁止仅由沉默产 outcome）", o)
		}
	}
	if len(OutcomeLinkTargetTypes) != 5 || len(FeedbackTargetTypes) != 7 {
		t.Errorf("目标词表宽度 = %d/%d，期望 02:817-821 五类与七-5 O-3 七类",
			len(OutcomeLinkTargetTypes), len(FeedbackTargetTypes))
	}
}

func castStatuses() []string {
	out := []string{}
	for _, s := range OutcomeStatuses {
		out = append(out, string(s))
	}
	return out
}

func castResults() []string {
	out := []string{}
	for _, r := range OutcomeResults {
		out = append(out, string(r))
	}
	return out
}

func castOrigins() []string {
	out := []string{}
	for _, o := range OutcomeOrigins {
		out = append(out, string(o))
	}
	return out
}

func castFeedbackTypes() []string {
	out := []string{}
	for _, f := range FeedbackTypes {
		out = append(out, string(f))
	}
	return out
}

// llm_inference 顶到 recorded 是 O-1/O-5 最容易被后来者「顺手放宽」的一格，
// 所以这里把四条来源的映射全钉死，而不是只断言那一条。
func TestOutcomeStatusForOrigin(t *testing.T) {
	cases := map[OutcomeOrigin]OutcomeStatus{
		OriginUserStatement:    OutcomeStatusRecorded,
		OriginActionCompletion: OutcomeStatusRecorded,
		OriginProductEvent:     OutcomeStatusRecorded,
		OriginLLMInference:     OutcomeStatusCandidate,
	}
	for origin, want := range cases {
		if got := OutcomeStatusForOrigin(origin); got != want {
			t.Errorf("OutcomeStatusForOrigin(%s) = %s，期望 %s", origin, got, want)
		}
	}
	if OutcomeStatusForOrigin("silence") != OutcomeStatusCandidate {
		t.Error("未知 origin 必须落到最不可信的一档，不能默认 recorded")
	}
}

func TestNextPatternStatusMatrix(t *testing.T) {
	type row struct {
		current PatternStatus
		action  PatternAction
		want    PatternStatus
		ok      bool
	}
	cases := []row{
		// 01 §5.3 的主链：candidate→observed→supported→confirmed/active。
		{PatternCandidate, PatternConfirm, PatternConfirmed, true},
		{PatternSupported, PatternConfirm, PatternConfirmed, true},
		{PatternWeakened, PatternConfirm, PatternConfirmed, true},
		// 已在目标态或更前 ⇒ 不动（02 §32「无变化不 ++」）。
		{PatternConfirmed, PatternConfirm, PatternConfirmed, false},
		{PatternActive, PatternConfirm, PatternActive, false},
		// 削弱只对已经立起来的态有意义（01:320）。
		{PatternCandidate, PatternWeaken, PatternCandidate, false},
		{PatternObserved, PatternWeaken, PatternWeakened, true},
		{PatternActive, PatternWeaken, PatternWeakened, true},
		// 拒绝/过期是终局；终态不接受任何后续改写（用户后来的表态只留痕）。
		{PatternSupported, PatternReject, PatternRejected, true},
		{PatternConfirmed, PatternMarkExpired, PatternExpired, true},
		{PatternExpired, PatternConfirm, PatternExpired, false},
		{PatternRejected, PatternWeaken, PatternRejected, false},
		// revalidate 刻意不改状态：它只是「要求重算」的留痕。
		{PatternSupported, PatternRevalidate, PatternSupported, false},
		{PatternActive, PatternAction("delete"), PatternActive, false},
	}
	for _, c := range cases {
		got, ok := NextPatternStatus(c.current, c.action)
		if got != c.want || ok != c.ok {
			t.Errorf("NextPatternStatus(%s,%s) = (%s,%t)，期望 (%s,%t)",
				c.current, c.action, got, ok, c.want, c.ok)
		}
	}
	// 未知状态（库里有脏行）必须原样返回且 ok=false，不能被当成 candidate 复活。
	if got, ok := NextPatternStatus(PatternStatus("weird"), PatternConfirm); ok || got != "weird" {
		t.Errorf("未知状态被接受：(%s,%t)", got, ok)
	}
}

// 用户表态必须落到**同一个**转移函数上（07 §3）：如果 feedback 侧有第二张
// 「表态→状态字符串」的直写表，终态可不可翻就会在两处漂移。
func TestPatternActionForFeedback(t *testing.T) {
	cases := map[FeedbackType]struct {
		action PatternAction
		ok     bool
	}{
		FeedbackConfirm:         {PatternConfirm, true},
		FeedbackReject:          {PatternReject, true},
		FeedbackForgetFromModel: {PatternReject, true},
		FeedbackCorrect:         {PatternWeaken, true},
		FeedbackOutdated:        {PatternMarkExpired, true},
		// 部分认可既不是确认也不是否决（policy.go:86 已定它不算负面）。
		FeedbackPartiallyConfirm: {"", false},
		FeedbackType("unknown"):  {"", false},
	}
	for f, want := range cases {
		action, ok := PatternActionForFeedback(f)
		if ok != want.ok || action != want.action {
			t.Errorf("PatternActionForFeedback(%s) = (%s,%t)，期望 (%s,%t)",
				f, action, ok, want.action, want.ok)
		}
	}
}

func TestStatusForDerivedTarget(t *testing.T) {
	cases := []struct {
		target RefType
		f      FeedbackType
		want   string
		ok     bool
	}{
		{RefFact, FeedbackConfirm, "active", true},
		{RefFact, FeedbackReject, "invalidated", true},
		{RefFact, FeedbackCorrect, "invalidated", true},
		{RefFact, FeedbackOutdated, "outdated", true},
		{RefFact, FeedbackForgetFromModel, "invalidated", true},
		{RefInsight, FeedbackConfirm, "confirmed", true},
		{RefInsight, FeedbackReject, "retracted", true},
		{RefInsight, FeedbackCorrect, "corrected", true},
		{RefInsight, FeedbackOutdated, "expired", true},
		{RefInsight, FeedbackForgetFromModel, "dismissed", true},
		{RefOutcome, FeedbackCorrect, "corrected", true},
		{RefOutcome, FeedbackReject, "invalidated", true},
		// 部分认可一律不改写状态。
		{RefFact, FeedbackPartiallyConfirm, "", false},
		{RefInsight, FeedbackPartiallyConfirm, "", false},
		// goal/commitment 一格都不改：完成是**动作**的结果（不变量 5）。
		{RefGoal, FeedbackConfirm, "", false},
		{RefCommitment, FeedbackReject, "", false},
		{RefProactive, FeedbackConfirm, "", false},
		// outcome 的 confirm 不构成状态改写（它已经是记录）。
		{RefOutcome, FeedbackConfirm, "", false},
	}
	for _, c := range cases {
		got, ok := StatusForDerivedTarget(c.target, c.f)
		if got != c.want || ok != c.ok {
			t.Errorf("StatusForDerivedTarget(%s,%s) = (%q,%t)，期望 (%q,%t)",
				c.target, c.f, got, ok, c.want, c.ok)
		}
	}
	// 矩阵的每一格都必须落在 02 的词表里，不新造状态。
	for _, target := range FeedbackTargetTypes {
		for _, f := range FeedbackTypes {
			status, ok := StatusForDerivedTarget(target, f)
			if !ok {
				continue
			}
			switch target {
			case RefFact:
				assertIn(t, "fact_status", status,
					[]string{"candidate", "active", "rejected", "outdated", "invalidated"})
			case RefInsight:
				assertIn(t, "insight_status", status, []string{"draft", "validated", "published",
					"acknowledged", "confirmed", "corrected", "dismissed", "retracted", "expired"})
			case RefOutcome:
				if !OutcomeStatus(status).Valid() {
					t.Errorf("fact/insight 矩阵产出了 %q，不在 outcomes.status 词表", status)
				}
			}
		}
	}
}

func assertIn(t *testing.T, column, got string, allowed []string) {
	t.Helper()
	if !containsOne(allowed, got) {
		t.Errorf("%s 矩阵产出了 %q，不在 02 §3 词表 %v", column, got, allowed)
	}
}

func TestDisconfirmedConfidenceOnlyLowers(t *testing.T) {
	p := DefaultOutcomePolicy()
	if p.DisconfirmStep <= 0 || p.DisconfirmStep >= 1 {
		t.Fatalf("DisconfirmStep = %v，应在 (0,1)——0 等于不传播，1 等于清零", p.DisconfirmStep)
	}
	got, changed := p.DisconfirmedConfidence(0.80, true)
	if !changed {
		t.Fatal("有置信度时必须能降")
	}
	if got >= 0.80 || got < 0.70 {
		t.Errorf("0.80 的一个反例后 = %v，期望削 10%%（≈0.72）", got)
	}
	// NULL 置信度不是 0：不动它，否则「未知」会被伪装成「更低的未知」。
	if _, changed := p.DisconfirmedConfidence(0, false); changed {
		t.Error("HasConfidence=false 时不得改写分数")
	}
	// 下界截断：MinConfidence 默认 0，负数不应出现。
	if v, _ := p.DisconfirmedConfidence(0.01, true); v < 0 {
		t.Errorf("分数被削成负数 %v", v)
	}
}

func TestOutcomeTypeClassification(t *testing.T) {
	// 需要可核实对象的类型（04 §14/§18）与不需要的两类。
	for _, ty := range OutcomeTypes {
		need, ok := OutcomeTargetRequirement(ty)
		switch ty {
		case OutcomeBehaviorChange, OutcomeUserReportedChange:
			if ok {
				t.Errorf("%s 不该强制挂对象（用户全局自述的变化）", ty)
			}
		default:
			if !ok || need == "" {
				t.Errorf("%s 缺少目标要求（04 §14 的响应态都指向某条权威行）", ty)
			}
		}
	}
	disconfirming := []OutcomeType{OutcomePatternDisconfirmed, OutcomeBehaviorChange,
		OutcomeUserReportedChange}
	for _, ty := range OutcomeTypes {
		got := OutcomeDisconfirming(ty)
		want := containsOne(disconfirming, ty)
		if got != want {
			t.Errorf("OutcomeDisconfirming(%s) = %t，期望 %t", ty, got, want)
		}
	}
	// proactive_not_helpful 反的是那条触达，不是它背后的模式（04:274 同一立场）。
	if OutcomeDisconfirming(OutcomeProactiveNotHelpful) {
		t.Error("proactive_not_helpful 不得算作模式反例")
	}
	if !OutcomeCompletesTarget(OutcomeGoalCompleted) ||
		!OutcomeCompletesTarget(OutcomeCommitmentCompleted) {
		t.Error("goal/commitment 完成类必须落到既有表（02:824）")
	}
	if OutcomeCompletesTarget(OutcomeGoalProgress) {
		t.Error("goal_progress 不是完成")
	}
}

func TestCheckOutcomeProposal(t *testing.T) {
	base := func() OutcomeProposal {
		return OutcomeProposal{UserID: owner, Type: OutcomeGoalCompleted,
			Result: OutcomeResultBetter, Origin: OriginUserStatement,
			Targets: []TargetRef{{Type: RefGoal, ID: "goal-1"}}}
	}
	if err := checkOutcomeProposal(base()); err != nil {
		t.Fatalf("合法提案被拒：%v", err)
	}
	cases := []struct {
		name   string
		mutate func(*OutcomeProposal)
		want   string
	}{
		{"缺 user_id", func(p *OutcomeProposal) { p.UserID = " " }, "user_id"},
		{"未知 outcome_type", func(p *OutcomeProposal) { p.Type = "astrology_reading" }, "outcome_type"},
		{"未知 origin", func(p *OutcomeProposal) { p.Origin = "silence" }, "origin"},
		{"未知 result", func(p *OutcomeProposal) { p.Result = "great" }, "result"},
		{"空 result 必须被调用方归一化", func(p *OutcomeProposal) { p.Result = "" }, "result"},
		{"goal_completed 没挂 goal", func(p *OutcomeProposal) { p.Targets = nil }, "必须 link"},
		{"挂错对象类型", func(p *OutcomeProposal) {
			p.Targets = []TargetRef{{Type: RefFact, ID: "f1"}}
		}, "必须 link"},
		{"目标 id 为空", func(p *OutcomeProposal) {
			p.Targets = []TargetRef{{Type: RefGoal, ID: ""}}
		}, "不合法"},
		{"link 目标类型超出五类", func(p *OutcomeProposal) {
			p.Targets = []TargetRef{{Type: RefGoal, ID: "g1"}, {Type: RefFact, ID: "f1"}}
		}, "不合法"},
		// 不变量 5：「用户没回」不是 product_event，必须带可核实的负反馈陈述。
		{"沉默伪造成 outcome", func(p *OutcomeProposal) {
			p.Type, p.Origin, p.Result = OutcomeProactiveNotHelpful, OriginProductEvent,
				OutcomeResultNotRelevant
			p.Targets = []TargetRef{{Type: RefProactive, ID: "pa-1"}}
			p.Description = "   "
		}, "沉默不构成 outcome"},
	}
	for _, c := range cases {
		p := base()
		c.mutate(&p)
		err := checkOutcomeProposal(p)
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s：err = %v，期望含 %q", c.name, err, c.want)
		}
	}
	// 同一类合法化路径不能被误伤：product_event + 明确陈述是允许的。
	p := base()
	p.Type, p.Origin = OutcomeProactiveNotHelpful, OriginProductEvent
	p.Result = OutcomeResultNotRelevant
	p.Targets = []TargetRef{{Type: RefProactive, ID: "pa-1"}}
	p.Description = "user tapped not_helpful"
	if err := checkOutcomeProposal(p); err != nil {
		t.Errorf("合法的结构化产品事件被拒：%v", err)
	}
}

func TestCheckFeedbackProposal(t *testing.T) {
	base := func() FeedbackProposal {
		return FeedbackProposal{UserID: owner, Type: FeedbackReject,
			TargetType: RefInsight, TargetID: "insight-1"}
	}
	if err := checkFeedbackProposal(base()); err != nil {
		t.Fatalf("合法表态被拒：%v", err)
	}
	cases := []struct {
		name   string
		mutate func(*FeedbackProposal)
		want   string
	}{
		{"缺 user_id", func(f *FeedbackProposal) { f.UserID = "" }, "user_id"},
		{"未知 feedback_type", func(f *FeedbackProposal) { f.Type = "meh" }, "feedback_type"},
		{"无目标", func(f *FeedbackProposal) { f.TargetID = "" }, "权威记录"},
		{"目标类型超出七类", func(f *FeedbackProposal) { f.TargetType = "chat_message" },
			"target_type"},
	}
	for _, c := range cases {
		f := base()
		c.mutate(&f)
		if err := checkFeedbackProposal(f); err == nil ||
			!strings.Contains(err.Error(), c.want) {
			t.Errorf("%s：err = %v，期望含 %q", c.name, err, c.want)
		}
	}
}

func TestOnlyDependentGate(t *testing.T) {
	cases := []struct {
		row  DerivedSupport
		want bool
	}{
		// 唯一的支撑被撤回 ⇒ 进失效路径。
		{DerivedSupport{TotalSupport: 1, LiveSupport: 0}, true},
		{DerivedSupport{TotalSupport: 3, LiveSupport: 0}, true},
		// 还有其他活支撑 ⇒ 不算「仅依赖它」（O-6 的唯一门）。
		{DerivedSupport{TotalSupport: 3, LiveSupport: 2}, false},
		{DerivedSupport{TotalSupport: 1, LiveSupport: 1}, false},
		// total=0 是「本来就没支撑过它」，不能当成被它拖累。
		{DerivedSupport{TotalSupport: 0, LiveSupport: 0}, false},
	}
	for _, c := range cases {
		if got := c.row.OnlyDependent(); got != c.want {
			t.Errorf("OnlyDependent(%+v) = %t，期望 %t", c.row, got, c.want)
		}
	}
}

func TestVersionReasonKeepsDocumentedVocabulary(t *testing.T) {
	// 02:255-267 示例词表里的两条必须逐字命中（其余按 <目标>_<新状态> 同形派生）。
	if got := versionReason(RefPattern, string(PatternConfirmed), FeedbackConfirm); got !=
		"pattern_confirmed" {
		t.Errorf("pattern confirm reason = %q", got)
	}
	if got := versionReason(RefPattern, string(PatternRejected), FeedbackReject); got !=
		"pattern_rejected" {
		t.Errorf("pattern reject reason = %q", got)
	}
	if got := versionReason(RefFact, "invalidated", FeedbackCorrect); got != "fact_corrected" {
		t.Errorf("fact correct reason = %q，02:260 的示例词是 fact_corrected", got)
	}
	if got := versionReason(RefOutcome, string(OutcomeStatusCorrected), FeedbackCorrect); got !=
		"outcome_corrected" {
		t.Errorf("outcome correct reason = %q", got)
	}
}

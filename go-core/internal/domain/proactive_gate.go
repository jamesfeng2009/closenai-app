package domain

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"
)

// ProactiveGate 是主动触达的资格判定器（V3 04 §5 硬门 + §11 交付前十门 + §12 新鲜度，
// 叠上 PRD「四层公式至少满足 2 层」这一项目自有资格门）。
//
// 校验序（顺序本身是契约，落 eligibility_detail 供复现；出处与偏离见
// closenai-prd/V3方案裁决与分层-2026-10-12.md 七-4）：
//
//	schema → evidence → layers → confidence → safety → preference →
//	frequency → quiet_hours → duplicate → freshness → message_quality
//
// 十门是 04:211-220 的原序；`layers` 是第七个位置上新加的 PRD 门（closenai.html:637-638），
// 它排在 evidence 之后、confidence 之前，因为「引用了什么」已经判过，而「凭什么今天发」
// 要在置信之前决定——低置信 + 无层的样本必须先落层数码，否则 golden 无法复现。
type ProactiveGate struct {
	Policy Policy
}

// ProactiveAction 是资格门的三种结论。
type ProactiveAction string

const (
	// ActionProactiveEligible：全绿，落 eligible（带 scheduled_at 即待发送）。
	ActionProactiveEligible ProactiveAction = "eligible"
	// ActionProactiveBlock：此刻不能发（频次/静默时段/偏好/重复），落 blocked。
	ActionProactiveBlock ProactiveAction = "blocked"
	// ActionProactiveReject：本身没资格，落 blocked + reject_reason。
	ActionProactiveReject ProactiveAction = "rejected"
	// ActionProactiveCancel：引用的权威行已失效（04 §12），落 cancelled。
	ActionProactiveCancel ProactiveAction = "cancelled"
)

// ProactiveOutcome 是一次资格评估的结论。
type ProactiveOutcome struct {
	Action   ProactiveAction
	Status   string
	Reason   RejectReason
	IsReject bool // true⇒Reason 落 reject_reason；false⇒落 block_reason（P-9 的分工）
	Detail   string
	// Checks 只装**通过**的门，按校验序；被拦下时它证明前几关是绿的。
	Checks    []string
	Score     float64
	HitLayers []Layer
	// 频次/疲劳的计算结果，落 eligibility_detail 供复现与 O 侧评测。
	Cadence    Cadence
	SilentDays int
}

// ProactiveInputs 是资格门一次求值所需的全部库态（由 Service 在同一事务里取齐）。
type ProactiveInputs struct {
	Prefs      UserPrefs
	Evidence   []Evidence
	Refs       []RefState
	History    []ProactiveSendRow
	Corrected  []string
	SilentDays int
	// Anchor 是「关联当下」那一层核出来的权威锚点时刻（04 §10 要求触发可核实）。
	AnchorRefID string
	AnchorAt    time.Time
	HasAnchor   bool
	Now         time.Time
	Timezone    string
}

// Check 跑十一关，第一个失败即定论。
func (g ProactiveGate) Check(ctx context.Context, c *ProactiveCandidate, in ProactiveInputs) ProactiveOutcome {
	// 阶梯与沉默天数是**输入派生**的事实，不是「全绿才有」的结论：被频次关/停推档挡下的
	// 那一行也得带上当时处于哪一档，否则复盘只看到拒因、看不到上下文。
	out := ProactiveOutcome{Action: ActionProactiveEligible,
		Status:     string(ProactiveStatusEligible),
		Cadence:    g.cadence(in),
		SilentDays: in.SilentDays}
	fail := func(action ProactiveAction, status string, reason RejectReason, isReject bool, detail string) ProactiveOutcome {
		out.Action, out.Status, out.Reason, out.IsReject = action, status, reason, isReject
		out.Detail = detail
		return out
	}
	steps := []struct {
		name string
		run  func() (RejectReason, string, bool)
	}{
		{"schema", func() (RejectReason, string, bool) { return g.checkProactiveSchema(c) }},
		{"evidence", func() (RejectReason, string, bool) { return g.checkProactiveEvidence(c, in) }},
		{"layers", func() (RejectReason, string, bool) { return g.checkLayers(c, in) }},
		{"confidence", func() (RejectReason, string, bool) { return g.checkProactiveConfidence(c, in) }},
		{"safety", func() (RejectReason, string, bool) { return g.checkProactiveSafety(c) }},
		{"preference", func() (RejectReason, string, bool) { return g.checkPreference(c, in) }},
		{"frequency", func() (RejectReason, string, bool) { return g.checkFrequency(c, in) }},
		{"quiet_hours", func() (RejectReason, string, bool) { return g.checkQuietHours(c, in) }},
		{"duplicate", func() (RejectReason, string, bool) { return g.checkProactiveDuplicate(c, in) }},
		{"freshness", func() (RejectReason, string, bool) { return g.checkFreshness(c, in) }},
		{"message_quality", func() (RejectReason, string, bool) { return g.checkMessageQuality(c, in) }},
	}
	for _, step := range steps {
		reason, detail, isReject := step.run()
		if reason != "" {
			action, status := ActionProactiveBlock, string(ProactiveStatusBlocked)
			switch {
			case isReject && step.name == "freshness":
				action, status = ActionProactiveCancel, string(ProactiveStatusCancelled)
			case isReject:
				action, status = ActionProactiveReject, string(ProactiveStatusBlocked)
			}
			return fail(action, status, reason, isReject, detail)
		}
		out.Checks = append(out.Checks, step.name)
	}
	out.HitLayers = g.hitLayers(c, in)
	out.Score = g.eligibilityScore(out.HitLayers)
	out.Action = ActionProactiveEligible
	out.Status = string(ProactiveStatusEligible)
	return out
}

// 第 1 关 schema：trigger 必填且合法（04:60），引用条数有界，文本非空。
func (g ProactiveGate) checkProactiveSchema(c *ProactiveCandidate) (RejectReason, string, bool) {
	if c.UserID == "" {
		return ReasonSchemaInvalid, "缺少 user_id，无法核验任何归属", true
	}
	if !containsTriggerType(TriggerTypes, c.TriggerType) {
		return ReasonTriggerUnknown,
			fmt.Sprintf("trigger_type=%q 不在 04 §2 七类里", c.TriggerType), true
	}
	if !containsTriggerSource(TriggerSources, c.TriggerSource) {
		return ReasonTriggerUnknown,
			fmt.Sprintf("trigger_source=%q 不在 04 §3 七类里；每条 proactive 必须有显式触发（04:60）",
				c.TriggerSource), true
	}
	if strings.TrimSpace(c.Message) == "" {
		return ReasonSchemaInvalid, "message 为空：没有内容可发", true
	}
	if len(c.EvidenceIDs) > g.Policy.MaxEvidencePerCandidate ||
		len(c.RefIDs) > g.Policy.MaxEvidencePerCandidate {
		return ReasonSchemaInvalid, fmt.Sprintf(
			"引用条数 %d/%d 超过单候选上限 %d",
			len(c.EvidenceIDs), len(c.RefIDs), g.Policy.MaxEvidencePerCandidate), true
	}
	return "", "", false
}

// 第 2 关 evidence：存在性与归属 + 分类型最小条数（04 §6:108-127）。
//
// 「1 strong source」的 strong 在 V3 无量 ⇒ 取 N4 的 B 级下界（03 §6:152-155 原值）。
// pattern_followup 的「≥2 independent」用 N4 的同源/同日去重口径数出来，
// 不采信提案自报的条数（P-9/G-9 同一条理由）。
func (g ProactiveGate) checkProactiveEvidence(c *ProactiveCandidate, in ProactiveInputs) (RejectReason, string, bool) {
	owner := map[string]bool{}
	for _, ev := range in.Evidence {
		owner[ev.ID] = ev.UserID == c.UserID
	}
	for _, id := range c.EvidenceIDs {
		owned, seen := owner[id]
		if !seen {
			return ReasonEvidenceMissing, fmt.Sprintf("引用的证据 %s 在库里不存在", id), true
		}
		if !owned {
			return ReasonEvidenceNotOwned, fmt.Sprintf("引用的证据 %s 不属于该用户（不变量 9）", id), true
		}
	}
	p := g.Policy.Proactive
	switch c.TriggerType {
	case TriggerMemoryRecall:
		if len(c.EvidenceIDs) < p.MinEvidenceMemoryRecall {
			return ReasonEvidenceMissing, fmt.Sprintf(
				"memory_recall 至少要有 %d 条来源（04:109-110）", p.MinEvidenceMemoryRecall), true
		}
		if !anyStrong(in.Evidence, c.EvidenceIDs, p.StrongEvidenceMinStrength) {
			return ReasonEvidenceBelowFloor, fmt.Sprintf(
				"没有一条引用达到强支撑下限 %.2f", p.StrongEvidenceMinStrength), true
		}
	case TriggerPatternFollowup:
		if !hasLiveRef(in.Refs, RefPattern) {
			return ReasonEvidenceMissing, "pattern_followup 必须引用 1 条**活的** Pattern（04:112-114）", true
		}
		independent, _, _ := g.Policy.Evidence.Combined(scoredRows(in.Evidence, c.EvidenceIDs))
		if independent < p.MinIndependentPatternFollowup {
			return ReasonEvidenceBelowFloor, fmt.Sprintf(
				"独立观测 %d 条，低于 %d 条要求（04:115 + 03 §6 同源同日不重复计）",
				independent, p.MinIndependentPatternFollowup), true
		}
	case TriggerGoalFollowup:
		if _, ok := findRef(in.Refs, RefGoal, ""); !ok {
			return ReasonEvidenceMissing, "goal_followup 必须引用一条显式 Goal（04:117-118）", true
		}
	case TriggerCommitmentFollowup:
		if _, ok := findRef(in.Refs, RefCommitment, ""); !ok {
			return ReasonEvidenceMissing, "commitment_followup 必须引用一条显式 Commitment（04:120-121）", true
		}
	case TriggerOutcomeCheck:
		if len(in.Refs) == 0 {
			return ReasonEvidenceMissing,
				"outcome_check 必须指回上一封 proactive/goal/commitment 并带跟进窗口（04:123-127）", true
		}
	case TriggerReflectionPrompt:
		if len(c.EvidenceIDs) < p.MinEvidenceReflection {
			return ReasonEvidenceMissing, fmt.Sprintf(
				"reflection_prompt 至少要引用 %d 条已算分证据（V3 §6 未给，七-4 P-3 拍的默认）",
				p.MinEvidenceReflection), true
		}
	case TriggerMilestone:
		// 七-4 P-3：milestone 由时间/里程碑触发，改判「锚点必须可核实」而不索要证据条数。
		if !in.HasAnchor {
			return ReasonEvidenceMissing, "milestone 的时间锚点没能回查到权威行，无「今天是第几天」的依据", true
		}
	}
	return "", "", false
}

// 第 3 关 layers：PRD「为什么今天找你」四层公式（closenai.html:637-638 + 四层表）。
// 一条都没有 ⇒ 宁可不发（原话），所以这一关是硬门而不是打分。
func (g ProactiveGate) checkLayers(c *ProactiveCandidate, in ProactiveInputs) (RejectReason, string, bool) {
	hit := g.hitLayers(c, in)
	need := g.Policy.Proactive.MinLayersFor(c.IsSensitive())
	if len(hit) < need {
		return ReasonLayerBelowMinimum, fmt.Sprintf(
			"四层公式命中 %v 共 %d 层，低于 %d 层下限（closenai.html:637-638；敏感域 %d 层）",
			hit, len(hit), need, g.Policy.Proactive.SensitiveMinLayers), true
	}
	return "", "", false
}

// hitLayers 逐层核验。四层全部取自可核实的库态或文本本身，不接受提案自报「我属于第几层」：
//   - ①引用记忆：至少一条引用到的证据真实存在且属于该用户；
//   - ②发现模式：引用到 Pattern 或 Insight 且那一行还活着；
//   - ③关联当下：时间锚点回查得到，且与本次发送落在同一个本地日历日或未来（待发的排期）；
//   - ④开放提问：文本里有一个不要求「是/否」的问题。
func (g ProactiveGate) hitLayers(c *ProactiveCandidate, in ProactiveInputs) []Layer {
	var hit []Layer
	for _, id := range c.EvidenceIDs {
		for _, ev := range in.Evidence {
			if ev.ID == id && ev.UserID == c.UserID {
				hit = append(hit, LayerMemoryRecall)
				break
			}
		}
	}
	for _, ref := range in.Refs {
		if (ref.RefType == RefPattern || ref.RefType == RefInsight) &&
			!isStaleStatus(ref.RefType, ref.Status) {
			hit = append(hit, LayerPatternFound)
			break
		}
	}
	if in.HasAnchor && !in.AnchorAt.IsZero() {
		today := localDayStartUTC(in.Now, in.Timezone)
		anchorDay := localDayStartUTC(in.AnchorAt, in.Timezone)
		if !anchorDay.Before(today) {
			hit = append(hit, LayerContextNow)
		}
	}
	if hasOpenQuestion(c.Message) {
		hit = append(hit, LayerOpenQuestion)
	}
	return hit
}

// eligibilityScore 是 P-8 的落库口径：命中层数 / 4，只会是 0.25/0.50/0.75/1.00 四档，
// 与 proactives.eligibility_score DECIMAL(5,4) 的 0..1 CHECK（0002:368）天然对齐。
//
// V3 04:77-85 的六因子加权**不采纳**：减项 FatiguePenalty 全文无定义（04:84），
// 且 04:83 给 Safety 计 0.15 权重与 04:87「Safety 是硬门」并存无解释。
func (g ProactiveGate) eligibilityScore(hit []Layer) float64 {
	return float64(len(hit)) / 4.0
}

// 第 4 关 confidence：分类型下限（04 §6:131-137）+ 敏感域加严（04:139 + N3 G-5 的 0.85）。
func (g ProactiveGate) checkProactiveConfidence(c *ProactiveCandidate, in ProactiveInputs) (RejectReason, string, bool) {
	_ = in
	limit := g.Policy.Proactive.ConfidenceMinFor(c.TriggerType, c.IsSensitive())
	if c.Confidence < 0 || c.Confidence > 1 {
		return ReasonSchemaInvalid, fmt.Sprintf("confidence=%v 越出 0..1", c.Confidence), true
	}
	if limit > 0 && c.Confidence < limit {
		return ReasonConfidenceBelowThresh, fmt.Sprintf(
			"%s 的置信 %.4f 低于下限 %.2f（04 §6%s）",
			c.TriggerType, c.Confidence, limit, sensitiveNote(c.IsSensitive())), true
	}
	return "", "", false
}

func sensitiveNote(sensitive bool) string {
	if sensitive {
		return " + 敏感域加严"
	}
	return ""
}

// 第 5 关 safety：绝对优先（04:391），危机信号命中即停正常触达（04 §21）；
// 判定输入缺失按 D-8 fail-closed，超时不得等于放行。
func (g ProactiveGate) checkProactiveSafety(c *ProactiveCandidate) (RejectReason, string, bool) {
	if c.Safety == nil {
		return ReasonSafetyUnavailable, "缺少安全判定输入，按 fail-closed 拦下（D-8 / 01 不变量 10）", true
	}
	switch c.Safety.Status {
	case SafetyUnavailable:
		return ReasonSafetyUnavailable, "安全判定不可用，不得放行主动触达（04:391 安全优先）", true
	case SafetyCrisis:
		return ReasonSafetyBlocked, fmt.Sprintf(
			"命中危机信号 %v：主动触达管线让位给安全流程（04 §21）",
			c.Safety.Categories), true
	case SafetyUnsafe:
		// D-G17：这条也是拦下，但**不让位给安全流程**——主动触达对用户说一句
		// 「你还好吗」的前提是判定为危机，而 unsafe 只是「内容违规」。
		return ReasonSafetyBlocked, fmt.Sprintf(
			"命中违规内容 %v：主动触达不发（拦下，非危机，不进安全流程，D-G17）",
			c.Safety.Categories), true
	case SafetyOK:
		return "", "", false
	default:
		return ReasonSafetyUnavailable,
			fmt.Sprintf("安全状态 %q 不是四态之一，按 fail-closed 拦下", c.Safety.Status), true
	}
}

// 第 6 关 user preference：只认 users 表的实际列，不认提案自报（P-7 同一精神）。
//
// push_enabled=false 单独不拦：那是「不要系统通知」，不是「不要这条消息」；
// 现行链也是在执行层才判推送开关（journey_service.py:339）。真正的一票否决是
// ai_consent=false（现行链 deliver:267-270 直接 skip）与 push_frequency='off'。
func (g ProactiveGate) checkPreference(c *ProactiveCandidate, in ProactiveInputs) (RejectReason, string, bool) {
	_ = c
	if !in.Prefs.Found {
		return ReasonPreferenceOff, "读不到该用户的偏好行，无法确认同意状态（fail-closed）", false
	}
	if !in.Prefs.AIConsent {
		return ReasonPreferenceOff, "用户未授予 AI 权限（users.ai_consent=false）", false
	}
	if strings.EqualFold(in.Prefs.PushFrequency, "off") {
		return ReasonPreferenceOff, "用户把主动触达频率设为 off", false
	}
	return "", "", false
}

// 第 7 关 frequency：频次上限 + 疲劳阶梯（含停推）。
func (g ProactiveGate) checkFrequency(c *ProactiveCandidate, in ProactiveInputs) (RejectReason, string, bool) {
	verdict := g.Policy.Proactive.CheckFrequency(FrequencyInput{
		Trigger:      c.TriggerType,
		CommitmentID: commitmentRefID(in.Refs),
		Prefs:        in.Prefs,
		Cadence:      g.cadence(in),
		History:      in.History,
		Now:          in.Now,
	})
	if !verdict.OK {
		return verdict.Reason, verdict.Detail, false
	}
	return "", "", false
}

// 第 8 关 quiet hours：按用户本地时区（04:163）。发送时刻未给时按「现在」判。
func (g ProactiveGate) checkQuietHours(c *ProactiveCandidate, in ProactiveInputs) (RejectReason, string, bool) {
	at := in.Now
	if c.HasScheduledAt {
		at = c.ScheduledAt
	}
	zone := in.Timezone
	if g.Policy.Proactive.InQuietHours(at, zone) {
		return ReasonQuietHours, fmt.Sprintf(
			"本地 %02d:00 落在静默时段 %02d:00-%02d:00（04 §8 + 七-4 P-5 的改窗）",
			localHour(at, zone), g.Policy.Proactive.QuietStartHour, g.Policy.Proactive.QuietEndHour), false
	}
	_ = c
	return "", "", false
}

// 第 9 关 duplicate：同一句话在窗口内已发/已排 ⇒ 不再发第二遍。
func (g ProactiveGate) checkProactiveDuplicate(c *ProactiveCandidate, in ProactiveInputs) (RejectReason, string, bool) {
	if id := g.Policy.Proactive.duplicateOf(c.Message, in.History, in.Now, in.Timezone); id != "" {
		return ReasonDuplicateMessage, fmt.Sprintf(
			"窗口 %d 天内已有同一句话（%s），不得重发", g.Policy.Proactive.DuplicateWindowDays, id), false
	}
	return "", "", false
}

// 第 10 关 freshness：04 §12 的六个取消条件，全部按引用行的**当前库态**判。
// 命中即 cancelled（不是 blocked）：这一条 proactive 已经不该存在了。
func (g ProactiveGate) checkFreshness(c *ProactiveCandidate, in ProactiveInputs) (RejectReason, string, bool) {
	corrected := map[string]bool{}
	for _, id := range in.Corrected {
		corrected[id] = true
	}
	returned := map[string]bool{}
	for _, ref := range in.Refs {
		returned[ref.ID] = true
	}
	// 04 §10「LLM must not introduce unsupported facts」的可执行形式：引用到的每一行
	// 都必须真的在该用户的库里。ListRefStates 只返回查得到的行，所以「报了却没回来」
	// 要么是编造要么已被删——这条只能在集合层判，漏掉的行根本不会进下面的循环。
	for _, id := range c.RefIDs {
		if !returned[id] {
			return ReasonStaleTrigger, fmt.Sprintf(
				"引用的权威行 %s 在该用户名下查不到（04 §10 不得引入无依据的引用）", id), true
		}
	}
	if c.AnchorRefID != "" && !in.HasAnchor {
		return ReasonStaleTrigger, fmt.Sprintf(
			"锚点行 %s 给不出可核实的时刻，「今天是哪天」无从判定", c.AnchorRefID), true
	}
	for _, ref := range in.Refs {
		if corrected[ref.ID] {
			return ReasonStaleTrigger, fmt.Sprintf(
				"用户近期纠正过 %s %s，不得再按旧值触达（04:231）", ref.RefType, ref.ID), true
		}
		if isStaleStatus(ref.RefType, ref.Status) {
			return ReasonStaleTrigger, fmt.Sprintf(
				"引用的 %s 现在是 %s，队列里的触达已过期（04 §12）", ref.RefType, ref.Status), true
		}
	}
	// 沉默到期：04 §19「Later: proactive → expired」。到期时长 V3 没给，取
	// DefaultValidityHours（P-8 待标定项）；发送时刻的二次校验会走到这里。
	if c.HasExpiresAt && !in.Now.Before(c.ExpiresAt) {
		return ReasonStaleTrigger, fmt.Sprintf(
			"已超过有效期 %s，沉默不得事后补发（04 §19）", c.ExpiresAt.UTC().Format(time.RFC3339)), true
	}
	return "", "", false
}

// 第 11 关 message quality：PRD 的形态要求（closenai.html:663「50-150 字」）
// + N3 已有的标签词/绝对量词禁令（同一套规则，不建第二套）。
func (g ProactiveGate) checkMessageQuality(c *ProactiveCandidate, in ProactiveInputs) (RejectReason, string, bool) {
	_ = in
	runes := []rune(c.Message)
	n := len(runes)
	p := g.Policy.Proactive
	if n < p.MinMessageRunes || n > p.MaxMessageRunes {
		return ReasonMessageQuality, fmt.Sprintf(
			"消息 %d 字，越出 PRD 的 %d-%d 字区间（closenai.html:663）",
			n, p.MinMessageRunes, p.MaxMessageRunes), true
	}
	text := strings.ToLower(c.Message)
	for _, term := range LabelTerms {
		if strings.Contains(text, strings.ToLower(term)) {
			return ReasonLanguageOverreach, fmt.Sprintf(
				"主动触达措辞出现「%s」：不得把观察说成诊断或人格标签（规范 §1.2-§1.3）", term), true
		}
	}
	for _, term := range absoluteQuantifiers {
		if strings.Contains(text, strings.ToLower(term)) {
			return ReasonTemporalOverreach, fmt.Sprintf(
				"措辞出现绝对量词「%s」：有界窗口内的观察不得说成 always/从不（03:416-424）", term), true
		}
	}
	if c.IsSensitive() && !containsAny(text, HedgeTerms) {
		return ReasonLanguageOverreach, "敏感域触达未留限定语，不得把推断说成定论（03 §14 special policy）", true
	}
	return "", "", false
}

// cadence 由 Service 取到的沉默天数算出（阶梯只降不增，见 MinIntervalDays）。
func (g ProactiveGate) cadence(in ProactiveInputs) Cadence {
	return g.Policy.Proactive.CadenceOf(in.SilentDays)
}

// isStaleStatus 按引用类型给出「这条触达不该再发」的状态集合。
// 词表取自各表在 N2 的 status 取值（02 §8-§13），不是自由文本。
func isStaleStatus(kind RefType, status string) bool {
	switch kind {
	case RefPattern:
		return contains([]string{"rejected", "invalidated", "expired", "outdated"}, status)
	case RefFact:
		return contains([]string{"rejected", "corrected", "outdated", "invalidated"}, status)
	case RefGoal:
		return contains([]string{"completed", "cancelled", "invalidated"}, status)
	case RefCommitment:
		return contains([]string{"completed", "cancelled", "invalidated"}, status)
	case RefInsight:
		return contains([]string{"retracted", "expired", "corrected", "dismissed", "rejected"}, status)
	case RefOutcome:
		return contains([]string{"invalidated", "corrected"}, status)
	case RefProactive:
		return contains([]string{"blocked", "cancelled", "expired"}, status)
	default:
		return false
	}
}

// hasOpenQuestion 是 PRD 第④层「一个不需要是/否回答的问题」的可执行形式：
// 含问号 + 含特指疑问词，且不是「是不是/有没有/…吗」这类封闭式问句。
// 词表保守（只认的确切形态），判不出来就不算这一层——宁可少算一层，
// 也不能把封闭式问句算成开放层从而凑够 2 层发出去。
func hasOpenQuestion(text string) bool {
	if !strings.ContainsAny(text, "?？") {
		return false
	}
	lowered := strings.ToLower(text)
	for _, term := range openQuestionTerms {
		if strings.Contains(lowered, term) {
			for _, closed := range closedQuestionTerms {
				if strings.Contains(lowered, closed) {
					return false
				}
			}
			return true
		}
	}
	return false
}

var openQuestionTerms = []string{
	"什么", "为什么", "怎么", "怎样", "如何", "哪", "多少", "感觉", "想聊", "聊聊",
	"what", "why", "how", "which", "when", "where", "feel", "tell me",
}

var closedQuestionTerms = []string{
	"是不是", "有没有", "会不会", "好不好", "对不对", "可以吗", "行吗", "吗？", "吗?",
	"do you", "are you", "is it", "did you", "can you",
}

func containsTriggerType(set []TriggerType, want TriggerType) bool {
	for _, item := range set {
		if item == want {
			return true
		}
	}
	return false
}

func containsTriggerSource(set []TriggerSource, want TriggerSource) bool {
	for _, item := range set {
		if item == want {
			return true
		}
	}
	return false
}

func findRef(refs []RefState, kind RefType, id string) (RefState, bool) {
	for _, ref := range refs {
		if ref.RefType == kind && (id == "" || ref.ID == id) {
			return ref, true
		}
	}
	return RefState{}, false
}

// hasLiveRef 要求该类型至少有一条引用**在库里且没进入失效态**——04:112 写的是
// 「1 active Pattern」，光引用到一行不算。
func hasLiveRef(refs []RefState, kind RefType) bool {
	for _, ref := range refs {
		if ref.RefType == kind && !isStaleStatus(ref.RefType, ref.Status) {
			return true
		}
	}
	return false
}

func commitmentRefID(refs []RefState) string {
	for _, ref := range refs {
		if ref.RefType == RefCommitment {
			return ref.ID
		}
	}
	return ""
}

// anyStrong 判断引用集合里是否至少有一条达到强支撑下限。
func anyStrong(rows []Evidence, ids []string, min float64) bool {
	for _, id := range ids {
		for _, row := range rows {
			if row.ID == id && row.HasStrength && row.Strength >= min {
				return true
			}
		}
	}
	return false
}

// scoredRows 把引用到的证据折成 N4 的算分行（独立性判定共用一个函数，
// 免得 proactive 与 evidence 两侧对「独立」给出不同答案）。
func scoredRows(rows []Evidence, ids []string) []EvidenceRow {
	out := make([]EvidenceRow, 0, len(ids))
	for _, id := range ids {
		for _, row := range rows {
			if row.ID != id {
				continue
			}
			out = append(out, EvidenceRow{
				ID: row.ID, UserID: row.UserID, Strength: row.Strength,
				HasStrength: row.HasStrength, Role: row.Role,
				SourceID: row.SourceID, ObservedAt: row.ObservedAt,
			})
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

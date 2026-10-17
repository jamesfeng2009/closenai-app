package domain

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"unicode/utf8"
)

// Action 是 Gate 的三种结论之一。
type Action string

const (
	// ActionInsert：接纳并新建权威行（status 由 Service 按 Kind 定）。
	ActionInsert Action = "insert"
	// ActionReactivate：指纹命中既有 pattern，**刷新而非重复插**
	// （V3 03:310-311「reactivate equivalent pattern, no duplicates」）。
	ActionReactivate Action = "reactivate"
	// ActionReject：拒绝。行仍落库，status=rejected + reject_reason + generation_run_id（G-2）。
	ActionReject Action = "reject"
)

// Outcome 是 Gate 的结论。Detail 落 gate_detail / event payload，是「为什么被拦」的可解释面。
type Outcome struct {
	Action        Action
	Reason        RejectReason
	Detail        string
	DuplicateOfID string
	// Checks 是九关的逐项**通过**标记，顺序即 03 §14 的校验序（N4 定稿，G-3 的七关是它的前缀），
	// 落 gate_detail 供 golden 复现。
	Checks []string
}

// Reader 是 Gate 需要的只读视图（同一事务内取，避免判定与写入之间读到不同快照）。
type Reader interface {
	LoadEvidence(ctx context.Context, ids []string) ([]Evidence, error)
	FindPatternByFingerprint(ctx context.Context, userID, fingerprint string) (string, bool, error)
	FindLiveFactKey(ctx context.Context, userID, subjectKey, predicate, objectValue string) (string, bool, error)
	// RecentUserRejection 返回该用户在冷却窗口内**用户亲手拒绝过**（03 §12:335-338）
	// 的同指纹 pattern 行 id。数据面是 user_feedback(target_type='pattern',
	// feedback_type='reject')，不是 Gate 自己写的 rejected 行——两者语义不同：
	// 前者是「用户说这条不对」，后者是「领域层没接纳」，只有前者构成抑制签名。
	RecentUserRejection(ctx context.Context, userID, fingerprint string,
		withinDays int) (string, bool, error)
}

// Gate 是 candidate → 权威态的唯一判定器。
//
// 校验序（03 §14:361-371 原序，N4 定稿；G-3 的七关是它的前缀）：
// schema → evidence → temporal → claim type → confidence → safety →
// language → duplicate → user history。
type Gate struct {
	Policy Policy
}

// 合法集合的出处：
//   - origin 六值＝V3 01:419-426 = 02 §3 evidence_type。
//   - kind／claim_type＝上面 candidate.go 与 03 §14。
var allowedOrigins = []string{
	"direct", "repeated", "temporal", "behavioral", "user_confirmed", "user_corrected",
}

// absoluteQuantifiers：V3 03:416-424「Do not say "You always..." when the evidence
// only covers a bounded window」。V1 的 insight 全部算在有界窗口内算出（03 §13 的
// bounded structured evidence packet），所以绝对量词在 N3 一律拦，不做「窗口是否够宽」
// 的推断——那是 N4 的措辞矩阵，此处只落这一条确定性规则。
var absoluteQuantifiers = []string{
	"always", "never", "every time", "all the time", "constantly",
	"总是", "从不", "每一次", "每次都", "一直", "永远",
}

// causalConnectors：《沟通证据与措辞规范-2026-09-28》§1.2「相关当因果」禁令的动词表
// + V3 03 §15「X caused Y」。只在候选同时引用 ≥2 条证据时判定——禁令原文说的是
// 「禁用因果动词连接两条 Pattern」，单条观察里的「因为」不是这条规则要拦的东西，
// 扩大成通用黑名单会误杀合法观察句。
var causalConnectors = []string{
	"导致", "造成", "致使", "说明你", "是因为",
	"caused", "leads to", "results in", "makes you", "because of",
}

// Check 按序跑九关，第一个失败即定论（顺序本身是契约：低置信 + 因果句的样本
// 在 V3 原序里应先落 temporal/claim_type 而不是 confidence，golden 复现依赖这条）。
//
// Outcome.Checks 只装**通过**的关，按校验序排列；被拦下时它记录了「前几关是绿的」，
// 落进 gate_detail 就是 N4 九关 golden 的复现依据。
func (g Gate) Check(ctx context.Context, c *Candidate, r Reader) (Outcome, error) {
	out := Outcome{Action: ActionInsert}
	reject := func(reason RejectReason, detail string) Outcome {
		out.Action, out.Reason, out.Detail = ActionReject, reason, detail
		return out
	}

	steps := []struct {
		name string
		run  func() (RejectReason, string, error)
	}{
		{"schema", func() (RejectReason, string, error) {
			reason, detail := g.checkSchema(c)
			return reason, detail, nil
		}},
		{"evidence", func() (RejectReason, string, error) {
			return g.checkEvidence(ctx, c, r)
		}},
		{"temporal", func() (RejectReason, string, error) {
			reason, detail := g.checkTemporal(c)
			return reason, detail, nil
		}},
		{"claim_type", func() (RejectReason, string, error) {
			reason, detail := g.checkClaimType(c)
			return reason, detail, nil
		}},
		{"confidence", func() (RejectReason, string, error) {
			reason, detail := g.checkConfidence(c)
			return reason, detail, nil
		}},
		{"safety", func() (RejectReason, string, error) {
			reason, detail := g.checkSafety(c)
			return reason, detail, nil
		}},
		{"language", func() (RejectReason, string, error) {
			reason, detail := g.checkLanguage(c)
			return reason, detail, nil
		}},
	}
	for _, step := range steps {
		reason, detail, err := step.run()
		if err != nil {
			return Outcome{}, err
		}
		if reason != "" {
			return reject(reason, detail), nil
		}
		out.Checks = append(out.Checks, step.name)
	}

	dup, err := g.checkDuplicate(ctx, c, r)
	if err != nil {
		return Outcome{}, err
	}
	if dup.Action == ActionReject {
		return reject(dup.Reason, dup.Detail), nil
	}
	out.Checks = append(out.Checks, "duplicate")

	// 第 9 关 user history（03 §14:370、§12:335-338）。放在 duplicate 之后是原序要求，
	// 语义上也必须如此：第 8 关找到的「同指纹既有行」正是这一关要查的拒绝签名归属。
	// reactivate 也要过这一关——用户否掉的指纹不得靠一批新证据悄悄复活。
	history, reason, detail, err := g.checkUserHistory(ctx, c, r, dup)
	if err != nil {
		return Outcome{}, err
	}
	if reason != "" {
		halted := reject(reason, detail)
		// DuplicateOfID 非空＝库里已有该行，Service 不得再插一条（同指纹偏唯一索引
		// ix_patterns_user_fingerprint_unique 会直接 23505），只补审计事件。
		halted.DuplicateOfID = history
		return halted, nil
	}
	out.Checks = append(out.Checks, "user_history")
	out.Action, out.DuplicateOfID, out.Detail = dup.Action, dup.DuplicateOfID, dup.Detail
	return out, nil
}

func (g Gate) reject(reason RejectReason, detail string) Outcome {
	return Outcome{Action: ActionReject, Reason: reason, Detail: detail}
}

// GateDetail 把结论序列化成 insights.gate_detail 的文本。N2 覆写：状态列一律 String/TEXT，
// 不用 JSONB，所以这里只做编码，解析交给读路径（N9）。
func (o Outcome) GateDetail() string {
	raw, _ := json.Marshal(struct {
		Reason string   `json:"reason,omitempty"`
		Detail string   `json:"detail,omitempty"`
		Checks []string `json:"checks"`
	}{o.Reason.String(), o.Detail, o.Checks})
	return string(raw)
}

// 第 1 关 schema：畸形 LLM 产出在这里挡住（01 §25 LLM Failure Containment 的
// 「validate schema / allowed entity types / confidence range / time range」）。
func (g Gate) checkSchema(c *Candidate) (RejectReason, string) {
	switch c.Kind {
	case KindFact, KindPattern, KindInsight:
	default:
		return ReasonSchemaInvalid, fmt.Sprintf("kind=%q 不是 fact/pattern/insight 之一", c.Kind)
	}
	if c.UserID == "" {
		return ReasonSchemaInvalid, "user_id 为空：Python 不能替用户授权（09 §ownership）"
	}
	// generation_run_id 是不变量 8 的落点，缺它就没有可追溯性，不接受入表。
	if c.GenerationRunID == "" {
		return ReasonSchemaInvalid, "generation_run_id 为空，产出不可追溯"
	}
	if c.Confidence < 0 || c.Confidence > 1 {
		return ReasonSchemaInvalid, fmt.Sprintf("confidence=%v 越出 [0,1]", c.Confidence)
	}
	if len(c.EvidenceIDs) > g.Policy.MaxEvidencePerCandidate {
		return ReasonSchemaInvalid, fmt.Sprintf("证据引用 %d 条，超上限 %d",
			len(c.EvidenceIDs), g.Policy.MaxEvidencePerCandidate)
	}
	seen := map[string]bool{}
	for _, id := range c.EvidenceIDs {
		if id == "" {
			return ReasonSchemaInvalid, "证据引用里有空 id"
		}
		if seen[id] {
			return ReasonSchemaInvalid, fmt.Sprintf("证据 id %q 重复引用", id)
		}
		seen[id] = true
	}
	if c.HasWindow && !c.WindowFrom.IsZero() && !c.WindowTo.IsZero() &&
		c.WindowTo.Before(c.WindowFrom) {
		return ReasonSchemaInvalid, "time_to 早于 time_from"
	}
	for _, pair := range []struct {
		name, value string
		max         int
		required    bool
	}{
		{"subject_key", c.SubjectKey, g.Policy.MaxSubjectKeyLen, c.Kind == KindFact},
		{"predicate", c.Predicate, g.Policy.MaxPredicateLen, c.Kind == KindFact},
		{"pattern_type", c.PatternType, g.Policy.MaxEnumLen, c.Kind == KindPattern},
		{"scope", c.Scope, g.Policy.MaxScopeLen, false},
		{"claim_type", c.ClaimType, g.Policy.MaxEnumLen, c.Kind == KindInsight},
		{"fingerprint", c.Fingerprint, g.Policy.MaxFingerprintLen, false},
		{"origin", c.Origin, g.Policy.MaxEnumLen, c.Kind == KindFact},
	} {
		if pair.required && strings.TrimSpace(pair.value) == "" {
			return ReasonSchemaInvalid, pair.name + " 必填（按 kind=" + string(c.Kind) + "）"
		}
		if utf8.RuneCountInString(pair.value) > pair.max {
			return ReasonSchemaInvalid, fmt.Sprintf("%s 长 %d，超列宽 %d",
				pair.name, utf8.RuneCountInString(pair.value), pair.max)
		}
	}
	for _, pair := range []struct {
		name, value string
		required    bool
	}{
		{"object_value", c.ObjectValue, c.Kind == KindFact},
		{"description", c.Description, c.Kind == KindPattern},
		{"statement", c.Statement, c.Kind == KindInsight},
	} {
		if pair.required && strings.TrimSpace(pair.value) == "" {
			return ReasonSchemaInvalid, pair.name + " 必填（按 kind=" + string(c.Kind) + "）"
		}
	}
	if c.Kind == KindFact && !contains(allowedOrigins, c.Origin) {
		return ReasonSchemaInvalid, fmt.Sprintf("origin=%q 不在 01:419-426 的六值集合", c.Origin)
	}
	if c.Kind == KindPattern && c.Fingerprint == "" {
		return ReasonSchemaInvalid, "pattern 必须带指纹：08 §3 的幂等入口没有 key 就无法去重"
	}
	return "", ""
}

// 第 2 关证据：存在性 + 归属（tasks N3「引用 id 必须属于该 user」）
// + D 级下限（03:158-159）+ 派生 claim 的独立性下限（03 §6/§7）。
func (g Gate) checkEvidence(ctx context.Context, c *Candidate, r Reader) (RejectReason, string, error) {
	if len(c.EvidenceIDs) == 0 {
		// 03 §13「No supporting evidence = reject」；不变量 1/2。
		return ReasonEvidenceMissing, "candidate 不带任何证据引用", nil
	}
	rows, err := r.LoadEvidence(ctx, c.EvidenceIDs)
	if err != nil {
		return "", "", fmt.Errorf("domain: load evidence: %w", err)
	}
	found := make(map[string]Evidence, len(rows))
	for _, row := range rows {
		found[row.ID] = row
	}
	for _, id := range c.EvidenceIDs {
		row, ok := found[id]
		if !ok {
			return ReasonEvidenceNotFound, fmt.Sprintf("证据 %s 不存在", id), nil
		}
		if row.UserID != c.UserID {
			return ReasonEvidenceNotOwned,
				fmt.Sprintf("证据 %s 属于另一个用户", id), nil
		}
	}
	if c.Kind != KindInsight {
		return "", "", nil
	}
	for _, row := range rows {
		if row.HasStrength && row.Strength < g.Policy.EvidenceStrengthFloorForD {
			return ReasonEvidenceBelowFloor, fmt.Sprintf(
				"证据 %s 强度 %.4f 低于 D 级下限 %.4f", row.ID, row.Strength,
				g.Policy.EvidenceStrengthFloorForD), nil
		}
	}
	// 03:158-159「A/B can support normal derived claims，C is exploratory」。
	// observation 是对**所引证据本身**的复述，一条即可；trend/recurrence/change/
	// comparison/goal_progress/behavioral_sequence 都是从多条观测**派生**的断言，
	// 单条证据撑不起（§7 给 fact 的同形门是 ≥2 独立观测，这里同口径）。
	// 计数按 §6:161-163 去重：同 source_record、同一自然日各只算一次。
	if c.ClaimType != "" && c.ClaimType != "observation" {
		independent, _, conflict := g.Policy.Evidence.Combined(derivedRows(rows))
		if conflict {
			return ReasonEvidenceConflict,
				"存在不弱于最强支撑证据的 contradicting 证据，派生 claim 不得只采单侧（03 §7:186-187）", nil
		}
		if independent < g.Policy.Evidence.InsightMinIndependentForDerived {
			return ReasonEvidenceBelowFloor, fmt.Sprintf(
				"claim_type=%q 需 ≥%d 条独立证据，实际 %d 条（03 §6 派生 claim 需 A/B 级支撑；"+
					"未算分的证据行不计入，算分是 03:92-93 要求的确定性校验产物）",
				c.ClaimType, g.Policy.Evidence.InsightMinIndependentForDerived, independent), nil
		}
	}
	return "", "", nil
}

// derivedRows 把证据投影转成聚合口径用的行；role 为空按 DDL 默认 supporting 处理。
func derivedRows(rows []Evidence) []EvidenceRow {
	out := make([]EvidenceRow, 0, len(rows))
	for _, row := range rows {
		role := row.Role
		if role == "" {
			role = "supporting"
		}
		out = append(out, EvidenceRow{
			ID: row.ID, UserID: row.UserID, Strength: row.Strength,
			HasStrength: row.HasStrength, Role: role,
			SourceID: row.SourceID, ObservedAt: row.ObservedAt,
		})
	}
	return out
}

// 第 5 关置信（03 §14 排在 Temporal、Claim Type 之后）。0 下限＝该关对该 kind
// 不设限（V3 未给数，见 Policy 注释）。
func (g Gate) checkConfidence(c *Candidate) (RejectReason, string) {
	var min float64
	switch c.Kind {
	case KindInsight:
		if c.IsSensitive() {
			min = g.Policy.InsightSensitiveConfMin
		} else {
			min = g.Policy.InsightConfidenceMin
		}
	case KindFact:
		min = g.Policy.FactConfidenceMin
	case KindPattern:
		min = g.Policy.PatternConfidenceMin
	}
	if min > 0 && c.Confidence < min {
		return ReasonConfidenceBelowThresh, fmt.Sprintf(
			"confidence=%.4f < %s 下限 %.4f", c.Confidence, c.Kind, min)
	}
	return "", ""
}

// 第 4 关 claim type：默认受限集合一律拒（03 §14）；pattern 的禁用类型同理
// （03 §10：attachment_style/personality_type/diagnosis/trauma_type）。
func (g Gate) checkClaimType(c *Candidate) (RejectReason, string) {
	if c.Kind == KindInsight {
		if contains(RestrictedClaimTypes, c.ClaimType) {
			return ReasonClaimTypeRestricted, fmt.Sprintf("claim_type=%q V1 默认受限", c.ClaimType)
		}
		if !contains(AllowedClaimTypes, c.ClaimType) {
			return ReasonClaimTypeRestricted, fmt.Sprintf("claim_type=%q 不在 V1 允许集合", c.ClaimType)
		}
	}
	if c.Kind == KindPattern && contains(ForbiddenPatternTypes, c.PatternType) {
		return ReasonClaimTypeRestricted, fmt.Sprintf("pattern_type=%q 属禁用类型", c.PatternType)
	}
	// 措辞越界：自称 observation 却用因果动词连接两条以上证据，仍按 claim type 拒。
	if len(c.EvidenceIDs) >= 2 {
		text := strings.ToLower(c.Statement + " " + c.Description + " " + c.ObjectValue)
		for _, term := range causalConnectors {
			if strings.Contains(text, strings.ToLower(term)) {
				return ReasonClaimTypeRestricted,
					fmt.Sprintf("措辞用「%s」把共现写成因果（规范 §1.2 / 03 §15）", term)
			}
		}
	}
	return "", ""
}

// 第 3 关时间窗（03 §14 原序在 Claim Type 之前）：绝对量词措辞在有界窗口下拒（03:416-424）。
func (g Gate) checkTemporal(c *Candidate) (RejectReason, string) {
	text := strings.ToLower(c.Statement + " " + c.Description + " " + c.ObjectValue)
	for _, term := range absoluteQuantifiers {
		if strings.Contains(text, strings.ToLower(term)) {
			return ReasonTemporalOverreach,
				fmt.Sprintf("措辞含绝对量词「%s」，而证据只覆盖有界窗口（03 §14 Temporal wording）", term)
		}
	}
	return "", ""
}

// 第 6 关安全：危机硬覆盖 + D-8 的 fail-closed。只管「信号存在与否」，
// 措辞的确定性强度在第 7 关（03 §14 把 Safety 与 Language 分成两关，此处不合关）。
func (g Gate) checkSafety(c *Candidate) (RejectReason, string) {
	if c.Safety == nil || c.Safety.Status == SafetyUnavailable {
		return ReasonSafetyUnavailable, "安全判定缺失，超时不得等于放行（D-8）"
	}
	if c.Safety.Status == SafetyCrisis {
		return ReasonSafetyBlocked, fmt.Sprintf(
			"危机信号 %v 退出常规管道（03 §14 Safety）", c.Safety.Categories)
	}
	if c.Safety.Status == SafetyUnsafe {
		// D-G17（批 4）：拦是拦，但措辞不能说成危机。三态时代这一格挤在 crisis 里，
		// 于是「黑名单命中」在 reject reason 上被读成「用户处在危险中」——
		// 前者只需拦下，后者会牵出安全流程（04 §21 的触达让位）。
		// 码复用 safety_blocked：reason 值集是两侧对账面，加新码要连 Python 的规范一起动。
		return ReasonSafetyBlocked, fmt.Sprintf(
			"违规内容 %v 拦下（非危机，不进危机链路，D-G17）", c.Safety.Categories)
	}
	if c.Safety.Status != SafetyOK {
		return ReasonSafetyUnavailable, fmt.Sprintf("未知安全态 %q", c.Safety.Status)
	}
	return "", ""
}

// 第 7 关 language：措辞不得把观察说成诊断/人格标签；敏感域必须留限定语。
//
// 与第 4 关 claim_type 的分工（刻意不建第二套规则，tasks N4:490）：
// claim_type 判**提案自称的类型**（cause/diagnosis/… 一律拒）与共现写成因果的措辞；
// 本关判**没有类型标签可依据的语言面越界**——提案自称 observation，句式里却出现
// 03 §8 的标签词或诊断句式，第 4 关看不见它。
func (g Gate) checkLanguage(c *Candidate) (RejectReason, string) {
	text := strings.ToLower(c.Statement + " " + c.Description + " " + c.ObjectValue)
	for _, term := range LabelTerms {
		if strings.Contains(text, strings.ToLower(term)) {
			return ReasonLanguageOverreach, fmt.Sprintf(
				"措辞出现「%s」：观察不得说成诊断或人格标签（规范 §1.2-§1.3 / 03 §8）", term)
		}
	}
	if c.Kind == KindInsight && c.IsSensitive() && !containsAny(text, HedgeTerms) {
		return ReasonLanguageOverreach,
			"敏感域（危机/医疗信号）的 insight 未留限定语，不得把推断说成定论（03 §14 敏感域 special policy）"
	}
	return "", ""
}

// 第 9 关 user history：用户拒绝过的同指纹不得立即重新生成（03 §12:335-338）。
//
// 只对 pattern 生效：insight 的重复键 V3 从未给出（insights 表没有指纹列，
// user_feedback 也只按 target_id 挂），N3 已裁定「不自造键」，该缺口归 N6 的
// user_feedback 写入侧。这里半做等于凭空发明键。
func (g Gate) checkUserHistory(ctx context.Context, c *Candidate, r Reader,
	dup Outcome) (string, RejectReason, string, error) {
	if c.Kind != KindPattern || c.Fingerprint == "" {
		return "", "", "", nil
	}
	id, found, err := r.RecentUserRejection(ctx, c.UserID, c.Fingerprint,
		g.Policy.RejectionCooldownDays)
	if err != nil {
		return "", "", "", fmt.Errorf("domain: recent user rejection: %w", err)
	}
	if !found {
		return "", "", "", nil
	}
	why := fmt.Sprintf("用户曾在 %d 天冷却窗内否定过同一指纹（%s），不得直接再生成（03 §12）",
		g.Policy.RejectionCooldownDays, id)
	if dup.Action == ActionReactivate {
		why = "指纹命中既有行，但该指纹在冷却窗内被用户否定过：" + why
	}
	return id, ReasonSuppressedByUserHistory, why, nil
}

// 第 8 关重复。
//   - pattern：指纹命中既有行（含 rejected）⇒ 刷新既有行，不插第二条。
//     这里必须覆盖 rejected：N2 给 (user_id, fingerprint) 建了偏唯一索引，
//     再插一条会直接撞 23505；拒绝签名冷却抑制是 N4 的第 9 关，不在此半做。
//   - fact：同 (subject_key, predicate, object_value) 已有活行 ⇒ 拒 duplicate。
//   - insight：V3 没给 insight 的重复键（insights 表也无指纹列），不自造；
//     该关对 insight 是显式空转，归 N4 的证据集去重。
func (g Gate) checkDuplicate(ctx context.Context, c *Candidate, r Reader) (Outcome, error) {
	if c.Kind == KindPattern {
		id, found, err := r.FindPatternByFingerprint(ctx, c.UserID, c.Fingerprint)
		if err != nil {
			return Outcome{}, fmt.Errorf("domain: find pattern: %w", err)
		}
		if found {
			return Outcome{Action: ActionReactivate, DuplicateOfID: id,
				Detail: "指纹命中既有 pattern，刷新 last_supported_at"}, nil
		}
	}
	if c.Kind == KindFact {
		id, found, err := r.FindLiveFactKey(ctx, c.UserID, c.SubjectKey, c.Predicate, c.ObjectValue)
		if err != nil {
			return Outcome{}, fmt.Errorf("domain: find fact: %w", err)
		}
		if found {
			return g.reject(ReasonDuplicate,
				fmt.Sprintf("同 key 的活事实已存在（%s），不再插第二条", id)), nil
		}
	}
	return Outcome{Action: ActionInsert}, nil
}

func contains(set []string, value string) bool {
	for _, item := range set {
		if item == value {
			return true
		}
	}
	return false
}

func containsAny(text string, terms []string) bool {
	for _, term := range terms {
		if strings.Contains(text, strings.ToLower(term)) {
			return true
		}
	}
	return false
}

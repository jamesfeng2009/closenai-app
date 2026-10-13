package domain

import (
	"os"
	"strconv"
	"strings"
)

// RejectReason 是机器可读的拒绝码，落 reject_reason VARCHAR(64)。
//
// 出处：本项目自定（G-1，closenai-prd/V3方案裁决与分层-2026-10-12.md 七-2）。
// 取证：V3 全 21 篇文档没有任何一处定义 reason 词表，而 tasks N3 要求
// 「reject 必带机器可读 reason 码」，所以词表在这里定稿并被 arch/gate 测试锁住；
// 改名等于改契约，须同步 N9 读路径与 O 侧评测。
type RejectReason string

const (
	ReasonSchemaInvalid         RejectReason = "schema_invalid"
	ReasonEvidenceMissing       RejectReason = "evidence_missing"
	ReasonEvidenceNotFound      RejectReason = "evidence_not_found"
	ReasonEvidenceNotOwned      RejectReason = "evidence_not_owned"
	ReasonEvidenceBelowFloor    RejectReason = "evidence_below_floor"
	ReasonConfidenceBelowThresh RejectReason = "confidence_below_threshold"
	ReasonClaimTypeRestricted   RejectReason = "claim_type_restricted"
	ReasonTemporalOverreach     RejectReason = "temporal_overreach"
	ReasonSafetyBlocked         RejectReason = "safety_blocked"
	ReasonSafetyUnavailable     RejectReason = "safety_unavailable"
	ReasonDuplicate             RejectReason = "duplicate"
	// 以下三码由 N4 加入（V3 03 §14 九关里的 Language 与 User History，
	// 加 §7:186-187 的冲突证据）：出处 closenai-prd/V3方案裁决与分层-2026-10-12.md 七-2.1。
	// evidence_conflict 之所以不复用 evidence_below_floor：冲突是「另一侧同样强」，
	// 低分是「这条本身弱」，O 侧评测与 N9 读路径要给用户的解释完全不同。
	ReasonLanguageOverreach       RejectReason = "language_overreach"
	ReasonSuppressedByUserHistory RejectReason = "suppressed_by_user_history"
	ReasonEvidenceConflict        RejectReason = "evidence_conflict"
	// 以下九码由 N5 加入（V3 04 §5 硬门 + §11 交付前十门 + §12 新鲜度）。
	// 出处：closenai-prd/V3方案裁决与分层-2026-10-12.md 七-4 P-9。
	// 与前面十四码同表不同义：这里的落点是 proactives.block_reason/reject_reason 两列，
	// 语义分工是「block＝此刻不能发（频次/静默时段/偏好/重复/新鲜度/措辞）」、
	// 「reject＝本身没资格（trigger/层数/置信/证据/安全）」。
	ReasonTriggerUnknown    RejectReason = "trigger_unknown"
	ReasonLayerBelowMinimum RejectReason = "layer_below_minimum"
	ReasonPreferenceOff     RejectReason = "preference_off"
	ReasonFrequencyCap      RejectReason = "frequency_cap"
	ReasonQuietHours        RejectReason = "quiet_hours"
	ReasonDuplicateMessage  RejectReason = "duplicate_message"
	ReasonStaleTrigger      RejectReason = "stale_trigger"
	ReasonMessageQuality    RejectReason = "message_quality"
	ReasonFatigueStopped    RejectReason = "fatigue_stopped"
	// 以下一码由 N6 加入（01 §22 不变量 9「源证据被撤回 ⇒ 按 policy 让依赖它的
	// 派生 claim 失效」）。不复用 evidence_below_floor：那条是「本身分数低」，
	// 这条是「支撑它的源被撤了」，前者可重算救回、后者按 O-6 只能失效。
	ReasonEvidenceWithdrawn RejectReason = "evidence_withdrawn"
)

// RejectReasons 是合法集合，Gate/Service 之外任何人写 reject_reason 都要过这张表。
var RejectReasons = []RejectReason{
	ReasonSchemaInvalid,
	ReasonEvidenceMissing,
	ReasonEvidenceNotFound,
	ReasonEvidenceNotOwned,
	ReasonEvidenceBelowFloor,
	ReasonConfidenceBelowThresh,
	ReasonClaimTypeRestricted,
	ReasonTemporalOverreach,
	ReasonSafetyBlocked,
	ReasonSafetyUnavailable,
	ReasonDuplicate,
	ReasonLanguageOverreach,
	ReasonSuppressedByUserHistory,
	ReasonEvidenceConflict,
	// N5 的九码（P-9，14→23）。
	ReasonTriggerUnknown,
	ReasonLayerBelowMinimum,
	ReasonPreferenceOff,
	ReasonFrequencyCap,
	ReasonQuietHours,
	ReasonDuplicateMessage,
	ReasonStaleTrigger,
	ReasonMessageQuality,
	ReasonFatigueStopped,
	// N6 的一码（O-6 源失效传播，23→24）。
	ReasonEvidenceWithdrawn,
}

// ProactiveNegativeFeedback 是「用户对主动触达说了不要」的三类可核实动作。
// 出处：V3 04 §9 的信号清单（dismissals / mute actions / negative feedback）落到
// N2 已有的 user_feedback 表——02:157-163 的六值词表里只有这三值是负面。
//
// partially_confirm **不算负面**：它是「部分同意」，把它计成负反馈会让一次
// 部分认可变成降频理由，方向与 04 §9 相反。
var ProactiveNegativeFeedback = []string{"reject", "outdated", "forget_from_model"}

// LabelTerms 是 Language 关（03 §14 第 7 关）的标签/诊断措辞禁令，取自
// 《沟通证据与措辞规范-2026-09-28》§1.2/§1.3 与 V3 03 §8（attachment_style /
// personality_type / diagnosis / trauma_type 四类不得成为 pattern_type）。
//
// 词表刻意保守（只拦「把观察说成结论/诊断/人格标签」的确切形态），因为这一关
// 拦错就等于把合法观察一起毙掉；03 §15 的因果措辞**已在 claim_type 关判定**
// （G-3 校验序），这里不建第二套规则（tasks N4:490）。
var LabelTerms = []string{
	"抑郁症", "焦虑症", "双相", "躁郁", "创伤后应激", "PTSD",
	"依恋类型", "依恋风格", "人格障碍", "人格类型",
	"诊断", "确诊", "心理疾病", "精神疾病",
	"clinical diagnosis", "personality disorder", "attachment style",
	"you are diagnosed", "you have a disorder",
}

// HedgeTerms：敏感域（危机/医疗信号）的候选必须带限定语，不得把推断说成定论。
// 出处：规范 §1.3「降低确定性表达」+ 03 §14 敏感域「special policy」。
// N4 只在这一条受限场景里要求限定语，不对所有 insight 强加——V3 没有给
// 「所有观察都必须 hedging」的依据，扩大成通用规则会误杀精确观察句。
var HedgeTerms = []string{
	"可能", "似乎", "看起来", "倾向", "也许", "我注意到", "过去", "最近",
	"may", "might", "seems", "appears", "noticed", "tends", "over the last",
}

// AllowedClaimTypes / RestrictedClaimTypes 逐字取 V3 03 §14（375-394 行）。
// restricted 是「默认受限」，不是永久禁止：解禁需要显式 policy，N3 一律 reject。
var (
	AllowedClaimTypes = []string{
		"observation", "comparison", "trend", "recurrence", "change",
		"goal_progress", "behavioral_sequence",
	}
	RestrictedClaimTypes = []string{
		"cause", "diagnosis", "personality_label", "future_prediction",
		"relationship_judgment", "medical_conclusion",
	}
	// ForbiddenPatternTypes 取 V3 03 §10（209-213 行）：类型标签/诊断不得成为纵向模式。
	ForbiddenPatternTypes = []string{
		"attachment_style", "personality_type", "diagnosis", "trauma_type",
	}
)

// Policy 是 Gate 的数值门限。D-4/G-5 已拍：V3 的数字只作交叉核对，正式值由 N4
// 用现有用户数据回测标定，所以这里**不硬编码**——默认值可被 env 覆盖，标定后
// 只换部署环境变量，代码零改动。
//
// 0 表示「该关不设限」。不是偷懒：V3 对 fact/pattern 的准入没有置信下限，
// 03:272-277 给的是**生命周期分档**（<0.45 仍是 candidate），不是拒绝条件；
// 把它们当拒绝线是我造规则。
type Policy struct {
	InsightConfidenceMin      float64
	InsightSensitiveConfMin   float64
	FactConfidenceMin         float64
	PatternConfidenceMin      float64
	EvidenceStrengthFloorForD float64
	MaxSubjectKeyLen          int
	MaxPredicateLen           int
	MaxEnumLen                int
	MaxFingerprintLen         int
	MaxScopeLen               int
	MaxEvidencePerCandidate   int
	// Evidence 是 N4 算分的数值面（五因子表 + A/B/C/D 分档 + §7 晋升门）。
	Evidence EvidencePolicy
	// Proactive 是 N5 的频次/疲劳/静默时段数值面（04 §5–§12 + PRD 触达数字）。
	Proactive ProactivePolicy
	// Outcome 是 N6 的传播数值面（04 §16–§18 + 01 §19；V3 一处数都没给）。
	Outcome OutcomePolicy
	// Delivery 是 N7 的投递数值面（18 §5/§6/§13 + 14 §7/§8；退避阶梯是 14 §8 原值，
	// 批大小/租约/次数上限/保留期都是待标定的本地默认值）。
	Delivery DeliveryPolicy
	// Read 是 N9 权威读路径的裁剪面（裁决七-7 N9-11：上限只裁列表，不裁内容）。
	Read ReadPolicy
	// RejectionCooldownDays：用户拒绝过的 pattern 指纹在此窗口内不得重新生成
	// （03 §12:335-338「拒绝同时产生 suppression signature/cooldown」）。
	// V3 没给天数，属 G-5 口径的待标定默认值。
	RejectionCooldownDays int
}

// DefaultPolicy 的取值口径（逐条标出处）：
//   - 0.70 / 0.85：V3 03 §14「Normal Insight ≥0.70」「Sensitive ≥0.85」，交叉核对值。
//   - 0.45：V3 03 §5 的 D 级下界（D < 0.45 不得支持用户可见 claim，03:158-159）。
//   - 128 / 64 / 160：N2 DDL 的列宽，超长会被数据库拒绝，先在这里判成 schema_invalid，
//     错误才可解释（01 §25 LLM Failure Containment 要求校验畸形产出，而不是让它撞库）。
func DefaultPolicy() Policy {
	return Policy{
		InsightConfidenceMin:      envFloat("CLOSEN_INSIGHT_MIN_CONFIDENCE", 0.70),
		InsightSensitiveConfMin:   envFloat("CLOSEN_INSIGHT_SENSITIVE_MIN_CONFIDENCE", 0.85),
		FactConfidenceMin:         envFloat("CLOSEN_FACT_MIN_CONFIDENCE", 0),
		PatternConfidenceMin:      envFloat("CLOSEN_PATTERN_MIN_CONFIDENCE", 0),
		EvidenceStrengthFloorForD: envFloat("CLOSEN_EVIDENCE_D_FLOOR", 0.45),
		MaxSubjectKeyLen:          128,
		MaxPredicateLen:           128,
		MaxEnumLen:                64,
		MaxFingerprintLen:         160,
		MaxScopeLen:               64,
		MaxEvidencePerCandidate:   envInt("CLOSEN_MAX_EVIDENCE_PER_CANDIDATE", 64),
		Evidence:                  DefaultEvidencePolicy(),
		Proactive:                 DefaultProactivePolicy(),
		Outcome:                   DefaultOutcomePolicy(),
		Delivery:                  DefaultDeliveryPolicy(),
		Read:                      DefaultReadPolicy(),
		RejectionCooldownDays:     envInt("CLOSEN_REJECTION_COOLDOWN_DAYS", 30),
	}
}

// DefaultEvidencePolicy 的出处与裁定（N4）：
//   - 八类来源可靠度、四档 explicitness、五档衰减：逐字取 V3 03 §5 的三张表
//     （03:112-121、03:128-133、03:140-144），按 D-4/G-5 只作**交叉核对值**，
//     正式值由标定脚本回测后换 env。
//   - specificity 三档、repetition 曲线、A/B/C/D 分档：V3 点了因子名却没给数值
//     （§5 只有三张表，§6 只给分档区间），数值属本项目自定；分档区间 0.85/0.65/0.45
//     是 §6:152-155 的原值。
//   - ScoreMode 默认 geomean 而**不是** §5 的字面连乘：连乘会把 03 §5 表里最正常的
//     一档（0.85×1.00×1.00×单次×30 天内）压到 0.51=C 级，与 §6「C 只能探索」叠加后
//     等于把正常证据一律判为不可用——tasks N4:487 点名的这条缺陷在代码里必须拍板，
//     口径见 evidence_score.go 的 ScoreEvidence 注释。`CLOSEN_EVIDENCE_SCORE_MODE=product`
//     可切回字面口径做回测。
//   - §7 晋升门（≥2 条独立观测 + combined ≥0.75）是 V3 03:175-181 原值。
func DefaultEvidencePolicy() EvidencePolicy {
	decay := envFloats("CLOSEN_EVIDENCE_DECAY", []float64{1.00, 0.85, 0.65, 0.40, 0.25})
	return EvidencePolicy{
		ScoreMode: envString("CLOSEN_EVIDENCE_SCORE_MODE", scoreModeGeomean),

		ReliabilityStatement:  envFloat("CLOSEN_EVIDENCE_REL_STATEMENT", 1.00),
		ReliabilityCorrection: envFloat("CLOSEN_EVIDENCE_REL_CORRECTION", 1.00),
		ReliabilitySelection:  envFloat("CLOSEN_EVIDENCE_REL_SELECTION", 0.95),
		ReliabilityBehavior:   envFloat("CLOSEN_EVIDENCE_REL_BEHAVIOR", 0.90),
		ReliabilityEmotion:    envFloat("CLOSEN_EVIDENCE_REL_EMOTION", 0.90),
		ReliabilityExtraction: envFloat("CLOSEN_EVIDENCE_REL_EXTRACTION", 0.85),
		ReliabilityInference:  envFloat("CLOSEN_EVIDENCE_REL_INFERENCE", 0.55),
		ReliabilityFramework:  envFloat("CLOSEN_EVIDENCE_REL_FRAMEWORK", 0.20),

		ExplicitnessExplicit:      envFloat("CLOSEN_EVIDENCE_EXPLICIT", 1.00),
		ExplicitnessStronglyImpl:  envFloat("CLOSEN_EVIDENCE_STRONGLY_IMPLIED", 0.75),
		ExplicitnessWeaklyImplied: envFloat("CLOSEN_EVIDENCE_WEAKLY_IMPLIED", 0.45),
		ExplicitnessSpeculative:   envFloat("CLOSEN_EVIDENCE_SPECULATIVE", 0.10),

		SpecificitySpecific:   envFloat("CLOSEN_EVIDENCE_SPECIFIC", 1.00),
		SpecificityContextual: envFloat("CLOSEN_EVIDENCE_CONTEXTUAL", 0.70),
		SpecificityGeneric:    envFloat("CLOSEN_EVIDENCE_GENERIC", 0.40),

		RepetitionBase: envFloat("CLOSEN_EVIDENCE_REP_BASE", 0.60),
		RepetitionStep: envFloat("CLOSEN_EVIDENCE_REP_STEP", 0.15),

		Decay30:  decay[0],
		Decay90:  decay[1],
		Decay180: decay[2],
		Decay365: decay[3],
		DecayOld: decay[4],

		ClassAFloor: envFloat("CLOSEN_EVIDENCE_CLASS_A", 0.85),
		ClassBFloor: envFloat("CLOSEN_EVIDENCE_CLASS_B", 0.65),
		ClassCFloor: envFloat("CLOSEN_EVIDENCE_CLASS_C", 0.45),

		FactMinIndependent: envInt("CLOSEN_FACT_MIN_INDEPENDENT", 2),
		FactCombinedMin:    envFloat("CLOSEN_FACT_COMBINED_MIN", 0.75),

		InsightMinIndependentForDerived: envInt("CLOSEN_INSIGHT_MIN_INDEPENDENT_EVIDENCE", 2),

		RepetitionWindowDays: envInt("CLOSEN_EVIDENCE_REPETITION_WINDOW_DAYS", 1),

		InsightStaleAfterDays: envInt("CLOSEN_INSIGHT_STALE_AFTER_DAYS", 90),
	}
}

func envFloat(name string, fallback float64) float64 {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return fallback
	}
	value, err := strconv.ParseFloat(raw, 64)
	if err != nil {
		return fallback
	}
	return value
}

func envInt(name string, fallback int) int {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return fallback
	}
	value, err := strconv.Atoi(raw)
	if err != nil {
		return fallback
	}
	return value
}

func envString(name, fallback string) string {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return fallback
	}
	return raw
}

// envFloats 读逗号分隔的浮点表（衰减曲线用）。长度不符即回退整表——
// 标定脚本要求「一条 env 换一整条曲线」，逐项漏配会把曲线切成半新半旧，
// 那种混合体没法回测复现。
func envFloats(name string, fallback []float64) []float64 {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return fallback
	}
	parts := strings.Split(raw, ",")
	if len(parts) != len(fallback) {
		return fallback
	}
	values := make([]float64, len(parts))
	for i, part := range parts {
		value, err := strconv.ParseFloat(strings.TrimSpace(part), 64)
		if err != nil {
			return fallback
		}
		values[i] = value
	}
	return values
}

// envInts 读逗号分隔的整数表（N7 的退避阶梯用）。
//
// 与 envFloats 不同，这里**不要求等长**：阶梯的长度本身是部署决定的一部分
// （14 §8 画了三级，只留两级是合法收敛），而衰减曲线按档位索引、必须整条替换。
// 任一段解析失败或出现非正数都回退整表——半新半旧的阶梯会让退避节奏不可复现。
func envInts(name string, fallback []int) []int {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return fallback
	}
	parts := strings.Split(raw, ",")
	values := make([]int, 0, len(parts))
	for _, part := range parts {
		value, err := strconv.Atoi(strings.TrimSpace(part))
		if err != nil || value <= 0 {
			return fallback
		}
		values = append(values, value)
	}
	if len(values) == 0 {
		return fallback
	}
	return values
}

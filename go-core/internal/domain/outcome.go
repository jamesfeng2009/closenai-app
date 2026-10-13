// N6 的词表、数值面与生命周期行为（V3 04 §15–§18、01 §13–§20、02 §3 枚举）。
//
// 四套词表各有唯一事实源，裁定见 closenai-prd/V3方案裁决与分层-2026-10-12.md 七-5：
//   - outcome_type   ＝ 04:288-297 的九类（02 §22 的 DDL 根本没给这一列，N2 补的）
//   - outcomes.status ＝ 02:176-180 三值 + candidate（偏离理由见 O-1 末段）
//   - outcomes.result＝ 02:182-189 六值
//   - outcomes.origin＝ 04:279-285 的三条权威来源 + llm_inference
//   - feedback_type  ＝ 02:156-163 六值（**不是** 04:259-268 的六个响应态，映射见 O-2）
package domain

import (
	"fmt"
	"strings"
	"time"
)

// OutcomeType 是 04 §15 的九类，逐字 snake_case。
type OutcomeType string

const (
	OutcomeGoalProgress        OutcomeType = "goal_progress"
	OutcomeGoalCompleted       OutcomeType = "goal_completed"
	OutcomeCommitmentCompleted OutcomeType = "commitment_completed"
	OutcomeBehaviorChange      OutcomeType = "behavior_change"
	OutcomeUserReportedChange  OutcomeType = "user_reported_change"
	OutcomeProactiveHelpful    OutcomeType = "proactive_helpful"
	OutcomeProactiveNotHelpful OutcomeType = "proactive_not_helpful"
	OutcomePatternConfirmed    OutcomeType = "pattern_confirmed"
	OutcomePatternDisconfirmed OutcomeType = "pattern_disconfirmed"
)

var OutcomeTypes = []OutcomeType{
	OutcomeGoalProgress, OutcomeGoalCompleted, OutcomeCommitmentCompleted,
	OutcomeBehaviorChange, OutcomeUserReportedChange,
	OutcomeProactiveHelpful, OutcomeProactiveNotHelpful,
	OutcomePatternConfirmed, OutcomePatternDisconfirmed,
}

func (t OutcomeType) Valid() bool { return containsOne(OutcomeTypes, t) }

func (s OutcomeStatus) Valid() bool { return containsOne(OutcomeStatuses, s) }

// OutcomeStatus 是 02:176-180 的三值，外加一个 candidate。
//
// 加 candidate 是必要的偏离：04:285 逐字「LLM inference is a candidate only」，
// 而 02 给的三值里没有能表达「登记了但没采信」的那一个——recorded 的字面就是
// 「已记录为事实」。不加这一值，「是否权威」就只能靠调用方每次都记得再看 origin 列，
// 那不是一个可执行的门。（对照 N3 的先例：insight 被拒时我用现成的 draft 而没有造
// rejected，因为 draft 的语义真能覆盖「未发布」；这里覆盖不了。）
type OutcomeStatus string

const (
	OutcomeStatusCandidate   OutcomeStatus = "candidate"
	OutcomeStatusRecorded    OutcomeStatus = "recorded"
	OutcomeStatusCorrected   OutcomeStatus = "corrected"
	OutcomeStatusInvalidated OutcomeStatus = "invalidated"
)

var OutcomeStatuses = []OutcomeStatus{
	OutcomeStatusCandidate, OutcomeStatusRecorded,
	OutcomeStatusCorrected, OutcomeStatusInvalidated,
}

// OutcomeResult 是 02:182-189 的六值。
type OutcomeResult string

const (
	OutcomeResultBetter      OutcomeResult = "better"
	OutcomeResultSame        OutcomeResult = "same"
	OutcomeResultWorse       OutcomeResult = "worse"
	OutcomeResultMixed       OutcomeResult = "mixed"
	OutcomeResultNotRelevant OutcomeResult = "not_relevant"
	OutcomeResultUnknown     OutcomeResult = "unknown"
)

var OutcomeResults = []OutcomeResult{
	OutcomeResultBetter, OutcomeResultSame, OutcomeResultWorse,
	OutcomeResultMixed, OutcomeResultNotRelevant, OutcomeResultUnknown,
}

func (r OutcomeResult) Valid() bool { return containsOne(OutcomeResults, r) }

// OutcomeOrigin 是 04:279-285 的来源四值。前三条是**权威来源**，第四条只允许产
// candidate——这一条由 OutcomeStatusForOrigin 表达，不靠文档约定。
//
// 词表里**没有 silence**：不变量 5「No Outcome inferred solely from silence」
// 的数据面形式就是「没有一种 origin 叫沉默」，加上 O-2 的「ignored 不写行」。
type OutcomeOrigin string

const (
	OriginUserStatement    OutcomeOrigin = "user_statement"
	OriginActionCompletion OutcomeOrigin = "action_completion"
	OriginProductEvent     OutcomeOrigin = "product_event"
	OriginLLMInference     OutcomeOrigin = "llm_inference"
)

var AuthoritativeOutcomeOrigins = []OutcomeOrigin{
	OriginUserStatement, OriginActionCompletion, OriginProductEvent,
}

var OutcomeOrigins = append(append([]OutcomeOrigin{}, AuthoritativeOutcomeOrigins...),
	OriginLLMInference)

func (o OutcomeOrigin) Valid() bool { return containsOne(OutcomeOrigins, o) }

// OutcomeStatusForOrigin 把 04:285 的那句「LLM inference is a candidate only」
// 变成一个函数：非权威来源**不可能**产出 recorded。
func OutcomeStatusForOrigin(o OutcomeOrigin) OutcomeStatus {
	for _, a := range AuthoritativeOutcomeOrigins {
		if a == o {
			return OutcomeStatusRecorded
		}
	}
	return OutcomeStatusCandidate
}

// FeedbackType 是 02:156-163 的六值，落 user_feedback.feedback_type VARCHAR(32)。
//
// 与 04 §14 的六个**响应态**的关系（O-2）：answered 就是 proactives.status='responded'，
// ignored 不写任何行，confirmed/corrected/rejected/dismissed 用这张表的
// confirm/correct/reject/forget_from_model 表示，原词留在 metadata_json.response_state。
type FeedbackType string

const (
	FeedbackConfirm          FeedbackType = "confirm"
	FeedbackReject           FeedbackType = "reject"
	FeedbackPartiallyConfirm FeedbackType = "partially_confirm"
	FeedbackCorrect          FeedbackType = "correct"
	FeedbackOutdated         FeedbackType = "outdated"
	FeedbackForgetFromModel  FeedbackType = "forget_from_model"
)

var FeedbackTypes = []FeedbackType{
	FeedbackConfirm, FeedbackReject, FeedbackPartiallyConfirm,
	FeedbackCorrect, FeedbackOutdated, FeedbackForgetFromModel,
}

func (f FeedbackType) Valid() bool { return containsOne(FeedbackTypes, f) }

// OutcomeLinkTargetTypes 逐字取 02:817-821（outcome_links 的建议目标五类）。
// 02:824 那句「goal 与 commitment 必须落到应用真正使用的那张 canonical 表，
// 不要维护重复的 goal/commitment 状态」在这里是**必须**遵守而不是建议：
// 本批的目标行就是 personal_goals / personal_commitments（见 canonical.sql 的
// CompleteGoal/CompleteCommitment），没有第二套目标状态。
var OutcomeLinkTargetTypes = []RefType{
	RefGoal, RefCommitment, RefPattern, RefInsight, RefProactive,
}

// FeedbackTargetTypes 是 user_feedback 允许的七类目标（O-3：对 02:690-695 三值的
// 有意偏离，理由与数据面复用 ListRefStates 的七表 UNION 都写在那一节）。
var FeedbackTargetTypes = []RefType{
	RefFact, RefPattern, RefInsight, RefGoal, RefCommitment, RefOutcome, RefProactive,
}

// ── pattern 生命周期（01 §5.3:280-330，八态）────────────────────────────────
//
// 07 §3 的硬要求是「生命周期转移是领域行为，不是随便 UPDATE 仓库」，所以这里给的是
// **转移函数**而不是状态字符串：非法转移返回 ok=false，调用方不得写库。

type PatternStatus string

const (
	PatternCandidate PatternStatus = "candidate"
	PatternObserved  PatternStatus = "observed"
	PatternSupported PatternStatus = "supported"
	PatternConfirmed PatternStatus = "confirmed"
	PatternActive    PatternStatus = "active"
	PatternWeakened  PatternStatus = "weakened"
	PatternExpired   PatternStatus = "expired"
	PatternRejected  PatternStatus = "rejected"
)

var PatternStatuses = []PatternStatus{
	PatternCandidate, PatternObserved, PatternSupported, PatternConfirmed,
	PatternActive, PatternWeakened, PatternExpired, PatternRejected,
}

// live：还能被生命周期转移改写的状态。expired/rejected 是终局，用户后来的表态
// 只留痕（01 §13 的 correction 链不覆盖已落定的历史）。
func (s PatternStatus) live() bool {
	return s != PatternExpired && s != PatternRejected
}

func (s PatternStatus) Valid() bool { return containsOne(PatternStatuses, s) }

// PatternAction 是领域层认可的转移动作，不是 SQL 动词。
type PatternAction string

const (
	PatternConfirm     PatternAction = "confirm"
	PatternReject      PatternAction = "reject"
	PatternWeaken      PatternAction = "weaken"
	PatternMarkExpired PatternAction = "mark_expired"
	PatternRevalidate  PatternAction = "revalidate"
)

// NextPatternStatus 是 01 §5.3 那张链表的代码形式。
//
// 已经处在目标态或更前面的状态 ⇒ ok=false（不动状态、不 ++版本，对齐 02 §32
// 「无变化不 ++」与「不是每次写入都是变更」）。revalidate 刻意不改状态：它只是
// 「要求重算」的留痕，结论由分数或用户表态决定。
func NextPatternStatus(current PatternStatus, action PatternAction) (PatternStatus, bool) {
	// 未知状态（库里混进来的脏行）不接受任何转移：把它当 candidate 复活，
	// 等于让一次脏数据获得「被用户确认」的资格。
	if !current.live() || !current.Valid() {
		return current, false
	}
	if action == PatternRevalidate {
		return current, false
	}
	switch action {
	case PatternConfirm:
		// 01 §15 Path A：用户确认。confirmed/active 已经在前，不重复写。
		if current == PatternConfirmed || current == PatternActive {
			return current, false
		}
		return PatternConfirmed, true
	case PatternReject:
		return PatternRejected, true
	case PatternWeaken:
		// 01:320「New evidence reduces confidence or recurrence」——从 candidate
		// 起就还没有「被削弱」可言，只有已经立起来的态才需要降。
		switch current {
		case PatternObserved, PatternSupported, PatternConfirmed, PatternActive:
			return PatternWeakened, true
		default:
			return current, false
		}
	case PatternMarkExpired:
		return PatternExpired, true
	default:
		return current, false
	}
}

// PatternActionForFeedback 把一次用户表态翻译成 pattern 的转移动作（O-9）。
//
// 存在的理由：07 §3 要求生命周期转移是**领域行为**。如果表态走一张「feedback→状态
// 字符串」的直写表、outcome 走转移函数，同一个 pattern 就有两套改写语义，终态
// （expired/rejected）能不能翻、weaken 从哪些态才生效就会分叉。所以这里一律先转成
// PatternAction，再交给 NextPatternStatus 判合法性。
//
// ok=false 的两种情况：partially_confirm（既不是确认也不是否决，policy.go:86 已定
// 它不算负面），以及未知的表态（校验层已挡，这里是防御性收口）。
func PatternActionForFeedback(f FeedbackType) (PatternAction, bool) {
	switch f {
	case FeedbackConfirm:
		return PatternConfirm, true
	case FeedbackReject, FeedbackForgetFromModel:
		// forget_from_model 逐字取 01:673：模型失效不等于数据删除，
		// 但对该模式的采信必须停在这里。
		return PatternReject, true
	case FeedbackCorrect:
		// 用户的纠正是「新的反面证据」，01:320 的 weakened 定义逐字对得上；
		// 不直接 rejected——描述不准的模式可能仍然成立（01:677 的分离原则）。
		return PatternWeaken, true
	case FeedbackOutdated:
		return PatternMarkExpired, true
	default:
		return "", false
	}
}

// OutcomePolicy 是 N6 的数值面。V3 在 outcome 传播这一节**一个数都没给**
// （04:305-313 只有流程箭头，01:814-837 只有 "confidence may decrease"），
// 所以下面每个值都按「最小干预」拍定并 env 可覆盖，口径见七-5 O-4/O-5。
type OutcomePolicy struct {
	// DisconfirmStep：pattern_disconfirmed / behavior_change 每次把 pattern 的
	// confidence 乘以 (1 − 这个值)。0.10 是「一次反例只削 10%」的量级——
	// 大到复盘看得出方向，小到一个反例不足以推翻长期模式（04:313）。
	DisconfirmStep float64
	// MinConfidence：削到底的下界。取 0 是因为 V3 没给下界，而 0 以下没有意义；
	// 真正的「不再算模式」由 NextPatternStatus 的 weakened/expired 表达，不靠截断分数。
	MinConfidence float64
	// AutoConfirmFromOutcome：outcome（而非用户亲手表态）能否把 pattern 自动升成
	// confirmed。**默认 false**——01:697 逐字「This policy must be conservative」，
	// 且 01 §15 的 Path B 要的是「重复的高质量观测」而不是单条 outcome。
	AutoConfirmFromOutcome bool
}

func DefaultOutcomePolicy() OutcomePolicy {
	return OutcomePolicy{
		DisconfirmStep:         envFloat("CLOSEN_OUTCOME_DISCONFIRM_STEP", 0.10),
		MinConfidence:          envFloat("CLOSEN_OUTCOME_MIN_CONFIDENCE", 0),
		AutoConfirmFromOutcome: envInt("CLOSEN_PATTERN_AUTO_CONFIRM", 0) == 1,
	}
}

// DisconfirmedConfidence 是 O-4 的算分形式：只降，且**不碰证据条数与回链**。
// 没有置信度（NULL）的行不动——「未知」不是 0，把未知削成更低的未知是假动作。
func (p OutcomePolicy) DisconfirmedConfidence(confidence float64, has bool) (float64, bool) {
	if !has {
		return 0, false
	}
	next := confidence * (1 - p.DisconfirmStep)
	if next < p.MinConfidence {
		next = p.MinConfidence
	}
	return next, true
}

// TargetRef 是一条 outcome/feedback 指向的权威行。
type TargetRef struct {
	Type RefType
	ID   string
}

func (t TargetRef) String() string { return string(t.Type) + ":" + t.ID }

// OutcomeProposal 是 Python 侧提交的 outcome 提案（不变量 7：提案不是结论）。
type OutcomeProposal struct {
	UserID          string
	PersonalModelID string
	Type            OutcomeType
	Result          OutcomeResult
	Origin          OutcomeOrigin
	Description     string
	OccurredAt      time.Time
	Targets         []TargetRef
	GenerationRunID string
}

// OutcomeRow 是落 outcomes 表所需的全部列（该表没有 personal_model_id 列，
// 见 DDL `0002_canonical_runtime.sql:148-161`，所以行结构里也不带）。
type OutcomeRow struct {
	ID              string
	UserID          string
	OutcomeType     string
	Status          string
	Result          string
	Origin          string
	Description     string
	OccurredAt      time.Time
	HasOccurredAt   bool
	ModelVersion    int64
	GenerationRunID string
}

// OutcomeReceipt 是一次记录的结果，含传播到的行。
type OutcomeReceipt struct {
	ID           string
	Status       string
	ModelVersion int64
	// Patterns 是 outcome→pattern 的实际改写（没动的行不在这里）。
	Patterns []PatternChange
	// Revalidated 是因 pattern 变化而被要求重算的 insight id。
	Revalidated []string
	// CancelledProactives 是因目标完成/失效而取消的排队触达 id。
	CancelledProactives []string
	CompletedTarget     string
	// Detail 解释「为什么什么都没传播」（candidate 的说明）。
	Detail string
}

// PatternChange 是一条 pattern 生命周期的变更结论。
type PatternChange struct {
	PatternID  string
	From       PatternStatus
	To         PatternStatus
	Confidence float64
	HasConf    bool
	Action     PatternAction
}

// FeedbackProposal 是一次用户表态。
type FeedbackProposal struct {
	UserID         string
	Type           FeedbackType
	TargetType     RefType
	TargetID       string
	CorrectionText string
	// ResponseState 是 04 §14 的原词（answered/confirmed/corrected/rejected/
	// ignored/dismissed），只进 metadata_json，不当作第二个枚举列。
	ResponseState string
	Metadata      map[string]string
}

// FeedbackRow 是落 user_feedback 的行。
type FeedbackRow struct {
	ID             string
	UserID         string
	FeedbackType   string
	TargetType     string
	TargetID       string
	CorrectionText string
	Metadata       string
}

// FeedbackReceipt 是一次表态的结果。
type FeedbackReceipt struct {
	ID           string
	TargetStatus string
	Changed      bool
	ModelVersion int64
	Detail       string
}

// PatternState 是生命周期改写需要的 pattern 现状（只读面）。
type PatternState struct {
	ID              string
	PersonalModelID string
	Status          PatternStatus
	Confidence      float64
	HasConfidence   bool
	EvidenceCount   int64
	ScoreDetail     string
	ModelVersion    int64
}

// PropagationResult 是一次源失效传播的结论（01 §22 不变量 9）。
type PropagationResult struct {
	EvidenceID string
	// Invalidated 是「仅依赖这条证据」而被改写的派生行。
	Invalidated []DerivedChange
	// Monitored 是有其他活支撑、因此只留痕不改写的派生行 id。
	Monitored []string
	// RevalidationRequested 是「仅依赖它」的 insight：状态改写交给 N4 的重算入口
	// （见 requestInsightRevalidation 的根因说明），本批只留请求事件。
	RevalidationRequested []string
	// ModelVersion 是传播后的版本（没有失效时等于传播前的版本）。
	ModelVersion int64
	Detail       string
}

// DerivedChange 是一条派生行的失效结论：To==From 表示「只留痕，状态没动」。
type DerivedChange struct {
	Kind        Kind
	ID          string
	From        string
	To          string
	Reason      string
	OnlySupport bool
}

// PatternLifecycleRow 是写回 patterns 的一条生命周期变更。
//
// EvidenceCount 由调用方**原样带回**当前值：04:313 的「单个反例不清除历史证据」
// 在数据面上的形式就是「这一列不由本批的写入口改写」。
type PatternLifecycleRow struct {
	ID            string
	UserID        string
	Status        string
	Confidence    float64
	HasConfidence bool
	EvidenceCount int64
	ScoreDetail   string
	ConfirmAt     bool
	RejectAt      bool
	ExpireAt      bool
	RejectReason  string
	ModelVersion  int64
}

// FactStatusRow 是一次 fact 的状态改写（02:104-110 五值词表）。
type FactStatusRow struct {
	ID           string
	UserID       string
	Status       string
	InvalidateAt bool
	RejectReason string
	ModelVersion int64
}

// DerivedSupport 是一条派生行对某条证据的依赖度（01 §22 不变量 9 的判定面）。
type DerivedSupport struct {
	Kind         Kind
	ID           string
	Status       string
	TotalSupport int64
	LiveSupport  int64
}

// OnlyDependent 判断「这条派生行是否仅依赖被撤回的那条证据」（O-6 的唯一门）：
// 活支撑归零 **且** 它确实曾依赖这条（total>0）。有其他支撑的行不算。
func (d DerivedSupport) OnlyDependent() bool {
	return d.TotalSupport > 0 && d.LiveSupport == 0
}

// QueuedProactive 是仍在队列里的触达行（完成目标后要按 packet 判断是否取消）。
type QueuedProactive struct {
	ID      string
	Status  string
	Trigger string
	Detail  string
}

// OutcomeTargetRequirement 是 O-4 里「这类 outcome 必须挂在可核实的对象上」那半句。
//
// 依据：04 §14 的响应态都是「对某条 proactive」的响应，04 §18 的示例是
// Goal/Action/Proactive/Insight；没有对象就无法核实，也无法算 04 §22 的三个度量。
// behavior_change / user_reported_change 允许不挂对象（用户全局自述的变化）。
func OutcomeTargetRequirement(t OutcomeType) (RefType, bool) {
	switch t {
	case OutcomeGoalProgress, OutcomeGoalCompleted:
		return RefGoal, true
	case OutcomeCommitmentCompleted:
		return RefCommitment, true
	case OutcomeProactiveHelpful, OutcomeProactiveNotHelpful:
		return RefProactive, true
	case OutcomePatternConfirmed, OutcomePatternDisconfirmed:
		return RefPattern, true
	default:
		return "", false
	}
}

// OutcomeDisconfirming 是「这类 outcome 是对既有结论的反例」，04:313 的传播入口。
//
// 只降不删：pattern_disconfirmed 是显式反例；behavior_change / user_reported_change
// 挂到 pattern 上时也是「事实变了」的信号（01 §16 的 update 而非 erase）。
// proactive_not_helpful 反的是**那条触达**，不是它背后的模式——用户说「这条提醒没
// 用」不等于「这个模式不存在」，所以不算模式反例。
func OutcomeDisconfirming(t OutcomeType) bool {
	switch t {
	case OutcomePatternDisconfirmed, OutcomeBehaviorChange, OutcomeUserReportedChange:
		return true
	default:
		return false
	}
}

// OutcomeCompletesTarget 是「这类 outcome 意味着目标/承诺真的完成了」。
//
// 02:824 要求落到应用真正使用的那张 canonical 表，所以这里只允许 goal/commitment
// 两类，且完成动作走既有 CompleteGoal/CompleteCommitment，不在 outcomes 表上再造
// 一份完成状态。
func OutcomeCompletesTarget(t OutcomeType) bool {
	return t == OutcomeGoalCompleted || t == OutcomeCommitmentCompleted
}

// checkOutcomeProposal 是畸形提案的第一道（01 §25 的方向：校验 LLM 产出，
// 而不是让它撞库）。返回 error 时调用方**不写任何行**。
func checkOutcomeProposal(p OutcomeProposal) error {
	if strings.TrimSpace(p.UserID) == "" {
		return fmt.Errorf("domain: outcome.user_id is required")
	}
	if !p.Type.Valid() {
		return fmt.Errorf("domain: 未知 outcome_type %q（04:288-297 只给九类）", p.Type)
	}
	if !p.Origin.Valid() {
		return fmt.Errorf("domain: 未知 outcome.origin %q（04:279-285 三条权威来源 + llm_inference）",
			p.Origin)
	}
	if p.Result == "" {
		// 缺省值在这里归一化，**不传播给调用方**：checkOutcomeProposal 收的是值拷贝，
		// 在它里面改 p.Result 只会让校验通过而落库仍是空串（result 列 NOT NULL）。
		// 归一化由 RecordOutcome 在提案上做。
		return fmt.Errorf("domain: outcome.result 为空时须由调用方归一化为 unknown（02:182-189 六值）")
	}
	if !p.Result.Valid() {
		return fmt.Errorf("domain: 未知 outcome.result %q（02:182-189 六值）", p.Result)
	}
	if need, ok := OutcomeTargetRequirement(p.Type); ok && !hasTarget(p.Targets, need) {
		return fmt.Errorf("domain: outcome_type %s 必须 link 至少一条 %s（04 §14/§18 的可核实对象）",
			p.Type, need)
	}
	// 不变量 5 的数据面：沉默不是来源，所以「proactive 没帮上忙」这件事必须来自
	// 用户的可核实动作，不能来自「用户没回」。llm_inference 也满足这条——它落 candidate。
	if p.Type == OutcomeProactiveNotHelpful && p.Origin == OriginProductEvent &&
		!hasAnyFeedbackSignal(p.Description) {
		return fmt.Errorf("domain: proactive_not_helpful 需要可核实的负反馈陈述，" +
			"沉默不构成 outcome（01:810、04:274、不变量 5）")
	}
	for _, t := range p.Targets {
		if t.ID == "" || !containsRefType(OutcomeLinkTargetTypes, t.Type) {
			return fmt.Errorf("domain: outcome_links 目标 %q:%q 不合法（02:817-821 五类）",
				t.Type, t.ID)
		}
	}
	return nil
}

// hasAnyFeedbackSignal：product_event 要能表示「用户按了不要」，就得在陈述里带
// 这个动作本身。这里只做**存在性**校验，不解析语义——语义归 N7/N8 的事件面。
func hasAnyFeedbackSignal(description string) bool {
	return strings.TrimSpace(description) != ""
}

// checkFeedbackProposal 校验一次用户表态。
func checkFeedbackProposal(f FeedbackProposal) error {
	if strings.TrimSpace(f.UserID) == "" {
		return fmt.Errorf("domain: feedback.user_id is required")
	}
	if !f.Type.Valid() {
		return fmt.Errorf("domain: 未知 feedback_type %q（02:156-163 六值）", f.Type)
	}
	if strings.TrimSpace(f.TargetID) == "" {
		return fmt.Errorf("domain: feedback 必须指向一行权威记录（02:697 不得做无校验的多态外键）")
	}
	if !containsRefType(FeedbackTargetTypes, f.TargetType) {
		return fmt.Errorf("domain: 未知 feedback target_type %q（七类见七-5 O-3）", f.TargetType)
	}
	return nil
}

// StatusForDerivedTarget 是表态对**派生目标**状态列的影响（O-9 矩阵的非 pattern 半边）。
//
// 每一格都取 02 词表里的原值，不新造状态；返回 ok=false 表示「这类目标不因这种
// 表态改状态」——留痕照写（不变量 6），只是不越权改写。
// pattern 不在这里：它的改写必须过 NextPatternStatus（见 PatternActionForFeedback）。
// goal/commitment 一格都不改：完成它们是**动作**的结果，由 RecordOutcome 走
// CompleteGoal/CompleteCommitment，不是「用户说了一句」的结果。
func StatusForDerivedTarget(target RefType, f FeedbackType) (string, bool) {
	if f == FeedbackPartiallyConfirm {
		// 「部分同意」既不是确认也不是否决（policy.go:86 已定它不算负面），
		// 拿它改状态会把一次部分认可变成降权理由。
		return "", false
	}
	switch target {
	case RefFact:
		switch f {
		case FeedbackConfirm:
			return "active", true
		case FeedbackReject, FeedbackForgetFromModel:
			// 02:108 的 invalidated 正是 01:677「model invalidation ≠ data deletion」。
			return "invalidated", true
		case FeedbackCorrect:
			return "invalidated", true
		case FeedbackOutdated:
			return "outdated", true
		}
	case RefInsight:
		switch f {
		case FeedbackConfirm:
			return "confirmed", true
		case FeedbackReject:
			return "retracted", true
		case FeedbackCorrect:
			return "corrected", true
		case FeedbackOutdated:
			return "expired", true
		case FeedbackForgetFromModel:
			return "dismissed", true
		}
	case RefOutcome:
		switch f {
		case FeedbackCorrect:
			return string(OutcomeStatusCorrected), true
		case FeedbackReject, FeedbackForgetFromModel, FeedbackOutdated:
			return string(OutcomeStatusInvalidated), true
		}
	}
	return "", false
}

func hasTargets(targets []TargetRef, types ...RefType) bool {
	for _, t := range targets {
		for _, want := range types {
			if t.Type == want {
				return true
			}
		}
	}
	return false
}

func hasTarget(targets []TargetRef, want RefType) bool { return hasTargets(targets, want) }

func targetsOfType(targets []TargetRef, want RefType) []string {
	out := []string{}
	for _, t := range targets {
		if t.Type == want {
			out = append(out, t.ID)
		}
	}
	return out
}

func containsOne[T comparable](list []T, v T) bool {
	for _, item := range list {
		if item == v {
			return true
		}
	}
	return false
}

func containsRefType(list []RefType, v RefType) bool { return containsOne(list, v) }

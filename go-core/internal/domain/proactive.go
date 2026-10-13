// Package domain 的 N5 面：Proactive（AI 主动触达）的资格门、频次与疲劳。
//
// 分层口径与 N3/N4 一致（不变量 7 + D-1/P5）：Python 只提案「要对用户说什么、
// 凭什么说」，Go 判「此刻能不能发」，PostgreSQL 记终局。本文件不认识 HTTP/MQ，
// 触发面归 N8/O2（口径见 closenai-prd/V3方案裁决与分层-2026-10-12.md 七-4 P-1）。
package domain

import "time"

// TriggerType 是 V3 04 §2（04:46-54）的七类 proactive，逐字 snake_case。
//
// 落库列是 proactives.trigger_type VARCHAR(64)（0002_canonical_runtime.sql:352）。
// 选 §2 而不是 §3 的 trigger source 作落库词表，理由见七-4 P-2：§6 的证据与置信
// 门槛表是以 §2 为键的，只有它能被确定性执行。
type TriggerType string

const (
	TriggerMemoryRecall       TriggerType = "memory_recall"
	TriggerPatternFollowup    TriggerType = "pattern_followup"
	TriggerGoalFollowup       TriggerType = "goal_followup"
	TriggerCommitmentFollowup TriggerType = "commitment_followup"
	TriggerReflectionPrompt   TriggerType = "reflection_prompt"
	TriggerOutcomeCheck       TriggerType = "outcome_check"
	TriggerMilestone          TriggerType = "milestone"
)

// TriggerTypes 是合法集合；不在表内的 trigger_type 一律 trigger_unknown。
var TriggerTypes = []TriggerType{
	TriggerMemoryRecall,
	TriggerPatternFollowup,
	TriggerGoalFollowup,
	TriggerCommitmentFollowup,
	TriggerReflectionPrompt,
	TriggerOutcomeCheck,
	TriggerMilestone,
}

// TriggerSource 是 V3 04 §3（04:62-70）的七个触发源。原文是英文短语且大小写/
// 连字符不统一（`new Pattern`、`user-requested follow-up`），这里拍成 snake_case
// 常量并序列化进 eligibility_detail，不加列、不动 DDL（七-4 P-2）。
//
// 04:60「Every proactive must have an explicit trigger」是硬门第一条：留空即拒。
type TriggerSource string

const (
	SourceNewPattern          TriggerSource = "new_pattern"
	SourcePatternRecurrence   TriggerSource = "pattern_recurrence"
	SourceGoalDue             TriggerSource = "goal_due"
	SourceCommitmentDue       TriggerSource = "commitment_due"
	SourcePreviousProactive   TriggerSource = "previous_proactive"
	SourceUserRequestedFollow TriggerSource = "user_requested_follow_up"
	SourcePendingOutcome      TriggerSource = "pending_outcome"
)

// TriggerSources 是合法集合。
var TriggerSources = []TriggerSource{
	SourceNewPattern,
	SourcePatternRecurrence,
	SourceGoalDue,
	SourceCommitmentDue,
	SourcePreviousProactive,
	SourceUserRequestedFollow,
	SourcePendingOutcome,
}

// ProactiveStatus 是 02:165-174 的八态。DDL 里 proactives.status 是 VARCHAR(24)
// 无 CHECK（N2 口径：canonical 表不用 PG enum），词表由这里守。
type ProactiveStatus string

const (
	ProactiveStatusCandidate ProactiveStatus = "candidate"
	ProactiveStatusEligible  ProactiveStatus = "eligible"
	ProactiveStatusBlocked   ProactiveStatus = "blocked"
	ProactiveStatusScheduled ProactiveStatus = "scheduled"
	ProactiveStatusSent      ProactiveStatus = "sent"
	ProactiveStatusResponded ProactiveStatus = "responded"
	ProactiveStatusExpired   ProactiveStatus = "expired"
	ProactiveStatusCancelled ProactiveStatus = "cancelled"
)

// ProactiveStatuses 是合法集合。
var ProactiveStatuses = []ProactiveStatus{
	ProactiveStatusCandidate, ProactiveStatusEligible, ProactiveStatusBlocked,
	ProactiveStatusScheduled,
	ProactiveStatusSent, ProactiveStatusResponded, ProactiveStatusExpired, ProactiveStatusCancelled,
}

// RefType 是候选引用的权威行类型（04 §10 的「pattern/fact/goal」+ 闭环需要的
// outcome 与上一封 proactive）。值与 ListRefStates 的 UNION 分支一一对应。
type RefType string

const (
	RefPattern    RefType = "pattern"
	RefGoal       RefType = "goal"
	RefCommitment RefType = "commitment"
	RefInsight    RefType = "insight"
	RefFact       RefType = "fact"
	RefOutcome    RefType = "outcome"
	RefProactive  RefType = "proactive"
)

// RefState 是一条引用的当前库态（Freshness 关的输入；04 §12 的取消条件全部
// 要能回查到权威行的状态才可执行）。
type RefState struct {
	RefType  RefType
	ID       string
	Status   string
	AnchorAt time.Time
	HasTime  bool
}

// UserPrefs 是 UserPreference 关的核验面。数据面是 legacy users 表（读 legacy
// 表在 Go 侧已有先例：internal/store/users.go:21-23「1:1 翻译，两侧看同一行」），
// 字段对应 backend/models/user.py:25-46。
//
// 这些值**不从提案取**：调用方自报「用户同意过」等于把 policy 交给非权威侧。
type UserPrefs struct {
	UserID        string
	Timezone      string
	PushEnabled   bool
	PushFrequency string // daily|important|off
	AIConsent     bool
	Tier          string
	QuotaUsed     int
	CreatedAt     time.Time
	Found         bool
}

// ProactiveSendRow 是频次/疲劳/查重关需要的历史行。
type ProactiveSendRow struct {
	ID             string
	Status         string
	TriggerType    string
	Message        string
	ScheduledAt    time.Time
	HasScheduledAt bool
	SentAt         time.Time
	HasSentAt      bool
	RespondedAt    time.Time
	HasResponded   bool
	// Detail 是那一封的 eligibility_detail 原文（packet 由领域层解析，store 不拆 JSON）。
	Detail string
}

// ProactiveRow 是写入口的落库行。
type ProactiveRow struct {
	ID                string
	UserID            string
	PersonalModelID   string
	Status            string
	TriggerType       string
	Message           string
	ScheduledAt       time.Time
	HasScheduledAt    bool
	ExpiresAt         time.Time
	HasExpiresAt      bool
	EligibilityScore  float64
	EligibilityDetail string
	BlockReason       string
	RejectReason      string
	GenerationModel   string
	GenerationVersion string
	GenerationRunID   string
	ModelVersion      int64
	JourneyEventID    string
	HasJourneyEventID bool
}

// ProactiveStatusRow 是一次状态改写。Reason 落哪一列由 Kind 决定（七-4 P-9：
// block＝此刻不能发，reject＝本身没资格）。
type ProactiveStatusRow struct {
	UserID       string
	ID           string
	Status       string
	BlockReason  string
	RejectReason string
	SentAt       time.Time
	HasSentAt    bool
	Detail       string
}

// ProactiveCandidate 是 Python 侧的主动触达提案。
//
// 04 §10 明确 LLM 拿到的是受限包（trigger/evidence/pattern/fact/goal/time context/
// user preference/safety policy/tone policy），返回 message/evidence refs/action type，
// 且「must not introduce unsupported facts」——最后这条在 Go 侧的可执行形式是
// 「引用必须全部可核实」：EvidenceIDs 与 RefIDs 的每一条都要在库里、属于该用户、
// 状态不构成 stale（evidence/claim 关 + Freshness 关）。
type ProactiveCandidate struct {
	UserID          string
	PersonalModelID string // 留空则由 Service 定位/创建该用户的模型行
	ID              string // 重跑同一次生成时传入，用于幂等；留空即新建

	TriggerType   TriggerType
	TriggerSource TriggerSource

	Message    string
	Confidence float64

	EvidenceIDs []string
	RefIDs      []string

	// AnchorAt 是「关联当下」那一层的时间锚点，必须来自权威行（goal 的 target_at、
	// commitment 的 due_at、里程碑对应的注册日/上一封 sent_at…），由 Go 从库里核，
	// 不采信提案自报——提案只能指出锚点在哪一行，时刻由那行说了算。
	AnchorRefType RefType
	AnchorRefID   string

	// DayNo 供 milestone/剧本类引用现网 journey 的 day_no（04 §3 的 Goal due 类比）。
	ScheduledAt    time.Time
	HasScheduledAt bool
	ExpiresAt      time.Time
	HasExpiresAt   bool

	JourneyEventID string

	GenerationRunID string
	GenerationModel string
	GenerationVer   string

	Safety *Safety // nil 视为判定缺失 → fail-closed（D-8）
}

// IsSensitive 沿用 N3 的敏感域口径（危机类别或医疗信号命中即算）。
func (c *ProactiveCandidate) IsSensitive() bool {
	if c.Safety == nil {
		return false
	}
	for _, category := range c.Safety.Categories {
		switch category {
		case "self_harm", "relationship_violence", "medical":
			return true
		}
	}
	return false
}

// Layer 是 PRD「为什么今天找你」四层公式里的一层。
//
// 出处：closenai-prd/zodiacsync-prd/closenai.html:637-638「每条 AI 主动消息必须
// 包含至少一个『为什么是今天』的理由。如果没有任何理由，就不发消息——宁可不发」，
// 以及紧随其后的四层表（①引用记忆 ②发现模式 ③关联当下 ④开放提问）与
// 「四层公式（至少满足 2 层才发送）」。
type Layer string

const (
	LayerMemoryRecall Layer = "memory_recall"
	LayerPatternFound Layer = "pattern_found"
	LayerContextNow   Layer = "context_now"
	LayerOpenQuestion Layer = "open_question"
)

// Layers 是按序落 eligibility_detail 的四层标识。
var Layers = []Layer{LayerMemoryRecall, LayerPatternFound, LayerContextNow, LayerOpenQuestion}

// Cadence 是疲劳阶梯的档位（七-4 P-6）。
type Cadence string

const (
	CadenceNormal    Cadence = "normal"
	CadenceShortCare Cadence = "short_care"
	CadenceWeekly    Cadence = "weekly"
	CadenceStopped   Cadence = "stopped"
)

// T4-02 的词表与生命周期行为：goal（目标）与 action（行动）。
//
// 词表出处是 PLAN §5 决策 6 与 §7，两格偏离要先说清：
//   - **行动**复用 `personal_commitments`（不新建 actions 表），四态
//     `active / completed / skipped / cancelled`；`in_progress` 本期不做——
//     列上装得下（status 是 varchar(32)，无 CHECK），但 Baseline 没有要求它，
//     加了就要有人定义「进行中」和「活跃」在用户屏幕上怎么区分；
//   - **目标**只有三态 `active / completed / cancelled`：`skipped` 是
//     「这一天的这一次没做」的行动语义，目标上没有这一格——T4-01 只给
//     `personal_commitments` 加了 `skipped_at`，`personal_goals` 连这一列都没有。
//     把 skip 开放给 goal 会让一次合法转移写不出列，那是 500 的形状。
//
// 状态机为什么在这里而不是 SQL 里：`UPDATE ... WHERE id=$1 AND user_id=$2
// AND status='active'` 会把同一套转移判据写第二遍，而 T4-01 的闸只认 domain 这一侧
// （canonical.sql 里 SkipCommitment/CancelCommitment 的注释同口径）。
// 这里放的是**唯一那张表**；store 只负责「跳到哪一列、哪个时间要留」。
package domain

import (
	"errors"
	"fmt"
	"strings"
	"time"
	"unicode/utf8"
)

// 两套状态词，逐字取库里已有的默认值（`status varchar(32) DEFAULT 'active'`）。
const (
	StatusActive    = "active"
	StatusCompleted = "completed"
	StatusCancelled = "cancelled"
	// StatusSkipped 只属于行动（见文件头：personal_goals 没有 skipped_at 列）。
	StatusSkipped = "skipped"
)

// GoalStatuses 与 ActionStatuses 是两面的合法值集合。**不相等**不是笔误。
var (
	GoalStatuses   = []string{StatusActive, StatusCompleted, StatusCancelled}
	ActionStatuses = []string{StatusActive, StatusCompleted, StatusSkipped, StatusCancelled}
)

// maxGoalTitle 是标题长度上限（工单 §怎么做.1 的 1–200）。
// 列上是 `text NOT NULL`，没有 CHECK：这一格约束放在 domain 是因为它的用户后果
// 在输入框，而放开不限长等于让一次粘贴立一颗永远读不完的目标。
const maxGoalTitle = 200

// LifecycleVerb 是「对一颗目标/行动做哪个动作」。
//
// 它是三枚动词而不是四个状态：调用方（HTTP 层）拿到的是 `to=completed` 这样的
// 目标态还是动词？取动词——因为「complete」同时决定了跳到哪一态**和**刷哪一列，
// 而让调用方直接给目标态会让 `status=skipped` 打到 goal 上（那一列根本不存在），
// 那种请求在词表上合法、在库上不合法，最难归因。
type LifecycleVerb string

const (
	VerbComplete LifecycleVerb = "complete"
	VerbSkip     LifecycleVerb = "skip"
	VerbCancel   LifecycleVerb = "cancel"
)

var lifecycleVerbs = []LifecycleVerb{VerbComplete, VerbSkip, VerbCancel}

func (v LifecycleVerb) Valid() bool { return containsOne(lifecycleVerbs, v) }

// StatusForVerb 把动词折成目标态。skip 只在行动侧被 NextActionStatus 接受。
func StatusForVerb(v LifecycleVerb) (string, bool) {
	switch v {
	case VerbComplete:
		return StatusCompleted, true
	case VerbSkip:
		return StatusSkipped, true
	case VerbCancel:
		return StatusCancelled, true
	}
	return "", false
}

// ErrGoalIllegalTransition 与 ErrActionIllegalTransition 是两张转移表各自的拒绝。
//
// 为什么分成两个错误而不是一句 `ErrIllegalTransition`：HTTP 层的文案要指出是哪一面
// （「目标没有跳过这一态」与「已完成的行动不能回退」是两句不同的话），
// 而错误码都是 409——分成两个类型不影响状态码，只影响可解释性。
var (
	ErrGoalIllegalTransition   = errors.New("domain: illegal goal transition")
	ErrActionIllegalTransition = errors.New("domain: illegal action transition")
	// ErrGoalNotActive 是「往一颗已结束的目标下挂新行动」。
	// 409 而不是 400：请求本身合形，是**这一颗**的当前状态不允许（工单 §验收）。
	ErrGoalNotActive = errors.New("domain: goal is not active")
	// ErrInvalidGoalProposal 与 ErrInvalidActionProposal 是入参不合形 ⇒ 400。
	ErrInvalidGoalProposal   = errors.New("domain: invalid goal proposal")
	ErrInvalidActionProposal = errors.New("domain: invalid action proposal")
)

// NextGoalStatus 是目标那张转移表：active → completed | cancelled。
// 返回 (目标态, 合法)。终态出发的任何转移一律非法——「回退」不是这里的动作，
// 重新立一颗目标是新建，不是把历史那颗改回 active（01:677「失效 ≠ 删除」同一立场：
// 已落定的历史不改写，新的意图用新的行表达）。
func NextGoalStatus(current string, verb LifecycleVerb) (string, bool) {
	if current != StatusActive {
		return "", false
	}
	switch verb {
	case VerbComplete:
		return StatusCompleted, true
	case VerbCancel:
		return StatusCancelled, true
	}
	// skip：personal_goals 没有 skipped_at（见文件头）。
	return "", false
}

// NextActionStatus 是行动那张转移表：active → completed | skipped | cancelled。
func NextActionStatus(current string, verb LifecycleVerb) (string, bool) {
	if current != StatusActive {
		return "", false
	}
	switch verb {
	case VerbComplete:
		return StatusCompleted, true
	case VerbSkip:
		return StatusSkipped, true
	case VerbCancel:
		return StatusCancelled, true
	}
	return "", false
}

// checkTitle 是两个 proposal 共用的那一格判据（1–200 rune）。
// 用 rune 而不是 byte：中文标题按 byte 数会把 67 个字判成超限。
func checkTitle(title string, err error) (string, error) {
	trimmed := strings.TrimSpace(title)
	if trimmed == "" {
		return "", fmt.Errorf("%w: title 不能为空", err)
	}
	if utf8.RuneCountInString(trimmed) > maxGoalTitle {
		return "", fmt.Errorf("%w: title 超过 %d 字", err, maxGoalTitle)
	}
	return trimmed, nil
}

// GoalProposal 是新建目标；TargetAt 可以为零值（＝不设截止）。
type GoalProposal struct {
	UserID      string
	Title       string
	Description string
	TargetAt    time.Time
	HasTargetAt bool
}

// ActionProposal 是新建行动。GoalID 空串＝不挂在任何目标下（库里写 NULL），
// 这一格是 T4-01 的测点：`goal_id` 可空且「空」必须真的落成 NULL 而不是空串。
type ActionProposal struct {
	UserID      string
	GoalID      string
	Title       string
	Description string
	DueAt       time.Time
	HasDueAt    bool
}

// GoalRow 是落 personal_goals 的列（表词汇：与 canonical.sql 的 InsertGoal 同形）。
type GoalRow struct {
	ID              string
	UserID          string
	PersonalModelID string
	Title           string
	Description     string
	Status          string
	StartedAt       time.Time
	TargetAt        time.Time
	HasTargetAt     bool
}

// CommitmentRow 是落 personal_commitments 的列。GoalID 空串 ⇒ NULL。
type CommitmentRow struct {
	ID              string
	UserID          string
	PersonalModelID string
	Title           string
	Description     string
	Status          string
	DueAt           time.Time
	HasDueAt        bool
	GoalID          string
}

// GoalRecord 是列表面的一行（读投影，比 GoalRow 多 completed_at 等库里的时间列）。
type GoalRecord struct {
	ID          string
	Title       string
	Description string
	Status      string
	StartedAt   time.Time
	TargetAt    time.Time
	CompletedAt time.Time
	CreatedAt   time.Time
	UpdatedAt   time.Time
}

// ActionRecord 是行动列表的一行。GoalID 空串＝这一颗没挂在目标下。
type ActionRecord struct {
	ID          string
	Title       string
	Description string
	Status      string
	DueAt       time.Time
	CompletedAt time.Time
	SkippedAt   time.Time
	GoalID      string
	CreatedAt   time.Time
	UpdatedAt   time.Time
}

// GoalListView 与 ActionListView 是两条读面的产物。
//
// `Truncated` 而不是游标：目标与行动是**人手立的东西**，一个用户量级在几十条，
// 而 insights 那一条面会随模型跑批无限增长，才需要 keyset 游标（T3-01）。
// 给这一面对象加游标等于造一个没有第二个调用方的机制。
type GoalListView struct {
	Items     []GoalRecord
	Truncated bool
}

type ActionListView struct {
	Items     []ActionRecord
	Truncated bool
}

// GoalReceipt 是一次目标写操作的结论。
type GoalReceipt struct {
	ID     string
	Status string
	// Changed=false 表示这次请求没改任何东西（重复点同一枚按钮的幂等面）。
	Changed bool
	// EventID 是留痕那条权威事件的 id；幂等返回时为空（没写事件也就没有 id）。
	EventID      string
	ModelVersion int64
	Detail       string
}

// ActionReceipt 是一次行动写操作的结论。GoalID 回带来，让调用方不必再查一次。
type ActionReceipt struct {
	ID           string
	Status       string
	GoalID       string
	Changed      bool
	EventID      string
	ModelVersion int64
	Detail       string
}

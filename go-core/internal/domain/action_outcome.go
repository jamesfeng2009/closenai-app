// T4-03：用户自述的「行动结果」入口（`POST /internal/v3/actions/{id}/outcomes` 的领域面）。
//
// ── 为什么这是一条**新入口**而不是让 handler 直接调 RecordOutcome ──────────────
// `RecordOutcome` 收的是 `OutcomeProposal`，那是给 N6 的内部调用方（跑批、consumer）用的
// 全形提案：origin 由调用方声明、targets 由调用方拼装、传播照单执行。把它直接接到
// 一条 HTTP 端点上有两件事不对：
//   1. 客户端就能自报 origin——`llm_inference` 与 `user_statement` 之间差的是
//      「这条算不算模型相信的事」（O-5 的 ++版本判据），这个决定不能由请求体给；
//   2. §怎么做.5 的同日幂等要在**同一个事务**里读「最近一条同型」再决定写不写，
//      而 RecordOutcome 自带 InTx（嵌套事务在真库上是 pgx 报错，在假桩里却全绿）。
// 所以本文件只做三件事：钉死 origin、钉死 targets、加那一格同日判定，
// 事务体仍然复用 `recordOutcomeWithin`——写路径只有一条。
//
// ── 词表改判（登记在结案行）──────────────────────────────────────────────────
// 工单 §怎么做.2 点名的 `OriginUserReported` 在 domain 的 origin 词表里**不存在**
// （真身是 outcome.go:90-95 的四值）。这里取 `OriginUserStatement`：它是三条权威来源
// 之一 ⇒ 落 recorded、++模型版本，正是「用户自己说这件事发生了」的语义。
// 新造一枚 origin 就是给词表加第二个事实源，而 O-5 的版本判据是按这一枚举分道的。
//
// 同理，§怎么做.1 的 result 四值（improved/no_change/worse/unclear）与 02:182-189
// 的六值（better/same/worse/mixed/not_relevant/unknown）不同源：这一面**收六值**，
// 「没变化=same」「变差=worse」「说不清=unknown」都在内（工单 §验收 要的四种 result
// 各一条，指的是「不许只留正向」，不是要再造一张四值表）。

package domain

import (
	"context"
	"fmt"
	"strings"
	"time"
)

// ActionOutcomeProposal 是一条「我这个行动结果如何」的用户自述。
type ActionOutcomeProposal struct {
	UserID   string
	ActionID string
	Type     OutcomeType
	Result   OutcomeResult
	// Note 是用户自己写的那句话，落到 outcomes.description（不是提案结论）。
	Note       string
	OccurredAt time.Time
	// OperationID 是客户端的防重复点击键。**本期不落库**：outcomes 表没有 metadata 列
	// （0002:148-161），同日幂等因此按 (user, target, outcome_type, UTC 日) 判，
	// 这一格只在回执里回显。已登记为缺陷（归后续：要么加列，要么按 outbox 的
	// 稳定 id 思路做幂等表）。
	OperationID string
}

// ActionOutcomeReceipt 是一次记录的回执。
type ActionOutcomeReceipt struct {
	OutcomeID       string
	ActionID        string
	OutcomeType     string
	Result          string
	Status          string
	Changed         bool
	ModelVersion    int64
	OperationID     string
	CompletedTarget string
	// Detail 在幂等那一格必须有：Changed=false 时客户端不知道「为什么没写」就只能猜，
	// 而「刚刚记下了」与「今天已经记过了」在屏上是两句话。
	Detail string
}

// RecordActionOutcome 记一条行动结果。
//
// 四格判据各有归属：
//   - origin/targets 由这里钉死（OriginUserStatement + 这一条行动），客户端改不了；
//   - 「outcome_type 与对象类型匹不匹配」由 `checkOutcomeProposal` 里那一格
//     `OutcomeTargetRequirement` 判（工单 §怎么做.3 点名的既有逻辑）：本面只 link
//     commitment，所以 `goal_progress`／`pattern_*`／`proactive_*` 会在这里 400，
//     而不是写出一条挂在行动上的目标级结果；
//   - 跨用户 ⇒ `verifyTargets` 回 ErrInvalidOwnership ⇒ 404（与「不存在」同码，
//     v3_feedback.go 的同一判据）；
//   - 同日同型重复 ⇒ 回首次结果、Changed=false、**不写第二行**（§怎么做.5）。
func (s *Service) RecordActionOutcome(ctx context.Context,
	p ActionOutcomeProposal) (ActionOutcomeReceipt, error) {
	userID := strings.TrimSpace(p.UserID)
	actionID := strings.TrimSpace(p.ActionID)
	if userID == "" {
		return ActionOutcomeReceipt{}, fmt.Errorf("%w: user_id 不能为空",
			ErrInvalidOutcomeProposal)
	}
	if actionID == "" {
		return ActionOutcomeReceipt{}, fmt.Errorf("%w: action_id 不能为空",
			ErrInvalidOutcomeProposal)
	}
	result := p.Result
	if result == "" {
		// 与 RecordOutcome 同一格归一化：缺省是 unknown，不是 400（02:182-189）。
		result = OutcomeResultUnknown
	}
	proposal := OutcomeProposal{
		UserID:      userID,
		Type:        p.Type,
		Result:      result,
		Origin:      OriginUserStatement,
		Targets:     []TargetRef{{Type: RefCommitment, ID: actionID}},
		OccurredAt:  p.OccurredAt.UTC(),
		Description: p.Note,
	}
	if err := checkOutcomeProposal(proposal); err != nil {
		return ActionOutcomeReceipt{}, err
	}
	receipt := ActionOutcomeReceipt{}
	err := s.db.InTx(ctx, func(ctx context.Context, st Store) error {
		latest, err := st.LatestOutcomeForTarget(ctx, userID, RefCommitment,
			actionID, p.Type)
		if err != nil {
			return err
		}
		if latest.Found && sameUTCDate(latest.CreatedAt, s.now()) {
			receipt = ActionOutcomeReceipt{OutcomeID: latest.ID, ActionID: actionID,
				OutcomeType: latest.OutcomeType, Result: latest.Result,
				Status: latest.Status, Changed: false, ModelVersion: latest.ModelVersion,
				OperationID: p.OperationID,
				Detail: fmt.Sprintf("今天已经记过一条 %s 结果（outcome=%s），这次不重复记账",
					latest.OutcomeType, latest.ID)}
			return nil
		}
		inner, err := s.recordOutcomeWithin(ctx, st, proposal)
		if err != nil {
			return err
		}
		receipt = ActionOutcomeReceipt{OutcomeID: inner.ID, ActionID: actionID,
			OutcomeType: string(p.Type), Result: string(result), Status: inner.Status,
			Changed: true, ModelVersion: inner.ModelVersion, OperationID: p.OperationID,
			CompletedTarget: inner.CompletedTarget, Detail: inner.Detail}
		return nil
	})
	if err != nil {
		return ActionOutcomeReceipt{}, err
	}
	return receipt, nil
}

// sameUTCDate 是「两条记录是不是同一天」。
//
// 按 UTC 而不是按用户时区：这一面拿不到用户所在时区（users 表没有那一列），
// 而「同一天」如果被两处按不同口径解释，重复提交就会在一边的日界线上写进第二行。
// 单一口径比「更贴近用户直觉」重要——跨日界的那一次本来就该是两条独立观测
// （04 §22 的按天记账要的就是这个）。
func sameUTCDate(a time.Time, b time.Time) bool {
	x, y := a.UTC(), b.UTC()
	return x.Year() == y.Year() && x.YearDay() == y.YearDay()
}

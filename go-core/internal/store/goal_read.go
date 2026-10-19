package store

// ── T4-02 列表面（`GET /internal/v3/goals` 与 `/actions`）─────────────────────
//
// 与 T3-01 那条读面同一条纪律：这里没有任何判断。status 词表、limit 的默认与上限、
// 「截断了没有」全在 `domain.GoalBrowser`；仓储只搬参数与列。
//
// 挂在池面 `*Canonical` 而不是事务面：一次列表读不是一笔要一起提交的改写
// （canonical_read.go 的头注释同口径）。
//
// 为什么读投影里没有 `personal_model_id`：这一面唯一的调用方只拿到 user_id，
// 而模型行是这个人的一部分，不是他要看的字段。

import (
	"context"
	"fmt"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// 编译期闸：领域接口一改，这里先红，而不是等 cmd/go-api 装配时才发现。
var _ domain.GoalReader = (*Canonical)(nil)

func (c *Canonical) ListGoalRecords(ctx context.Context, userID, status string,
	limit int) ([]domain.GoalRecord, error) {
	if limit <= 0 {
		// 「取 0 条」与「取全部」在 SQL 里是两个形状（LIMIT 0 ⇄ 没有 LIMIT），
		// 所以这里拒绝，而不是替调用方猜一个（insight_browse_read.go 同判据）。
		return nil, fmt.Errorf("store: goal list needs a positive limit, got %d", limit)
	}
	rows, err := c.q.ListGoals(ctx, canonicaldb.ListGoalsParams{
		UserID: userID, Status: status, RowLimit: int32(limit)})
	if err != nil {
		return nil, wrap("list goals", err)
	}
	items := make([]domain.GoalRecord, 0, len(rows))
	for _, row := range rows {
		items = append(items, domain.GoalRecord{
			ID: row.ID, Title: row.Title, Description: row.Description.String,
			Status: row.Status, StartedAt: timeOf(row.StartedAt),
			TargetAt: timeOf(row.TargetAt), CompletedAt: timeOf(row.CompletedAt),
			CreatedAt: timeOf(row.CreatedAt), UpdatedAt: timeOf(row.UpdatedAt),
		})
	}
	return items, nil
}

func (c *Canonical) ListActionRecords(ctx context.Context, userID, status string,
	limit int) ([]domain.ActionRecord, error) {
	if limit <= 0 {
		return nil, fmt.Errorf("store: action list needs a positive limit, got %d", limit)
	}
	rows, err := c.q.ListCommitments(ctx, canonicaldb.ListCommitmentsParams{
		UserID: userID, Status: status, RowLimit: int32(limit)})
	if err != nil {
		return nil, wrap("list commitments", err)
	}
	items := make([]domain.ActionRecord, 0, len(rows))
	for _, row := range rows {
		// GoalID 空串＝库里那一格是 NULL（textOf 的反向：可空列的两个方向必须同形，
		// 否则「没挂在目标下」会在写入侧变成 NULL、读取侧变成 ""，
		// 而前端拿到 "" 会去渲染一个空的分组标题）。
		items = append(items, domain.ActionRecord{
			ID: row.ID, Title: row.Title, Description: row.Description.String,
			Status: row.Status, DueAt: timeOf(row.DueAt),
			CompletedAt: timeOf(row.CompletedAt), SkippedAt: timeOf(row.SkippedAt),
			GoalID:    row.GoalID.String,
			CreatedAt: timeOf(row.CreatedAt), UpdatedAt: timeOf(row.UpdatedAt),
		})
	}
	return items, nil
}

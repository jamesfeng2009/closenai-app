package store

// ── T5-04：proactives 的只读审计面（`GET /internal/v3/proactives`）─────────────
//
// 与 T3-01／T4-02 两条读面同一条纪律：这里没有任何判断。「哪几态算超期」在
// canonical.sql 的那一条谓词里（唯一一处），八态词表、limit 的默认与上限、
// 「截断了没有」全在 `domain.ProactiveAuditBrowser`；仓储只搬参数与列。
//
// 挂在池面 `*Canonical` 而不是事务面：一次审计读不是一笔要一起提交的改写
// （canonical_read.go 的头注释同口径）。
//
// 为什么投影里没有 `message`/`eligibility_detail`：正文与评估包都是用户内容，
// 审计问的是「发没发、为什么没发」，多搬一列就是多开一条数据外流的路
// （工单 §怎么做.1 点名的七格里本来也没有它们）。

import (
	"context"
	"fmt"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// 编译期闸：领域接口一改，这里先红，而不是等 cmd/go-api 装配时才发现。
var _ domain.ProactiveAuditReader = (*Canonical)(nil)

func (c *Canonical) ListProactiveAuditRecords(ctx context.Context, userID, status string,
	limit int, stale bool) ([]domain.ProactiveAuditRecord, error) {
	if limit <= 0 {
		// 「取 0 条」与「取全部」在 SQL 里是两个形状（LIMIT 0 ⇄ 没有 LIMIT），
		// 所以这里拒绝，而不是替调用方猜一个（goal_read.go 同判据）。
		return nil, fmt.Errorf(
			"store: proactive audit needs a positive limit, got %d", limit)
	}
	rows, err := c.q.ListProactivesForAudit(ctx, canonicaldb.ListProactivesForAuditParams{
		UserID: userID, Status: status, Stale: stale, RowLimit: int32(limit)})
	if err != nil {
		return nil, wrap("list proactives for audit", err)
	}
	items := make([]domain.ProactiveAuditRecord, 0, len(rows))
	for _, row := range rows {
		items = append(items, domain.ProactiveAuditRecord{
			ID: row.ID, Status: row.Status, TriggerType: row.TriggerType,
			ScheduledAt: timeOf(row.ScheduledAt), SentAt: timeOf(row.SentAt),
			RespondedAt: timeOf(row.RespondedAt), ExpiresAt: timeOf(row.ExpiresAt),
			BlockReason: row.BlockReason.String, RejectReason: row.RejectReason.String,
			JourneyEventID: row.JourneyEventID.String,
			CreatedAt:      timeOf(row.CreatedAt), UpdatedAt: timeOf(row.UpdatedAt),
		})
	}
	return items, nil
}

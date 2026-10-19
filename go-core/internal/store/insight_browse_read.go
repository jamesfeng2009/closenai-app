package store

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgtype"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// ── T3-01 列表面（`GET /internal/v3/insights`；裁决七-10）───────────────────────
//
// 与 N9 那组同一条纪律：**这里没有任何判断**。可见状态集合、limit 的默认与上限、
// 游标的编解码、「无证据不上线」的语义（SQL 的内连已经把它实现了，见 canonical.sql 的
// `ListInsightsForBrowsing`）全在 `domain.InsightBrowser`。仓储只做两件事：搬参数、搬行。
//
// 挂在池面 `*Canonical` 而不是事务面：一次列表读不是一笔要一起提交的改写
// （与 canonical_read.go 的头注释同一条口径）。

// 编译期闸：领域接口一改，这里先红，而不是等 cmd/go-api 装配时才发现。
var _ domain.InsightBrowseReader = (*Canonical)(nil)

func (c *Canonical) ListInsightsForBrowsing(ctx context.Context, userID string,
	statuses []string, after domain.InsightKey, hasAfter bool,
	limit int) ([]domain.InsightListItem, error) {
	if limit <= 0 {
		// 「取 0 条」与「取全部」在 SQL 里是两个完全不同的形状（LIMIT 0 ⇄ 没有 LIMIT），
		// 所以这里拒绝，而不是替调用方猜一个。
		return nil, fmt.Errorf("store: insights list needs a positive limit, got %d", limit)
	}
	if len(statuses) == 0 {
		// 空集合会让 `= ANY('{})` 恒假、列表**永远为空**，而领域层从没见过这种输入：
		// 宁可报错，也不给一条静默的空列表（那种红只有用户看得到）。
		return nil, fmt.Errorf("store: insights list needs at least one status")
	}
	arg := canonicaldb.ListInsightsForBrowsingParams{
		UserID: userID, Statuses: statuses, RowLimit: int32(limit),
	}
	if hasAfter {
		arg.AfterAt = pgtype.Timestamptz{Time: after.PublishedAt, Valid: true}
		arg.AfterID = after.ID
	}
	rows, err := c.q.ListInsightsForBrowsing(ctx, arg)
	if err != nil {
		return nil, wrap("list insights for browsing", err)
	}
	items := make([]domain.InsightListItem, 0, len(rows))
	for _, row := range rows {
		confidence, hasConfidence, err := numericToFloat(row.Confidence)
		if err != nil {
			return nil, fmt.Errorf("store: insight %s confidence: %w", row.ID, err)
		}
		items = append(items, domain.InsightListItem{
			ID: row.ID, Statement: row.Statement, ClaimType: row.ClaimType,
			Status: row.Status, Confidence: confidence, HasConfidence: hasConfidence,
			PublishedAt: timeOf(row.PublishedAt), EvidenceCount: row.EvidenceCount,
		})
	}
	return items, nil
}

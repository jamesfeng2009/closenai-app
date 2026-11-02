// T7-02 分析事件接收台账的 PG 读面。
//
// 与 backend/repositories/analytics_receipt_repository.py 同一套 SQL 语义：
// claim 的胜负只由 `INSERT … ON CONFLICT DO NOTHING` 的 RowsAffected 决定，
// 不能先 SELECT 再 INSERT——两个并发请求会同时看到「没有行」而各自外发一份。
// DDL 属主仍是 alembic（revision b7c9d1e3f5a2），这里只读写。
package store

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
)

const claimAnalyticsReceipt = `
	INSERT INTO analytics_event_receipts (user_id, event_id, status)
	VALUES ($1, $2, 'pending')
	ON CONFLICT (user_id, event_id) DO NOTHING`

// Claim 认领一条事件。claimed=false 时 prior 是已有行状态；
// 行不存在（并发删除后读不到）返回空串，调用方按可重试处理。
func (p *PG) Claim(ctx context.Context, userID, eventID string) (bool, string, error) {
	tag, err := p.pool.Exec(ctx, claimAnalyticsReceipt, userID, eventID)
	if err != nil {
		return false, "", err
	}
	if tag.RowsAffected() == 1 {
		return true, "", nil
	}
	var status string
	err = p.pool.QueryRow(ctx,
		`SELECT status FROM analytics_event_receipts WHERE user_id = $1 AND event_id = $2`,
		userID, eventID).Scan(&status)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, "", nil
	}
	if err != nil {
		return false, "", err
	}
	return false, status, nil
}

func (p *PG) MarkAccepted(ctx context.Context, userID, eventID string) error {
	_, err := p.pool.Exec(ctx,
		`UPDATE analytics_event_receipts
		    SET status = 'accepted', reason = NULL, updated_at = now()
		  WHERE user_id = $1 AND event_id = $2`, userID, eventID)
	return err
}

func (p *PG) Release(ctx context.Context, userID, eventID string) error {
	_, err := p.pool.Exec(ctx,
		`DELETE FROM analytics_event_receipts WHERE user_id = $1 AND event_id = $2`,
		userID, eventID)
	return err
}

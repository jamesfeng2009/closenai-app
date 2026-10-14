// N8 批 1 的 legacy 表读面（analytics + discovery 两个域）。
//
// 口径沿用 users.go 的包注释：SQL 是 backend/repositories 里对应那条的 1:1 翻译，
// 灰度期两侧读同一批行才有「契约 diff 为空」可言；DDL 属主仍是 alembic（D-7）。
// 唯一的收缩是 SELECT 的列——Python 侧 ORM 拉整行，Go 侧只取判定要用的两列，
// WHERE 谓词一字未动（`id = $1 AND status = 'active'` 与 mood 那条同）。
package store

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// UserProfile 是 user_repository.get_user 的活跃用户判据 + 两个域要用的列
// （行形状定义在 domain.UserProfile，见 internal/domain/rows.go）。
// found=false 同时覆盖 Python 侧「get_user 返回 None」与「require_user 抛 40401」两种用法。
const selectUserProfile = `
	SELECT language, analytics_consent
	FROM users
	WHERE id = $1 AND status = 'active'`

func (p *PG) UserProfile(ctx context.Context, userID string) (domain.UserProfile, error) {
	var out domain.UserProfile
	row := p.pool.QueryRow(ctx, selectUserProfile, userID)
	err := row.Scan(&out.Language, &out.AnalyticsConsent)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, nil
	}
	if err != nil {
		return out, err
	}
	out.Found = true
	return out, nil
}

// MoodCheckins 是 mood_repository.get_history(days) 的同一张网：
// 只按 user_id + created_at 下界过滤，倒序返回。
//
// 倒序不是排版偏好——domain.averageValue 逐项相加求均值，
// 相加顺序换了就在浮点尾数上给了另一侧一个可观察的差异。
const selectMoodCheckins = `
	SELECT mood_emoji, created_at
	FROM mood_checkins
	WHERE user_id = $1 AND created_at >= $2
	ORDER BY created_at DESC`

func (p *PG) MoodCheckins(ctx context.Context, userID string, since time.Time) ([]domain.Checkin, error) {
	rows, err := p.pool.Query(ctx, selectMoodCheckins, userID, since)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []domain.Checkin
	for rows.Next() {
		var c domain.Checkin
		if err := rows.Scan(&c.Emoji, &c.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

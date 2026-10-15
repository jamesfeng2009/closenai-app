// N8 批 5 的 legacy 表读面（mood + reflections 两个域，三条纯读）。
//
// 口径沿用 legacy_batch1.go 的包注释：SQL 是 backend/repositories 里对应那条的
// 1:1 翻译，只收缩 SELECT 的列、不动 WHERE 与 ORDER BY——灰度期两侧读同一批行，
// 「契约 diff 为空」才有可比之处。
//
// ⚠️ 本域**继承**了批 4 登记的那条排序缺陷：`ORDER BY created_at` 没有次级排序键，
// PostgreSQL 在同一时刻的多行之间给出的顺序未定义（Python 侧的 `func.now()` 是
// 事务级时间戳，同一事务里的多行天然同刻）。夹具因此把每一行的 created_at 钉成
// **互不相同的瞬间**；生产数据里若出现同刻多行，两侧的行序可以不同，
// 而那是响应数组的顺序，不是格式差异。growth 那一批（7 条）开工前必须先结这条。
package store

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// MoodHistory 是 mood_repository.get_recent（:155-165）：只按 user_id 过滤、倒序、LIMIT。
// 两个 COALESCE 对应 service 层的 `r.mood_label or ""` / `r.ai_interpretation or ""`：
// NULL 与空串都给 ""，所以这里不必区分（夹具 history-nulls 同时种了 NULL 和 "" 两行）。
const selectMoodHistory = `
	SELECT id, mood_emoji, COALESCE(mood_label, ''), COALESCE(ai_interpretation, ''), created_at
	FROM mood_checkins
	WHERE user_id = $1
	ORDER BY created_at DESC
	LIMIT $2`

func (p *PG) MoodHistory(ctx context.Context, userID string,
	limit int64) ([]domain.MoodCheckinRow, error) {
	rows, err := p.pool.Query(ctx, selectMoodHistory, userID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []domain.MoodCheckinRow{}
	for rows.Next() {
		var r domain.MoodCheckinRow
		if err := rows.Scan(&r.ID, &r.Emoji, &r.Label, &r.Interpretation, &r.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// MoodTrendRows 是 mood_repository.count_by_day（:187-205）的 SQL 部分：
// 窗口下界 + **升序**。后半段（按用户本地时区折算日历日、当日取首个）在
// domain.MoodTrend——Python 侧那一步也是在 Python 里做的（注释写明为兼容 SQLite），
// 所以 Go 也必须读原始行而不是在 SQL 里 date_trunc，否则时区回退口径就分叉了。
const selectMoodTrendRows = `
	SELECT mood_emoji, created_at
	FROM mood_checkins
	WHERE user_id = $1 AND created_at >= $2
	ORDER BY created_at ASC`

func (p *PG) MoodTrendRows(ctx context.Context, userID string,
	since time.Time) ([]domain.Checkin, error) {
	rows, err := p.pool.Query(ctx, selectMoodTrendRows, userID, since)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []domain.Checkin{}
	for rows.Next() {
		var c domain.Checkin
		if err := rows.Scan(&c.Emoji, &c.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

// MoodUserTimezone 是 user_repository.get_user 的 WHERE（status='active'，
// deleted 与不存在都查不到）再只取 timezone 一列。
// found=false 对应 Python 的 `user is None`⇒时区 None⇒按 UTC 折算，**不是** 40401。
const selectMoodUserTimezone = `
	SELECT timezone
	FROM users
	WHERE id = $1 AND status = 'active'`

func (p *PG) MoodUserTimezone(ctx context.Context,
	userID string) (timezone string, found bool, err error) {
	row := p.pool.QueryRow(ctx, selectMoodUserTimezone, userID)
	if err := row.Scan(&timezone); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return "", false, nil
		}
		return "", false, err
	}
	return timezone, true, nil
}

// Reflections 是 growth_repository.list_by_type（:79-89）：
// user_id + record_type **精确**等值、倒序、LIMIT。content 与 related_memory_ids
// 都是 TEXT，后者按 COALESCE 折成空串交给 domain.SplitMemoryIDs 判 falsy。
const selectReflections = `
	SELECT id, content, COALESCE(related_memory_ids, ''), created_at
	FROM growth_records
	WHERE user_id = $1 AND record_type = $2
	ORDER BY created_at DESC
	LIMIT $3`

func (p *PG) Reflections(ctx context.Context, userID, recordType string,
	limit int64) ([]domain.ReflectionRow, error) {
	rows, err := p.pool.Query(ctx, selectReflections, userID, recordType, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []domain.ReflectionRow{}
	for rows.Next() {
		var r domain.ReflectionRow
		if err := rows.Scan(&r.ID, &r.Content, &r.MemoryIDs, &r.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

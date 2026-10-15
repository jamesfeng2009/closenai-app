// N8 批 5 的 legacy 表读面（mood + reflections 两个域，三条纯读）。
//
// 口径沿用 legacy_batch1.go 的包注释：SQL 是 backend/repositories 里对应那条的
// 1:1 翻译，只收缩 SELECT 的列、不动 WHERE 与 ORDER BY——灰度期两侧读同一批行，
// 「契约 diff 为空」才有可比之处。
//
// ⚠️ 批 4 登记的那条排序缺陷（`ORDER BY created_at` 没有次级排序键，PG 在同一时刻的
// 多行之间给出的顺序未定义——Python 侧的 `func.now()` 是事务级时间戳，同一事务里的
// 多行天然同刻）在 mood 这两条上已于 2026-10-15 结案：两侧的 ORDER BY 都补了 `id`
// 次级键，且**方向必须与各自主键一致**（history 双 DESC、trend 双 ASC）。
// growth 那批（D-A）先结，mood 这批随后结；`get_history`/`list_between` 那两条同型
// 缺陷按「发现的缺陷不顺手修」另批立项，本文件与 legacy_batch1.go 的
// selectMoodCheckins 仍是无次级键的形状。
//
// 补之前夹具把每一行的 created_at 钉成互不相同的瞬间来绕开这条；补之后夹具才敢种
// 同刻行：`mood-h-tie`（history，倒序）与 `mood-t-tie`（trend，升序 ⇒ 当日首行取
// 主键小的那条当代表 emoji）。两格各**三行同瞬**，且主键的升序与降序排列都既不等于
// 插入序、也不等于插入序的逆序——两行做不到：数据库没有次级键时只可能给这两支之一
// （本批第一版实测：两行时 `ORDER BY created_at DESC` 给的正是与 `id DESC` 重合的那支，
// 撤掉次级键字节不动，那格空转）。三行才让「键在不在」在 PG 的字节面上必然可观测。
// 变异见 mutate_n8_gates.py 的 #188（history 撤键）与 #196（trend 撤键），两条都红。
//
// id 是 uuid4 ⇒ 次级键给的是字典序不是写入序：它保证两侧可复现，
// 不声称恢复历史同刻行的真实先后。
package store

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// MoodHistory 是 mood_repository.get_recent（:155-172）：只按 user_id 过滤、倒序、LIMIT。
// 两个 COALESCE 对应 service 层的 `r.mood_label or ""` / `r.ai_interpretation or ""`：
// NULL 与空串都给 ""，所以这里不必区分（夹具 history-nulls 同时种了 NULL 和 "" 两行）。
// `id DESC` 不是装饰：created_at 同刻的多行（夹具每种三行）没有它就该由查询计划决定顺序，
// 而 LIMIT 30 截断落在谁身上也随之决定（方向必须与 Python 侧一致，见包注释）。
const selectMoodHistory = `
	SELECT id, mood_emoji, COALESCE(mood_label, ''), COALESCE(ai_interpretation, ''), created_at
	FROM mood_checkins
	WHERE user_id = $1
	ORDER BY created_at DESC, id DESC
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

// MoodTrendRows 是 mood_repository.count_by_day（:194-216）的 SQL 部分：
// 窗口下界 + **升序**。后半段（按用户本地时区折算日历日、当日取首个）在
// domain.MoodTrend——Python 侧那一步也是在 Python 里做的（注释写明为兼容 SQLite），
// 所以 Go 也必须读原始行而不是在 SQL 里 date_trunc，否则时区回退口径就分叉了。
// `id ASC` 与主键同方向：同刻三行落在同一本地日时，「当日首个」就是主键小的那条；
// 一侧写成 DESC ⇒ labels/values 直接换档（夹具 mood-t-tie 那格证的就是这个，
// 三行而不是两行的理由见本文件包注释）。
const selectMoodTrendRows = `
	SELECT mood_emoji, created_at
	FROM mood_checkins
	WHERE user_id = $1 AND created_at >= $2
	ORDER BY created_at ASC, id ASC`

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

// Reflections 是 growth_repository.list_by_type（:86-96）：
// user_id + record_type **精确**等值、倒序、LIMIT。content 与 related_memory_ids
// 都是 TEXT，后者按 COALESCE 折成空串交给 domain.SplitMemoryIDs 判 falsy。
//
// `id DESC` 不是装饰：同一事务写入的多行 created_at 逐位相同，少了这条次级键，
// Python 侧与这里的返回顺序都由查询计划决定，11 条回放在「同刻两行」格子上
// 不可能逐字一致。两侧的次级键必须同方向（Python 用 .desc()，这里用 DESC）。
const selectReflections = `
	SELECT id, content, COALESCE(related_memory_ids, ''), created_at
	FROM growth_records
	WHERE user_id = $1 AND record_type = $2
	ORDER BY created_at DESC, id DESC
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

// N8 批 6 的 legacy 表读面（memories 域两条纯读）。
//
// SQL 仍是 backend/repositories/memory_repository.py 里那两条的 1:1 翻译，
// 与批 1–5 的唯一不同是**这里必须收缩列**：Python 侧写的是 `select(Memory)`
// （SELECT 全列，含 embedding），而 embedding 是 halfvec(512)（models/memory.py:36），
// Go 全仓没有 halfvec 的解析器（grep 实测），且这一列根本不出现在响应里
// （_to_response 不搬它，memory_service.py:115-129）。所以 Go 只取响应要的 11 列——
// 收缩的是「读什么」，不是「筛什么/怎么排」：WHERE 与 ORDER BY 逐字照抄。
//
// 三条 SQL 分开写（而不是 `$n = ” OR category = $n` 这种就地开关），
// 因为 Python 侧就是「先建 stmt，`if category:` 再 .where(...)」两次拼装
// （memory_repository.py:112-118）；把判据藏进 SQL 会让「空串 category 到底过不过滤」
// 这一格在两侧各长一半。空串与缺参在 Python 都是假值 ⇒ 都走无 category 那一条。
//
// ⚠️ 两条读**过滤条件不同**，这是今天真实存在的行为差异，不许「统一」：
//   - 列表过 `status = 'current'`（:109 的默认参数），详情不过（:61-70 的 WHERE 只有
//     id/user_id/deleted）⇒ 一条 superseded 记忆在列表消失、在详情仍可读。
//   - `deleted = false` 两侧同写法：DDL 是 `BOOLEAN NOT NULL DEFAULT FALSE`
//     （alembic 9e1b3d5f7a2c:27），所以不需要 NULL 分支；但**不许**顺手改成
//     `IS DISTINCT FROM true`——那是另一种语义，且现网没有它的位置。
//
// `ORDER BY created_at DESC, id DESC` 的 `id` 次级键与 Python 侧同日同向补上
// （memory_repository.py:119）：这一列的 server_default 是 now()，PG 里它是事务级
// 时间戳 ⇒ 同一事务写入的多行必然同瞬。夹具因此种了同刻**三行**且把主键最小的
// 那行插在中间（两行做不到：无键时数据库只可能给插入序或其逆序，撤键字节不动，
// 判据来自批 5 那次实测空转，tasks.md 的 M-G 段有记录）。
// id 是 uuid4 ⇒ 次级键给的是字典序不是写入序：它保证两侧可复现，不声称恢复同刻行的真实先后。
package store

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// memoryResponseColumns 是这两条端点的响应真正会读到的列（models/schemas.py:49-63
// 里除 similarity/regressed 两个常量键之外全部来自列；那两个键由 httpapi 写死）。
const memoryResponseColumns = `id, user_id, content, category, status, conviction_score,
		source, created_at, superseded_at, transition_reason, labels`

// selectMemoryList 是 memory_repository.list 不带 category 的那一支。
const selectMemoryList = `
	SELECT ` + memoryResponseColumns + `
	FROM memories
	WHERE user_id = $1 AND status = 'current' AND deleted = false
	ORDER BY created_at DESC, id DESC
	LIMIT $2`

// selectMemoryListByCategory 是同一支加上 `AND category = $2`——
// category 是**精确等值**，不做 lower/前缀这类「更宽松」的实现（Python 那边就是 ==）。
const selectMemoryListByCategory = `
	SELECT ` + memoryResponseColumns + `
	FROM memories
	WHERE user_id = $1 AND status = 'current' AND deleted = false AND category = $2
	ORDER BY created_at DESC, id DESC
	LIMIT $3`

// selectMemoryByWholeID 是 memory_repository.get(:61-70)：主键 + 归属 + 软删，
// **没有 status 过滤**。user_id 进 WHERE 就是这条端点唯一的越权防线，
// 所以「别人的 id」与「不存在的 id」在字节上必须是同一个 40403（决策点 ⑤）。
const selectMemoryByWholeID = `
	SELECT ` + memoryResponseColumns + `
	FROM memories
	WHERE id = $1 AND user_id = $2 AND deleted = false`

func (p *PG) MemoryList(ctx context.Context, userID string, limit int64,
	category string) ([]domain.MemoryRow, error) {
	query, args := selectMemoryList, []any{userID, limit}
	if category != "" {
		query, args = selectMemoryListByCategory, []any{userID, category, limit}
	}
	rows, err := p.pool.Query(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []domain.MemoryRow{}
	for rows.Next() {
		r, err := scanMemoryRows(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

func (p *PG) MemoryByID(ctx context.Context, memoryID,
	userID string) (domain.MemoryRow, bool, error) {
	r, err := scanMemoryRows(p.pool.QueryRow(ctx, selectMemoryByWholeID, memoryID, userID))
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return domain.MemoryRow{}, false, nil
		}
		return domain.MemoryRow{}, false, err
	}
	return r, true, nil
}

// memoryScanner 让上面三条共用一份列序：Query 的 rows 与 QueryRow 都满足它，
// 少一处「改列忘了改另一条」的缝（这两条端点读的是同一批列，形状必须同源）。
type memoryScanner interface {
	Scan(dest ...any) error
}

func scanMemoryRows(rows memoryScanner) (domain.MemoryRow, error) {
	var (
		r            domain.MemoryRow
		supersededAt pgtype.Timestamptz
		reason       pgtype.Text
		labels       pgtype.Text
	)
	err := rows.Scan(&r.ID, &r.UserID, &r.Content, &r.Category, &r.Status,
		&r.Conviction, &r.Source, &r.CreatedAt, &supersededAt, &reason, &labels)
	if err != nil {
		return r, err
	}
	// 三个可空列原样带上「有没有值」：transition_reason 的 NULL 与 "" 在响应里
	// 分别是 null 与 ""（pydantic 的 Optional[str]），这一格不能靠 SQL 折掉。
	if supersededAt.Valid {
		r.HasSupersededAt = true
		r.SupersededAt = supersededAt.Time
	}
	if reason.Valid {
		r.HasTransitionReason = true
		r.TransitionReason = reason.String
	}
	// labels 是唯一可以折的：NULL 与 "" 在 Python 的 _parse_labels(:102-112) 里同判
	// （`if not raw: return None`）⇒ 都进 null。
	if labels.Valid {
		r.Labels = labels.String
	}
	return r, nil
}

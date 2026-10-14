// N8 批 2 的 legacy 表读面（curriculum 域）。
//
// 口径沿用 legacy_batch1.go 的包注释：SQL 是 backend/repositories 里对应那条的
// 1:1 翻译，灰度期两侧读同一批行才有「契约 diff 为空」可言；DDL 属主仍是 alembic（D-7）。
// curriculum 的存储刻意复用 journey_events（curriculum_service.py 模块头：无新表），
// 所以这里读的是剧本表而不是主题卡表。
package store

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// selectCurriculumUser 的 WHERE 与 user_repository.get_user 逐字同
// （`id = $1 AND status = 'active'`）：已注销用户两侧都当作不存在 ⇒ 40401。
// 三列先落到指针：2026-10-14 实测 alembic head 它们都是 NOT NULL（timezone 还没有默认值），
// 但 Python 读到的 None 会走 safe_zoneinfo→UTC、`not None`→未同意，
// 所以迁移前的库真出现 NULL 时，折算方式必须与那侧一致而不是报错。
const selectCurriculumUser = `
	SELECT timezone, language, ai_consent
	FROM users
	WHERE id = $1 AND status = 'active'`

func (p *PG) CurriculumUser(ctx context.Context, userID string) (domain.CurriculumUser, error) {
	var out domain.CurriculumUser
	var tz, lang *string
	var consent *bool
	row := p.pool.QueryRow(ctx, selectCurriculumUser, userID)
	if err := row.Scan(&tz, &lang, &consent); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return out, nil
		}
		return out, err
	}
	out.Found = true
	out.ID = userID
	if tz != nil {
		out.Timezone = *tz
	}
	if lang != nil {
		out.Language = *lang
	}
	out.AIConsent = consent != nil && *consent
	return out, nil
}

// selectCurriculumWeek 是 journey_repository.list_kind_between 的同一张网：
// 四个谓词一个不动，正序也不能动——同一周内格是「后写覆盖先写」，
// 顺序换了被覆盖的那一行就换成另一行，响应里的 id/payload 跟着换。
const selectCurriculumWeek = `
	SELECT id, day_no, payload, memory_refs
	FROM journey_events
	WHERE user_id = $1 AND event_kind = $2 AND scheduled_at >= $3 AND scheduled_at < $4
	ORDER BY scheduled_at ASC
	LIMIT $5`

func (p *PG) CurriculumCards(ctx context.Context, userID, kind string,
	start, end time.Time, limit int) ([]domain.CurriculumCard, error) {
	rows, err := p.pool.Query(ctx, selectCurriculumWeek, userID, kind, start, end, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []domain.CurriculumCard
	for rows.Next() {
		var (
			c             domain.CurriculumCard
			payload, refs *string
		)
		if err := rows.Scan(&c.ID, &c.DayNo, &payload, &refs); err != nil {
			return nil, err
		}
		if payload != nil {
			c.Payload = *payload
		}
		if refs != nil {
			c.MemoryRefs = *refs
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

// selectJourneyEventByID 是 journey_repository.get 的那一条：
// **不过滤 user_id**（越权判定在 mark_read 里做，回 403 而不是 404）。
const selectJourneyEventByID = `
	SELECT id, user_id, event_kind, payload
	FROM journey_events
	WHERE id = $1`

func (p *PG) JourneyEventOwner(ctx context.Context, eventID string) (domain.CurriculumEvent, error) {
	var out domain.CurriculumEvent
	var payload *string
	row := p.pool.QueryRow(ctx, selectJourneyEventByID, eventID)
	// id 取的是**列值**而不是路径参数：Python 响应里的 id 来自 event.id。
	// 两者在 URL 编码/大小写不规范的客户请求上可以不同，贴路径参数等于回显客户端输入。
	if err := row.Scan(&out.ID, &out.UserID, &out.Kind, &payload); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return out, nil
		}
		return out, err
	}
	out.Found = true
	if payload != nil {
		out.Payload = *payload
	}
	return out, nil
}

// updateJourneyPayload 是 journey_repository.set_payload 只有 payload 那一支
// （memory_refs 传 None 时 Python 也不写这列）。
const updateJourneyPayload = `
	UPDATE journey_events SET payload = $2 WHERE id = $1`

func (p *PG) SetJourneyPayload(ctx context.Context, eventID, payload string) error {
	_, err := p.pool.Exec(ctx, updateJourneyPayload, eventID, payload)
	return err
}

// N8 批 3 的 legacy 表读写面（journey 域）。
//
// 口径沿用批 1/批 2：SQL 是 backend/repositories/journey_repository.py 与
// onboarding_repository.py 里对应那几条的 1:1 翻译，灰度期两侧读同一批行、
// 写同一种文本，才有「切流前后契约 diff 为空」可言；DDL 属主仍是 alembic（D-7）。
//
// 批 3 与批 2 的差别：GET /journey/events 会**写库**（ensure_prefilled 幂等预写），
// 所以这里除了 SELECT 还有写面，而且写面落在**一个事务**里（Python 侧是请求级事务，
// 见 PrefillJourney 的注释）——灰度期两侧读同一批行、写同一种文本，
// 才有「切流前后契约 diff 为空」可言。
package store

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/idgen"
)

// selectJourneyUser 与 user_repository.get_user 逐字同一条 WHERE（id + status='active'）。
// push_time 必须是**列原文**：Python 用 `user.push_time or "09:00"`，
// NULL 与空串落同一分支，而 "0"（一个字符）走的是 int() 成功那支 ⇒ 00:00 语义不同。
const selectJourneyUser = `
	SELECT timezone, language, push_time, created_at
	FROM users
	WHERE id = $1 AND status = 'active'`

func (p *PG) JourneyUser(ctx context.Context, userID string) (domain.JourneyUser, error) {
	var out domain.JourneyUser
	var tz, lang, push *string
	var created *time.Time
	row := p.pool.QueryRow(ctx, selectJourneyUser, userID)
	if err := row.Scan(&tz, &lang, &push, &created); err != nil {
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
	if push != nil {
		out.PushTimeRaw = *push
	}
	if created != nil {
		out.CreatedAt = *created
		out.HasCreatedAt = true
	}
	return out, nil
}

// selectOnboardingBase 是 onboarding_repository.get 里 ensure_prefilled 真正用到的那一列。
// Python 用 scalar_one_or_none()：每 user_id 一个唯一索引（models/journey.py:80），
// 出现两行时 Python 抛、Go 取第一行——这条发散登记在 tasks.md，夹具不造双行。
// created_at 为 NULL 时按「没有基准行」返回（Python 的 `profile and profile.created_at`
// 落 falsy 一侧，退回 user.created_at，而不是拿 None 当基准）。
const selectOnboardingBase = `
	SELECT created_at
	FROM onboarding_profiles
	WHERE user_id = $1
	LIMIT 1`

func (p *PG) OnboardingBase(ctx context.Context, userID string) (time.Time, bool, error) {
	var created *time.Time
	err := p.pool.QueryRow(ctx, selectOnboardingBase, userID).Scan(&created)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return time.Time{}, false, nil
		}
		return time.Time{}, false, err
	}
	if created == nil {
		return time.Time{}, false, nil
	}
	return *created, true, nil
}

// selectJourneyForUser 是 list_for_user(statuses=None, limit=50)：
// **没有 status 过滤**（注销用户的行照样列出来，这是契约不是疏忽），
// 排序是 day_no 正序再 scheduled_at 正序——批 2 的周视图按 scheduled_at 单键，
// 两处的 ORDER BY 不同，抄混了行序就换。
const selectJourneyForUser = `
	SELECT id, day_no, event_kind, status, scheduled_at, sent_at,
	       acknowledged, payload, memory_refs
	FROM journey_events
	WHERE user_id = $1
	ORDER BY day_no ASC, scheduled_at ASC
	LIMIT $2`

func (p *PG) JourneyEvents(ctx context.Context, userID string,
	limit int) ([]domain.JourneyEventRow, error) {
	rows, err := p.pool.Query(ctx, selectJourneyForUser, userID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []domain.JourneyEventRow
	for rows.Next() {
		var (
			e             domain.JourneyEventRow
			sent          *time.Time
			ack           *bool
			payload, refs *string
		)
		if err := rows.Scan(&e.ID, &e.DayNo, &e.Kind, &e.Status, &e.ScheduledAt,
			&sent, &ack, &payload, &refs); err != nil {
			return nil, err
		}
		if sent != nil {
			e.SentAt = *sent
		}
		e.HasSentAt = sent != nil
		e.Acknowledged = ack != nil && *ack
		if payload != nil {
			e.Payload = *payload
		}
		if refs != nil {
			e.MemoryRefs = *refs
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// countJourneyUnseen 是 count_unseen 的三个谓词。`acknowledged IS FALSE` 与
// SQLAlchemy 的 `.is_(False)` 同形（`= false` 在 NULL 行上结果不同，历史脏值要认）。
const countJourneyUnseen = `
	SELECT count(*)
	FROM journey_events
	WHERE user_id = $1 AND status = 'sent' AND acknowledged IS FALSE`

func (p *PG) JourneyUnseenCount(ctx context.Context, userID string) (int, error) {
	var n int
	if err := p.pool.QueryRow(ctx, countJourneyUnseen, userID).Scan(&n); err != nil {
		return 0, err
	}
	return n, nil
}

// selectJourneyOwner 是 journey_repository.get（不过滤 user_id：越权与不存在
// 合成同一条 40402，不泄露他人 ID 有效性——:908 的注释就是那句话）。
const selectJourneyOwner = `
	SELECT id, user_id
	FROM journey_events
	WHERE id = $1`

func (p *PG) JourneyOwner(ctx context.Context, eventID string) (domain.JourneyOwner, error) {
	var out domain.JourneyOwner
	err := p.pool.QueryRow(ctx, selectJourneyOwner, eventID).Scan(&out.ID, &out.UserID)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return out, nil
		}
		return out, err
	}
	out.Found = true
	return out, nil
}

// acknowledgeJourneyEvent 是 repository.acknowledge 的两个 values：
// 一次 UPDATE 同时改 status 与 acknowledged，**不加 user_id 谓词**（归属判定在 service）。
const acknowledgeJourneyEvent = `
	UPDATE journey_events SET status = 'seen', acknowledged = true WHERE id = $1`

func (p *PG) AcknowledgeJourneyEvent(ctx context.Context, eventID string) error {
	_, err := p.pool.Exec(ctx, acknowledgeJourneyEvent, eventID)
	return err
}

// selectJourneyKindDay 是 get_by_kind 的三个谓词。Python 的 `.scalars().first()`
// 没有 ORDER BY ⇒ 取哪一行由执行计划决定；Go 的 QueryRow 同形。夹具刻意不造
// (user,kind,day) 重复行——「重复行两侧各取一行」不是可证的契约，登记在 tasks.md。
const selectJourneyKindDay = `
	SELECT id, status
	FROM journey_events
	WHERE user_id = $1 AND event_kind = $2 AND day_no = $3
	LIMIT 1`

// updateJourneySchedule 是 schedule() 命中既有行那一支的两个赋值
// （existing.scheduled_at = ...；payload 非 None 才写——prefill 永远给 dict）。
const updateJourneySchedule = `
	UPDATE journey_events SET scheduled_at = $2, payload = $3 WHERE id = $1`

// insertJourneyEvent 是 schedule() 的 INSERT 那一支：status 由 Python 显式写
// 'pending'（不是靠 server_default，:57 传的就是字面量），memory_refs 这一支 prefill
// 永远给 NULL（`memory_refs=None` ⇒ 列不写值 ⇒ 默认 NULL）。
const insertJourneyEvent = `
	INSERT INTO journey_events
	    (id, user_id, event_kind, day_no, status, scheduled_at, payload, memory_refs)
	VALUES ($1, $2, $3, $4, 'pending', $5, $6, NULL)`

// skipJourneyEvent 是 repository.skip：只动 status，永不删行（§5.19.3 频控铁律）。
const skipJourneyEvent = `
	UPDATE journey_events SET status = 'skipped' WHERE id = $1`

// PrefillJourney 是 journey_service.prefill 的循环体（:174-187）落库那一段。
//
// **为什么在一个事务里**：Python 侧这六组「查重⇒改/插⇒可能要 skip」跑在同一
// 请求事务里，由 CommitBeforeResponseRoute 在响应发出前一次提交；任何一步抛异常
// 就整批回滚。逐条 autocommit 会留下「前三行已写、后三行失败」的状态，
// 而 Python 那边是「一行都没有」——下一轮读取两侧就给出不同字节，灰度期表现为
// 同一用户切流前后剧本行数不同。原子性是这里唯一能对齐的那半。
//
// skip 的判据是 `event.status == "pending"`，其中 status 是**这一行本次落库前**的状态
// （既有行不被 schedule 改），所以要先读回 status 再决定，新插入的行恒为 pending。
func (p *PG) PrefillJourney(ctx context.Context, userID string,
	rows []domain.JourneyPrefill) error {
	if len(rows) == 0 {
		return nil
	}
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }() // Commit 之后 Rollback 返回 ErrTxDone，忽略

	for _, row := range rows {
		var id, status string
		err := tx.QueryRow(ctx, selectJourneyKindDay, userID, row.Kind, row.DayNo).
			Scan(&id, &status)
		switch {
		case errors.Is(err, pgx.ErrNoRows):
			id = idgen.New()
			if _, err := tx.Exec(ctx, insertJourneyEvent, id, userID, row.Kind, row.DayNo,
				row.ScheduledAt, row.Payload); err != nil {
				return err
			}
			status = "pending"
		case err != nil:
			return err
		default:
			if _, err := tx.Exec(ctx, updateJourneySchedule, id, row.ScheduledAt,
				row.Payload); err != nil {
				return err
			}
		}
		if row.Past && status == "pending" {
			if _, err := tx.Exec(ctx, skipJourneyEvent, id); err != nil {
				return err
			}
		}
	}
	return tx.Commit(ctx)
}

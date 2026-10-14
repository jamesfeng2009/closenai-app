// N8 批 4 的 legacy 表读写面（onboarding 域）。
//
// SQL 是三条 Python 路的 1:1 翻译：
//
//	user_repository.get_user / ensure_referral_code（:76-89、:363-389）
//	onboarding_repository.get / upsert（:19-52）
//	journey_repository.schedule / skip（批 3 已搬，这里复用同一组 SQL 常量）
//
// **一处事务、三处写**：Python 的 submit 用的是同一个 AsyncSession，由
// CommitBeforeResponseRoute 在响应前一次提交；任一步抛异常整批回滚。拆成三个
// autocommit 会留下「推荐码已写、profile 没写」的中间态，而 Python 那边是一行都没有，
// 灰度期同一个账号就会在两侧看到不同的 profile 行数。原子性是这里唯一能对齐的那半
// （口径与批 3 的 PrefillJourney 同一条）。
//
// PrefillJourney（legacy_batch3.go:216）的函数体**一字未动**：它是变异锚点 #90/#110 的
// 命中目标。所以这里另写一支 prefillJourneyTx（只收 pgx.Tx），循环体重复一遍是刻意的。
package store

import (
	"context"
	"crypto/rand"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/idgen"
)

// 推荐码的格式与随机源逐字对应 backend/core/referral.py:13-21。
// 29 字符集（去掉易混的 0/1/8/9 与 I/L/O）× 8 位 ≈ 2^38 空间。
const (
	referralAlphabet = "ABCDEFGHJKMNPQRSTUVWXYZ234567"
	referralCodeLen  = 8
	// referralRetry 是 user_repository.py:25 的 _REFERRAL_RETRY。
	referralRetry = 5
)

// errReferralGeneration 对应 :387-389 的 AppException(INTERNAL_ERROR, "推荐码生成失败…")。
// 连撞 5 次只可能是随机源坏了；两侧都不出 200，所以 handler 不必区分它和别的库错误。
var errReferralGeneration = errors.New("store: referral code generation failed")

// newReferralCode 是 `"".join(secrets.choice(ALPHABET) for _ in range(8))`。
//
// 取字节用**拒绝采样**而不是 `byte % 29`：29 不整除 256，直接取模会让前 256 mod 29 个
// 字符概率略高，而 Python 的 secrets.choice 内部是 `randbelow`（无偏）。
// 码值本身不进任何可逐字比对的响应面（随机），但对齐分布是「两侧今天/明天都发给
// 同一批人」这件事的前提，所以照 Python 来。
func newReferralCode() (string, error) {
	const alphabetLen = len(referralAlphabet) // len 的返回类型是 int，不是无类型常量
	const limit = 256 - 256%alphabetLen       // 只接受 [0, limit) 的字节，去掉尾部偏斜
	out := make([]byte, referralCodeLen)
	var raw [1]byte
	for i := range out {
		for {
			if _, err := rand.Read(raw[:]); err != nil {
				return "", err
			}
			if v := int(raw[0]); v < limit {
				out[i] = referralAlphabet[v%alphabetLen]
				break
			}
		}
	}
	return string(out), nil
}

// selectOnboardingUser 比批 3 的 selectJourneyUser 多一列 referral_code：
// submit 要拿它判「有没有码」，而 ensure_prefilled 不要。列原文（含 NULL）都要搬，
// 因为 Python 的判据是 `if user.referral_code:`（:373），NULL 与空串同一条分支。
const selectOnboardingUser = `
	SELECT timezone, language, push_time, created_at, referral_code
	FROM users
	WHERE id = $1 AND status = 'active'`

// selectReferralTaken 是查重那一条 SELECT（:378-380）。
// **不限 status**：已注销用户占过的码不能复用，否则深链会指向错的人（:377 的注释）。
const selectReferralTaken = `
	SELECT 1 FROM users WHERE referral_code = $1 LIMIT 1`

// updateReferralCode 对应 `user.referral_code = code; await db.flush()`：
// SQLAlchemy 的脏跟踪只写变化的那一列，谓词是主键。
const updateReferralCode = `
	UPDATE users SET referral_code = $2 WHERE id = $1`

// selectProfileIDForUser 是 upsert 里 `self.get(db, user_id)` 用到的那一列（有无行＋主键）。
// 投影按调用点收窄（同批 3 的 selectOnboardingBase 口径）：Python select 的是整个实体。
const selectProfileIDForUser = `
	SELECT id FROM onboarding_profiles WHERE user_id = $1 LIMIT 1`

// insertOnboardingProfile 的 created_at/updated_at **不写值**：两列都是
// server_default=func.now()（models/journey.py:90-95），SQLAlchemy 的 INSERT 也把它们留着
// 交给库默认。persona_type 传 nil ⇒ NULL（Python 那边是 None）。
// language 传的是**请求原文**，空串就是空串，不是 NULL。
const insertOnboardingProfile = `
	INSERT INTO onboarding_profiles
	    (id, user_id, persona_type, answers, first_read, language)
	VALUES ($1, $2, $3, $4, $5, $6)`

// updateOnboardingProfile 带上 `updated_at = now()`：模型声明了 onupdate=func.now()，
// SQLAlchemy 会把这一列拼进 UPDATE 的 SET 列表。
//
// **已登记发散**（不是本批缺陷）：Python 侧只在「值真的变了」时才发这条 UPDATE
// （SQLAlchemy 脏跟踪），所以「同一份 answers 连提两次」时第二次不发 UPDATE、
// updated_at 保持旧值；Go 这里无条件发，updated_at 会往前推。该列不进任何响应面
// （restore 只回 created_at），且它的值本来就是写入时刻，跨侧无法逐字比对，
// 所以按「不可比对的列」登记，不为它加一次全列比较。
const updateOnboardingProfile = `
	UPDATE onboarding_profiles
	SET persona_type = $2, answers = $3, first_read = $4, language = $5, updated_at = now()
	WHERE id = $1`

// selectOnboardingProfile 是 restore 用到的四列（onboarding_repository.get，:19-23）。
// first_read/language/created_at 三列都可空，NULL 与「空串/零值」在 JSON 面上不同形，
// 所以全部扫进指针（判空口径见 httpapi/onboarding_read.go）。
const selectOnboardingProfile = `
	SELECT id, first_read, language, created_at
	FROM onboarding_profiles
	WHERE user_id = $1
	LIMIT 1`

func (p *PG) OnboardingProfile(ctx context.Context, userID string) (domain.OnboardingProfile, error) {
	var out domain.OnboardingProfile
	var firstRead, language *string
	var createdAt *time.Time
	err := p.pool.QueryRow(ctx, selectOnboardingProfile, userID).
		Scan(&out.ID, &firstRead, &language, &createdAt)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return out, nil
		}
		return out, err
	}
	out.Found = true
	if firstRead != nil {
		out.FirstRead = *firstRead
	}
	if language != nil {
		out.Language = *language
		out.HasLanguage = true
	}
	if createdAt != nil {
		out.CreatedAt = *createdAt
		out.HasCreatedAt = true
	}
	return out, nil
}

// SubmitOnboarding 在一个事务里落三处写（推荐码、profile、六行剧本），返回 profile.id。
//
// 顺序照抄 submit（:182-195）：读账号 ⇒ 定码 ⇒ build（卡片文本依赖码）⇒ upsert profile
// ⇒ prefill。**build 在码定下来之后调用**，所以 handler 拿到的 share_url 与列里那个必然同源。
//
// 三处写的条件不同源，这里一格都不能合并：
//   - 推荐码：`if user is not None`（:183）且 `if user.referral_code` 为空才生成（:373）
//   - profile：**无条件** upsert（:189，不在 if 块里）
//   - 剧本：`if user is not None`（:194）
func (p *PG) SubmitOnboarding(ctx context.Context, userID string,
	build domain.OnboardingSubmitBuilder) (string, error) {
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return "", err
	}
	defer func() { _ = tx.Rollback(ctx) }() // Commit 之后 Rollback 返回 ErrTxDone，忽略

	user, code, err := loadOnboardingUser(ctx, tx, userID)
	if err != nil {
		return "", err
	}
	if user.Found && code == "" {
		// 惰性生成：随机 ⇒ 查重 ⇒ UPDATE，最多 referralRetry 轮（:375-385）。
		// 撞码就换下一个随机值重来，**不**在事务里回滚任何东西。
		generated, genErr := generateReferralCode(ctx, tx, userID)
		if genErr != nil {
			return "", genErr
		}
		code = generated
	}

	write, err := build(domain.OnboardingSubmitInput{User: user, Code: code})
	if err != nil {
		return "", err
	}

	profileID, err := upsertOnboardingProfile(ctx, tx, userID, write)
	if err != nil {
		return "", err
	}
	if user.Found {
		if err := prefillJourneyTx(ctx, tx, userID, write.JourneyRows); err != nil {
			return "", err
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return "", err
	}
	return profileID, nil
}

// loadOnboardingUser 读账号列＋码列原文。Found=false 覆盖两种情形：行不存在、
// status≠'active'（get_user 的谓词，:83）——Python 两种都得到 None，于是三处写里
// 只剩 profile 那一处。
func loadOnboardingUser(ctx context.Context, tx pgx.Tx,
	userID string) (domain.JourneyUser, string, error) {
	var out domain.JourneyUser
	var tz, lang, push, code *string
	var created *time.Time
	err := tx.QueryRow(ctx, selectOnboardingUser, userID).
		Scan(&tz, &lang, &push, &created, &code)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return out, "", nil
		}
		return out, "", err
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
	if code != nil {
		return out, *code, nil
	}
	return out, "", nil
}

// generateReferralCode 是 ensure_referral_code 的循环体：撞码就换下一个，
// 5 轮都没空位才失败（Python 抛 AppException，Go 返回错误；两条都不出 200）。
//
// 「查重通过 ⇒ UPDATE」之间没有行锁，两个并发请求可能同时认定同一个空位；
// Python 侧靠 referral_code 的 UNIQUE 索引在提交时拒掉一笔（:368-371 的注释写明
// 「宁可导出失败也不发重码」）。Go 这里同形：UPDATE 撞唯一约束就整事务回滚。
func generateReferralCode(ctx context.Context, tx pgx.Tx, userID string) (string, error) {
	for attempt := 0; attempt < referralRetry; attempt++ {
		candidate, err := newReferralCode()
		if err != nil {
			return "", err
		}
		var one int
		err = tx.QueryRow(ctx, selectReferralTaken, candidate).Scan(&one)
		if errors.Is(err, pgx.ErrNoRows) {
			if _, execErr := tx.Exec(ctx, updateReferralCode, userID, candidate); execErr != nil {
				return "", execErr
			}
			return candidate, nil
		}
		if err != nil {
			return "", err
		}
		// 查到行 ⇒ 码被占，换下一个随机值。
	}
	return "", errReferralGeneration
}

// upsertOnboardingProfile 是 repository.upsert 的 SELECT-then-INSERT/UPDATE（:35-51）。
// 新行的 id 是 str(uuid4())，与批 3 的 journey_events 主键同一个生成器（互认基线）。
func upsertOnboardingProfile(ctx context.Context, tx pgx.Tx,
	userID string, write domain.OnboardingWrite) (string, error) {
	var persona any
	if write.PersonaType != "" {
		persona = write.PersonaType
	}
	var existingID string
	err := tx.QueryRow(ctx, selectProfileIDForUser, userID).Scan(&existingID)
	switch {
	case errors.Is(err, pgx.ErrNoRows):
		newID := idgen.New()
		if _, execErr := tx.Exec(ctx, insertOnboardingProfile, newID, userID, persona,
			write.AnswersText, write.FirstReadText, write.Language); execErr != nil {
			return "", execErr
		}
		return newID, nil
	case err != nil:
		return "", err
	default:
		if _, execErr := tx.Exec(ctx, updateOnboardingProfile, existingID, persona,
			write.AnswersText, write.FirstReadText, write.Language); execErr != nil {
			return "", execErr
		}
		return existingID, nil
	}
}

// prefillJourneyTx 是 PrefillJourney 的**事务内**版本：同一个循环体，
// 但执行器由调用方的 tx 提供（因为 profile 与推荐码要一起提交）。
// 循环体的判据一条都不许改：`day0+offset < today` 且**落库前** status=='pending' 才 skip。
//
// 为什么不抽公共函数：PrefillJourney 的函数体是变异锚点 #90/#110 的命中目标，
// 搬空它就等于把批 3 已证过的两条闸拆掉。重复这十几行是刻意的（口径同 pybody.go 头注释）。
func prefillJourneyTx(ctx context.Context, tx pgx.Tx, userID string,
	rows []domain.JourneyPrefill) error {
	for _, row := range rows {
		var id, status string
		err := tx.QueryRow(ctx, selectJourneyKindDay, userID, row.Kind, row.DayNo).
			Scan(&id, &status)
		switch {
		case errors.Is(err, pgx.ErrNoRows):
			id = idgen.New()
			if _, execErr := tx.Exec(ctx, insertJourneyEvent, id, userID, row.Kind, row.DayNo,
				row.ScheduledAt, row.Payload); execErr != nil {
				return execErr
			}
			status = "pending"
		case err != nil:
			return err
		default:
			if _, execErr := tx.Exec(ctx, updateJourneySchedule, id, row.ScheduledAt,
				row.Payload); execErr != nil {
				return execErr
			}
		}
		if row.Past && status == "pending" {
			if _, execErr := tx.Exec(ctx, skipJourneyEvent, id); execErr != nil {
				return execErr
			}
		}
	}
	return nil
}

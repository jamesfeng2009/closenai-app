// N8 批 4 的 onboarding 存储面：真库集成测。
//
// httpapi 那边已经有假件测（onboarding_test.go）钉住响应形状与三处写的条件分支，
// 这一份只管**只有真库能答**的四类：
//  1. **一个事务真的是一个事务**：build 回调报错／六行里的某一行插不进去时，
//     推荐码与 profile 必须一行都不留。Python 用的是同一个 AsyncSession，
//     拆成三条 autocommit 的写法会留下「有码没卡」的中间态——响应字节要等下一轮才露出来，
//     而这里当场翻红。
//  2. **upsert 落在同一行**：第二次提交必须 UPDATE 原行（profile.id 不变、行数仍为 1）。
//     假件测里 upsert 只是调用名，主键与行数只有库知道。
//  3. **推荐码的惰性生成与复用**：列里为空 ⇒ 生成并落库、形状合 referral.py 的字符集；
//     已有值 ⇒ 原样复用且**不再** UPDATE（这是 share_url 在灰度期两侧同源的前提）。
//  4. **列的可空性**：onboarding_profiles 除 id/user_id 外五列都可空
//     （2026-10-15 在真库 information_schema 实测），所以 restore 读到的
//     language/created_at 确实可能是 NULL ⇒ Has* 位不是冗余。
//
// 未设 TEST_DATABASE_URL 时跳过，与其余 store 集成测同口径。
package store

import (
	"context"
	"errors"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

const batch4Prefix = "n8b4s-"

// batch4CodeRE 与 backend/core/referral.py:16 的 REFERRAL_CODE_RE 同形
// （字符集去掉 0/1/8/9 与 I/L/O）。跨侧那条同源闸在 test_n8_batch4_contract.py。
var batch4CodeRE = regexp.MustCompile(`^[ABCDEFGHJKMNPQRSTUVWXYZ234567]{8}$`)

func batch4Clean(t *testing.T, pool *pgxpool.Pool) {
	t.Helper()
	ctx := context.Background()
	for _, q := range []string{
		`DELETE FROM journey_events WHERE user_id LIKE $1`,
		`DELETE FROM onboarding_profiles WHERE user_id LIKE $1`,
		`DELETE FROM users WHERE id LIKE $1`,
	} {
		if _, err := pool.Exec(ctx, q, batch4Prefix+"%"); err != nil {
			t.Fatalf("clean %s: %v", q, err)
		}
	}
}

func batch4Pool(t *testing.T) (*PG, *pgxpool.Pool, context.Context) {
	t.Helper()
	pool := poolForTest(t)
	batch4Clean(t, pool)
	t.Cleanup(func() { batch4Clean(t, pool) })
	return NewPG(pool), pool, context.Background()
}

func batch4SeedUser(t *testing.T, pool *pgxpool.Pool, id, status string,
	code *string) {
	t.Helper()
	created := time.Date(2026, 10, 10, 6, 30, 0, 0, time.UTC)
	_, err := pool.Exec(context.Background(), `
		INSERT INTO users (id, timezone, streak_count, relationship_level, chat_count,
			daily_quota_used, tier, language, analytics_consent, ai_consent, status,
			push_time, created_at, referral_code)
		VALUES ($1, 'UTC', 0, 'L1', 0, 0, 'free', 'en', false, true, $2, '09:00', $3, $4)`,
		id, status, created, code)
	if err != nil {
		t.Fatalf("seed user %s: %v", id, err)
	}
}

// batch4Write 是 handler 会交给 store 的那三处文本（假件里同形，这里不必过 HTTP 层）。
func batch4Write() domain.OnboardingWrite {
	return domain.OnboardingWrite{
		PersonaType:   "INTJ",
		AnswersText:   `[{"q": "conflict", "option": "speak_now"}]`,
		FirstReadText: `{"assertions": [], "card_payload": {"share_url": "https://x"}}`,
		Language:      "zh-CN",
	}
}

func batch4Plan(userID string) []domain.JourneyPrefill {
	user := domain.JourneyUser{Found: true, ID: userID, Timezone: "UTC",
		Language: "en", PushTimeRaw: "09:00"}
	now := time.Date(2026, 10, 14, 14, 45, 30, 0, time.UTC)
	rows := domain.JourneyPrefillPlan(user, nil, now)
	out := make([]domain.JourneyPrefill, 0, len(rows))
	for _, row := range rows {
		out = append(out, domain.JourneyPrefill{Kind: row.Kind, DayNo: row.DayNo,
			ScheduledAt: row.ScheduledAt, Past: row.Past,
			Payload: `{"kind": "x"}`})
	}
	return out
}

func batch4Builder(rows []domain.JourneyPrefill, err error) domain.OnboardingSubmitBuilder {
	return func(domain.OnboardingSubmitInput) (domain.OnboardingWrite, error) {
		if err != nil {
			return domain.OnboardingWrite{}, err
		}
		w := batch4Write()
		w.JourneyRows = rows
		return w, nil
	}
}

func batch4ProfileRowCount(t *testing.T, pool *pgxpool.Pool, userID string) int {
	t.Helper()
	var n int
	if err := pool.QueryRow(context.Background(),
		`SELECT count(*) FROM onboarding_profiles WHERE user_id = $1`, userID).Scan(&n); err != nil {
		t.Fatalf("count profiles: %v", err)
	}
	return n
}

func batch4JourneyCount(t *testing.T, pool *pgxpool.Pool, userID string) int {
	t.Helper()
	var n int
	if err := pool.QueryRow(context.Background(),
		`SELECT count(*) FROM journey_events WHERE user_id = $1`, userID).Scan(&n); err != nil {
		t.Fatalf("count events: %v", err)
	}
	return n
}

func batch4ReferralCode(t *testing.T, pool *pgxpool.Pool, userID string) *string {
	t.Helper()
	var code *string
	if err := pool.QueryRow(context.Background(),
		`SELECT referral_code FROM users WHERE id = $1`, userID).Scan(&code); err != nil {
		t.Fatalf("read referral_code: %v", err)
	}
	return code
}

// TestSubmitOnboardingWritesThreeAndIsIdempotent 是本批的主干：
// 一次提交写满三处，第二次提交**不新增行**、profile.id 不变、码不变。
func TestSubmitOnboardingWritesThreeAndIsIdempotent(t *testing.T) {
	pg, pool, ctx := batch4Pool(t)
	userID := batch4Prefix + "full"
	batch4SeedUser(t, pool, userID, "active", nil)

	if got := batch4ReferralCode(t, pool, userID); got != nil {
		t.Fatalf("种子账号该没有码，读到 %q", *got)
	}
	firstID, err := pg.SubmitOnboarding(ctx, userID, batch4Builder(batch4Plan(userID), nil))
	if err != nil {
		t.Fatalf("SubmitOnboarding: %v", err)
	}
	code := batch4ReferralCode(t, pool, userID)
	if code == nil || !batch4CodeRE.MatchString(*code) {
		t.Fatalf("推荐码没生成或形状不对：%v", code)
	}
	if n := batch4ProfileRowCount(t, pool, userID); n != 1 {
		t.Errorf("profile 行数 = %d，期望 1", n)
	}
	if n := batch4JourneyCount(t, pool, userID); n != len(domain.ScriptEvents) {
		t.Errorf("剧本行数 = %d，期望 %d", n, len(domain.ScriptEvents))
	}
	// 一行的 status 都还是 pending：day0=今天 ⇒ 没有 Past 行 ⇒ 不该被 skip。
	var skipped int
	if err := pool.QueryRow(ctx,
		`SELECT count(*) FROM journey_events WHERE user_id = $1 AND status <> 'pending'`,
		userID).Scan(&skipped); err != nil {
		t.Fatalf("count skipped: %v", err)
	}
	if skipped != 0 {
		t.Errorf("POST 当天有 %d 行被置成非 pending", skipped)
	}

	// 第二次：列里已有码 ⇒ 复用（不 UPDATE），profile 原地覆盖。
	secondID, err := pg.SubmitOnboarding(ctx, userID, batch4Builder(batch4Plan(userID), nil))
	if err != nil {
		t.Fatalf("第二次 SubmitOnboarding: %v", err)
	}
	if secondID != firstID {
		t.Errorf("第二次拿到新的 profile.id（%s → %s）：upsert 走成了 INSERT", firstID, secondID)
	}
	if again := batch4ReferralCode(t, pool, userID); again == nil || *again != *code {
		t.Errorf("已有码被换掉：%v → %v", code, again)
	}
	if n := batch4ProfileRowCount(t, pool, userID); n != 1 {
		t.Errorf("重复提交后 profile 行数 = %d，期望仍为 1", n)
	}
	if n := batch4JourneyCount(t, pool, userID); n != len(domain.ScriptEvents) {
		t.Errorf("重复提交后剧本行数 = %d，期望仍为 %d", n, len(domain.ScriptEvents))
	}
	// language 落的是**请求原文**（不是 locale_for 折出来的 zh/en）：
	// 「重进 App 按生成当时的语言复现」靠的就是这一列不被归一。
	var lang string
	if err := pool.QueryRow(ctx,
		`SELECT language FROM onboarding_profiles WHERE user_id = $1`, userID).
		Scan(&lang); err != nil {
		t.Fatalf("read language: %v", err)
	}
	if lang != "zh-CN" {
		t.Errorf("language = %q，期望请求原文 zh-CN", lang)
	}
}

// TestSubmitOnboardingInactiveUserStillWritesProfile 钉「三处写条件不同源」的真库面：
// status≠active 的账号读不到（Found=false）⇒ 不生成码、不写剧本，但 profile 照写。
func TestSubmitOnboardingInactiveUserStillWritesProfile(t *testing.T) {
	pg, pool, ctx := batch4Pool(t)
	userID := batch4Prefix + "gone"
	batch4SeedUser(t, pool, userID, "deleted", nil)

	if _, err := pg.SubmitOnboarding(ctx, userID, batch4Builder(batch4Plan(userID), nil)); err != nil {
		t.Fatalf("SubmitOnboarding: %v", err)
	}
	if n := batch4ProfileRowCount(t, pool, userID); n != 1 {
		t.Errorf("profile 行数 = %d，期望 1（Python 的 upsert 不在 if user 块里）", n)
	}
	if n := batch4JourneyCount(t, pool, userID); n != 0 {
		t.Errorf("剧本行数 = %d，期望 0", n)
	}
	if got := batch4ReferralCode(t, pool, userID); got != nil {
		t.Errorf("注销账号拿到了推荐码：%q", *got)
	}
	// 不存在的 id 同形：Python 那边 get_user 返回 None，两条分支同一条路。
	missing := batch4Prefix + "no-such"
	if _, err := pg.SubmitOnboarding(ctx, missing, batch4Builder(nil, nil)); err != nil {
		t.Fatalf("SubmitOnboarding(不存在的账号): %v", err)
	}
	if n := batch4ProfileRowCount(t, pool, missing); n != 1 {
		t.Errorf("不存在账号的 profile 行数 = %d，期望 1", n)
	}
}

// TestSubmitOnboardingRollsBackEverything 是本批唯一能证明「一个事务」的闸：
// 推荐码已经 UPDATE 进 users，随后 build 报错 ⇒ 码必须回到 NULL。
// 逐条 autocommit 的写法在这里翻红（灰度期表现为「有码没卡」）。
func TestSubmitOnboardingRollsBackEverything(t *testing.T) {
	pg, pool, ctx := batch4Pool(t)
	userID := batch4Prefix + "rollback"
	batch4SeedUser(t, pool, userID, "active", nil)

	if _, err := pg.SubmitOnboarding(ctx, userID,
		batch4Builder(nil, errors.New("build failed"))); err == nil {
		t.Fatal("build 报错却提交成功了")
	}
	if got := batch4ReferralCode(t, pool, userID); got != nil {
		t.Errorf("回滚后推荐码留下了：%q（Python 那边这三处写在同一个 session 里）", *got)
	}
	if n := batch4ProfileRowCount(t, pool, userID); n != 0 {
		t.Errorf("回滚后 profile 行数 = %d，期望 0", n)
	}

	// 第二条打断点：让第 6 行插不进去（day_no 越界，真库实测会抛 integer out of range），
	// 前 5 行与 profile、码都必须一起回滚。
	rows := batch4Plan(userID)
	rows[len(rows)-1].DayNo = 1 << 31
	if _, err := pg.SubmitOnboarding(ctx, userID, batch4Builder(rows, nil)); err == nil {
		t.Fatal("day_no 越界却提交成功了")
	}
	if n := batch4JourneyCount(t, pool, userID); n != 0 {
		t.Errorf("打断第 6 行后剧本行数 = %d，期望 0（前 5 行没跟着回滚）", n)
	}
	if n := batch4ProfileRowCount(t, pool, userID); n != 0 {
		t.Errorf("打断第 6 行后 profile 行数 = %d，期望 0", n)
	}
	if got := batch4ReferralCode(t, pool, userID); got != nil {
		t.Errorf("打断第 6 行后推荐码留下了：%q", *got)
	}
}

// TestSubmitOnboardingReusesExistingCode 钉「已有码原样复用」在真库上成立：
// 这是 share_url 在两侧、两次请求之间逐字相同的前提——码若换，深链归因就断。
func TestSubmitOnboardingReusesExistingCode(t *testing.T) {
	pg, pool, ctx := batch4Pool(t)
	userID := batch4Prefix + "hascode"
	// 这个字面量**不能**与夹具里那四个（QQ22WW33 / ZZ22XX33 / AB2CD3EF / TT55KK77）重合：
	// `go test ./...` 会并行跑 httpapi 与 store 两个包，前者按 n8b1- 前缀把夹具种子灌进
	// 同一台 dev 库，而 uq_users_referral_code 是全表唯一、不按前缀隔离 ⇒ 同串必撞。
	// 判据要证的只是「列里已有值 ⇒ 原样复用」，与具体码无关，所以这里只换个不重命的形状。
	existing := "Z4R7VJ2K"
	batch4SeedUser(t, pool, userID, "active", &existing)

	if _, err := pg.SubmitOnboarding(ctx, userID, batch4Builder(batch4Plan(userID), nil)); err != nil {
		t.Fatalf("SubmitOnboarding: %v", err)
	}
	if got := batch4ReferralCode(t, pool, userID); got == nil || *got != existing {
		t.Errorf("已有码 %q 被换成 %v", existing, got)
	}
}

// TestOnboardingProfileColumnNullability 读回四列的三种形态：
// 完整行、language/first_read 为 NULL、以及根本没有行。
// （persona_type/answers/first_read/language/created_at 五列都可空，实测见本文件头。）
func TestOnboardingProfileColumnNullability(t *testing.T) {
	pg, pool, ctx := batch4Pool(t)
	full := batch4Prefix + "full-row"
	nulls := batch4Prefix + "null-row"
	for _, id := range []string{full, nulls} {
		batch4SeedUser(t, pool, id, "active", nil)
	}
	created := time.Date(2026, 10, 3, 1, 2, 3, 0, time.UTC)
	if _, err := pool.Exec(ctx, `
		INSERT INTO onboarding_profiles (id, user_id, persona_type, answers, first_read,
			language, created_at)
		VALUES ($1, $2, 'INTJ', '[]', $3, 'zh', $4)`,
		batch4Prefix+"p1", full, `{"card_payload": {}}`, created); err != nil {
		t.Fatalf("seed full profile: %v", err)
	}
	// 显式写 NULL 覆盖 server_default：restore 那边语言/时刻都会是 JSON null。
	if _, err := pool.Exec(ctx, `
		INSERT INTO onboarding_profiles (id, user_id, persona_type, answers, first_read,
			language, created_at)
		VALUES ($1, $2, NULL, NULL, NULL, NULL, NULL)`, batch4Prefix+"p2", nulls); err != nil {
		t.Fatalf("seed null profile: %v", err)
	}

	got, err := pg.OnboardingProfile(ctx, full)
	if err != nil || !got.Found || got.ID != batch4Prefix+"p1" ||
		got.FirstRead != `{"card_payload": {}}` || !got.HasLanguage || got.Language != "zh" ||
		!got.HasCreatedAt || !got.CreatedAt.Equal(created) {
		t.Errorf("完整行读成 %+v（err=%v）", got, err)
	}

	nullGot, err := pg.OnboardingProfile(ctx, nulls)
	if err != nil || !nullGot.Found {
		t.Fatalf("NULL 行：%+v err=%v", nullGot, err)
	}
	// first_read 为 NULL ⇒ 列原文折成空串，handler 据此走 `or "{}"` 那一支（200，不是 40402）。
	if nullGot.FirstRead != "" || nullGot.HasLanguage || nullGot.HasCreatedAt {
		t.Errorf("NULL 列读成 %+v，期望 FirstRead=\"\" 且两个 Has* 为 false", nullGot)
	}

	missing, err := pg.OnboardingProfile(ctx, batch4Prefix+"none")
	if err != nil || missing.Found {
		t.Errorf("无行应当 Found=false 且无错：%+v err=%v", missing, err)
	}
}

// TestSubmitOnboardingPayloadBytesStayVerbatim 确认库侧只搬文本、不做二次编码：
// CPython 的 json.dumps 默认分隔符（`", "`／`": "`）与全角标点必须逐字进列。
// 这一条是夹具回放能对上字节的前提。
func TestSubmitOnboardingPayloadBytesStayVerbatim(t *testing.T) {
	pg, pool, ctx := batch4Pool(t)
	userID := batch4Prefix + "bytes"
	batch4SeedUser(t, pool, userID, "active", nil)
	if _, err := pg.SubmitOnboarding(ctx, userID, batch4Builder(batch4Plan(userID), nil)); err != nil {
		t.Fatalf("SubmitOnboarding: %v", err)
	}
	var answers, firstRead string
	if err := pool.QueryRow(ctx,
		`SELECT answers, first_read FROM onboarding_profiles WHERE user_id = $1`, userID).
		Scan(&answers, &firstRead); err != nil {
		t.Fatalf("read columns: %v", err)
	}
	if answers != `[{"q": "conflict", "option": "speak_now"}]` {
		t.Errorf("answers 列被重新编码了：%s", answers)
	}
	if !strings.Contains(firstRead, `"share_url": "https://x"`) {
		t.Errorf("first_read 列的分隔符不是 CPython 默认形：%s", firstRead)
	}
}

// N8 批 3 的 journey 存储面：真库集成测。
//
// httpapi 那边已经有假件测（journey_test.go）钉住调用次序与响应形状，
// 这一份只管**只有真库能答**的四类：
//  1. **幂等预写到底改的是哪一行**：get_by_kind 命中 ⇒ UPDATE 原行（id 不变、
//     不新增行）；没命中 ⇒ INSERT。回放进两次若多出 6 行，现网表现为「同一用户的
//     剧本翻倍」，而假件测里 prefill 只是一串调用名。
//  2. **skip 的判据是落库前的 status**：Python 在 schedule() 之后拿的是**既有行**的
//     status（schedule 不改 status），已 sent 的行即使落在过去也**不能**被改成 skipped
//     ——那是把已送达的推送记录擦掉。这一条只在真库上可测，因为它跨了两条 SQL。
//  3. **整批回滚**：六组「查重⇒改/插⇒skip」在 Python 是同一请求事务，Go 也必须是一个
//     tx；用 day_no 溢出（integer 越界，真库实测会抛）打断第 6 行，
//     断言前 5 行**一行都不留**。逐条 autocommit 的写法在这里翻红，
//     而响应字节要等下一轮读取、且只有恰好撞上才会露出来。
//  4. **列原文**：push_time 的 NULL/”/'0' 三态、created_at 的有无、
//     onboarding_profiles.created_at 为 NULL 时按「没有基准」退回——
//     Python 用的是 `profile and profile.created_at`，不是「有行就用」。
//
// 未设 TEST_DATABASE_URL 时跳过，与其余 store 集成测同口径。
package store

import (
	"context"
	"fmt"
	"math"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

const batch3Prefix = "n8b3s-"

func batch3Clean(t *testing.T, pool *pgxpool.Pool) {
	t.Helper()
	ctx := context.Background()
	// 子表在前：journey_events.user_id 与 onboarding_profiles.user_id 都指向 users。
	for _, q := range []string{
		`DELETE FROM journey_events WHERE user_id LIKE $1`,
		`DELETE FROM onboarding_profiles WHERE user_id LIKE $1`,
		`DELETE FROM users WHERE id LIKE $1`,
	} {
		if _, err := pool.Exec(ctx, q, batch3Prefix+"%"); err != nil {
			t.Fatalf("clean %s: %v", q, err)
		}
	}
}

func batch3Pool(t *testing.T) (*PG, *pgxpool.Pool, context.Context) {
	t.Helper()
	pool := poolForTest(t)
	batch3Clean(t, pool)
	t.Cleanup(func() { batch3Clean(t, pool) })
	return NewPG(pool), pool, context.Background()
}

// batch3SeedUser 写 created_at 的三种形态（NULL / 给定值），
// 因为 ensure_prefilled 的「基准日从哪来」正取决于这一列有没有值。
func batch3SeedUser(t *testing.T, pool *pgxpool.Pool, id string, pushTime *string,
	createdAt *time.Time) {
	t.Helper()
	_, err := pool.Exec(context.Background(), `
		INSERT INTO users (id, timezone, streak_count, relationship_level, chat_count,
			daily_quota_used, tier, language, analytics_consent, ai_consent, status,
			push_time, created_at)
		VALUES ($1, 'UTC', 0, 'L1', 0, 0, 'free', 'en', false, true, 'active', $2, $3)`,
		id, pushTime, createdAt)
	if err != nil {
		t.Fatalf("seed user %s: %v", id, err)
	}
}

func batch3SeedProfile(t *testing.T, pool *pgxpool.Pool, userID string,
	createdAt *time.Time) {
	t.Helper()
	_, err := pool.Exec(context.Background(), `
		INSERT INTO onboarding_profiles (id, user_id, created_at)
		VALUES ($1, $2, $3)`, batch3Prefix+"p-"+userID[len(batch3Prefix):], userID, createdAt)
	if err != nil {
		t.Fatalf("seed profile for %s: %v", userID, err)
	}
}

// batch3Plan 用固定基准日造 Python prefill 的那六行（day0 已过 ⇒ 前三行 Past）。
func batch3Plan(base time.Time, userID string) []domain.JourneyPrefill {
	user := domain.JourneyUser{Found: true, ID: userID, Timezone: "UTC",
		Language: "en", PushTimeRaw: "09:00", CreatedAt: base, HasCreatedAt: true}
	rows := domain.JourneyPrefillPlan(user, &base, base.AddDate(0, 0, 4))
	out := make([]domain.JourneyPrefill, 0, len(rows))
	for _, row := range rows {
		out = append(out, domain.JourneyPrefill{Kind: row.Kind, DayNo: row.DayNo,
			ScheduledAt: row.ScheduledAt, Past: row.Past,
			Payload: fmt.Sprintf(`{"kind": %q, "day_no": %d, "language": "en"}`,
				row.Kind, row.DayNo)})
	}
	return out
}

func batch3RowCount(t *testing.T, pool *pgxpool.Pool, userID string) int {
	t.Helper()
	var n int
	if err := pool.QueryRow(context.Background(),
		`SELECT count(*) FROM journey_events WHERE user_id = $1`, userID).Scan(&n); err != nil {
		t.Fatalf("count: %v", err)
	}
	return n
}

func TestJourneyUserReadsColumnVerbatim(t *testing.T) {
	pg, pool, ctx := batch3Pool(t)
	created := time.Date(2026, 10, 1, 6, 30, 0, 0, time.UTC)
	batch3SeedUser(t, pool, batch3Prefix+"null-push", nil, &created)
	batch3SeedUser(t, pool, batch3Prefix+"empty-push", text(""), &created)
	batch3SeedUser(t, pool, batch3Prefix+"zero-push", text("0"), &created)

	for _, tc := range []struct {
		id   string
		push string
	}{
		// NULL 与 '' 在 Python 的 `or "09:00"` 里同侧，但列原文必须分别是 "" 与 ""，
		// 而 "0" 是**非空串**⇒走 int("0") 那一支（0 点），三者语义不能糊成一个。
		{batch3Prefix + "null-push", ""},
		{batch3Prefix + "empty-push", ""},
		{batch3Prefix + "zero-push", "0"},
	} {
		got, err := pg.JourneyUser(ctx, tc.id)
		if err != nil {
			t.Fatalf("JourneyUser(%s): %v", tc.id, err)
		}
		// created_at 在 DDL 里是 NOT NULL（2026-10-15 information_schema 实测四列的可空性：
		// 只有 push_time 可空）。所以 ensure_prefilled 的「两个基准日都没有」那条提前返回
		// 在真库上不可达——httpapi 的假件测仍钉着它，因为那是代码分支而不是数据分支。
		if !got.Found || got.PushTimeRaw != tc.push || !got.HasCreatedAt ||
			!got.CreatedAt.Equal(created) {
			t.Errorf("%s 读成 %+v（push=%q）", tc.id, got, tc.push)
		}
	}

	// status='deleted' 与「无此行」都必须 Found=false：Python 的 get_user 把 status
	// 写在 WHERE 里，读不到就 return None ⇒ 预写**静默跳过**，响应照旧 200。
	batch3SeedUser(t, pool, batch3Prefix+"gone", nil, &created)
	if _, err := pool.Exec(ctx, `UPDATE users SET status = 'deleted' WHERE id = $1`,
		batch3Prefix+"gone"); err != nil {
		t.Fatalf("delete user: %v", err)
	}
	for _, id := range []string{batch3Prefix + "gone", batch3Prefix + "no-such"} {
		got, err := pg.JourneyUser(ctx, id)
		if err != nil || got.Found {
			t.Errorf("%s：found=%v err=%v，必须是「无行 + 无错」", id, got.Found, err)
		}
	}
}

func TestOnboardingBaseAbsentNullAndPresent(t *testing.T) {
	pg, pool, ctx := batch3Pool(t)
	created := time.Date(2026, 10, 2, 1, 0, 0, 0, time.UTC)
	batch3SeedUser(t, pool, batch3Prefix+"no-profile", nil, &created)
	batch3SeedUser(t, pool, batch3Prefix+"null-base", nil, &created)
	batch3SeedProfile(t, pool, batch3Prefix+"null-base", nil)
	batch3SeedUser(t, pool, batch3Prefix+"has-base", nil, &created)
	base := time.Date(2026, 9, 28, 12, 0, 0, 0, time.UTC)
	batch3SeedProfile(t, pool, batch3Prefix+"has-base", &base)

	if got, ok, err := pg.OnboardingBase(ctx, batch3Prefix+"no-profile"); err != nil || ok {
		t.Errorf("无基准行：%v %v，期望 false+无错（Python 退回 users.created_at）", got, ok)
	}
	// 有行但 created_at 为 NULL：Python 的 `profile and profile.created_at` 落 falsy，
	// 与「没有行」同一支。这里若把零值 time 当基准日，剧本会全落在 1970 年。
	if got, ok, err := pg.OnboardingBase(ctx, batch3Prefix+"null-base"); err != nil || ok {
		t.Errorf("基准行为 NULL：%v %v err=%v，期望按「没有基准」", got, ok, err)
	}
	got, ok, err := pg.OnboardingBase(ctx, batch3Prefix+"has-base")
	if err != nil || !ok || !got.Equal(base) {
		t.Errorf("基准行：%v %v err=%v，期望 %s", got, ok, err, base)
	}
}

func TestPrefillJourneyIsIdempotentAndSkipsPast(t *testing.T) {
	pg, pool, ctx := batch3Pool(t)
	user := batch3Prefix + "idem"
	created := time.Date(2026, 10, 10, 3, 0, 0, 0, time.UTC)
	batch3SeedUser(t, pool, user, nil, &created)
	rows := batch3Plan(created, user)
	if len(rows) != 6 {
		t.Fatalf("预写行数 = %d，期望 6（剧本六条）", len(rows))
	}

	if err := pg.PrefillJourney(ctx, user, rows); err != nil {
		t.Fatalf("prefill 第一轮: %v", err)
	}
	first := batch3RowCount(t, pool, user)

	// 第二轮同一份计划：命中既有行走 UPDATE，id 不变、行数不涨。
	if err := pg.PrefillJourney(ctx, user, rows); err != nil {
		t.Fatalf("prefill 第二轮: %v", err)
	}
	if got := batch3RowCount(t, pool, user); got != first {
		t.Errorf("第二轮后行数 = %d，第一轮 = %d：幂等失效就是剧本翻倍", got, first)
	}

	var (
		ids         []string
		statuses    []string
		payloadText []string
	)
	queryRows, err := pool.Query(ctx, `
		SELECT id, status, payload FROM journey_events
		WHERE user_id = $1 ORDER BY day_no ASC, scheduled_at ASC`, user)
	if err != nil {
		t.Fatalf("读回: %v", err)
	}
	for queryRows.Next() {
		var id, status, payload string
		if err := queryRows.Scan(&id, &status, &payload); err != nil {
			t.Fatalf("scan: %v", err)
		}
		ids, statuses, payloadText = append(ids, id), append(statuses, status), append(payloadText, payload)
	}
	queryRows.Close()

	// day_no 0/1/2/3 的 scheduled_at 早于 today（now = 基准 + 4 天）⇒ Past ⇒ pending 被
	// 改成 skipped；5 和 7 还在未来，必须留在 pending。
	for i, want := range []string{"skipped", "skipped", "skipped", "skipped",
		"pending", "pending"} {
		if statuses[i] != want {
			t.Errorf("第 %d 行 status = %s，期望 %s（Past 是 day.Before(today)，不是 day_no<=3）",
				i, statuses[i], want)
		}
	}
	// 落进 payload 列的文本是 CPython **默认**分隔符（": " 与 ", "），
	// 与响应面的紧凑形不同：这一列两侧都会被对方读到，文本不一致下一轮预写就会比出差异。
	if want := `{"kind": "day0_read", "day_no": 0, "language": "en"}`; payloadText[0] != want {
		t.Errorf("payload 列 = %q，期望 %q", payloadText[0], want)
	}
	if len(ids) != 6 || ids[0] == "" {
		t.Fatalf("id 集合异常：%v", ids)
	}

	// 改基准日再跑一轮：UPDATE 命中的是同一批 id（不是删了重插）。
	shifted := created.AddDate(0, 0, 2)
	if err := pg.PrefillJourney(ctx, user, batch3Plan(shifted, user)); err != nil {
		t.Fatalf("prefill 改基准: %v", err)
	}
	var scheduled time.Time
	if err := pool.QueryRow(ctx, `SELECT scheduled_at FROM journey_events
		WHERE user_id = $1 AND event_kind = 'day7_report'`, user).Scan(&scheduled); err != nil {
		t.Fatalf("读 day7: %v", err)
	}
	// day7 = 基准 + 7 天 09:00；基准挪两天 ⇒ 这一行也必须跟着挪。
	want := time.Date(2026, 10, 19, 9, 0, 0, 0, time.UTC)
	if !scheduled.Equal(want) {
		t.Errorf("day7 scheduled_at = %s，期望 %s（UPDATE 支没写进去）", scheduled, want)
	}
	if got := batch3RowCount(t, pool, user); got != 6 {
		t.Errorf("改基准后行数 = %d，期望仍是 6", got)
	}
}

func TestPrefillJourneyNeverRewritesSentRow(t *testing.T) {
	pg, pool, ctx := batch3Pool(t)
	user := batch3Prefix + "sent"
	created := time.Date(2026, 10, 10, 3, 0, 0, 0, time.UTC)
	batch3SeedUser(t, pool, user, nil, &created)
	sentAt := time.Date(2026, 10, 10, 9, 0, 5, 0, time.UTC)
	if _, err := pool.Exec(ctx, `
		INSERT INTO journey_events (id, user_id, event_kind, day_no, status,
			scheduled_at, sent_at, payload)
		VALUES ($1, $2, 'day0_read', 0, 'sent', $3, $4, '{"old": 1}')`,
		batch3Prefix+"sent-row", user, created.AddDate(0, 0, 0), sentAt); err != nil {
		t.Fatalf("seed sent row: %v", err)
	}

	// day0 落在过去 ⇒ Past=true，但既有行 status 是 sent ⇒ skip 的那道门不得命中。
	if err := pg.PrefillJourney(ctx, user, batch3Plan(created, user)); err != nil {
		t.Fatalf("prefill: %v", err)
	}
	var (
		status    string
		scheduled time.Time
	)
	if err := pool.QueryRow(ctx, `SELECT status, scheduled_at FROM journey_events
		WHERE id = $1`, batch3Prefix+"sent-row").Scan(&status, &scheduled); err != nil {
		t.Fatalf("读回: %v", err)
	}
	if status != "sent" {
		t.Errorf("已送达的行被改成 %s：skip 的判据读成了「落库后的 status」", status)
	}
	var payload string
	if err := pool.QueryRow(ctx, `SELECT payload FROM journey_events WHERE id = $1`,
		batch3Prefix+"sent-row").Scan(&payload); err != nil {
		t.Fatalf("读 payload: %v", err)
	}
	if want := `{"kind": "day0_read", "day_no": 0, "language": "en"}`; payload != want {
		t.Errorf("payload = %q，期望被预写覆盖成 %q", payload, want)
	}
}

func TestPrefillJourneyRollsBackWholeBatch(t *testing.T) {
	pg, pool, ctx := batch3Pool(t)
	user := batch3Prefix + "rollback"
	created := time.Date(2026, 10, 10, 3, 0, 0, 0, time.UTC)
	batch3SeedUser(t, pool, user, nil, &created)

	// 第 7 行的 day_no 越界（integer ⇒ "integer out of range"，真库实测），
	// 前五行的 INSERT 已经在同一个 tx 里做过。
	rows := append(batch3Plan(created, user), domain.JourneyPrefill{
		Kind: "day9_report", DayNo: math.MaxInt32 + 1, ScheduledAt: created,
		Payload: `{"kind": "day9_report", "day_no": 0, "language": "en"}`})
	if err := pg.PrefillJourney(ctx, user, rows); err == nil {
		t.Fatalf("day_no 溢出必须报错")
	}
	if got := batch3RowCount(t, pool, user); got != 0 {
		t.Errorf("失败后残留 %d 行，期望 0：Python 的请求级事务是整批回滚，"+
			"逐条 autocommit 会让两侧下一轮读出不同字节", got)
	}
}

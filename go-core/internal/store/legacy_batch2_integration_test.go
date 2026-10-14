// N8 批 2 的 curriculum 存储读面：真库集成测。
//
// httpapi 那边已经有 28 条逐字节回放（testdata/n8_contract.json 的 curriculum 组），
// 它证的是**响应字节**；这一份证的是只有真库能答、而回放**结构上看不到**的四类：
//  1. 谓词的**开闭**：`scheduled_at >= start AND < end`。回录制与回放相隔跨过周一才会翻，
//     夹具把时钟冻在锚点上，所以两侧永远在同一周内——贴窗口的行要靠这里显式插。
//  2. LIMIT 50 截到哪一端：截断由 ORDER BY 决定，Python 取的是 scheduled_at 正序的前 50 条。
//     回放的种子里没有超过 50 条的用户，这一条只能在这里测。
//  3. 同格两行谁留下：`ORDER BY scheduled_at ASC` 一乱，被覆盖的行就换一条，
//     而 domain 层的「后写覆盖」在假数据上是看不出的。
//  4. status≠active 与「别人的事件」这两条边界在 SQL 层的**归属**：
//     前者必须在查询里（40401），后者必须**不在**查询里（403，见 selectJourneyEventByID）。
//
// 未设 TEST_DATABASE_URL 时跳过，与其余 store 集成测同口径。
package store

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

const (
	batch2Prefix = "n8b2s-"
	// 锚点固定：本文件不比日历，只比「给定窗口的行集合」，所以不需要当下时钟。
	batch2Monday = "2026-10-12T00:00:00+00:00"
)

func batch2SeedUser(t *testing.T, pool *pgxpool.Pool, id, timezone string,
	aiConsent bool, status string) {
	t.Helper()
	_, err := pool.Exec(context.Background(), `
		INSERT INTO users (id, timezone, streak_count, relationship_level, chat_count,
			daily_quota_used, tier, language, analytics_consent, ai_consent, status)
		VALUES ($1, $2, 0, 'L1', 0, 0, 'free', 'en', false, $3, $4)`,
		id, timezone, aiConsent, status)
	if err != nil {
		t.Fatalf("seed user %s: %v", id, err)
	}
}

func batch2SeedEvent(t *testing.T, pool *pgxpool.Pool, id, userID, kind, scheduledAt string,
	dayNo int, payload, refs *string) {
	t.Helper()
	_, err := pool.Exec(context.Background(), `
		INSERT INTO journey_events (id, user_id, event_kind, day_no, scheduled_at,
			payload, memory_refs)
		VALUES ($1, $2, $3, $4, $5::timestamptz, $6, $7)`,
		id, userID, kind, dayNo, scheduledAt, payload, refs)
	if err != nil {
		t.Fatalf("seed event %s: %v", id, err)
	}
}

// batch2Clean 前缀删：子表在前（journey_events.user_id 有外键指向 users）。
func batch2Clean(t *testing.T, pool *pgxpool.Pool) {
	t.Helper()
	ctx := context.Background()
	if _, err := pool.Exec(ctx, `DELETE FROM journey_events WHERE id LIKE $1`, batch2Prefix+"%"); err != nil {
		t.Fatalf("clean journey_events: %v", err)
	}
	if _, err := pool.Exec(ctx, `DELETE FROM users WHERE id LIKE $1`, batch2Prefix+"%"); err != nil {
		t.Fatalf("clean users: %v", err)
	}
}

func batch2Pool(t *testing.T) (*PG, *pgxpool.Pool, context.Context) {
	t.Helper()
	pool := poolForTest(t)
	batch2Clean(t, pool)
	t.Cleanup(func() { batch2Clean(t, pool) })
	return NewPG(pool), pool, context.Background()
}

func text(s string) *string { return &s }

func TestCurriculumUserStatusAndConsent(t *testing.T) {
	pg, pool, ctx := batch2Pool(t)
	batch2SeedUser(t, pool, batch2Prefix+"on", "Asia/Shanghai", true, "active")
	batch2SeedUser(t, pool, batch2Prefix+"off", "UTC", false, "active")
	batch2SeedUser(t, pool, batch2Prefix+"gone", "UTC", true, "deleted")

	got, err := pg.CurriculumUser(ctx, batch2Prefix+"on")
	if err != nil {
		t.Fatalf("CurriculumUser: %v", err)
	}
	// ID 是**参数**贴回去的（Python 侧 require_user 拿的是 ORM 行的 id，同值），
	// 但 Timezone/Language 必须来自列值：URL 里的 id 与库里的大小写不同也不能改列值。
	if !got.Found || got.ID != batch2Prefix+"on" || got.Timezone != "Asia/Shanghai" ||
		got.Language != "en" || !got.AIConsent {
		t.Errorf("同意用户读成 %+v", got)
	}

	off, err := pg.CurriculumUser(ctx, batch2Prefix+"off")
	if err != nil {
		t.Fatalf("CurriculumUser(off): %v", err)
	}
	if !off.Found || off.AIConsent {
		t.Errorf("未同意用户读成 %+v：AIConsent 读反会让 40303 门失效", off)
	}

	// status 谓词在**查询里**：已注销 ⇒ Found=false ⇒ 上层 40401，而不是 40303。
	// 两者换侧就是在给已注销用户回「请先同意 AI 条款」。
	gone, err := pg.CurriculumUser(ctx, batch2Prefix+"gone")
	if err != nil {
		t.Fatalf("CurriculumUser(gone): %v", err)
	}
	if gone.Found {
		t.Errorf("deleted 用户被判存在： %+v", gone)
	}

	missing, err := pg.CurriculumUser(ctx, batch2Prefix+"no-such-user")
	if err != nil || missing.Found {
		t.Errorf("不存在的用户： found=%v err=%v（必须是 无行+无错，由调用方出 40401）",
			missing.Found, err)
	}
}

func TestCurriculumCardsWindowIsHalfOpen(t *testing.T) {
	pg, pool, ctx := batch2Pool(t)
	user := batch2Prefix + "window"
	batch2SeedUser(t, pool, user, "UTC", true, "active")

	start := time.Date(2026, 10, 12, 0, 0, 0, 0, time.UTC)
	end := start.AddDate(0, 0, 7)

	// 左端点**含**、右端点**不含**；紧挨两端各插一行，偏移只差 1 微秒。
	batch2SeedEvent(t, pool, batch2Prefix+"w-start", user, "curriculum",
		batch2Monday, 0, text(`{"title":"起"}`), nil)
	batch2SeedEvent(t, pool, batch2Prefix+"w-before", user, "curriculum",
		"2026-10-11T23:59:59.999999+00:00", 6, text(`{"title":"窗口前"}`), nil)
	batch2SeedEvent(t, pool, batch2Prefix+"w-end", user, "curriculum",
		"2026-10-19T00:00:00+00:00", 0, text(`{"title":"止"}`), nil)
	batch2SeedEvent(t, pool, batch2Prefix+"w-inside", user, "curriculum",
		"2026-10-14T08:00:00+00:00", 2, text(`{"title":"中"}`), nil)
	// 别的 kind 与别人的行都必须被过滤掉。
	batch2SeedEvent(t, pool, batch2Prefix+"w-other-kind", user, "journey",
		"2026-10-14T09:00:00+00:00", 3, text(`{"title":"别的剧本"}`), nil)
	batch2SeedEvent(t, pool, batch2Prefix+"w-other-user", batch2Prefix+"off", "curriculum",
		"2026-10-14T10:00:00+00:00", 4, text(`{"title":"别人的卡"}`), nil)
	// 别的用户的行：user_id 谓词必须在查询里（这一条与上面不同向）。
	// 命中它会让 A 看到 B 的主题卡；而**事件表**那条恰好相反，见 TestJourneyEventOwner。

	cards, err := pg.CurriculumCards(ctx, user, domain.CurriculumKind, start, end,
		domain.CurriculumListLimit)
	if err != nil {
		t.Fatalf("CurriculumCards: %v", err)
	}
	ids := make([]string, 0, len(cards))
	for _, c := range cards {
		ids = append(ids, c.ID)
	}
	want := []string{batch2Prefix + "w-start", batch2Prefix + "w-inside"}
	if fmt.Sprint(ids) != fmt.Sprint(want) {
		t.Errorf("命中的行 = %v，期望 %v（起含、止不含、别的 kind/用户不进）", ids, want)
	}
	if len(cards) > 0 && cards[0].Payload != `{"title":"起"}` {
		t.Errorf("payload 搬运错了：%q", cards[0].Payload)
	}
	// NULL 的两列必须是空串而不是 panic：Python 侧那边 parse_json(None) 走默认值分支。
	if cards[0].MemoryRefs != "" {
		t.Errorf("memory_refs 为 NULL 时应为空串，实为 %q", cards[0].MemoryRefs)
	}
}

func TestCurriculumCardsOrderAndLimit(t *testing.T) {
	pg, pool, ctx := batch2Pool(t)
	user := batch2Prefix + "limit"
	batch2SeedUser(t, pool, user, "UTC", true, "active")

	start := time.Date(2026, 10, 12, 0, 0, 0, 0, time.UTC)
	end := start.AddDate(0, 0, 7)

	// 51 条同槽（day_no 恒 0）行，scheduled_at 每小时递增一条：
	// 正序 + LIMIT 50 ⇒ 前 50 条，最后一条被截掉。截断端点错一条，
	// 「后写覆盖先写」留下的就是另一张卡，而 domain 层的比较看不出任何异常。
	for i := 0; i < 51; i++ {
		batch2SeedEvent(t, pool, fmt.Sprintf("%srow-%02d", batch2Prefix, i), user, "curriculum",
			start.Add(time.Duration(i)*time.Hour).Format(time.RFC3339),
			0, text(fmt.Sprintf(`{"title":"第%d条"}`, i)), nil)
	}

	cards, err := pg.CurriculumCards(ctx, user, domain.CurriculumKind, start, end,
		domain.CurriculumListLimit)
	if err != nil {
		t.Fatalf("CurriculumCards: %v", err)
	}
	if len(cards) != domain.CurriculumListLimit {
		t.Fatalf("取回 %d 条，期望 %d", len(cards), domain.CurriculumListLimit)
	}
	if cards[0].ID != batch2Prefix+"row-00" || cards[49].ID != batch2Prefix+"row-49" {
		t.Errorf("截断端点错了：首=%s 尾=%s", cards[0].ID, cards[49].ID)
	}
	for i := 1; i < len(cards); i++ {
		if cards[i-1].DayNo != cards[i].DayNo {
			t.Fatalf("同槽前提破了：%s day_no=%d 而 %s day_no=%d",
				cards[i-1].ID, cards[i-1].DayNo, cards[i].ID, cards[i].DayNo)
		}
	}

	// 同一格两行：ASC 顺序把后写的那条放在**后面**，domain 层的 bySlot 覆盖才留下它。
	week := domain.BuildCurriculumWeek(cards, start, start.AddDate(0, 0, 2))
	if len(week.Days) != 1 || week.Days[0].ID != batch2Prefix+"row-49" {
		t.Errorf("同格覆盖结果 = %+v，期望 id=%s", week.Days, batch2Prefix+"row-49")
	}
}

func TestJourneyEventOwnerDoesNotFilterUser(t *testing.T) {
	pg, pool, ctx := batch2Pool(t)
	owner := batch2Prefix + "owner"
	batch2SeedUser(t, pool, owner, "UTC", true, "active")
	payload := `{"title":"T","read_at":null}`
	batch2SeedEvent(t, pool, batch2Prefix+"ev", owner, "curriculum",
		"2026-10-13T00:00:00+00:00", 1, text(payload), text(`["m1"]`))

	ev, err := pg.JourneyEventOwner(ctx, batch2Prefix+"ev")
	if err != nil {
		t.Fatalf("JourneyEventOwner: %v", err)
	}
	// 这一条读的是**别人的**行（本函数只插了 owner 一行，所以断言的是 owner）：
	// SQL 不过滤 user_id 是刻意的——换成带 user_id 的查询，「不存在」与「越权」
	// 就分成两个状态码，这个端点变成 id 枚举 oracle。
	if !ev.Found || ev.UserID != owner || ev.Kind != domain.CurriculumKind ||
		ev.Payload != payload {
		t.Errorf("事件读成 %+v", ev)
	}
	if ev.ID != batch2Prefix+"ev" {
		t.Errorf("id 应取列值，实为 %q", ev.ID)
	}

	missing, err := pg.JourneyEventOwner(ctx, batch2Prefix+"no-such-event")
	if err != nil || missing.Found {
		t.Errorf("无行必须 Found=false 且不报错：found=%v err=%v", missing.Found, err)
	}
}

func TestSetJourneyPayloadLeavesOtherColumns(t *testing.T) {
	pg, pool, ctx := batch2Pool(t)
	user := batch2Prefix + "writer"
	batch2SeedUser(t, pool, user, "UTC", true, "active")
	refs := `[{"memory_id":"m1"}]`
	batch2SeedEvent(t, pool, batch2Prefix+"write", user, "curriculum",
		"2026-10-13T00:00:00+00:00", 1, text(`{"title":"T","read_at":null}`), text(refs))

	// mark_read 写回的是**整份 payload 文本**，memory_refs 一个字都不许动
	// （Python 的 set_payload 只带 payload 参数时同样不碰 refs 列）。
	next := `{"title":"T","read_at":"2026-10-14T14:45:30+00:00"}`
	if err := pg.SetJourneyPayload(ctx, batch2Prefix+"write", next); err != nil {
		t.Fatalf("SetJourneyPayload: %v", err)
	}
	ev, err := pg.JourneyEventOwner(ctx, batch2Prefix+"write")
	if err != nil {
		t.Fatalf("回读: %v", err)
	}
	if ev.Payload != next {
		t.Errorf("payload = %q，期望 %q", ev.Payload, next)
	}
	// 其余列由一条裸 SELECT 核对（JourneyEventOwner 只取四列，证不了 refs/day_no/
	// scheduled_at 没被顺手写）：UPDATE 语句只有 payload 一个赋值，这里钉住它。
	var (
		gotRefs   *string
		gotDayNo  int
		gotSched  time.Time
		gotStatus string
		gotAck    bool
	)
	if err := pool.QueryRow(ctx, `
		SELECT memory_refs, day_no, scheduled_at, status, acknowledged
		FROM journey_events WHERE id = $1`, batch2Prefix+"write").
		Scan(&gotRefs, &gotDayNo, &gotSched, &gotStatus, &gotAck); err != nil {
		t.Fatalf("回读其余列: %v", err)
	}
	if gotRefs == nil || *gotRefs != refs {
		t.Errorf("memory_refs 被改写：%v", gotRefs)
	}
	if gotDayNo != 1 || !gotSched.Equal(time.Date(2026, 10, 13, 0, 0, 0, 0, time.UTC)) {
		t.Errorf("定位列被动了：day_no=%d scheduled_at=%s", gotDayNo, gotSched)
	}
	if gotStatus != "pending" || gotAck {
		t.Errorf("状态列被动了：status=%q acknowledged=%v", gotStatus, gotAck)
	}
	if ev.Kind != domain.CurriculumKind || ev.UserID != user {
		t.Errorf("其余列被动了：%+v", ev)
	}

	// 不存在的 id：UPDATE 影响 0 行，Python 侧那边同样静默（调用方已先判过越权）。
	if err := pg.SetJourneyPayload(ctx, batch2Prefix+"no-such-event", next); err != nil {
		t.Errorf("改不存在的行不应报错：%v", err)
	}
}

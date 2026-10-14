// N8 的 HTTP 逐字节回放（夹具的 cases 块）。
//
// 分两组是因为「需不需要库」决定了这两组能在哪里跑：
//   - validation 组全部 400，Python 侧一次 store 都没碰，所以 Go 侧也不许碰——
//     这里的 store 是「一被调用就把测试弄红」的假件，顺带证了这条依赖顺序本身。
//   - seeded / curriculum / journey 组真读库，必须用**同一个 TEST_DATABASE_URL**、
//     按夹具同一批行播种；后两组的时钟钉在夹具的 recorded_at（响应里有日历字段）。
//
// seeded 组按 days_ago **相对当下**播种（批 1 只有 7/14 天窗口、没有日历字段），
// journey 组的 users.created_at 按**绝对**时刻播种（剧本 day_no 由它推出）。
package httpapi

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/auth"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/config"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/dsn"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/posthog"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store"
)

// n8GeneratedAtRe 与生成脚本的 GENERATED_AT_RE 必须是同一条正则，两侧掩掉同一个字段、
// 其余字节一个都不许掩——否则「diff 为空」就成了空话。
var n8GeneratedAtRe = regexp.MustCompile(`"generated_at":"[^"]*"`)

const n8MaskedField = `"generated_at":"<generated_at>"`

func maskGeneratedAt(body string) string {
	return n8GeneratedAtRe.ReplaceAllString(body, n8MaskedField)
}

// n8EventIDRe 与生成脚本的 EVENT_ID_RE 也是同一条：只吃 `str(uuid4())` 形状的
// `"id":"…"`。它刻意不匹配 `"trace_id":"00000000-…"` 与 `"memory_id":"…"`——那两键前面
// 接的是下划线，不在 `"id":"` 这个前缀里。RE2 不支持变宽 lookbehind，所以两侧都只能
// 写成这种「连前缀一起替换」的形状。
var n8EventIDRe = regexp.MustCompile(
	`"id":"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"`)

const n8MaskedEventID = `"id":"<event_id>"`

// maskJourneyEventIDs 掩掉 uuid 形状主键，并把**次数**交回给调用方核对。
// 计数不是附属信息：GET /journey/events 会预写六行，第二次访问必须幂等（还是 6 行、
// 还是掩 6 次）。只看「掩完字节相等」的话，多插 6 行也会得到同样的 6 个 `<event_id>`。
func maskJourneyEventIDs(body string) (string, int) {
	var n int
	out := n8EventIDRe.ReplaceAllStringFunc(body, func(string) string {
		n++
		return n8MaskedEventID
	})
	return out, n
}

// ── 假件 ─────────────────────────────────────────────────────────────────────

// n8ForbiddenStore 给 validation 组用：读库即红。
type n8ForbiddenStore struct{ t *testing.T }

func (f n8ForbiddenStore) UserProfile(_ context.Context, userID string) (domain.UserProfile, error) {
	f.t.Errorf("validation 用例读了 users(%s)——Python 侧校验失败根本不进 store", userID)
	return domain.UserProfile{}, errors.New("n8: store must not be read")
}

func (f n8ForbiddenStore) MoodCheckins(_ context.Context, userID string, _ time.Time) ([]domain.Checkin, error) {
	f.t.Errorf("validation 用例读了 mood_checkins(%s)——Python 侧校验失败根本不进 store", userID)
	return nil, errors.New("n8: store must not be read")
}

// n8RecordingRelay 记下发出去的事件，但**绝不联网**（测试里不许打 posthog）。
//
// mutex 不是装饰：relayEvents 是并发派发的，没有锁时这里追加的是竞争写，
// 同一份夹具跑两次能得到 116 和 119 两个数——「外发条数＝响应声称的条数」这条
// 断言就成了随机闸门（实测 -run TestN8 交替红绿）。
type n8RecordingRelay struct {
	t    *testing.T
	mu   sync.Mutex
	seen []posthog.Event
}

func (r *n8RecordingRelay) Capture(_ context.Context, ev posthog.Event) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.seen = append(r.seen, ev)
	return nil
}

func (r *n8RecordingRelay) count() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.seen)
}

func newN8Server(t *testing.T, users UserProfileReader, moods MoodReader, relay Relay,
	batch2 *Batch2, batch3 *Batch3) *Server {
	t.Helper()
	cfg := config.Config{AppName: "ClosenAI", Environment: "development", HTTPAddr: ":0"}
	signer := auth.NewSigner("n8-contract-golden-secret-n8-contract-golden",
		30*time.Minute, 30*24*time.Hour)
	// 这两条路由不碰 auth.Service；灰度闸留 nil＝全量由 Go 回答（见 cutover.Selected）。
	return NewServer(cfg, signer, nil, nil, &Batch1{Users: users, Moods: moods, Relay: relay},
		batch2, batch3)
}

// replayN8Case 把一个夹具用例打回 Go 侧，并把响应压到与录制同样的口径。
// 返回值是 Go 侧本次掩掉的 uuid 主键个数（journey 组用它做「不是空跑」的判据）。
func replayN8Case(t *testing.T, handler http.Handler, c n8Case) int {
	t.Helper()
	var body io.Reader
	if c.RequestRaw != nil {
		body = bytes.NewBufferString(*c.RequestRaw)
	}
	req := httptest.NewRequest(c.Method, c.Path, body)
	for k, v := range c.Headers {
		req.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != c.Status {
		t.Errorf("status = %d，Python 侧 %d\n go: %s\n py: %s",
			rec.Code, c.Status, rec.Body.String(), c.ResponseText)
	}
	// 两道掩码都**同时**作用于两侧：录制体已按同一口径掩过（再掩一次必须无变化，
	// 由 TestN8MaskedFieldsStayRedacted 守），Go 体在这里现掩。
	text := maskGeneratedAt(rec.Body.String())
	text, masks := maskJourneyEventIDs(text)
	if text != c.ResponseText {
		t.Errorf("响应体逐字不符\n go: %s\n py: %s", text, c.ResponseText)
	}
	if masks != c.IDMasks {
		t.Errorf("%s 掩掉 %d 个 uuid 主键，Python 侧 %d 个——预写的行数变了"+
			"（字节相同只是因为两侧都被掩成同一串占位符）", c.Name, masks, c.IDMasks)
	}
	if got := rec.Header().Get("X-Trace-Id"); got != c.Headers["X-Trace-Id"] {
		t.Errorf("X-Trace-Id = %q，want %q", got, c.Headers["X-Trace-Id"])
	}
	if got := rec.Header().Get("Content-Type"); got != "application/json" {
		t.Errorf("Content-Type = %q，want application/json", got)
	}
	return masks
}

// ── validation 组：无库 ──────────────────────────────────────────────────────

func TestN8ValidationContractBytes(t *testing.T) {
	file := loadN8Fixture(t)
	forbidden := n8ForbiddenStore{t: t}
	handler := newN8Server(t, forbidden, forbidden, &n8RecordingRelay{t: t}, nil, nil).Handler()

	var replayed int
	for _, c := range file.Cases {
		if c.Group != "validation" {
			continue
		}
		replayed++
		t.Run(c.Name, func(t *testing.T) {
			replayN8Case(t, handler, c)
		})
	}
	if replayed == 0 {
		t.Fatal("validation 组为空：分组键 group 漂移了")
	}
	t.Logf("validation 组 %d 条 400 体逐字回放（全程未读库）", replayed)
}

// TestN8MaskedFieldsStayRedacted 守的是掩码自身的失效模式：
// 录制文本里若还留着没被掩掉的易变值，「逐字比」就成了比时间戳或比 uuid。
//
// 白名单只有一个键名漂移的余地，所以这里不认「组名」只认 `masked_fields`：
// 生成脚本想再掩第三样字段，必须同时在这里登记，否则两侧口径就悄悄分叉了。
func TestN8MaskedFieldsStayRedacted(t *testing.T) {
	file := loadN8Fixture(t)
	want := []string{"generated_at", "journey_event_id"}
	if len(file.MaskedFields) != len(want) {
		t.Fatalf("masked_fields 必须是 %v，实为 %v", want, file.MaskedFields)
	}
	for i, name := range want {
		if file.MaskedFields[i] != name {
			t.Fatalf("masked_fields 顺序/键名与闸不符：第 %d 项 want %s got %s", i, name, file.MaskedFields[i])
		}
	}
	if len(file.GeneratedAtSamples) == 0 {
		t.Fatal("夹具没带 generated_at_samples，掩码闸在空跑")
	}
	for _, sample := range file.GeneratedAtSamples {
		probe := `{"data":{"generated_at":"` + sample + `"}}`
		if maskGeneratedAt(probe) == probe {
			t.Errorf("正则没吃掉录制值 %q——两侧口径已经不同", sample)
		}
	}

	var journeyMasks, journeyPlaceholder int
	for _, c := range file.Cases {
		if rest, n := maskJourneyEventIDs(maskGeneratedAt(c.ResponseText)); rest != c.ResponseText || n != 0 {
			t.Errorf("%s 的录制体里还有未掩码的易变值（漏掩 %d 处）：%s", c.Name, n, c.ResponseText)
		}
		count := strings.Count(c.ResponseText, n8MaskedEventID)
		if count != c.IDMasks {
			t.Errorf("%s 录制体里 <event_id> 出现 %d 次，夹具记的 id_masks=%d 不符",
				c.Name, count, c.IDMasks)
		}
		if c.Group == "journey" {
			journeyMasks += c.IDMasks
			journeyPlaceholder += count
		}
	}
	// 闸别：journey 组若整组没有 uuid 主键，上面三条会全绿但一条也没证——
	// 那意味着 INSERT 支（预写新行）从用例里消失了。
	if journeyMasks == 0 || journeyPlaceholder != journeyMasks {
		t.Errorf("journey 组没有 uuid 主键可掩（id_masks 合计 %d、占位符合计 %d）："+
			"预写插入那支没被任何用例走到", journeyMasks, journeyPlaceholder)
	}
	t.Logf("journey 组共掩掉 %d 个 uuid 主键，非 journey 组录制体零残留", journeyMasks)
}

// ── seeded 组：真库 ──────────────────────────────────────────────────────────

func n8Pool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	raw := os.Getenv("TEST_DATABASE_URL")
	if raw == "" {
		if os.Getenv("CLOSEN_REQUIRE_PG") == "1" {
			t.Fatal("CLOSEN_REQUIRE_PG=1 但 TEST_DATABASE_URL 未设置——" +
				"声明「同基线」时这一条必须是 fail 而不是 skip")
		}
		t.Skip("TEST_DATABASE_URL not set")
	}
	url := dsn.Postgres(raw)
	pool, err := pgxpool.New(context.Background(), url)
	if err != nil {
		t.Fatalf("pool: %v", err)
	}
	t.Cleanup(pool.Close)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := pool.Ping(ctx); err != nil {
		// 只报 @ 之后的部分：口令不能进测试日志。
		host := url
		if i := strings.LastIndex(url, "@"); i >= 0 {
			host = url[i+1:]
		}
		t.Fatalf("ping %s: %v", host, err)
	}
	return pool
}

// n8SeedRows 播种夹具给的同一批行：
//   - mood_checkins 按 days_ago **相对当下**（批 1 只有 7/14 天窗口，没有日历字段）；
//   - journey_events 按 scheduled_at **绝对时刻**（批 2 的响应里有 week_start/date，
//     两侧必须读同一串瞬间，见夹具的 recorded_at）；
//   - users 的 push_time / created_at 与 onboarding_profiles.created_at 全是**列原文/
//     绝对时刻**（批 3）：剧本 day_no 由基准日推出，diff 一次就要整批行错位。
func n8SeedRows(t *testing.T, pool *pgxpool.Pool, seed n8Seed) {
	t.Helper()
	ctx := context.Background()
	n8CleanSeedRows(t, pool, ctx)
	t.Cleanup(func() { n8CleanSeedRows(t, pool, context.Background()) })

	for _, u := range seed.Users {
		if _, err := pool.Exec(ctx, `
			INSERT INTO users (id, timezone, streak_count, relationship_level,
				chat_count, daily_quota_used, tier, language, analytics_consent,
				ai_consent, status, push_time, created_at)
			VALUES ($1, $2, 0, 'L1', 0, 0, 'free', $3, $4, $5, $6, $7, $8)
			ON CONFLICT (id) DO UPDATE SET language = EXCLUDED.language,
				analytics_consent = EXCLUDED.analytics_consent,
				timezone = EXCLUDED.timezone, ai_consent = EXCLUDED.ai_consent,
				status = EXCLUDED.status, push_time = EXCLUDED.push_time,
				created_at = EXCLUDED.created_at`,
			u.ID, u.Timezone, u.Language, u.AnalyticsConsent, u.AIConsent, u.Status,
			u.PushTime, u.CreatedAt); err != nil {
			t.Fatalf("seed user %s: %v", u.ID, err)
		}
	}
	for _, m := range seed.MoodCheckins {
		days := strconv.FormatFloat(m.DaysAgo, 'f', -1, 64)
		if _, err := pool.Exec(ctx, `
			INSERT INTO mood_checkins (id, user_id, mood_emoji, created_at)
			VALUES ($1, $2, $3, now() - ($4 || ' days')::interval)
			ON CONFLICT (id) DO NOTHING`,
			m.ID, m.UserID, m.MoodEmoji, days); err != nil {
			t.Fatalf("seed mood %s: %v", m.ID, err)
		}
	}
	for _, e := range seed.JourneyEvents {
		if _, err := pool.Exec(ctx, `
			INSERT INTO journey_events (id, user_id, event_kind, day_no, scheduled_at,
				payload, memory_refs, status, sent_at, acknowledged)
			VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
			ON CONFLICT (id) DO UPDATE SET user_id = EXCLUDED.user_id,
				event_kind = EXCLUDED.event_kind, day_no = EXCLUDED.day_no,
				scheduled_at = EXCLUDED.scheduled_at, payload = EXCLUDED.payload,
				memory_refs = EXCLUDED.memory_refs, status = EXCLUDED.status,
				sent_at = EXCLUDED.sent_at, acknowledged = EXCLUDED.acknowledged`,
			e.ID, e.UserID, e.EventKind, e.DayNo, e.ScheduledAt, e.Payload, e.MemoryRefs,
			e.Status, e.SentAt, e.Acknowledged); err != nil {
			t.Fatalf("seed journey %s: %v", e.ID, err)
		}
	}
	// 其余列按生成脚本的字面量写死（'D01'/'[]'/'{}'/'en'）：这一批只读 created_at，
	// 但两侧把同一批行写成同一串值，才谈得上「灰度期读同一批行」。
	for _, p := range seed.OnboardingProfiles {
		if _, err := pool.Exec(ctx, `
			INSERT INTO onboarding_profiles (id, user_id, persona_type, answers,
				first_read, language, created_at)
			VALUES ($1, $2, 'D01', '[]', '{}', 'en', $3)
			ON CONFLICT (id) DO UPDATE SET user_id = EXCLUDED.user_id,
				created_at = EXCLUDED.created_at`,
			p.ID, p.UserID, p.CreatedAt); err != nil {
			t.Fatalf("seed onboarding profile %s: %v", p.ID, err)
		}
	}
}

func n8CleanSeedRows(t *testing.T, pool *pgxpool.Pool, ctx context.Context) {
	t.Helper()
	// **两个键**都要匹配：journey 的预写支会插出 uuid 形状的新主键（不带 n8b1- 前缀），
	// 只按 id 清就留下上一轮回放写下的行，下一轮读到 12 行而非 6 行。
	// mood_checkins.user_id 与 onboarding_profiles.user_id 有外键，子表先删。
	for _, q := range []string{
		`DELETE FROM mood_checkins WHERE id LIKE 'n8b1-%' OR user_id LIKE 'n8b1-%'`,
		`DELETE FROM onboarding_profiles WHERE id LIKE 'n8b1-%' OR user_id LIKE 'n8b1-%'`,
		`DELETE FROM journey_events WHERE id LIKE 'n8b1-%' OR user_id LIKE 'n8b1-%'`,
		`DELETE FROM users WHERE id LIKE 'n8b1-%'`,
	} {
		if _, err := pool.Exec(ctx, q); err != nil {
			t.Fatalf("clean seed rows (%s): %v", q, err)
		}
	}
}

// n8RelayedRe 从录好的 200 体里取 relayed 计数，用来核对「外发条数＝响应声称的条数」。
var n8RelayedRe = regexp.MustCompile(`"relayed":(\d+)`)

func TestN8SeededContractBytes(t *testing.T) {
	file := loadN8Fixture(t)
	pool := n8Pool(t)
	n8SeedRows(t, pool, file.Seed)

	relay := &n8RecordingRelay{t: t}
	pg := store.NewPG(pool)
	handler := newN8Server(t, pg, pg, relay, nil, nil).Handler()

	var replayed, wantRelayed int
	for _, c := range file.Cases {
		if c.Group != "seeded" {
			continue
		}
		replayed++
		if m := n8RelayedRe.FindStringSubmatch(c.ResponseText); m != nil {
			n, err := strconv.Atoi(m[1])
			if err != nil {
				t.Fatalf("%s 的 relayed 读不出数字：%q", c.Name, m[1])
			}
			wantRelayed += n
		}
		t.Run(c.Name, func(t *testing.T) {
			replayN8Case(t, handler, c)
		})
	}
	if replayed == 0 {
		t.Fatal("seeded 组为空：分组键 group 漂移了")
	}
	// Python 侧是入队即算成功，所以外发条数必须等于录制体里 relayed 的总和。
	if got := relay.count(); got != wantRelayed {
		t.Errorf("外发事件 %d 条，与录制体 relayed 合计 %d 不符", got, wantRelayed)
	}
	t.Logf("seeded 组 %d 条（真读 users/mood_checkins）逐字回放，外发事件 %d 条",
		replayed, relay.count())
}

// ── curriculum 组：真库 + 冻时钟 ──────────────────────────────────────────────

// n8Anchor 把夹具的 recorded_at 读成 UTC 时刻；读不出就等于夹具换了格式。
func n8Anchor(t *testing.T, file n8Fixture) time.Time {
	t.Helper()
	if file.RecordedAt == "" {
		t.Fatal("夹具没有 recorded_at：curriculum 的周视图会随重放日期整周平移，" +
			"逐字比失去意义（生成方式见 scripts/gen_n8_contract_goldens.py）")
	}
	at, err := time.Parse(time.RFC3339, file.RecordedAt)
	if err != nil {
		t.Fatalf("recorded_at %q 不是 RFC3339: %v", file.RecordedAt, err)
	}
	return at.UTC()
}

// TestN8CurriculumContractBytes 是批 2 的验收闸：curriculum 两条端点的响应字节
// 逐字等于真 FastAPI 录下来的那串。
//
// 时钟必须钉在 recorded_at：week_start、days[].date、revealed 都由「用户本地今天」推出，
// 回放只要跨过本地午夜（更常见是跨过周一），Go 侧算出的就是另一周——那时逐字比在比
// 两个不同的周，红与绿都不证明任何事。
//
// 用例**按夹具顺序**跑：POST /curriculum/{id}/read 会真写库，紧随其后的 GET 必须读到
// 写后的状态（Python 录制时就是同一串顺序、同一个 session 提交在响应之前）。
func TestN8CurriculumContractBytes(t *testing.T) {
	file := loadN8Fixture(t)
	pool := n8Pool(t)
	n8SeedRows(t, pool, file.Seed)
	anchor := n8Anchor(t, file)

	forbidden := &n8NoRelayStore{t: t}
	pg := store.NewPG(pool)
	srv := newN8Server(t, pg, pg, forbidden, &Batch2{Store: pg}, nil)
	srv.now = func() time.Time { return anchor }
	handler := srv.Handler()

	var replayed int
	for _, c := range file.Cases {
		if c.Group != "curriculum" {
			continue
		}
		replayed++
		t.Run(c.Name, func(t *testing.T) {
			replayN8Case(t, handler, c)
		})
	}
	if replayed == 0 {
		t.Fatal("curriculum 组为空：分组键 group 漂移了")
	}
	t.Logf("curriculum 组 %d 条逐字回放（锚点 %s，真读 users/journey_events）",
		replayed, anchor.Format(time.RFC3339))
}

// n8NoRelayStore 给 curriculum 组用：这两条端点一条外发都不该有。
type n8NoRelayStore struct{ t *testing.T }

func (n *n8NoRelayStore) Capture(context.Context, posthog.Event) error {
	n.t.Errorf("curriculum 用例发起了外发——Python 侧这个域一个分析事件都不打")
	return nil
}

// ── journey 组：真库 + 冻时钟 + 读接口会写库 ───────────────────────────────────

// TestN8JourneyContractBytes 是批 3 的验收闸：journey 两条端点的响应字节逐字等于
// 真 FastAPI 录下来的那串，且**掩码次数**也相等。
//
// 这一组比批 1/批 2 多一件：GET /journey/events 会先预写再读，所以它不是纯读回放——
// 用例必须按夹具顺序跑（ack 之后紧跟的 GET 要读到 seen），而且清理要按 user_id 也删一遍
// （预写插出来的行主键是 uuid，不带 n8b1- 前缀）。
//
// 时钟同样钉在 recorded_at：day_no 由「用户本地今天 - 基准日」推出，回放跨过一个本地午夜
// 就会让六行的 day_no 全体 +1，而那是**响应里的数字**，不是格式差异。
func TestN8JourneyContractBytes(t *testing.T) {
	file := loadN8Fixture(t)
	pool := n8Pool(t)
	n8SeedRows(t, pool, file.Seed)
	anchor := n8Anchor(t, file)

	pg := store.NewPG(pool)
	srv := newN8Server(t, pg, pg, &n8NoRelayStore{t: t},
		&Batch2{Store: pg}, &Batch3{Store: pg})
	srv.now = func() time.Time { return anchor }
	handler := srv.Handler()

	var replayed, goMasks int
	for _, c := range file.Cases {
		if c.Group != "journey" {
			continue
		}
		replayed++
		t.Run(c.Name, func(t *testing.T) {
			goMasks += replayN8Case(t, handler, c)
		})
	}
	if replayed == 0 {
		t.Fatal("journey 组为空：分组键 group 漂移了")
	}
	if goMasks == 0 {
		t.Fatal("journey 组 Go 侧一个 uuid 主键都没掩：预写的 INSERT 支没被走到，" +
			"上面全绿不证明任何事")
	}
	t.Logf("journey 组 %d 条逐字回放（锚点 %s，含预写的读接口），Go 侧掩码合计 %d 次",
		replayed, anchor.Format(time.RFC3339), goMasks)
}

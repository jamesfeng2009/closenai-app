// N8 批 1 的 HTTP 逐字节回放（夹具的 cases 块）。
//
// 分两组是因为「需不需要库」决定了这两组能在哪里跑：
//   - validation 组全部 400，Python 侧一次 store 都没碰，所以 Go 侧也不许碰——
//     这里的 store 是「一被调用就把测试弄红」的假件，顺带证了这条依赖顺序本身。
//   - seeded 组真读 users/mood_checkins，必须用**同一个 TEST_DATABASE_URL**、
//     按夹具里的 days_ago 相对当下播种（窗口边界两侧必须落在同一侧）。
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

func newN8Server(t *testing.T, users UserProfileReader, moods MoodReader, relay Relay) *Server {
	t.Helper()
	cfg := config.Config{AppName: "ClosenAI", Environment: "development", HTTPAddr: ":0"}
	signer := auth.NewSigner("n8-contract-golden-secret-n8-contract-golden",
		30*time.Minute, 30*24*time.Hour)
	// 这两条路由不碰 auth.Service；灰度闸留 nil＝全量由 Go 回答（见 cutover.Selected）。
	return NewServer(cfg, signer, nil, nil, &Batch1{Users: users, Moods: moods, Relay: relay})
}

// replayN8Case 把一个夹具用例打回 Go 侧，并把响应压到与录制同样的口径。
func replayN8Case(t *testing.T, handler http.Handler, c n8Case) {
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
	if got, want := maskGeneratedAt(rec.Body.String()), c.ResponseText; got != want {
		t.Errorf("响应体逐字不符\n go: %s\n py: %s", got, want)
	}
	if got := rec.Header().Get("X-Trace-Id"); got != c.Headers["X-Trace-Id"] {
		t.Errorf("X-Trace-Id = %q，want %q", got, c.Headers["X-Trace-Id"])
	}
	if got := rec.Header().Get("Content-Type"); got != "application/json" {
		t.Errorf("Content-Type = %q，want application/json", got)
	}
}

// ── validation 组：无库 ──────────────────────────────────────────────────────

func TestN8ValidationContractBytes(t *testing.T) {
	file := loadN8Fixture(t)
	forbidden := n8ForbiddenStore{t: t}
	handler := newN8Server(t, forbidden, forbidden, &n8RecordingRelay{t: t}).Handler()

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

// TestN8MaskOnlyGeneratedAt 守的是掩码自身的失效模式：
// 录制文本里若还留着没被掩掉的 generated_at 值，「逐字比」就成了比时间戳。
func TestN8MaskOnlyGeneratedAt(t *testing.T) {
	file := loadN8Fixture(t)
	if len(file.MaskedFields) != 1 || file.MaskedFields[0] != "generated_at" {
		t.Fatalf("masked_fields 只允许 generated_at，实为 %v", file.MaskedFields)
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
	for _, c := range file.Cases {
		if c.Group != "seeded" {
			continue
		}
		if maskGeneratedAt(c.ResponseText) != c.ResponseText {
			t.Errorf("%s 的录制体里还有未掩码的 generated_at：%s", c.Name, c.ResponseText)
		}
	}
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

// n8SeedRows 按夹具的 days_ago **相对当下**播种：录制与回放可能相隔数小时，
// 用绝对时间戳会让 7/14 天窗口两侧落在不同侧，那就不再是「同一批行」。
func n8SeedRows(t *testing.T, pool *pgxpool.Pool, seed n8Seed) {
	t.Helper()
	ctx := context.Background()
	n8CleanSeedRows(t, pool, ctx)
	t.Cleanup(func() { n8CleanSeedRows(t, pool, context.Background()) })

	for _, u := range seed.Users {
		if _, err := pool.Exec(ctx, `
			INSERT INTO users (id, timezone, streak_count, relationship_level,
				chat_count, daily_quota_used, tier, language, analytics_consent)
			VALUES ($1, 'UTC', 0, 'L1', 0, 0, 'free', $2, $3)
			ON CONFLICT (id) DO UPDATE SET language = EXCLUDED.language,
				analytics_consent = EXCLUDED.analytics_consent`,
			u.ID, u.Language, u.AnalyticsConsent); err != nil {
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
}

func n8CleanSeedRows(t *testing.T, pool *pgxpool.Pool, ctx context.Context) {
	t.Helper()
	// mood_checkins.user_id 有外键，子表先删。
	if _, err := pool.Exec(ctx, `DELETE FROM mood_checkins WHERE id LIKE 'n8b1-%'`); err != nil {
		t.Fatalf("clean mood_checkins: %v", err)
	}
	if _, err := pool.Exec(ctx, `DELETE FROM users WHERE id LIKE 'n8b1-%'`); err != nil {
		t.Fatalf("clean users: %v", err)
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
	handler := newN8Server(t, pg, pg, relay).Handler()

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

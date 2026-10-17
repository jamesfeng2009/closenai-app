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

// maskJourneyEventIDs 是 n8EventIDRe 的「总是应用」版本，只留给掩码自检用
// （自检要的是「录制体里不该还留着 uuid 形状的主键」，与回放时的按计数应用无关）。
func maskJourneyEventIDs(body string) (string, int) {
	var n int
	out := n8EventIDRe.ReplaceAllStringFunc(body, func(string) string {
		n++
		return n8MaskedEventID
	})
	return out, n
}

// 批 4（onboarding）另两条掩码。与上面两条同一个原则：**只有本次运行真的现算出来的值**
// 才许掩，其余一律逐字比——所以这两条不是「按组无条件掩」，而是按夹具给的次数掩
// （见生成脚本 record() 的 runtime_code / run_profiles 两张表）。
//
// n8ReferralLinkRe 是录制侧 REFERRAL_LINK_RE 的同一条。字符集写成 [A-Z2-7]{8} 是
// backend/core/referral.py 那 29 字符（A-Z 去 I/L/O ＋ 2-7）的**超集**：宁可多认也不漏认，
// 多掩由 code_masks 计数暴露成红。
var n8ReferralLinkRe = regexp.MustCompile(`/r/[A-Z2-7]{8}`)

const n8MaskedReferralLink = `/r/<code>`

// n8CreatedAtRe 与生成脚本的 CREATED_AT_RE 同一条：只吃字符串形态的 created_at。
var n8CreatedAtRe = regexp.MustCompile(`"created_at":"[^"]*"`)

const n8MaskedCreatedAt = `"created_at":"<created_at>"`

// maskIf 是三条「按计数应用」的掩码共用的形状。
//
// want==0 时**不数也不掩**：夹具记 0 有两种意思——这条响应里根本没有这个值，
// 或者有、但它是**确定值**（种子已带码 ⇒ `/r/QQ22WW33` 全串、种子行 ⇒ created_at 的
// 绝对时刻）。后一种正是本批要的证据，把它数出来再断言 0 会把「刻意不掩」读成发散。
// 漏网的口子由逐字节比对兜住：Go 若在该留原文的地方现算了一个随机值，字节先红。
//
// want>0 时数与掩都做，计数就是判据：多一行/多算一次码 ⇒ 次数不等 ⇒ 红。
func maskIf(body string, re *regexp.Regexp, placeholder string, want int) (string, int) {
	if want == 0 {
		return body, 0
	}
	n := len(re.FindAllStringIndex(body, -1))
	return re.ReplaceAllString(body, placeholder), n
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

// n8ForbiddenOnboarding 给 validation 组用。它存在的理由不是「多一个假件」：
// Batch4 为 nil 时那两条路由**根本不注册**（server.go 的 routes），于是 26 条
// onboarding 的 400 用例会变成 404 而红——红得没有信息量。挂上这个假件才是
// 「路由在、但校验失败一次库都不许碰」这条真实依赖顺序。
type n8ForbiddenOnboarding struct{ t *testing.T }

func (f n8ForbiddenOnboarding) SubmitOnboarding(_ context.Context, userID string,
	_ domain.OnboardingSubmitBuilder) (string, error) {
	f.t.Errorf("validation 用例写了 onboarding(%s)——Python 侧校验失败根本不进 store", userID)
	return "", errors.New("n8: store must not be written")
}

func (f n8ForbiddenOnboarding) OnboardingProfile(_ context.Context, userID string) (domain.OnboardingProfile, error) {
	f.t.Errorf("validation 用例读了 onboarding_profiles(%s)——Python 侧校验失败根本不进 store", userID)
	return domain.OnboardingProfile{}, errors.New("n8: store must not be read")
}

// n8ForbiddenBatch5 给批 5 的 validation 组用，理由与 n8ForbiddenOnboarding 同一条：
// Batch5 为 nil 时那三条路由不注册，15 条 mood/reflection 的 400 用例会红成 404。
// 挂上这个假件以后，红绿才在说「路由在、校验失败时一次库都不许碰」。
//
// 四个方法各自的消息写的是**表名**，因为这一组里读库的分支有三条
// （mood_checkins 两次、users 一次由 n8ForbiddenStore 守、growth_records 一次）。
type n8ForbiddenBatch5 struct{ t *testing.T }

func (f n8ForbiddenBatch5) MoodHistory(_ context.Context, userID string, _ int64) ([]domain.MoodCheckinRow, error) {
	f.t.Errorf("validation 用例读了 mood_checkins(%s)——Python 侧校验失败根本不进 store", userID)
	return nil, errors.New("n8: store must not be read")
}

func (f n8ForbiddenBatch5) MoodTrendRows(_ context.Context, userID string, _ time.Time) ([]domain.Checkin, error) {
	f.t.Errorf("validation 用例读了 mood_checkins 的窗口查询(%s)——Python 侧校验失败根本不进 store", userID)
	return nil, errors.New("n8: store must not be read")
}

func (f n8ForbiddenBatch5) MoodUserTimezone(_ context.Context, userID string) (string, bool, error) {
	f.t.Errorf("validation 用例读了 users 的时区(%s)——Python 侧校验失败根本不进 store", userID)
	return "", false, errors.New("n8: store must not be read")
}

func (f n8ForbiddenBatch5) Reflections(_ context.Context, userID string, _ string, _ int64) ([]domain.ReflectionRow, error) {
	f.t.Errorf("validation 用例读了 growth_records(%s)——Python 侧校验失败根本不进 store", userID)
	return nil, errors.New("n8: store must not be read")
}

// n8ForbiddenBatch6 给批 6 的 validation 组（memories 的 limit 越界/非整数那一档）。
// 两个方法各自的错误消息点**表名**：这两条读的都是 memories，但读的动作有两个
// （列表带 LIMIT、详情按主键），少守一个就会有一条用例在「校验失败却打库」上悄悄绿。
type n8ForbiddenBatch6 struct{ t *testing.T }

func (f n8ForbiddenBatch6) MemoryList(_ context.Context, userID string, _ int64,
	_ string) ([]domain.MemoryRow, error) {
	f.t.Errorf("validation 用例读了 memories(%s)——Python 侧 limit 校验失败根本不进 store", userID)
	return nil, errors.New("n8: memories list must not be read")
}

func (f n8ForbiddenBatch6) MemoryByID(_ context.Context, memoryID,
	userID string) (domain.MemoryRow, bool, error) {
	f.t.Errorf("validation 用例读了 memories(%s/%s)——详情那条根本没有 query 参数",
		userID, memoryID)
	return domain.MemoryRow{}, false, errors.New("n8: memories detail must not be read")
}

// n8RecordingRelay 记下发出去的事件，但**绝不联网**（测试里不许打 posthog）。
//
// 恒回 nil 不是偷懒，是这一组的**前提**：夹具录制侧装的是同一个「每条都送出去了」的
// 成功假件（scripts/gen_n8_contract_goldens.py 顶部），所以这里的 nil 必须与那支同形，
// 逐字节比才在比同一件事。D-G18 之后 nil 的含义收窄成「capture 端点真的收下了」，
// 想测「送不出去 ⇒ 落 dropped」那一支请用 analytics_relay_count_test.go 的假件。
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
	batch2 *Batch2, batch3 *Batch3, batch4 *Batch4, batch5 *Batch5, batch6 *Batch6) *Server {
	t.Helper()
	cfg := config.Config{AppName: "ClosenAI", Environment: "development", HTTPAddr: ":0"}
	signer := auth.NewSigner("n8-contract-golden-secret-n8-contract-golden",
		30*time.Minute, 30*24*time.Hour)
	// 这两条路由不碰 auth.Service；灰度闸留 nil＝全量由 Go 回答（见 cutover.Selected）。
	return NewServer(cfg, signer, nil, nil, &Batch1{Users: users, Moods: moods, Relay: relay},
		batch2, batch3, batch4, batch5, batch6)
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
	// 四道掩码都**同时**作用于两侧：录制体已按同一口径掩过（再掩一次必须无变化，
	// 由 TestN8MaskedFieldsStayRedacted 守），Go 体在这里现掩。
	// 后三道按夹具给的计数应用，理由见 maskIf——种子已带码／种子已有行那些用例的
	// share_url 与 created_at 是**确定值**，掩掉就把证据掩掉了。
	text := maskGeneratedAt(rec.Body.String())
	text, masks := maskIf(text, n8EventIDRe, n8MaskedEventID, c.IDMasks)
	text, codeMasks := maskIf(text, n8ReferralLinkRe, n8MaskedReferralLink, c.CodeMasks)
	text, createdMasks := maskIf(text, n8CreatedAtRe, n8MaskedCreatedAt, c.CreatedAtMasks)
	if text != c.ResponseText {
		t.Errorf("响应体逐字不符\n go: %s\n py: %s", text, c.ResponseText)
	}
	if masks != c.IDMasks {
		t.Errorf("%s 掩掉 %d 个 uuid 主键，Python 侧 %d 个——预写的行数变了"+
			"（字节相同只是因为两侧都被掩成同一串占位符）", c.Name, masks, c.IDMasks)
	}
	if codeMasks != c.CodeMasks {
		t.Errorf("%s 掩掉 %d 个 /r/<code>，Python 侧 %d 个——推荐码那一支走了不同的分支"+
			"（0＝种子已带码、逐字可比；1＝本次现算）", c.Name, codeMasks, c.CodeMasks)
	}
	if createdMasks != c.CreatedAtMasks {
		t.Errorf("%s 掩掉 %d 个 created_at，Python 侧 %d 个——profile 行是不是本次新写的，"+
			"两侧读到了不同的事", c.Name, createdMasks, c.CreatedAtMasks)
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
	onb := n8ForbiddenOnboarding{t: t}
	b5 := n8ForbiddenBatch5{t: t}
	handler := newN8Server(t, forbidden, forbidden, &n8RecordingRelay{t: t},
		nil, nil, &Batch4{Store: onb},
		&Batch5{Moods: b5, Reflections: b5, Users: forbidden},
		&Batch6{Memories: n8ForbiddenBatch6{t: t}}).Handler()

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
	// 批 5 的 15 条（trend 的 days 九条＋reflections 的 limit 六条）必须真的在这组里：
	// 组名换了、或用例被删空，上面那句 replayed!=0 仍然绿，而 400 面已经没人回放了。
	// 只设下界不设精确值——以后再加一格校验用例时这里不该变红。
	var batch5Validated int
	for _, c := range file.Cases {
		if c.Group == "validation" && (strings.HasPrefix(c.Path, "/api/v1/mood/") ||
			strings.HasPrefix(c.Path, "/api/v1/reflections")) {
			batch5Validated++
		}
	}
	if batch5Validated < 15 {
		t.Fatalf("validation 组里批 5 的校验用例只有 %d 条，录制时是 15 条（days 9 + limit 6）"+
			"——少一条就是 queryInt 的某一格失去了观测点", batch5Validated)
	}
	// 批 6 的八条 limit 校验同一条纪律：这组只设下界，绿的前提是「真的走到 store 之前」。
	var batch6Validated int
	for _, c := range file.Cases {
		if c.Group == "validation" && strings.HasPrefix(c.Path, "/api/v1/memories") {
			batch6Validated++
		}
	}
	if batch6Validated < 8 {
		t.Fatalf("validation 组里批 6 的校验用例只有 %d 条，录制时是 8 条（limit 的 abc/0/101/"+
			"-1/2.5/空白/%%20/双错序）——少一条就是 MemoriesLimitSpec 的某一格没了观测点，"+
			"而 n8ForbiddenBatch6 也再没机会翻脸", batch6Validated)
	}
	t.Logf("validation 组 %d 条 400 体逐字回放（全程未读库，其中批 5 %d 条、批 6 %d 条）",
		replayed, batch5Validated, batch6Validated)
}

// TestN8MaskedFieldsStayRedacted 守的是掩码自身的失效模式：
// 录制文本里若还留着没被掩掉的易变值，「逐字比」就成了比时间戳或比 uuid。
//
// 白名单只有一个键名漂移的余地，所以这里不认「组名」只认 `masked_fields`：
// 生成脚本想再掩第三样字段，必须同时在这里登记，否则两侧口径就悄悄分叉了。
func TestN8MaskedFieldsStayRedacted(t *testing.T) {
	file := loadN8Fixture(t)
	want := []string{"generated_at", "journey_event_id", "referral_link", "created_at"}
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
	var onbCodeMasks, onbCodePlaceholder, onbCreated, onbCreatedPlaceholder int
	for _, c := range file.Cases {
		if rest, n := maskJourneyEventIDs(maskGeneratedAt(c.ResponseText)); rest != c.ResponseText || n != 0 {
			t.Errorf("%s 的录制体里还有未掩码的易变值（漏掩 %d 处）：%s", c.Name, n, c.ResponseText)
		}
		count := strings.Count(c.ResponseText, n8MaskedEventID)
		if count != c.IDMasks {
			t.Errorf("%s 录制体里 <event_id> 出现 %d 次，夹具记的 id_masks=%d 不符",
				c.Name, count, c.IDMasks)
		}
		// 这两条**不能**用「再掩一次必须无变化」的形式检查：种子已带码的用例里
		// `/r/QQ22WW33` 是刻意留下的确定值，掩了就把证据掩了。所以这里比的是
		// **占位符出现次数＝夹具声明的次数**——多掩/漏掩都会让两边对不上。
		codePlaceholders := strings.Count(c.ResponseText, n8MaskedReferralLink)
		if codePlaceholders != c.CodeMasks {
			t.Errorf("%s 录制体里 /r/<code> 出现 %d 次，夹具记的 code_masks=%d 不符",
				c.Name, codePlaceholders, c.CodeMasks)
		}
		createdPlaceholders := strings.Count(c.ResponseText, n8MaskedCreatedAt)
		if createdPlaceholders != c.CreatedAtMasks {
			t.Errorf("%s 录制体里 <created_at> 出现 %d 次，夹具记的 created_at_masks=%d 不符",
				c.Name, createdPlaceholders, c.CreatedAtMasks)
		}
		if c.Group == "journey" {
			journeyMasks += c.IDMasks
			journeyPlaceholder += count
		}
		if c.Group == "onboarding" {
			onbCodeMasks += c.CodeMasks
			onbCodePlaceholder += codePlaceholders
			onbCreated += c.CreatedAtMasks
			onbCreatedPlaceholder += createdPlaceholders
		}
	}
	// 闸别：journey 组若整组没有 uuid 主键，上面三条会全绿但一条也没证——
	// 那意味着 INSERT 支（预写新行）从用例里消失了。
	if journeyMasks == 0 || journeyPlaceholder != journeyMasks {
		t.Errorf("journey 组没有 uuid 主键可掩（id_masks 合计 %d、占位符合计 %d）："+
			"预写插入那支没被任何用例走到", journeyMasks, journeyPlaceholder)
	}
	// 同一个道理套在批 4 的两条新掩码上：整组一条都没掩 ⇒ 惰性生成码那一支
	// 与「本次新写 profile」那一支都从用例面消失了，上面全绿不证明任何事。
	// 而且这两条必须是**部分**掩：一条都不掩（全是种子值）与全都掩同样可疑，
	// 所以还要求「未掩的那一侧」也有观测点（见 n8UnmaskedShareURLCount）。
	if onbCodeMasks == 0 || onbCodePlaceholder != onbCodeMasks {
		t.Errorf("onboarding 组没有 /r/<code> 可掩（code_masks 合计 %d、占位符合计 %d）："+
			"惰性生成码那支没被任何用例走到", onbCodeMasks, onbCodePlaceholder)
	}
	if onbCreated == 0 || onbCreatedPlaceholder != onbCreated {
		t.Errorf("onboarding 组没有 <created_at> 可掩（created_at_masks 合计 %d、占位符合计 %d）："+
			"profile 的 INSERT 支没被任何用例走到", onbCreated, onbCreatedPlaceholder)
	}
	if seeded := n8UnmaskedShareURLCount(file); seeded == 0 {
		t.Errorf("onboarding 组没有任何一条**未掩**的 share_url：「种子已带码 ⇒ 逐字复用」" +
			"那支失去观测点，code_masks 就成了「全掩」的空话")
	}
	t.Logf("journey 组共掩掉 %d 个 uuid 主键；onboarding 组掩 %d 个码 / %d 个 created_at、"+
		"另有 %d 条逐字比对的种子码；其余录制体零残留",
		journeyMasks, onbCodeMasks, onbCreated, n8UnmaskedShareURLCount(file))
}

// n8UnmaskedShareURLCount 数「录制体里带着**明文**推荐码」的用例条数——
// 也就是夹具刻意不掩、要靠逐字比来证「已有码不许被换」的那几条。
func n8UnmaskedShareURLCount(file n8Fixture) int {
	var n int
	for _, c := range file.Cases {
		if c.Group != "onboarding" || c.CodeMasks > 0 {
			continue
		}
		if len(n8ReferralLinkRe.FindAllStringIndex(c.ResponseText, -1)) > 0 {
			n++
		}
	}
	return n
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
//   - mood_checkins 有**两条通道**：批 1 的行按 days_ago 相对当下（discovery 只读
//     7/14 天窗的计数与均值，没有日历字段），批 5 的行按 created_at 绝对时刻
//     （trend 的 labels 是 `%m-%d`，history 把 created_at 原样写进响应）；
//   - growth_records（批 5）按绝对时刻播种，record_type 与 related_memory_ids 给列原文；
//   - journey_events 按 scheduled_at **绝对时刻**（批 2 的响应里有 week_start/date，
//     两侧必须读同一串瞬间，见夹具的 recorded_at）；
//   - users 的 push_time / created_at 与 onboarding_profiles.created_at 全是**列原文/
//     绝对时刻**（批 3）：剧本 day_no 由基准日推出，diff 一次就要整批行错位；
//   - users.referral_code 与 onboarding_profiles 的四列也是**列原文**（批 4）：
//     NULL 与空串在 submit 的两支里落同一个 share_url，但种子码存在与否决定
//     「惰性生成」这一支走不走，而那是响应字节；profile 四列里 first_read 的
//     **键序**直接决定 restore 响应的键序，所以必须原样插同一串字节。
func n8SeedRows(t *testing.T, pool *pgxpool.Pool, seed n8Seed) {
	t.Helper()
	ctx := context.Background()
	n8CleanSeedRows(t, pool, ctx)
	t.Cleanup(func() { n8CleanSeedRows(t, pool, context.Background()) })

	for _, u := range seed.Users {
		if _, err := pool.Exec(ctx, `
			INSERT INTO users (id, timezone, streak_count, relationship_level,
				chat_count, daily_quota_used, tier, language, analytics_consent,
				ai_consent, status, push_time, created_at, referral_code)
			VALUES ($1, $2, 0, 'L1', 0, 0, 'free', $3, $4, $5, $6, $7, $8, $9)
			ON CONFLICT (id) DO UPDATE SET language = EXCLUDED.language,
				analytics_consent = EXCLUDED.analytics_consent,
				timezone = EXCLUDED.timezone, ai_consent = EXCLUDED.ai_consent,
				status = EXCLUDED.status, push_time = EXCLUDED.push_time,
				created_at = EXCLUDED.created_at, referral_code = EXCLUDED.referral_code`,
			u.ID, u.Timezone, u.Language, u.AnalyticsConsent, u.AIConsent, u.Status,
			u.PushTime, u.CreatedAt, u.ReferralCode); err != nil {
			t.Fatalf("seed user %s: %v", u.ID, err)
		}
	}
	for _, m := range seed.MoodCheckins {
		// 两条通道按**键是否缺席**分派：批 1 的行只有 days_ago（相对回放当下），
		// 批 5 的行只有 created_at（绝对）。绝对行走相对口径会让 `/mood/trend` 的
		// `%m-%d` 标签随重放日期整批漂移，而那是响应里的字节。
		// 反过来把批 1 的行钉成绝对时刻，会让已结案的 discovery 用例换一批行。
		if m.CreatedAt != nil {
			if m.DaysAgo != 0 {
				t.Fatalf("种子 %s 同时给了 created_at 与 days_ago——两条通道只能走一条", m.ID)
			}
			if _, err := pool.Exec(ctx, `
				INSERT INTO mood_checkins (id, user_id, mood_emoji, mood_label,
					ai_interpretation, created_at)
				VALUES ($1, $2, $3, $4, $5, $6)
				ON CONFLICT (id) DO UPDATE SET user_id = EXCLUDED.user_id,
					mood_emoji = EXCLUDED.mood_emoji,
					mood_label = EXCLUDED.mood_label,
					ai_interpretation = EXCLUDED.ai_interpretation,
					created_at = EXCLUDED.created_at`,
				m.ID, m.UserID, m.MoodEmoji, m.MoodLabel, m.AIInterpretation,
				*m.CreatedAt); err != nil {
				t.Fatalf("seed mood(abs) %s: %v", m.ID, err)
			}
			continue
		}
		days := strconv.FormatFloat(m.DaysAgo, 'f', -1, 64)
		if _, err := pool.Exec(ctx, `
			INSERT INTO mood_checkins (id, user_id, mood_emoji, created_at)
			VALUES ($1, $2, $3, now() - ($4 || ' days')::interval)
			ON CONFLICT (id) DO NOTHING`,
			m.ID, m.UserID, m.MoodEmoji, days); err != nil {
			t.Fatalf("seed mood %s: %v", m.ID, err)
		}
	}
	// 批 5 的 `GET /reflections` 读这张表。record_type 与 related_memory_ids 都给
	// **列原文**：夹具里刻意种了一条 "Reflection"（首字母大写）来钉「精确等值」，
	// 而 refs 的 NULL / "" / "," / "a,,b" 四形对应 falsy 判据与 split 段数。
	for _, g := range seed.GrowthRecords {
		if _, err := pool.Exec(ctx, `
			INSERT INTO growth_records (id, user_id, record_type, content,
				related_memory_ids, created_at)
			VALUES ($1, $2, $3, $4, $5, $6)
			ON CONFLICT (id) DO UPDATE SET user_id = EXCLUDED.user_id,
				record_type = EXCLUDED.record_type, content = EXCLUDED.content,
				related_memory_ids = EXCLUDED.related_memory_ids,
				created_at = EXCLUDED.created_at`,
			g.ID, g.UserID, g.RecordType, g.Content, g.RelatedMemoryIDs,
			g.CreatedAt); err != nil {
			t.Fatalf("seed growth %s: %v", g.ID, err)
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
	// 四列全部来自夹具的**列原文**（批 4 之前这里写死 'D01'/'[]'/'{}'/'en'，
	// 生成脚本对批 3 那几条至今仍带这四个字面量——所以这条改动不换批 3 的字节）。
	// 两侧把同一批行写成同一串值，才谈得上「灰度期读同一批行」。
	for _, p := range seed.OnboardingProfiles {
		if _, err := pool.Exec(ctx, `
			INSERT INTO onboarding_profiles (id, user_id, persona_type, answers,
				first_read, language, created_at)
			VALUES ($1, $2, $3, $4, $5, $6, $7)
			ON CONFLICT (id) DO UPDATE SET user_id = EXCLUDED.user_id,
				persona_type = EXCLUDED.persona_type, answers = EXCLUDED.answers,
				first_read = EXCLUDED.first_read, language = EXCLUDED.language,
				created_at = EXCLUDED.created_at`,
			p.ID, p.UserID, p.PersonaType, p.Answers, p.FirstRead, p.Language,
			p.CreatedAt); err != nil {
			t.Fatalf("seed onboarding profile %s: %v", p.ID, err)
		}
	}
	// 批 6：drift_score **必须显式写**——DDL 实测它是 NOT NULL 且没有 server_default
	// （conviction_score 同理，所以那一个走夹具的值而不是字面量）。
	// updated_at / emotion_context / superseded_by / reverted_from / generation_run_id /
	// embedding 六列留给 server_default 或 NULL：这两条端点的响应面一个都不读，
	// 而 embedding 是 halfvec——Go 全仓没有它的解析器，所以读侧也必须收缩列。
	for _, m := range seed.Memories {
		if _, err := pool.Exec(ctx, `
			INSERT INTO memories (id, user_id, category, content, status,
				conviction_score, source, created_at, superseded_at,
				transition_reason, labels, deleted, drift_score)
			VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 0.0)
			ON CONFLICT (id) DO UPDATE SET user_id = EXCLUDED.user_id,
				category = EXCLUDED.category, content = EXCLUDED.content,
				status = EXCLUDED.status,
				conviction_score = EXCLUDED.conviction_score,
				source = EXCLUDED.source, created_at = EXCLUDED.created_at,
				superseded_at = EXCLUDED.superseded_at,
				transition_reason = EXCLUDED.transition_reason,
				labels = EXCLUDED.labels, deleted = EXCLUDED.deleted`,
			m.ID, m.UserID, m.Category, m.Content, m.Status, m.ConvictionScore,
			m.Source, m.CreatedAt, m.SupersededAt, m.TransitionReason, m.Labels,
			m.Deleted); err != nil {
			t.Fatalf("seed memory %s: %v", m.ID, err)
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
		// 批 5：/reflections 是纯读，但**必须先清**——上一轮遗留的行会让 LIMIT 截断
		// 落在不同行上（夹具的 reflections-many 钉的正是「20 条里截到第几条」）。
		`DELETE FROM growth_records WHERE id LIKE 'n8b1-%' OR user_id LIKE 'n8b1-%'`,
		// 批 6：同理——memories.user_id 上没有外键（实测 pg_constraint 零行），
		// 但不清就会让上一轮回放的行留在 LIMIT 的截断窗口里。
		`DELETE FROM memories WHERE id LIKE 'n8b1-%' OR user_id LIKE 'n8b1-%'`,
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
	handler := newN8Server(t, pg, pg, relay, nil, nil, nil, nil, nil).Handler()

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
	// 录制侧的成功假件（见 n8RecordingRelay 的注释）意味着「每条都送出去了」，
	// 所以外发条数必须等于录制体里 relayed 的总和。这条锚点在 D-G18 之后才真正咬得住：
	// 计数改成跟随 Capture 结果之前，这里比的是两个恒等式（len(events) 与 len(events)），
	// 撤掉假件、录制体整批变 `relayed:0` 也照样绿；现在 Python 侧的同一判据在
	// test_n8_batch1_contract.py::test_fixture_analytics_counts_add_up_on_the_success_face。
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
	srv := newN8Server(t, pg, pg, forbidden, &Batch2{Store: pg}, nil, nil, nil, nil)
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
		&Batch2{Store: pg}, &Batch3{Store: pg}, nil, nil, nil)
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

// ── onboarding 组：真库 + 冻时钟 + 一次请求写三处 ───────────────────────────────

// TestN8OnboardingContractBytes 是批 4 的验收闸：onboarding 两条端点的响应字节逐字等于
// 真 FastAPI 录下来的那串，且**三条掩码的次数**也相等。
//
// 这一组独有的两件事：
//  1. 配置面必须取夹具的 share_url_inputs（而不是 Go 侧默认值）。share_url 会**落进
//     first_read 列**、restore 按列复现不回查配置 ⇒ 两侧站点配置不同＝同一个账号
//     切流前后拿到两条不同的深链，而那是**列里的字节**，灰度期谁也发现不了。
//  2. 掩码按夹具给的次数应用：种子已带码的用例（o-code-zh、o-lang-upper-zh、
//     o-restore-full）比的是**明文全串**，那是「已有码不许被换」的响应面证据；
//     只有本次现算码的那几条才允许 /r/<code>。created_at 同理（种子行的绝对时刻
//     钉的是 `+00:00` 序列化形状）。
//
// 顺序照夹具走：POST 之后紧跟的 GET 必须读到写过的那一行，restore-before-submit
// 必须读到 40402。
func TestN8OnboardingContractBytes(t *testing.T) {
	file := loadN8Fixture(t)
	pool := n8Pool(t)
	n8SeedRows(t, pool, file.Seed)
	anchor := n8Anchor(t, file)

	pg := store.NewPG(pool)
	srv := newN8Server(t, pg, pg, &n8NoRelayStore{t: t}, nil, nil, &Batch4{Store: pg}, nil, nil)
	srv.cfg.SiteURL = file.ShareURLInputs.SiteURL
	srv.cfg.AppStoreURL = file.ShareURLInputs.AppStoreURL
	srv.now = func() time.Time { return anchor }
	handler := srv.Handler()

	var replayed, goMasks, goCodes, goCreated int
	for _, c := range file.Cases {
		if c.Group != "onboarding" {
			continue
		}
		replayed++
		// 已注销账号那两条：Python 的 get_user 过滤掉 deleted ⇒ user is None ⇒
		// **只写 profile**，一行剧本都不写。Go 若把三处写成同一个条件，
		// 这里要么 404 要么少一行——两者都是字节差，不需要额外断言。
		t.Run(c.Name, func(t *testing.T) {
			goMasks += replayN8Case(t, handler, c)
		})
		goCodes += c.CodeMasks
		goCreated += c.CreatedAtMasks
	}
	if replayed == 0 {
		t.Fatal("onboarding 组为空：分组键 group 漂移了")
	}
	// 「不是空跑」的判据：这一组必须有现算码的用例（否则惰性生成分支消失了）、
	// 有新写 profile 的用例（否则 INSERT 支消失了）、也有 uuid 主键。
	// 这三个数取的是**夹具声明**的次数——Go 侧实际掩到的次数已在每条用例里
	// 逐条比过（replayN8Case 里那三条计数），所以这里只需保证「表不是全 0」。
	if goMasks == 0 || goCodes == 0 || goCreated == 0 {
		t.Fatalf("onboarding 组掩码计数为 0（uuid %d / 码 %d / created_at %d）："+
			"profile 的 INSERT 支或惰性生成码那支没被走到，上面全绿不证明任何事",
			goMasks, goCodes, goCreated)
	}
	t.Logf("onboarding 组 %d 条逐字回放（锚点 %s，site_url=%q），"+
		"Go 侧掩码合计 uuid %d / 码 %d / created_at %d",
		replayed, anchor.Format(time.RFC3339), file.ShareURLInputs.SiteURL,
		goMasks, goCodes, goCreated)
}

// ── mood / reflection 组（批 5）：真库 + 冻时钟，四条掩码全为 0 ───────────────────

// n8Count 数一组用例里响应体带着某个片段的条数，用作「这一组不是空跑」的判据。
// 逐字比本身已经保证「Go 的字节＝Python 的字节」，但整组若都退化成了同一种形态
// （例如种子行没插进去 ⇒ 40 条全是空数组），上面依然全绿。
func n8Count(cases []n8Case, fragment string) int {
	var n int
	for _, c := range cases {
		if strings.Contains(c.ResponseText, fragment) {
			n++
		}
	}
	return n
}

// TestN8MoodContractBytes 是批 5 的 mood 验收闸：GET /mood/history 与 GET /mood/trend
// 的响应字节逐字等于真 FastAPI 录下来的那串。
//
// 这一组与批 3/批 4 最大的不同是**一条掩码都不许用**（夹具里 id/code/created_at
// 三个计数全为 0）：history 的 id 是种子的 `n8b1-…` 形状而不是 uuid4，created_at 是
// 按 ANCHOR 算好的绝对时刻。于是 `Z` 这个序列化形状是**逐字比出来的**，
// 不是靠格式测推的——这也是批 5 取证推翻「批 5 走 +00:00」那句推定的那件事。
//
// 时钟仍要钉在 recorded_at：trend 的窗口下界是 `utc_now() − days*86400`，
// 而 `mood-t-edge` 那三行里有一行正好压在 ANCHOR−14d 这一瞬、另一行差一秒落在窗外。
// 回放晚一秒，边界行就会被读进/读出窗口（响应里的数字跟着变）。
func TestN8MoodContractBytes(t *testing.T) {
	file := loadN8Fixture(t)
	pool := n8Pool(t)
	n8SeedRows(t, pool, file.Seed)
	anchor := n8Anchor(t, file)

	relay := &n8RecordingRelay{t: t}
	pg := store.NewPG(pool)
	srv := newN8Server(t, pg, pg, relay, nil, nil, nil,
		&Batch5{Moods: pg, Reflections: pg, Users: pg}, nil)
	srv.now = func() time.Time { return anchor }
	handler := srv.Handler()

	var cases []n8Case
	for _, c := range file.Cases {
		if c.Group != "mood" {
			continue
		}
		cases = append(cases, c)
		t.Run(c.Name, func(t *testing.T) {
			replayN8Case(t, handler, c)
		})
	}
	if len(cases) == 0 {
		t.Fatal("mood 组为空：分组键 group 漂移了")
	}
	// 四个形态各要有观测点：非空/空数组 × history/trend。
	// 空态尤其要紧——Go 的 nil 切片 marshal 成 null，而契约是 []。
	for fragment, why := range map[string]string{
		`"checkins":[{`: "history 的非空数组（真读到了种子行）",
		`"checkins":[]`: "history 的空态（必须是 []，不是 null）",
		`"labels":["`:   "trend 的非空标签（%m-%d 日历字段）",
		`"labels":[]`:   "trend 的空态（同上）",
	} {
		if n := n8Count(cases, fragment); n == 0 {
			t.Errorf("mood 组没有任何一条走到 %s（缺片段 %q）——上面全绿不证明任何事", why, fragment)
		}
	}
	// summary 走的是 15 语言文案副本：若整组都是 ASCII，说明语言依赖那条链没被走到
	// （夹具的 trend-*-header 六条与 zh-db-preference 就是为这一格存在的）。
	if n8Count(cases, `"labels":["10-`) == 0 {
		t.Errorf("mood 组没有任何一条 trend 带出月-日标签：窗口与折算那一步没被走到")
	}
	if nonASCII := n8NonASCIISummaryCount(cases); nonASCII == 0 {
		t.Errorf("mood 组没有任何一条非英文 summary：文案副本那一条链（copy.t）没被走到")
	}
	// 本域零外发：Python 侧 mood_service 一个分析事件都不打，Go 侧若「顺手」补一条
	// 就是灰度期的数据面差异，而响应字节看不出来。
	if got := relay.count(); got != 0 {
		t.Errorf("mood 组外发了 %d 条事件，Python 侧这个域一条都不打", got)
	}
	t.Logf("mood 组 %d 条逐字回放（锚点 %s，真读 users/mood_checkins，零掩码、零外发）",
		len(cases), anchor.Format(time.RFC3339))
}

// n8NonASCIISummaryCount 数 summary 里带非 ASCII 文案的用例条数（即走了本地化副本）。
func n8NonASCIISummaryCount(cases []n8Case) int {
	var n int
	for _, c := range cases {
		i := strings.Index(c.ResponseText, `"summary":"`)
		if i < 0 {
			continue
		}
		for _, r := range c.ResponseText[i+len(`"summary":"`):] {
			if r == '"' {
				break
			}
			if r > 0x7f {
				n++
				break
			}
		}
	}
	return n
}

// TestN8ReflectionContractBytes 是批 5 的 reflections 验收闸：GET /reflections 的
// 响应字节逐字等于录制那串。
//
// 这一组最要紧的三格都在**列原文**上，而它们都只能靠逐字比：
//   - related_memory_ids 的 NULL / "" 两种列值都给 `null`（falsy 判据，不是 IS NULL）；
//   - "," 给 ["",""]、"a,,b" 给 ["a","","b"]（段数完全由逗号位置决定）；
//   - 夹具种了一条 record_type="Reflection"（首字母大写）必须**不出现**在响应里
//     ——Go 侧若把等值写成宽松匹配，这一格立刻红。
func TestN8ReflectionContractBytes(t *testing.T) {
	file := loadN8Fixture(t)
	pool := n8Pool(t)
	n8SeedRows(t, pool, file.Seed)
	anchor := n8Anchor(t, file)

	relay := &n8RecordingRelay{t: t}
	pg := store.NewPG(pool)
	srv := newN8Server(t, pg, pg, relay, nil, nil, nil,
		&Batch5{Moods: pg, Reflections: pg, Users: pg}, nil)
	srv.now = func() time.Time { return anchor }
	handler := srv.Handler()

	var cases []n8Case
	for _, c := range file.Cases {
		if c.Group != "reflection" {
			continue
		}
		cases = append(cases, c)
		t.Run(c.Name, func(t *testing.T) {
			replayN8Case(t, handler, c)
		})
	}
	if len(cases) == 0 {
		t.Fatal("reflection 组为空：分组键 group 漂移了")
	}
	for fragment, why := range map[string]string{
		`"data":[]`:                         "空态给空数组而不是 null",
		`"related_memory_ids":null`:         "列原文 NULL 与空串并成 null 那一支",
		`"related_memory_ids":["a","","b"]`: "split 的段数由逗号位置决定那一支",
		`"related_memory_ids":["",""]`:      `只有两个空 ID 的那一支（"," ⇒ ["",""]）`,
	} {
		if n := n8Count(cases, fragment); n == 0 {
			t.Errorf("reflection 组没有任何一条走到 %s（缺片段 %q）", why, fragment)
		}
	}
	// 精确等值那一格：同一个人名下种了 5 行，其中 daily_insight(g1)、
	// weekly_summary(g2) 与 "Reflection"(g4，首字母大写) 三行**都不该出现在响应里**。
	// 只断言「不出现」会被整组空响应蒙过，所以同时钉住 reflection 那两行确实出现了。
	for _, forbiddenID := range []string{"n8b1-refl-types-g1", "n8b1-refl-types-g2",
		"n8b1-refl-types-g4"} {
		for _, c := range cases {
			if strings.Contains(c.ResponseText, forbiddenID) {
				t.Errorf("%s 的响应里有 %s——record_type 的等值被写成了宽松匹配"+
					"（夹具那行给的是**列原文**类型，Python 侧走的是 SQL 的 =）",
					c.Name, forbiddenID)
			}
		}
	}
	if n8Count(cases, `"id":"n8b1-refl-types-g0"`) == 0 {
		t.Errorf("reflection 组没有一条响应带出 n8b1-refl-types-g0：上面那三条「不出现」" +
			"是被空响应当过去的，等值那一格没有观测点")
	}
	if got := relay.count(); got != 0 {
		t.Errorf("reflections 组外发了 %d 条事件，Python 侧 list 一条都不打", got)
	}
	t.Logf("reflection 组 %d 条逐字回放（锚点 %s，真读 growth_records，零掩码、零外发）",
		len(cases), anchor.Format(time.RFC3339))
}

// ── 批 6（memories）组：真库、两条纯读、零掩码、零外发 ─────────────────────────

// n8MemoryIDRe 按响应里出现的次序取出各行的主键（只用于同刻那一组的序核对）。
var n8MemoryIDRe = regexp.MustCompile(`"id":"([^"]+)"`)

// n8IsMemoriesList 分清「列表」与「详情」：详情那条 path 一定多一个段。
// 这一分不是形式主义——两支的 WHERE 不同（列表过 status 与 deleted、详情都不过），
// 把两条混在一组断言里就会让「列表漏了过滤」这种红看起来像详情坏了。
func n8IsMemoriesList(path string) bool {
	return path == "/api/v1/memories" || strings.HasPrefix(path, "/api/v1/memories?")
}

// TestN8MemoriesContractBytes 是批 6 的验收闸：memories 两条端点的响应字节逐字等于
// 真 FastAPI 录下来的那串。
//
// 这一组**不冻时钟**（与批 2–5 的唯一不同）：两条端点里没有由「今天」推出的字段，
// `created_at` 全是列原文。播种仍走绝对时刻，因为 LIMIT 截断与同刻次级键的落点
// 必须与录制时同一批行——那是**响应里的 id**，不是格式差异。
//
// 三条只能靠逐字比才成立的东西：
//   - conviction 是这批第一个**非整型浮点**列：`0.0`／`1.0`／`0.333` 三形都进响应，
//     Go 若用 encoding/json 的 float，`0.0` 会写成 `0`（字节不同、值相同，
//     而灰度期的前端不做 JSON 数值比较，它比的是渲染结果）。
//   - transition_reason 的 NULL 与 `""` 给 null 与 `""` **两种字节**（pydantic 的
//     Optional[str] 不折空串），夹具各留一格。
//   - labels 是**解析出来的**数组而不是列原文：坏 JSON／顶层数组／空数组全给 null，
//     非字符串元素走 Python 的 `str()`，嵌套容器走 `repr()`（含引号选择那一支）。
//
// ⚠️ 这两条 path 今天**不能上刀**（决策点 D-10）：同 path 还挂着 POST 与 DELETE，
// Ingress 的 path 匹配不看方法 ⇒ 声明键 CLOSEN_CUTOVER_MEMORIES、但不给 path，
// 所以这一组的绿证明的是「Go 已经能逐字节回答」，不证明「用户能走到」。
func TestN8MemoriesContractBytes(t *testing.T) {
	file := loadN8Fixture(t)
	pool := n8Pool(t)
	n8SeedRows(t, pool, file.Seed)

	relay := &n8RecordingRelay{t: t}
	pg := store.NewPG(pool)
	srv := newN8Server(t, pg, pg, relay, nil, nil, nil, nil, &Batch6{Memories: pg})
	handler := srv.Handler()

	var cases, listCases []n8Case
	for _, c := range file.Cases {
		if c.Group != "memories" {
			continue
		}
		cases = append(cases, c)
		if n8IsMemoriesList(c.Path) {
			listCases = append(listCases, c)
		}
		t.Run(c.Name, func(t *testing.T) {
			replayN8Case(t, handler, c)
		})
	}
	if len(cases) == 0 {
		t.Fatal("memories 组为空：分组键 group 漂移了")
	}
	if len(listCases) == 0 {
		t.Fatal("memories 组里没有列表用例：path 形状漂移，两支 WHERE 的差别没证人")
	}

	// 每个观测点都要有人证：任一片段整组不出现就说明对应那条复刻跑了偏，
	// 而上面那些逐字比会因为「两侧都偏了」照样绿（口径同批 5 的 reflection 组）。
	for fragment, why := range map[string]string{
		`"data":[]`:                             "空态给空数组而不是 null",
		`"conviction":0.0`:                      "conviction 走 pyFloatValue（encoding/json 会写成 0）",
		`"conviction":1.0`:                      "另一端界同样要带小数点",
		`"conviction":0.333`:                    "非整值浮点的 repr 形状",
		`"transition_reason":""`:                "空串那一支（pydantic 不折空串）",
		`"transition_reason":null`:              "NULL 那一支——两支必须在同一组里同时存在",
		`"superseded_at":"20`:                   "可空时刻列的有值支（Z 形，不是 +00:00）",
		`"labels":["commitment"]`:               "生产写入形状那一支",
		`"labels":["1","True","False","None"]`:  "非字符串元素走 Python 的 str()",
		`"labels":["['a', 1]","{'k': 'v'}"]`:    "嵌套容器走 repr()（list 与 dict 两支）",
		`"labels":["[\"it's\", 'say \"hi\"']"]`: "repr 的引号选择两支（Go 的 pyReprString）",
		`"similarity":null,"regressed":false`:   "两键恒为 null/false，且排在 labels 之前",
		`"code":40403`:                          "不存在／他人／软删／匿名四支必须同串",
	} {
		if n8Count(cases, fragment) == 0 {
			t.Errorf("memories 组没有任何一条走到 %s（缺片段 %q）", why, fragment)
		}
	}

	// 列表过 status='current' 与 deleted=false、详情两者都不过 ⇒ 这四行**只该**出现在详情里。
	// 只断言「列表里不出现」会被整组空响应当过去，所以同时钉住 st-current 确实进了列表。
	for _, forbiddenID := range []string{
		"n8b1-mem-status-st-super", "n8b1-mem-status-st-revert",
		"n8b1-mem-status-st-other", "n8b1-mem-del-dl-hidden",
	} {
		for _, c := range listCases {
			if strings.Contains(c.ResponseText, forbiddenID) {
				t.Errorf("%s 的列表响应里有 %s——WHERE 少了 status 或 deleted 过滤，"+
					"两侧会从同一批行里读出不同的列表（详情那条**允许**读到它，别一起改）",
					c.Name, forbiddenID)
			}
		}
	}
	if n8Count(listCases, `"id":"n8b1-mem-status-st-current"`) == 0 {
		t.Errorf("列表用例一个都没带出 n8b1-mem-status-st-current：上面四条「不出现」" +
			"是被空响应当过去的，两支 WHERE 的差别没有观测点")
	}

	// 同刻那一组：期望序由 id DESC 定，而它既不等于插入序也不等于逆插入序
	// （夹具把 id 最小的那行插在中间，判据来自批 5 的 M-G 空转实测）。
	// limit=1 那一格钉的是「同刻里谁活下来」——次级键的真实后果是截断落点，不是可读序。
	for name, want := range map[string][]string{
		"memories-tie": {"n8b1-mem-tie-tie-z", "n8b1-mem-tie-tie-m",
			"n8b1-mem-tie-tie-a", "n8b1-mem-tie-tie-old"},
		"memories-tie-limit-1": {"n8b1-mem-tie-tie-z"},
	} {
		var found *n8Case
		for i := range cases {
			if cases[i].Name == name {
				found = &cases[i]
				break
			}
		}
		if found == nil {
			t.Errorf("夹具里没有 %s 这一格：同刻次级键没有字节证人", name)
			continue
		}
		got := n8MemoryIDRe.FindAllStringSubmatch(found.ResponseText, -1)
		var ids []string
		for _, m := range got {
			if strings.HasPrefix(m[1], "n8b1-mem-tie-") {
				ids = append(ids, m[1])
			}
		}
		if strings.Join(ids, ",") != strings.Join(want, ",") {
			t.Errorf("%s 的行序是 %v，want %v——created_at 同瞬时次级键没生效或方向反了",
				name, ids, want)
		}
	}

	// category 的两支互为对照：空串在 Python 是假值（不进 WHERE ⇒ 全量，含 category="" 那行），
	// 一个空格是**真值**（进 WHERE ⇒ 空数组）。把空串当一次过滤就会少三行。
	if n8Count(listCases, `"id":"n8b1-mem-cat-ct-blank"`) == 0 {
		t.Errorf("没有任何列表响应带出 category=\"\" 那一行：`?category=` 的假值判据没证人")
	}

	// 本域零外发：memory_service 的这两条一个分析事件都不打（实测 grep capture/posthog 零命中），
	// Go 侧「顺手补一条」就是灰度期的数据面差异，而响应字节看不出来。
	if got := relay.count(); got != 0 {
		t.Errorf("memories 组外发了 %d 条事件，Python 侧这两条读一条都不打", got)
	}
	t.Logf("memories 组 %d 条逐字回放（列表 %d／详情 %d，真读 memories，零掩码、零外发、不冻时钟）",
		len(cases), len(listCases), len(cases)-len(listCases))
}

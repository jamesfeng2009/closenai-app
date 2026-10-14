// N8 批 1 的契约回放闸：go-core/testdata/n8_contract.json 由
// scripts/gen_n8_contract_goldens.py 从**真跑的 FastAPI**（同一份 openapi、同一个 PG）
// 录成字节，这里逐字重放。
//
// 为什么不是「跑几个用例看看像不像」：N8 的验收判据是「切流前后响应契约 diff 为空」，
// 而 diff 为空这件事只有拿另一侧录下来的**原始字节**当基准才可证。
// 夹具里各块各有闸别：
//   - occurred_at_corpus —— speedate 解析器的状态机（无需 HTTP、无需库）
//   - week_window_corpus / push_moment_corpus —— 两个日历核（无需 HTTP、无需库）
//   - validation 组 —— 400 体的每一字节（无需库：校验失败根本进不到 store）
//   - seeded / curriculum / journey 组 —— 200/404 体（必须真读同一批行、同一次播种；
//     后两组的时钟钉在 recorded_at）
package httpapi

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

type n8CorpusEntry struct {
	Input     json.RawMessage `json:"input"`
	OK        bool            `json:"ok"`
	Iso       string          `json:"iso"`
	UTCMicros int64           `json:"utc_micros"`
	Type      string          `json:"type"`
	Error     *string         `json:"error"`
}

// 夹具的**键名与空块**都是闸：生成脚本改了输出结构而这里没跟着改，解码会得到空切片，
// 于是三节测全在空跑（键名带 json tag 的错拼不会报错，只会静默变空）。
// loadN8Fixture 因此对每一块都拒绝为空。
type n8Fixture struct {
	Comment            string   `json:"_comment"`
	TraceID            string   `json:"trace_id"`
	MaskedFields       []string `json:"masked_fields"`
	GeneratedAtSamples []string `json:"generated_at_samples"`
	// RecordedAt 是录制那一刻的 UTC 时钟；回放时把 Server.now 冻在这里。
	// 没有它，curriculum 的 week_start 会随重放日期整周平移（见 server.go 的 now 字段）。
	RecordedAt       string          `json:"recorded_at"`
	Seed             n8Seed          `json:"seed"`
	OccurredAtCorpus []n8CorpusEntry `json:"occurred_at_corpus"`
	// WeekWindowCorpus 钉的是批 2 的日历核：给定「瞬间 + 时区名」，Python 侧现行
	// 实现算出的本地周一/今天/窗口两端是什么。这些形状不能只靠 HTTP 用例取证——
	// 录制与回放隔几分钟，week_start 永远落在同一周里。
	WeekWindowCorpus []n8WindowEntry `json:"week_window_corpus"`
	// PushMomentCorpus 钉的是批 3 的推送时刻核：给定「users.push_time 列原文 +
	// 时区名 + 剧本日」，Python 的 `_push_moment` 算出哪一个 UTC 瞬间。
	// 它不能只靠 journey 那 46 条 HTTP 用例取证——录制与回放只隔几分钟，落在 DST
	// 跳变日的那几条永远不会被重新走到，而缺位墙钟的换算口径正是这一批最容易写歪的地方。
	PushMomentCorpus []n8PushEntry `json:"push_moment_corpus"`
	Cases            []n8Case      `json:"cases"`
}

// n8PushEntry 一行＝一个 (push_time, 时区, 日历日) 的推送时刻观测，want 由 Python 现算。
// PushTime 用指针区分「列值为 NULL」与「列值是空串」——这两者在 `_push_moment` 里走
// 同一条 `or "09:00"`，但在列上是两种值，夹具必须都能表达。
// Transition=true 表示这一天在该时区有 DST 跳变（本地午夜缺位或重复）。
type n8PushEntry struct {
	TZ         string  `json:"tz"`
	PushTime   *string `json:"push_time"`
	Day        string  `json:"day"`
	Want       string  `json:"want"`
	Transition bool    `json:"transition"`
}

// n8WindowEntry 一行＝一个 (时区, 瞬间) 的窗口观测，四个值全部由 Python 现算。
// Gap=true 表示这一行本地今天的零点不唯一（春季拨快缺位，或秋季回拨重复）。
type n8WindowEntry struct {
	TZ     string `json:"tz"`
	At     string `json:"at"`
	Monday string `json:"monday"`
	Today  string `json:"today"`
	Start  string `json:"start"`
	End    string `json:"end"`
	Gap    bool   `json:"gap"`
}

type n8Seed struct {
	Users []struct {
		ID               string `json:"id"`
		Language         string `json:"language"`
		AnalyticsConsent bool   `json:"analytics_consent"`
		AIConsent        bool   `json:"ai_consent"`
		Timezone         string `json:"timezone"`
		Status           string `json:"status"`
		// PushTime 是 users.push_time 的**列原文**（VARCHAR(5)）。nil⇒SQL NULL，
		// 与空串在 Python 的 `user.push_time or "09:00"` 里落同一分支但列上不同；
		// "7"（一个字符）走 int() 成功那支 ⇒ 语义是 00:07，不是 07:00。
		PushTime *string `json:"push_time"`
		// CreatedAt 是**绝对** UTC 串：journey 的剧本基准日由它推出，
		// 用 days_ago 这类相对口径会让「重放当天算出的 day_no」随日期漂移。
		CreatedAt string `json:"created_at"`
	} `json:"users"`
	MoodCheckins []struct {
		ID        string `json:"id"`
		UserID    string `json:"user_id"`
		MoodEmoji string `json:"mood_emoji"`
		// DaysAgo 是**相对当下**的播种口径（批 1 沿用）：discovery 的响应里没有日历
		// 字段，只有 7/14 天窗口，两侧各用真实当下读同一批行即可；换成绝对时间戳反而
		// 会让跨过窗口边界的回放不再是「同一批行」。
		DaysAgo float64 `json:"days_ago"`
	} `json:"mood_checkins"`
	// JourneyEvents 是批 2（curriculum）的播种面：payload / memory_refs 给的是
	// **要插进 TEXT 列的原文**（null 就是 SQL NULL——parse_json 的空值分支只能这么造），
	// 两侧插同一串字节，解析结果才同源。
	JourneyEvents []struct {
		ID          string  `json:"id"`
		UserID      string  `json:"user_id"`
		EventKind   string  `json:"event_kind"`
		DayNo       int     `json:"day_no"`
		ScheduledAt string  `json:"scheduled_at"`
		Payload     *string `json:"payload"`
		MemoryRefs  *string `json:"memory_refs"`
		// 这三列批 1/批 2 靠 server_default（'pending'/NULL/false）就够了，因为那两个域
		// 只读 scheduled_at/payload。journey 的 GET 把三列全列进响应、unseen_count 的
		// 判据就是 `status='sent' AND acknowledged IS FALSE`，所以批 3 必须能播种四种
		// status 与「sent 但未读 / 已读」两种组合。
		Status       string  `json:"status"`
		SentAt       *string `json:"sent_at"`
		Acknowledged bool    `json:"acknowledged"`
	} `json:"journey_events"`
	// OnboardingProfiles 只需要 created_at 一列：`ensure_prefilled` 的基准日取
	// `profile.created_at if profile and profile.created_at else user.created_at`，
	// 所以这里刻意区分三种情况——没有行、有行但列 NULL、有行且有值。
	OnboardingProfiles []struct {
		ID        string  `json:"id"`
		UserID    string  `json:"user_id"`
		CreatedAt *string `json:"created_at"`
	} `json:"onboarding_profiles"`
}

type n8Case struct {
	Name         string            `json:"name"`
	Group        string            `json:"group"`
	Method       string            `json:"method"`
	Path         string            `json:"path"`
	Headers      map[string]string `json:"headers"`
	RequestRaw   *string           `json:"request_raw"`
	Status       int               `json:"status"`
	ResponseText string            `json:"response_text"`
	// IDMasks 是录制侧把响应体里 uuid4 形状的主键换成 `<event_id>` 的次数。
	// Go 侧必须掩到**同样多次**：只看「掩完后字节相等」会把「行数变了」这种最要紧的
	// 发散吃掉——预写幂等那条用例就靠这个数（第二次访问仍必须是 6 次，不是 12 次）。
	IDMasks int `json:"id_masks"`
}

func loadN8Fixture(t *testing.T) n8Fixture {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "testdata", "n8_contract.json"))
	if err != nil {
		t.Fatalf("read testdata/n8_contract.json（生成方式见 scripts/gen_n8_contract_goldens.py）: %v", err)
	}
	var file n8Fixture
	if err := json.Unmarshal(raw, &file); err != nil {
		t.Fatalf("decode n8_contract.json: %v", err)
	}
	for name, empty := range map[string]bool{
		"occurred_at_corpus":       len(file.OccurredAtCorpus) == 0,
		"cases":                    len(file.Cases) == 0,
		"seed.users":               len(file.Seed.Users) == 0,
		"seed.onboarding_profiles": len(file.Seed.OnboardingProfiles) == 0,
		"masked_fields":            len(file.MaskedFields) == 0,
		"week_window_corpus":       len(file.WeekWindowCorpus) == 0,
		"push_moment_corpus":       len(file.PushMomentCorpus) == 0,
	} {
		if empty {
			t.Fatalf("夹具的 %s 为空——键名漂移或生成脚本没落这一块，本节测会在空跑", name)
		}
	}
	return file
}

// n8Divergence 是一条已登记的发散点，**逐字记 Python 侧的实测形态**。
//
//   - `"92233720368547758.7"`：µs 还在 int64 内，但 CPython 的换算走浮点，回绕出一个
//     「看似合法」的 4892-10-07T21:52:49.312625+00:00 ⇒ Python **接受**、Go 拒绝。
//   - `"922337203685477580.7"`：Python 落回日期解析、报 invalid date separator，
//     Go 报 dates after 9999 ⇒ 两侧都拒，只是错误词不同。
//
// Python 侧的期望写死在这里而不是从夹具现读，是为了让「夹具换了形态」也变红：
// 见 TestN8OccurredAtDivergenceWhitelistStillExplains。两条均已按 2026-10-14 登记进 tasks.md。
type n8Divergence struct {
	pythonOK     bool
	pythonIso    string
	pythonType   string
	pythonError  string
	goRejectWord string
}

func (d n8Divergence) pythonSays() string {
	if d.pythonOK {
		return "accept iso=" + d.pythonIso
	}
	return "reject type=" + d.pythonType + " error=" + d.pythonError
}

func (d n8Divergence) matches(entry n8CorpusEntry) bool {
	if d.pythonOK {
		return entry.OK && entry.Iso == d.pythonIso
	}
	if entry.OK || entry.Type != d.pythonType {
		return false
	}
	got := ""
	if entry.Error != nil {
		got = *entry.Error
	}
	return got == d.pythonError
}

var n8SpeedateDivergences = map[string]n8Divergence{
	`"92233720368547758.7"`: {
		pythonOK:     true,
		pythonIso:    "4892-10-07T21:52:49.312625+00:00",
		goRejectWord: after9999Word,
	},
	`"922337203685477580.7"`: {
		pythonType:   "datetime_from_date_parsing",
		pythonError:  "invalid date separator, expected `-`",
		goRejectWord: after9999Word,
	},
}

func TestN8OccurredAtCorpus(t *testing.T) {
	file := loadN8Fixture(t)
	var skipped int
	for _, entry := range file.OccurredAtCorpus {
		name := string(entry.Input)
		if len(name) > 60 {
			name = name[:60] + "…"
		}
		t.Run(name, func(t *testing.T) {
			value, perr := pyJSONLoads(entry.Input)
			if perr != nil {
				t.Fatalf("夹具的 input 自己就不是合法 JSON：%v", perr)
			}
			got, gotErr := ParseOccurredAt(value)
			if div, skip := n8SpeedateDivergences[string(entry.Input)]; skip {
				if gotErr == nil {
					t.Fatalf("白名单条目 %s 现在被 Go 接受了（登记理由：Python %s / Go %s）"+
						"——实现变了，要么删条目要么重新取证",
						string(entry.Input), div.pythonSays(), div.goRejectWord)
				}
				if gotErr.word != div.goRejectWord {
					t.Fatalf("白名单条目 %s 的 Go 侧错误词从 %q 变成 %q——登记的口径已过期",
						string(entry.Input), div.goRejectWord, gotErr.word)
				}
				skipped++
				return
			}
			switch {
			case entry.OK && gotErr != nil:
				t.Fatalf("Python 接受 %s ⇒ iso=%s，Go 却拒绝：%s / %s",
					entry.Input, entry.Iso, gotErr.kind, gotErr.word)
			case !entry.OK && gotErr == nil:
				t.Fatalf("Python 拒绝 %s（type=%s error=%v），Go 却接受成 %s",
					entry.Input, entry.Type, entry.Error, got.Isoformat())
			case !entry.OK:
				if gotErr.kind.String() != entry.Type {
					t.Fatalf("type 不符：Python=%s Go=%s（输入 %s）",
						entry.Type, gotErr.kind, entry.Input)
				}
				want := ""
				if entry.Error != nil {
					want = *entry.Error
				}
				if gotErr.word != want {
					t.Fatalf("错误词不符：Python=%q Go=%q（输入 %s）",
						want, gotErr.word, entry.Input)
				}
			default:
				if got.Isoformat() != entry.Iso {
					t.Fatalf("isoformat 不符：Python=%s Go=%s（输入 %s）",
						entry.Iso, got.Isoformat(), entry.Input)
				}
				if got.UTCMicros() != entry.UTCMicros {
					t.Fatalf("UTC 微秒不符：Python=%d Go=%d（输入 %s）",
						entry.UTCMicros, got.UTCMicros(), entry.Input)
				}
			}
		})
	}
	t.Logf("occurred_at 语料 %d 条逐字比对，其中 %d 条走已登记白名单",
		len(file.OccurredAtCorpus), skipped)
	if skipped != len(n8SpeedateDivergences) {
		t.Fatalf("白名单声明了 %d 条却在语料里命中 %d 条——有条目已经失效",
			len(n8SpeedateDivergences), skipped)
	}
}

// TestN8OccurredAtDivergenceWhitelistStillExplains 防的是白名单烂掉：
// 每条跳过都必须仍然在夹具里、且 Python 侧仍然是登记的那一形态。
func TestN8OccurredAtDivergenceWhitelistStillExplains(t *testing.T) {
	file := loadN8Fixture(t)
	seen := map[string]bool{}
	for _, entry := range file.OccurredAtCorpus {
		key := string(entry.Input)
		div, wanted := n8SpeedateDivergences[key]
		if !wanted {
			continue
		}
		seen[key] = true
		if !div.matches(entry) {
			var recorded string
			if entry.OK {
				recorded = "accept iso=" + entry.Iso
			} else {
				recorded = "reject type=" + entry.Type
				if entry.Error != nil {
					recorded += " error=" + *entry.Error
				}
			}
			t.Errorf("%s 的 Python 侧形态已变：登记为 %q，夹具现在录到 %q——白名单要重新取证",
				key, div.pythonSays(), recorded)
		}
		value, err := pyJSONLoads(entry.Input)
		if err != nil {
			t.Fatalf("%s: %v", key, err)
		}
		if _, gotErr := ParseOccurredAt(value); gotErr == nil ||
			gotErr.word != div.goRejectWord {
			t.Errorf("%s: Go 侧现在是 %v，不再是登记的 %q——白名单理由已过期",
				key, gotErr, div.goRejectWord)
		}
	}
	for key, div := range n8SpeedateDivergences {
		if !seen[key] {
			t.Errorf("白名单条目 %s 在夹具里已不存在（登记：%s）——请删掉这条跳过", key, div.pythonSays())
		}
	}
}

// TestN8PyISOFormMatchesPydanticSerializer 补的是掩码留下的洞。
//
// generated_at 的**值**逐字比一定被掩码遮掉（录制与回放天然差一个时间戳），
// 但它的**格式**仍然是契约：客户端拿到的是字符串日期。期望值不是推的，是 2026-10-14
// 用响应面同一个序列化器实测的（backend/.venv，`TypeAdapter(datetime).dump_json`）：
//
//	µs=888714 → "…:41.888714Z"   µs=100000 → "…:05.100000Z"（固定 6 位，不去尾零）
//	µs=1      → "…:05.000001Z"   µs=0      → "…:05Z"（整段小数省略）
//
// 顺带记一句为什么不用 `jsonable_encoder`：它给的是 "+00:00"，而夹具里录到的真实字节
// 是 "Z" ——FastAPI 的 response_model 走的是 pydantic-core 序列化，两者不同。
// Go 侧只有 `time.Now().UTC()` 进这个函数，非 UTC 入参会被换算（Python 侧保留原 offset），
// 这一支在本端点上不可达，故不钉。
func TestN8PyISOFormMatchesPydanticSerializer(t *testing.T) {
	utc := time.Date(2026, 10, 14, 3, 4, 5, 0, time.UTC)
	for _, tc := range []struct {
		at   time.Time
		want string
	}{
		{time.Date(2026, 10, 14, 11, 23, 41, 888714000, time.UTC), "2026-10-14T11:23:41.888714Z"},
		{utc.Add(100 * time.Millisecond), "2026-10-14T03:04:05.100000Z"},
		{utc.Add(time.Microsecond), "2026-10-14T03:04:05.000001Z"},
		{utc, "2026-10-14T03:04:05Z"},
	} {
		if got := pyISO(tc.at); got != tc.want {
			t.Errorf("pyISO = %q，Python 侧 %q", got, tc.want)
		}
	}
}

// TestN8FixtureCorpusCoversBothRejectShapes 是夹具自身的闸：
// 语料必须同时含「拒绝」与「通过」两类，否则上一节的断言全在空跑。
func TestN8FixtureCorpusCoversBothRejectShapes(t *testing.T) {
	file := loadN8Fixture(t)
	var accepted, rejected, typed, fromParsing int
	for _, entry := range file.OccurredAtCorpus {
		switch {
		case entry.OK:
			accepted++
		case entry.Type == "datetime_type":
			typed++
			rejected++
		case entry.Type == "datetime_from_date_parsing":
			fromParsing++
			rejected++
		default:
			rejected++
		}
	}
	if accepted == 0 || rejected == 0 {
		t.Fatalf("语料只剩一类：accepted=%d rejected=%d", accepted, rejected)
	}
	if typed == 0 || fromParsing == 0 {
		t.Fatalf("三种错误类型至少要有两类各自观测点：type=%d from_date_parsing=%d",
			typed, fromParsing)
	}
	t.Logf("occurred_at 语料 %d 条：通过 %d / 拒绝 %d（其中 datetime_type %d、"+
		"datetime_from_date_parsing %d）", len(file.OccurredAtCorpus), accepted, rejected,
		typed, fromParsing)
}

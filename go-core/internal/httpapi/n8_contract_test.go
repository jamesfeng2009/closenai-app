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
//   - mood / reflection 组（批 5）—— 也是 200 体、也钉在 recorded_at：组名说的是
//     「回放要不要真库＋冻时钟」，不是域。两组的响应里有 created_at 与 `%m-%d` 标签，
//     所以按**绝对时刻**播种（见生成脚本的 M()/G()）
//   - mood_copy_corpus / language_resolve_corpus（批 5）—— 两枚**核闭包**（无需 HTTP）：
//     前者是 60 格文案副本，后者是 23 格语言归一；HTTP 用例每格只能命中一角，
//     静默分叉只有整张表列出来才拦得住
package httpapi

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
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
	// TemplateCorpus 钉的是批 4 的措辞闸（safety_service.filter_template）：
	// 两份 first_read 模板能产出的**每一条**候选文案（断言×选项、filler、closer、
	// summary_frame×16 persona），want 是 Python 现算的输出。
	// 为什么不能只靠 HTTP 用例：本域所有模板串实测一条都不该被闸掉，用例只能证「没掉」；
	// 而 Go 侧若把某条吞了，那张卡只是**静默少一句**——响应形状仍然合法。
	// 闭包比输出文本才叫「两侧判据同源」。
	TemplateCorpus []n8TemplateEntry `json:"template_corpus"`
	// PersonaUpperCorpus 钉的是 CPython 的**全案** str.upper()（不是 strings.ToUpper）：
	// 整个 BMP 里「upper 会变样，且结果是全 ASCII 或多码位」的码位，逐个给出四位输入的结果。
	// 它必须成表而不能只靠 "ßß"⇒"SSSS" 那一条用例：`ı`⇒`I`、`ſ`⇒`S` 这两支是
	// 「非 ASCII 字符拼出合法 persona」的**唯一**来源，用例面只能各挑一个字符。
	PersonaUpperCorpus []n8UpperEntry `json:"persona_upper_corpus"`
	// ReferralCorpus 钉的是 `site_url.rstrip('/')`：剥一串斜杠而不是一个。
	// 深链会**落进 first_read 列**并被 restore 复现，配置读歪一支就是切流前后不同链接。
	ReferralCorpus []n8LinkEntry `json:"referral_corpus"`
	// MoodCopyCorpus 钉的是批 5 的文案面：15 locale × 4 档判据 = **60 格闭包**，
	// want 由 Python 的 `copy.t("mood", …)` 现算（含它自己的两级回退）。
	// 为什么不能只靠 HTTP 用例：`/mood/trend` 每条用例只命中一格，
	// Go 侧那 60 条是 backend/data/copy/*.json 的逐字节副本——副本与源文件分叉时
	// 只有被命中的那一格会红，其余 59 格静默错。整张表列出来才叫闭包。
	MoodCopyCorpus []n8MoodCopyEntry `json:"mood_copy_corpus"`
	// LanguageResolveCorpus 钉的是批 5 摸到的那**两支语言归一**的第一支
	// （`language.py:30`：strip → split("-")[0] → lower → 精确成员判定）。
	// 它和 `copy.py:35 locale_for`（lower → strip → 前缀匹配）在同一个原始 header 上
	// 可以给出不同结论（实测 `zh_CN`：前者判「没给语言」、后者判 zh），
	// 而 `/mood/trend` 的 summary 恰好两级都走，所以两支分开钉。
	LanguageResolveCorpus []n8LanguageEntry `json:"language_resolve_corpus"`
	// ShareURLInputs 是录制侧 settings 的两个原文；Go 回放必须拿它们构造 cfg，
	// 而不是用 Go 侧的默认值（见生成脚本 share_url_inputs 的注释）。
	ShareURLInputs struct {
		SiteURL     string `json:"site_url"`
		AppStoreURL string `json:"app_store_url"`
	} `json:"share_url_inputs"`
	Cases []n8Case `json:"cases"`
}

// n8TemplateEntry 一行＝一条模板文案过一遍闸的观测：want 由 Python 现算。
// Reachable 标出真实流程走得到的那些（pattern×**同题**选项）；交叉格只是保险。
type n8TemplateEntry struct {
	Locale    string `json:"locale"`
	Kind      string `json:"kind"`
	Raw       string `json:"raw"`
	Reachable bool   `json:"reachable"`
	Want      string `json:"out"`
}

// n8UpperEntry 一行＝一个码位四位输入后的 upper 结果；ASCII4＝结果是否成为合法 persona。
type n8UpperEntry struct {
	Raw    string `json:"raw"`
	Upper  string `json:"upper"`
	ASCII4 bool   `json:"ascii4"`
}

// n8LinkEntry 一行＝一个 site_url 变体的深链输出。
type n8LinkEntry struct {
	SiteURL string `json:"site_url"`
	Link    string `json:"link"`
}

// n8MoodCopyEntry 一行＝一个 (locale, 判据键) 的 trend summary 文案，want 由 Python 现算。
// Key 形如 "trend.high"——Go 侧要把它喂给四档判据的哪一个是由 Key 唯一确定的，
// 所以这一格测的不只是文案，还有「avg>=4 走 high」这条映射。
type n8MoodCopyEntry struct {
	Locale string `json:"locale"`
	Key    string `json:"key"`
	Want   string `json:"out"`
}

// n8LanguageEntry 一行＝一个原始 X-Language 值的归一结果；Want=nil 在 Python 侧是
// 「这个头等于没给」（回落读库），Go 侧对应 normalizeLanguageCode 的 ok=false。
type n8LanguageEntry struct {
	Raw  string  `json:"raw"`
	Want *string `json:"want"`
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
		// ReferralCode 是 users.referral_code 的**列原文**（批 4）：
		// nil⇒NULL ⇒ POST 走惰性随机生成那一支（share_url 只能掩）；
		// 有值 ⇒ submit 复用同一个码，于是 share_url **全串逐字可比**，
		// 那正是「已有码不许被换」的响应面证据。列上有 UNIQUE（播种时实测撞过），
		// 所以种子码两两不同。
		ReferralCode *string `json:"referral_code"`
	} `json:"users"`
	MoodCheckins []struct {
		ID        string `json:"id"`
		UserID    string `json:"user_id"`
		MoodEmoji string `json:"mood_emoji"`
		// DaysAgo 是**相对当下**的播种口径（批 1 沿用）：discovery 的响应里没有日历
		// 字段，只有 7/14 天窗口，两侧各用真实当下读同一批行即可；换成绝对时间戳反而
		// 会让跨过窗口边界的回放不再是「同一批行」。
		DaysAgo float64 `json:"days_ago"`
		// CreatedAt 非 nil ⇒ 这一行走**绝对**通道（批 5），此时 DaysAgo 必须缺席
		// （夹具不给 0，所以这里也不许在绝对分支里读它）。
		// 为什么批 5 要另开一条：`GET /mood/trend` 的 labels 是 `%m-%d` 日历字段，
		// 沿用相对口径会让整批标签随重放日期漂移（见生成脚本 `M()` 的注释）。
		CreatedAt *string `json:"created_at"`
		// 两列给的是**列原文**：nil⇒SQL NULL、""⇒空串。`get_history` 的
		// `r.mood_label or ""`(:315) 对这两者响应相同但列上不同，夹具两种都种，
		// 于是这里必须用指针而不是 string——把 NULL 折成 "" 会让 history-nulls
		// 那条用例失去「两侧都种了 NULL 行」这个前提。
		MoodLabel        *string `json:"mood_label"`
		AIInterpretation *string `json:"ai_interpretation"`
	} `json:"mood_checkins"`
	// GrowthRecords 是批 5 的 `GET /reflections` 读的那张表。
	// RelatedMemoryIDs 给**列原文**（nil⇒NULL、""⇒空串、","⇒两个空 ID、"a,,b"⇒三段）：
	// reflection_service.py:137 判的是 falsy，而真值那一支的段数完全由逗号位置决定。
	GrowthRecords []struct {
		ID               string  `json:"id"`
		UserID           string  `json:"user_id"`
		RecordType       string  `json:"record_type"`
		Content          string  `json:"content"`
		RelatedMemoryIDs *string `json:"related_memory_ids"`
		CreatedAt        string  `json:"created_at"`
	} `json:"growth_records"`
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
	// 批 3 只用得到 created_at；批 4 的 restore 把 persona_type / answers /
	// first_read / language **四列全读进响应**（first_read 的列原文还决定键序），
	// 所以这四列给的是要插进 TEXT/VARCHAR 列的**原文**（null⇒SQL NULL）。
	// 批 3 那几条种子的四列仍是 'D01' / '[]' / '{}' / 'en' 四个字面量——
	// 换了口径就会改动批 3 已结案用例的字节，所以生成脚本按 LEGACY_PROFILE_COLUMNS 原样带出。
	OnboardingProfiles []struct {
		ID          string  `json:"id"`
		UserID      string  `json:"user_id"`
		PersonaType *string `json:"persona_type"`
		Answers     *string `json:"answers"`
		FirstRead   *string `json:"first_read"`
		Language    *string `json:"language"`
		// created_at 保留批 3 的三态口径（没有行 / 有行但列 NULL / 有行且有值），
		// 因为 `ensure_prefilled` 的基准日取的就是这一列，回落 user.created_at。
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
	// CodeMasks / CreatedAtMasks 是批 4 的另两条计数，口径与 IDMasks 相同，
	// 但**不是每条 onboarding 用例都掩**：种子已带码、种子已有 profile 行的那些用例
	// 的 share_url / created_at 是夹具给的确定值，录制侧刻意**不掩**（逐字比才是证据）。
	// 于是 Go 侧必须按夹具给的次数应用这两条掩码，而不是按组无脑掩。
	CodeMasks      int `json:"code_masks"`
	CreatedAtMasks int `json:"created_at_masks"`
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
		"seed.mood_checkins":       len(file.Seed.MoodCheckins) == 0,
		"seed.growth_records":      len(file.Seed.GrowthRecords) == 0,
		"seed.onboarding_profiles": len(file.Seed.OnboardingProfiles) == 0,
		"masked_fields":            len(file.MaskedFields) == 0,
		"week_window_corpus":       len(file.WeekWindowCorpus) == 0,
		"push_moment_corpus":       len(file.PushMomentCorpus) == 0,
		"template_corpus":          len(file.TemplateCorpus) == 0,
		"persona_upper_corpus":     len(file.PersonaUpperCorpus) == 0,
		"referral_corpus":          len(file.ReferralCorpus) == 0,
		"mood_copy_corpus":         len(file.MoodCopyCorpus) == 0,
		"language_resolve_corpus":  len(file.LanguageResolveCorpus) == 0,
		// 只判 site_url：U3-b 之后 `app_store_url` 的**合法值就是空**（两侧都改成
		// 「没上架 ⇒ 回落站点落地页」），把它当「夹具没落」打红等于把正确形态判错。
		// 这条判据要保的是「回放用的 cfg 至少有一个能拼出深链的站点」，不是「必须有商店链」。
		"share_url_inputs.site_url": file.ShareURLInputs.SiteURL == "",
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

// TestN8MoodCopyCorpusClosure 是批 5 的文案闭包闸：15 locale × 4 档判据 = 60 格，
// 每一格都拿 Go 侧的副本现算一遍与 Python 侧录制值逐字比。
//
// 走的是**公开判据**（domain.MoodTrendSummary）而不是内部的取键函数：每格先由 Key
// 决定喂哪一组分值，所以这一张表同时钉住了两件事——文案副本，以及
// 「avg>=4⇒high / avg>=3⇒mid / 其余⇒low / 空⇒empty」那条映射。
// 四个分值都取单元素，让 avg 恰好等于阈值本身（4.0 归 high、3.0 归 mid），
// low 用 1、empty 用空切片。
func TestN8MoodCopyCorpusClosure(t *testing.T) {
	file := loadN8Fixture(t)
	byKey := map[string][]int{
		"trend.empty": {},
		"trend.high":  {5},
		"trend.mid":   {3},
		"trend.low":   {1},
	}
	// 闸别：键名漂了（比如 Python 侧改成 "mood.trend.high"）会让下面查不到分值，
	// 全部落进未登记分支——所以那条 Fatalf 就是「表没在空跑」的判据。
	seenLocale := map[string]int{}
	seenKey := map[string]int{}
	for _, entry := range file.MoodCopyCorpus {
		values, ok := byKey[entry.Key]
		if !ok {
			t.Fatalf("夹具出现了未登记的判据键 %q（locale=%s）——生成脚本换了口径要同步这里",
				entry.Key, entry.Locale)
		}
		seenLocale[entry.Locale]++
		seenKey[entry.Key]++
		if entry.Want == "" {
			t.Errorf("%s / %s 的 Python 侧文案是空串——copy.t 的回退链坏了",
				entry.Locale, entry.Key)
			continue
		}
		if got := domain.MoodTrendSummary(values, entry.Locale); got != entry.Want {
			t.Errorf("文案不符 locale=%s key=%s\n go: %q\n py: %q",
				entry.Locale, entry.Key, got, entry.Want)
		}
	}
	if len(seenLocale) != 15 || len(seenKey) != 4 {
		t.Fatalf("闭包形状不对：期望 15 locale × 4 判据＝60 格，实为 locale %d 种、key %d 种（共 %d 行）",
			len(seenLocale), len(seenKey), len(file.MoodCopyCorpus))
	}
	t.Logf("mood 文案闭包 %d 格逐字比对（%d locale × %d 判据）",
		len(file.MoodCopyCorpus), len(seenLocale), len(seenKey))
}

// TestN8LanguageResolveCorpusClosure 钉的是批 5 第一支语言归一（`language.py:30`）的
// 23 格闭包。want 是 Python 现算的：null 表示「这个头等于没给」，Go 侧必须落在
// normalizeLanguageCode 的 ok=false 那一支，而不是返回 ""（后者会继续往下读库）。
//
// 这一张表存在的原因是摸底实测到的两支分叉：`zh_CN`（下划线不是分隔符）在第一支
// 判「不支持」、在第二支（copy.py 的前缀匹配）判 zh。夹具里两种口径的输入都有，
// 所以两支各自钉死、不许合并成一个「更聪明」的归一函数。
func TestN8LanguageResolveCorpusClosure(t *testing.T) {
	file := loadN8Fixture(t)
	var accepted, rejected int
	for _, entry := range file.LanguageResolveCorpus {
		code, ok := normalizeLanguageCode(entry.Raw)
		if entry.Want == nil {
			rejected++
			if ok {
				t.Errorf("Python 认为 %q 等于没给语言，Go 却归一成了 %q", entry.Raw, code)
			}
			continue
		}
		accepted++
		if !ok {
			t.Errorf("Python 把 %q 判为 %q，Go 却认为不受支持", entry.Raw, *entry.Want)
			continue
		}
		if code != *entry.Want {
			t.Errorf("%q 的归一码不符：py=%q go=%q", entry.Raw, *entry.Want, code)
		}
	}
	// 两条都非空才是闭包：全 accepted 意味着「不支持」那一支没有观测点，
	// 全 rejected 意味着 Go 侧返回什么都不会红。
	if accepted == 0 || rejected == 0 {
		t.Fatalf("语言归一闭包只剩一类：生效 %d / 视为没给 %d", accepted, rejected)
	}
	t.Logf("language_resolve 闭包 %d 格（生效 %d / 视为没给 %d）",
		len(file.LanguageResolveCorpus), accepted, rejected)
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

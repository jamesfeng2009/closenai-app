// N8 批 5（mood + reflections 两个域）的判定与文案面。
//
// 这三条端点都是**纯读**：history 一条查询、trend 两条（users.timezone + 窗口内的签到）、
// reflections 一条。没有 LLM、没有写入、没有外发，所以本文件里的东西只有四种：
// 分值表、按本地日历日聚合、阈值判据、15 语言文案副本。
//
// 两件值得先说清的事：
//  1. **分值表只有一份**。Python 侧 `MOOD_MAP` 在 mood_service.py:39-46 与 :50-57 被
//     逐字声明了两遍（内容完全相同），Go 侧合成一张是等价的；这条重复已登记在
//     tasks.md 的批 5 段，属「发现的缺陷不顺手修」，不在本批改 Python。
//  2. **两支语言归一不能合并**。`/mood/trend` 的 summary 先走 `language.py:30`
//     （`split("-")[0].lower()` ＋精确成员判定，en 在前）选出语言码，再走
//     `copy.py:35 locale_for`（`lower().strip()` ＋前缀匹配，zh 在前）取文案库。
//     同一个原始 header 两支可以给出不同结论，所以这里只实现第二支
//     （`CopyLocaleFor`），第一支留在 httpapi.normalizeLanguageCode。
package domain

import (
	_ "embed"
	"encoding/json"
	"sort"
	"strings"
	"time"
)

// MoodLevels 是 mood_service.py:39-46 的 MOOD_MAP 值侧（emoji → 1..5）。
// 未知表情取 3——那是 `MOOD_MAP.get(emoji, (None, 3))[1]` 的默认元组，
// 不是「中性情绪」的美化：签到写入侧不校验 emoji，库里历史行可能有任意字符。
var MoodLevels = map[string]int{
	"😊": 5,
	"😌": 4,
	"😰": 2,
	"😢": 1,
	"😡": 1,
	"😴": 3,
}

// MoodLevelDefault 是 MOOD_MAP 未命中时的分值。
const MoodLevelDefault = 3

// MoodHistoryLimit 是 get_history 里写死的 `limit=30`（mood_service.py:309）。
// 注意它与端点无关的另一个 30：`get_history(db, user_id, days=30)` 那个方法
// 走的是 days（discovery 用的窗口），本端点走的是 get_recent(limit=30)。
const MoodHistoryLimit = 30

// MoodTrendDefaultDays / MoodTrendMinDays / MoodTrendMaxDays 是
// `days: int = Query(14, ge=1, le=90)`（routers/mood.py:58）。
const (
	MoodTrendDefaultDays = 14
	MoodTrendMinDays     = 1
	MoodTrendMaxDays     = 90
)

// IntQuerySpec 是一个 FastAPI int 查询参数的三件套：默认值与两侧闭区间界。
// 界放在 domain 而不是 httpapi，是因为「越界了还查不查库」属判据（store 的窗口
// 参数直接拿它算），而报文形状属 HTTP 面（pyquery.go）。
type IntQuerySpec struct {
	Default int64
	Ge      int64
	Le      int64
}

// MoodTrendDaysSpec / ReflectionsLimitSpec 是把上面四个常量收成 queryInt 的入参形状。
var (
	MoodTrendDaysSpec = IntQuerySpec{
		Default: MoodTrendDefaultDays, Ge: MoodTrendMinDays, Le: MoodTrendMaxDays,
	}
	ReflectionsLimitSpec = IntQuerySpec{
		Default: ReflectionsDefaultLimit, Ge: ReflectionsMinLimit, Le: ReflectionsMaxLimit,
	}
)

// ReflectionsDefaultLimit / Min / Max 是 `limit: int = Query(20, ge=1, le=100)`
// （routers/reflection.py:64）；类型串 "reflection" 是 list 里传给
// growth_repository.list_by_type 的字面量（reflection_service.py:133）。
const (
	ReflectionsDefaultLimit = 20
	ReflectionsMinLimit     = 1
	ReflectionsMaxLimit     = 100
	ReflectionRecordType    = "reflection"
)

// MoodCheckinRow 是 GET /mood/history 每行要的五列。
// Label/Interpretation 已由 store 侧 COALESCE 成空串，对应 `r.mood_label or ""`。
type MoodCheckinRow struct {
	ID             string
	Emoji          string
	Label          string
	Interpretation string
	CreatedAt      time.Time
}

// StreakUpdatedInHistory 是 :317 那个**硬编码 0**：get_history 不参与 streak 计算，
// Go 侧若「顺手真算」就会在同一个账号切流前后给出不同的数字。
const StreakUpdatedInHistory = 0

// ReflectionRow 是 GET /reflections 每行要的四列。
// MemoryIDs 是 related_memory_ids 的**列原文**（store 侧把 NULL 折成空串，
// 因为 reflection_service 判的是 falsy 而不是 is None）。
type ReflectionRow struct {
	ID        string
	Content   string
	MemoryIDs string
	CreatedAt time.Time
}

// MoodTrend 是 count_by_day（mood_repository.py:194-215）＋ get_trend 里
// day_map 那一段（mood_service.py:326-336）的合成：
// 行必须按 created_at 升序、同刻按 id 升序进来（M-G 给 Python 侧补了 id 次级键，
// SQL 常量在 store/legacy_batch5.go），同一本地日历日只取首行，键序即 labels 序。
//
// 日历日取的是 `%m-%d`（只有月-日，没有年），所以跨年窗口会撞键——
// 已取证：le=90 的窗口最长 90 天，跨年撞键要求同一个标签在两行里出现两次，
// 而 90 天窗口最多跨一次年、两侧都是「12-xx/01-xx」不同键，故不可达（tasks.md 批 5 段）。
func MoodTrend(rows []Checkin, timezone string) (labels []string, values []int) {
	zone := loadUserZone(timezone)
	type bucket struct {
		label string
		value int
		// firstSeen 是行在输入序列里的位置，用于还原 dict 的插入序。
		firstSeen int
	}
	seen := make(map[string]bucket)
	for i, row := range rows {
		local := row.CreatedAt.In(zone)
		label := local.Format("01-02")
		if _, ok := seen[label]; ok {
			continue
		}
		value, ok := MoodLevels[row.Emoji]
		if !ok {
			value = MoodLevelDefault
		}
		seen[label] = bucket{label: label, value: value, firstSeen: i}
	}
	buckets := make([]bucket, 0, len(seen))
	for _, b := range seen {
		buckets = append(buckets, b)
	}
	sort.Slice(buckets, func(i, j int) bool { return buckets[i].firstSeen < buckets[j].firstSeen })

	labels = make([]string, 0, len(buckets))
	values = make([]int, 0, len(buckets))
	for _, b := range buckets {
		labels = append(labels, b.label)
		values = append(values, b.value)
	}
	return labels, values
}

// MoodTrendSummary 是 `_trend_summary`（mood_service.py:340-348）的四档判据。
// 阈值比较用 float64 除法，与 Python 的 `sum(values)/len(values)` 同精度：
// 分子至多 5*90、分母至多 90，两个方向都落在 float64 的精确整数域内。
func MoodTrendSummary(values []int, language string) string {
	if len(values) == 0 {
		return moodCopyString("empty", language)
	}
	total := 0
	for _, v := range values {
		total += v
	}
	avg := float64(total) / float64(len(values))
	switch {
	case avg >= 4:
		return moodCopyString("high", language)
	case avg >= 3:
		return moodCopyString("mid", language)
	default:
		return moodCopyString("low", language)
	}
}

// CopySupportedLocales 是 core/copy.py:26-28 的 SUPPORTED_LOCALES。
// **顺序是判据的一部分**：locale_for 做的是前缀匹配，先命中的胜出（zh 在 en 之前）。
// 它与 settings.supported_languages 是两张不同顺序的表，不能混用。
var CopySupportedLocales = []string{
	"zh", "en", "es", "ko", "ja", "ru",
	"id", "ms", "th", "de", "fr", "it", "pt", "vi", "km",
}

// CopyLocaleFor 是 core/copy.py:33-41：`lower().strip()` 后按上表前缀匹配，
// 空串或未命中回退 en。注意 strip 在 lower **之后**（Python 写的是
// `(language or "").lower().strip()`），对纯空白串两者等价，但对 " es "
// 这类带空格的值，前缀匹配吃的是剥掉空白的那一份。
func CopyLocaleFor(language string) string {
	code := PyTrimSpace(strings.ToLower(language))
	if code == "" {
		return "en"
	}
	for _, locale := range CopySupportedLocales {
		if strings.HasPrefix(code, locale) {
			return locale
		}
	}
	return "en"
}

//go:embed templates/copy_mood.json
var moodCopyJSON []byte

// moodCopy 是 backend/data/copy/{locale}.json 里 mood.trend 四键的**逐字节副本**。
// 副本与源文件的相等由 Python 侧的闸（backend/tests/test_n8_batch5_contract.py）
// 与夹具的 60 格闭包（mood_copy_corpus）两头守——只比被 HTTP 用例命中的那一格
// 会让其余格静默分叉。
var moodCopy = func() map[string]map[string]string {
	var raw map[string]map[string]string
	if err := json.Unmarshal(moodCopyJSON, &raw); err != nil {
		panic("templates/copy_mood.json 解析失败: " + err.Error())
	}
	return raw
}()

// moodCopyString 是 `copy.t("mood", "trend.<key>", language)` 的三级回退：
// 目标语言 → en → 键名（`f"{namespace}.{key}"`）。最后一支在数据完整时不可达，
// 留着是因为 Python 侧的文案库真能缺键，而缺键时响应里出现的是 "mood.trend.high"
// 这样的字面量，不是空串。
func moodCopyString(key, language string) string {
	locale := CopyLocaleFor(language)
	if v := moodCopy[locale][key]; v != "" {
		return v
	}
	if v := moodCopy["en"][key]; v != "" {
		return v
	}
	return "mood.trend." + key
}

// SplitMemoryIDs 是 `r.related_memory_ids.split(",") if r.related_memory_ids else None`
// （reflection_service.py:122、:137）：**falsy 判据**不是「列是否 NULL」。
// 所以 NULL 与空串都⇒null，而 ","⇒["",""]、"a,,b"⇒["a","","b"]、"only"⇒["only"]。
// 返回 nil 表示 JSON null（与空切片不同，后者在本域不可达：split 至少给一个元素）。
func SplitMemoryIDs(raw string) []string {
	if raw == "" {
		return nil
	}
	return strings.Split(raw, ",")
}

// 批 5 判定的单测：只测**响应字节之外**还能成立的东西。
//
// 逐字契约由 go-core/testdata/n8_contract.json 的回放闸负责（②），这里管四件
// 那些 HTTP 用例**只能命中一格**的事：时区折算的整张表、当日取首行的顺序、
// 阈值的边界、两支语言归一的分叉。
package domain

import (
	"slices"
	"strings"
	"testing"
	"time"
)

func mustTime(t *testing.T, text string) time.Time {
	t.Helper()
	at, err := time.Parse(time.RFC3339, text)
	if err != nil {
		t.Fatalf("parse %q: %v", text, err)
	}
	return at
}

func TestMoodTrendLocalDayFirstWins(t *testing.T) {
	// 输入必须按 created_at **升序**（那是 count_by_day 的 ORDER BY），
	// 所以这里的顺序就是 SQL 结果的顺序。
	rows := []Checkin{
		{Emoji: "😌", CreatedAt: mustTime(t, "2026-10-13T23:00:00Z")},
		{Emoji: "😢", CreatedAt: mustTime(t, "2026-10-14T01:00:00Z")},
		{Emoji: "😊", CreatedAt: mustTime(t, "2026-10-14T05:00:00Z")},
	}
	// UTC：10-14 的两行落同一日历日，取**升序首行**（😢⇒1），不是后到的 😊⇒5。
	labels, values := MoodTrend(rows, "UTC")
	if !slices.Equal(labels, []string{"10-13", "10-14"}) || !slices.Equal(values, []int{4, 1}) {
		t.Fatalf("UTC 聚合 = %v/%v，期望 [10-13,10-14]/[4,1]", labels, values)
	}

	// Asia/Shanghai（+8）把 10-13T23:00Z 推到 10-14 本地日，于是只剩一格，
	// 而那一格的首行是 😌⇒4（同一条数据在两种时区下既是标签数也是取值）。
	labels, values = MoodTrend(rows, "Asia/Shanghai")
	if !slices.Equal(labels, []string{"10-14"}) || !slices.Equal(values, []int{4}) {
		t.Fatalf("Shanghai 聚合 = %v/%v，期望 [10-14]/[4]", labels, values)
	}
}

func TestMoodTrendUnknownZoneFallsBackToUTC(t *testing.T) {
	// time_utils.safe_zoneinfo：非法/空时区名一律 UTC，不报错、不 500。
	rows := []Checkin{{Emoji: "😴", CreatedAt: mustTime(t, "2026-10-14T05:00:00Z")}}
	for _, tz := range []string{"", "Not/AZone", "UTC+9", "  "} {
		labels, values := MoodTrend(rows, tz)
		if !slices.Equal(labels, []string{"10-14"}) || !slices.Equal(values, []int{3}) {
			t.Fatalf("时区 %q 聚合 = %v/%v，期望按 UTC 出 [10-14]/[3]", tz, labels, values)
		}
	}
}

func TestMoodTrendUnknownEmojiIsThree(t *testing.T) {
	// MOOD_MAP.get(emoji, (None, 3))[1]：库里历史行可以有任意字符。
	rows := []Checkin{{Emoji: "🫥", CreatedAt: mustTime(t, "2026-10-14T05:00:00Z")}}
	_, values := MoodTrend(rows, "UTC")
	if values[0] != MoodLevelDefault {
		t.Fatalf("未知表情 = %d，期望 %d", values[0], MoodLevelDefault)
	}
	// 分值表六条与 Python 的 MOOD_MAP 同源，由 Python 侧的闸双向比差集。
	if len(MoodLevels) != 6 {
		t.Fatalf("MoodLevels 六条被改动：%v", MoodLevels)
	}
}

func TestMoodTrendEmptyIsNotNil(t *testing.T) {
	labels, values := MoodTrend(nil, "UTC")
	if labels == nil || values == nil {
		t.Fatal("空态必须是非 nil 切片：nil 会 marshal 成 null，夹具给的是 []")
	}
	if len(labels) != 0 || len(values) != 0 {
		t.Fatalf("空态 = %v/%v", labels, values)
	}
}

func TestMoodTrendSummaryBoundaries(t *testing.T) {
	// 四档：空⇒empty，avg>=4⇒high，avg>=3⇒mid，否则 low。
	// 恰好 3.0 归 mid 是夹具 trend-exactly3（[2,5]）钉住的那一格。
	cases := []struct {
		values []int
		want   string
	}{
		{nil, "empty"},
		{[]int{4}, "high"},
		{[]int{3, 4, 5}, "high"}, // 4.0 仍然 high（>= 不是 >）
		{[]int{2, 5}, "mid"},     // 恰好 3.0
		{[]int{3}, "mid"},
		{[]int{2, 4}, "mid"}, // 3.0
		{[]int{1, 5}, "mid"}, // 3.0
		{[]int{2, 3}, "low"}, // 2.5
		{[]int{1}, "low"},
	}
	for _, c := range cases {
		got := MoodTrendSummary(c.values, "en")
		want := moodCopy["en"][c.want]
		if got != want {
			t.Fatalf("values=%v 落到 %q，期望 %q 那一格（%s）", c.values, got, want, c.want)
		}
	}
	// 判据本身：empty/high/mid/low 四格必须是**四个不同**的串，
	// 否则上面全绿只说明它们相等。
	seen := map[string]string{}
	for _, key := range []string{"empty", "high", "mid", "low"} {
		v := moodCopy["en"][key]
		if other, dup := seen[v]; dup {
			t.Fatalf("en 文案 %q 与 %q 相同⇒四档判据无法被区分", key, other)
		}
		seen[v] = key
	}
}

func TestCopyLocaleForPrefixOrder(t *testing.T) {
	// copy.py:35 的 `lower().strip()` ＋**前缀**匹配，且 zh 在表首；
	// 这与 language.py:30 的 `split("-")[0].lower()` ＋精确成员是两支不同的归一。
	cases := []struct{ raw, want string }{
		{"zh", "zh"}, {"ZH", "zh"}, {" zh ", "zh"}, {"zh-CN", "zh"}, // 前缀匹配吃区域后缀
		{"en", "en"}, {"en-GB", "en"}, {"English", "en"}, // 前缀 "en" 命中 en
		{"id", "id"}, {"it", "it"}, {"ja", "ja"}, {"km", "km"},
		{"", "en"}, {"   ", "en"}, {"xx", "en"}, {"cn", "en"}, {"tw", "en"},
		{"portuguese", "en"}, // 前缀不是 pt
		{"pt-br", "pt"},
	}
	for _, c := range cases {
		if got := CopyLocaleFor(c.raw); got != c.want {
			t.Fatalf("CopyLocaleFor(%q) = %q，期望 %q", c.raw, got, c.want)
		}
	}
	// 两支的分叉面：language.py 会把 zh_CN 判成「没给语言」，copy.py 却按前缀收下。
	if CopyLocaleFor("zh_CN") != "zh" {
		t.Fatal("copy 侧前缀匹配被改成精确匹配了——两支归一不能合并（D-4）")
	}
}

func TestMoodCopyCoversFifteenLocales(t *testing.T) {
	// 闭包：15 语言 × 4 键 = 60 格，缺一格就回退 en（moodCopyString 的第二级）。
	if len(moodCopy) != len(CopySupportedLocales) {
		t.Fatalf("文案库 %d 个语言目录，SUPPORTED_LOCALES %d 个",
			len(moodCopy), len(CopySupportedLocales))
	}
	for _, locale := range CopySupportedLocales {
		entries, ok := moodCopy[locale]
		if !ok {
			t.Fatalf("缺 %q 的 mood.trend 四键", locale)
		}
		for _, key := range []string{"empty", "high", "mid", "low"} {
			if entries[key] == "" {
				t.Fatalf("%s.trend.%s 是空串", locale, key)
			}
		}
	}
}

func TestSplitMemoryIDsFalsyNotNULL(t *testing.T) {
	// reflection_service 判的是 falsy：空串与 NULL 都给 null，
	// 而 "," 给 ["",""]、"a,,b" 给 ["a","","b"]、"only" 给 ["only"]。
	cases := []struct {
		raw  string
		want []string
	}{
		{"", nil},
		{",", []string{"", ""}},
		{"a,,b", []string{"a", "", "b"}},
		{"only", []string{"only"}},
		{"a,b", []string{"a", "b"}},
		{",,", []string{"", "", ""}},
		{"a,", []string{"a", ""}},
	}
	for _, c := range cases {
		got := SplitMemoryIDs(c.raw)
		if (got == nil) != (c.want == nil) || strings.Join(got, "|") != strings.Join(c.want, "|") {
			t.Fatalf("SplitMemoryIDs(%q) = %#v，期望 %#v", c.raw, got, c.want)
		}
	}
}

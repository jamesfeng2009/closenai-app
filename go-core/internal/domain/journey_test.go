// N8 批 3 的域层单测：剧本预写的计划算术与推送窗口解析。
//
// 与 internal/httpapi/n8_replay_test.go 的分工沿用批 2：那份是**跨语言**逐字闸
// （响应字节由真 FastAPI 录），这一份把容易被改坏的分支各自钉一条。四支最脆的：
//  1. `_push_moment` 的 except 分支：`"7"`（只有一段）与 `"9:30:00"`（三段）走的都是
//     **ValueError**（解包失败，实测文案 not enough / too many values to unpack——
//     Python 那句 `except (ValueError, IndexError)` 里的 IndexError 其实到不了，
//     因为这一支从来不按下标取段），`"abc:def"` 走 int() 的 ValueError。
//     三条都落 9,0，但它们是不同的一行代码——把段数判掉、或把「三段」当成取前两段，
//     脏值列的行为会静默换。
//  2. `or "09:00"` 只看真假：NULL 与空串同一条分支，而 `"0"` 是**真值**走解析
//     ⇒ 00:00。把判空写成「解析失败才兜底」会让 push_time=” 的用户差 9 小时。
//  3. Past 的界：Python 是 `day0 + offset < today`（严格早），day0 当天不算过去。
//     写成 <= 会把 D0 那一行预写成 skipped，红点少一个。
//  4. int() 的字符集：Nd 数字、下划线、Unicode 空白（见 TestJourneyPushTimeParsing）。
//     这一支的**逐取值**跨语言校验已在批 3② 落地：夹具的 push_moment_corpus 由
//     `scripts/gen_n8_contract_goldens.py` 调 `journey_service._push_moment` 现算，
//     Go 侧逐条比对在 internal/httpapi/n8_pushmoment_test.go。所以下面那些期望值不是
//     口径宣称，指回那份语料即可复核。
package domain

import (
	"testing"
	"time"
)

func TestJourneyPushTimeParsing(t *testing.T) {
	// 期望值一律按 **CPython 现算**（./.venv/bin/python 跑 int() 与 re.fullmatch 得到，
	// 不是推测）。前 11 条两侧本来就同结论；后 6 条专打「只认 ASCII 数字、只剥 ASCII
	// 空白」那版实现会静默落回 9:00 的一支。其中全角与阿拉伯-印度那两条**今天就能过
	// PATCH /user/settings**：`backend/models/schemas.py:400` 用的是
	// `re.fullmatch(r"\d{2}:\d{2}", v)`，Python 的 `\d` 默认 Unicode 感知，而列是
	// VARCHAR(5) 且 PG 按字符计宽（`backend/models/user.py:29`）⇒「１２:３４」正好 5 字符。
	// 所以这不是「历史脏值才碰得到」的分支：不实现就会与 `_push_moment`
	// （`backend/services/journey_service.py:238`）算出不同的 scheduled_at 字节。
	cases := []struct {
		raw          string
		hour, minute int
		why          string // 非空＝说明这一条在守哪一支
	}{
		{raw: "", hour: 9, minute: 0},      // NULL 与 '' 同一条 or 分支
		{raw: "09:00", hour: 9, minute: 0}, // 与兜底同值，但走的是解析
		{raw: "07:15", hour: 7, minute: 15},
		{raw: " 7 : 30 ", hour: 7, minute: 30},                // int() 吃首尾空白
		{raw: "+9:00", hour: 9, minute: 0},                    // int() 吃加号
		{raw: "0", hour: 9, minute: 0},                        // 只有一段 ⇒ 解包 ValueError
		{raw: "9:30:00", hour: 9, minute: 0},                  // 三段 ⇒ too many values ⇒ 9,0
		{raw: "１２：３４", hour: 9, minute: 0},                    // 全角冒号不分段 ⇒ 仍是一条 ValueError
		{raw: "abc:def", hour: 9, minute: 0},                  // ValueError
		{raw: "25:70", hour: 23, minute: 59},                  // 两侧各 clamp
		{raw: "-3:-9", hour: 0, minute: 0},                    // max(x,0) 先于 min
		{raw: "99999999999999999999:00", hour: 23, minute: 0}, // 任意长数字仍 clamp
		{raw: "9:", hour: 9, minute: 0},                       // 第二段空 ⇒ ValueError
		{raw: ":30", hour: 9, minute: 0},                      // 第一段空 ⇒ ValueError
		{raw: "١٢:٣٠", hour: 12, minute: 30, why: "，Nd：阿拉伯-印度数字"},
		{raw: "１２:３４", hour: 12, minute: 34, why: "，Nd：全角数字（PATCH 可达）"},
		// 位值取所属 run 起点：数学粗体 𝟙 是 U+1D7D9，而 Go 的 unicode.Nd.R32 把
		// 五个数学数字 run 并成了 1D7CE-1D7FF 一条 ⇒ 偏移 11 必须 %10 才是 1。
		{raw: "𝟙𝟚:𝟘𝟛", hour: 12, minute: 3, why: "，Nd：并表 run 的 %10"},
		{raw: "1_0:00", hour: 10, minute: 0, why: "，数字之间的下划线"},
		{raw: "1__0:00", hour: 9, minute: 0, why: "，连续下划线 Python 也判 ValueError"},
		{raw: " 9:30", hour: 9, minute: 30, why: "，NBSP（CPython 空白集＝Go IsSpace 同集合）"},
		// 反向钉住空白集的上界：\x1c 两侧都不是空白 ⇒ 不许因为用了 IsSpace 就多吃。
		{raw: "\x1c9:30", hour: 9, minute: 0, why: "，文件分隔符不是空白 ⇒ ValueError"},
	}
	for _, tc := range cases {
		hour, minute := parsePushTime(tc.raw)
		if hour != tc.hour || minute != tc.minute {
			t.Errorf("parsePushTime(%q) = (%d, %d)，期望 (%d, %d)%s",
				tc.raw, hour, minute, tc.hour, tc.minute, tc.why)
		}
	}
}

func TestJourneyPushMomentUsesLocalWallClock(t *testing.T) {
	// 期望值手算（+08:00 恒定，无 DST）：本地 07:15 ⇒ UTC 前一天 23:15。
	user := JourneyUser{Timezone: "Asia/Shanghai", PushTimeRaw: "07:15"}
	day := time.Date(2026, 10, 14, 0, 0, 0, 0, time.UTC) // 纯日历日（UTC 承载）
	got := PushMomentAt(user, day, loadUserZone(user.Timezone)).UTC()
	want := time.Date(2026, 10, 13, 23, 15, 0, 0, time.UTC)
	if !got.Equal(want) {
		t.Errorf("PushMomentAt = %s，期望 %s", got.Format(time.RFC3339), want.Format(time.RFC3339))
	}

	// 兜底时刻同样按本地墙钟：09:00 +08:00 ⇒ 01:00Z。
	user.PushTimeRaw = ""
	got = PushMomentAt(user, day, loadUserZone(user.Timezone)).UTC()
	want = time.Date(2026, 10, 14, 1, 0, 0, 0, time.UTC)
	if !got.Equal(want) {
		t.Errorf("空 push_time 的落点 = %s，期望 %s", got.Format(time.RFC3339), want.Format(time.RFC3339))
	}
}

func TestJourneyPrefillPlanShape(t *testing.T) {
	// base 与 now 都在同一天（UTC 时区）⇒ D0 不算过去，D1 起往后；这里 day0=10-10、today=10-14
	// ⇒ D0..D3 与 D5 全在过去，D7 也在过去（offset 7 > 4）。六行全 Past。
	user := JourneyUser{ID: "u-1", Timezone: "UTC", Language: "en", PushTimeRaw: "09:00"}
	base := time.Date(2026, 10, 10, 3, 0, 0, 0, time.UTC)
	now := time.Date(2026, 10, 14, 12, 0, 0, 0, time.UTC)

	plan := JourneyPrefillPlan(user, &base, now)
	if len(plan) != len(ScriptEvents) {
		t.Fatalf("计划 %d 行，期望 %d 行", len(plan), len(ScriptEvents))
	}
	wantKinds := []string{"day0_read", "day1_checkin_echo", "day2_first_outreach",
		"day3_weak_assert", "day5_stat", "day7_report"}
	wantOffsets := []int{0, 1, 2, 3, 5, 7}
	for i, row := range plan {
		if row.Kind != wantKinds[i] || row.DayNo != wantOffsets[i] {
			t.Errorf("第 %d 行 = (%s, %d)，期望 (%s, %d)",
				i, row.Kind, row.DayNo, wantKinds[i], wantOffsets[i])
		}
		// scheduled_at 必须是 day0+offset 的**本地** 09:00（UTC 时区⇒同一天 09:00Z）。
		want := time.Date(2026, 10, 10, 9, 0, 0, 0, time.UTC).
			AddDate(0, 0, wantOffsets[i])
		if !row.ScheduledAt.Equal(want) {
			t.Errorf("第 %d 行 scheduled_at = %s，期望 %s",
				i, row.ScheduledAt.Format(time.RFC3339), want.Format(time.RFC3339))
		}
		// day0=10-10、today=10-14 ⇒ offset 0/1/2/3 在过去（10-10..10-13），
		// 5 与 7 落在 10-15/10-17 ⇒ 未来。这条断言同时钉住「Past 用的是 offset 后的日子」
		// 而不是 day0 本身，以及 `< today` 的严格界（10-13 < 10-14 成立，10-14 不成立）。
		wantPast := wantOffsets[i] <= 3
		if row.Past != wantPast {
			t.Errorf("第 %d 行（offset %d）Past=%v，期望 %v", i, wantOffsets[i], row.Past, wantPast)
		}
	}

	// day0==today：D0 不出 Past，D1 起也不出；全部未来。
	plan = JourneyPrefillPlan(user, &now, now)
	for i, row := range plan {
		if row.Past {
			t.Errorf("day0==today 时第 %d 行仍 Past=true（<= 与 < 的差一）", i)
		}
	}

	// 边界：day0 = 昨天 ⇒ 只有 D0 在过去。
	yesterday := now.AddDate(0, 0, -1)
	plan = JourneyPrefillPlan(user, &yesterday, now)
	if !plan[0].Past || plan[1].Past {
		t.Errorf("day0=昨天时 Past = (%v, %v)，期望 (true, false)", plan[0].Past, plan[1].Past)
	}
}

func TestJourneyPrefillPlanWithoutBaseUsesLocalToday(t *testing.T) {
	// Python 的 `day0 = base_day or local_today(user.timezone)`：base 为 nil 才走这一支。
	// 锚点选 UTC 14:00 而 Shanghai 已跨到次日，用来钉「本地今天而不是 UTC 今天」——
	// 拿 now 的 UTC 日期当 day0 会让 Shanghai 用户整条剧本早一天。
	user := JourneyUser{Timezone: "Asia/Shanghai", PushTimeRaw: "09:00"}
	now := time.Date(2026, 10, 14, 14, 0, 0, 0, time.UTC) // 本地 10-14 22:00
	plan := JourneyPrefillPlan(user, nil, now)
	want := time.Date(2026, 10, 14, 1, 0, 0, 0, time.UTC) // 本地 10-14 09:00
	if !plan[0].ScheduledAt.Equal(want) {
		t.Errorf("D0 = %s，期望 %s", plan[0].ScheduledAt.Format(time.RFC3339), want.Format(time.RFC3339))
	}
	if plan[0].Past {
		t.Error("day0=本地今天时 D0 不该 Past")
	}

	// 同一刻在 UTC 时区里仍是 10-14；换成 Shanghai 的 base 日期也仍是 10-14 ⇒
	// 再往前推 10 小时（本地 10-14 08:00）两侧同一天，不构成判别；用 16:00Z 才跨日。
	east := JourneyUser{Timezone: "Pacific/Kiritimati"} // +14:00，无 DST
	plan = JourneyPrefillPlan(east, nil, now)
	// 本地 10-15 04:00 ⇒ 今天的落点是本地 10-15 的兜底 09:00 ⇒ 10-14 19:00Z。
	want = time.Date(2026, 10, 14, 19, 0, 0, 0, time.UTC)
	if !plan[0].ScheduledAt.Equal(want) {
		t.Errorf("+14 时区的 D0 = %s，期望 %s",
			plan[0].ScheduledAt.Format(time.RFC3339), want.Format(time.RFC3339))
	}
}

func TestJourneyPrefillPlanUnknownTimezoneFallsBackToUTC(t *testing.T) {
	// safe_zoneinfo 的失败分支：非法时区名按 UTC（与批 2 的 LoadLocation 回退同口径）。
	user := JourneyUser{Timezone: "Not/AZone", PushTimeRaw: "23:30"}
	base := time.Date(2026, 10, 10, 0, 0, 0, 0, time.UTC)
	now := base
	plan := JourneyPrefillPlan(user, &base, now)
	want := time.Date(2026, 10, 10, 23, 30, 0, 0, time.UTC)
	if !plan[0].ScheduledAt.Equal(want) {
		t.Errorf("非法时区的 D0 = %s，期望 %s",
			plan[0].ScheduledAt.Format(time.RFC3339), want.Format(time.RFC3339))
	}
}

func TestJourneyLocalDateOfUTC(t *testing.T) {
	// 2026-10-13T20:00Z：Shanghai 已是 10-14，UTC 与纽约仍是 10-13。
	// 这一格决定 ensure_prefilled 的基准日⇒算错一天就整条剧本平移。
	at := time.Date(2026, 10, 13, 20, 0, 0, 0, time.UTC)
	for tz, want := range map[string]string{
		"UTC":              "2026-10-13",
		"Asia/Shanghai":    "2026-10-14",
		"America/New_York": "2026-10-13",
		"Not/AZone":        "2026-10-13", // 回退 UTC
	} {
		if got := LocalDateOfUTC(at, tz).Format("2006-01-02"); got != want {
			t.Errorf("LocalDateOfUTC(2026-10-13T20:00Z, %q) = %s，期望 %s", tz, got, want)
		}
	}
}

func TestJourneyLanguageOrDefault(t *testing.T) {
	// `user.language or "en"`：NULL（扫描成 ""）与空串同一条分支；其余**原样**进 payload，
	// 不做大小写或区域归一化（与批 1 的 get_request_language 不同，这里没有查表）。
	for raw, want := range map[string]string{
		"":   "en",
		"en": "en",
		"zh": "zh",
	} {
		if got := (JourneyUser{Language: raw}).LanguageOrDefault(); got != want {
			t.Errorf("language %q ⇒ %q，期望 %q", raw, got, want)
		}
	}
	if got := (JourneyUser{Language: "ZH-CN"}).LanguageOrDefault(); got != "ZH-CN" {
		t.Errorf("未归一化是契约的一部分，得到 %q", got)
	}
}

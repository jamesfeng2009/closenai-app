// wallClockToUTC 的 Python 口径闸：异常墙钟（DST 缺位/重复）折成瞬间时，
// Go 必须落在 Python 的 fold=0 答案上。
//
// 期望值全部来自 **./.venv/bin/python 现算**（2026-10-14，`datetime.combine(date,
// time, tzinfo=ZoneInfo(tz)).astimezone(utc)`），不是推测。为什么要单独一份而不是只靠
// testdata 的两条语料：occurred_at_corpus / week_window_corpus 比的是**录制下来的**
// Python 答案，若哪天有人把这里换回裸 time.Date，夹具要重录才会红；这一份拿的是
// 手算的码位级事实，改了实现立刻红，且不需要库。
//
// 三条最脆的分支：
//  1. **严格落在缺位区间内**（纽约 02:30、伦敦 01:30、悉尼豪勋爵岛 02:15）——
//     Go 原生 time.Date 用切换后的偏移，比 Python 早整整一小时（30 分钟区的岛差半小时）；
//  2. **恰在切换瞬间**（Tehran/Santiago/Chatham 的 00:00/00:45）——Go 原生本来就对，
//     但用「guess 自身的偏移」实现会在这里读成切换后的偏移而错一小时，所以留了两条；
//  3. **重复**（纽约秋初 01:30）——两侧同取第一次出现，用来钉住「判别条件不能写成
//     猜测式的 ±1h 修正」，否则这一支会被顺手改坏。
package domain

import (
	"testing"
	"time"
)

func TestWallClockToUTCMatchesPythonFoldZero(t *testing.T) {
	cases := []struct {
		tz              string
		y, mo, d, h, mi int
		want            string // Python 现算的 UTC ISO
		why             string
	}{
		// ── 缺位：严格落在跳变区间内 ──
		{"America/New_York", 2026, 3, 8, 2, 30, "2026-03-08T07:30:00Z", "，02:00→03:00 缺位中段"},
		{"America/New_York", 2026, 3, 8, 2, 0, "2026-03-08T07:00:00Z", "，缺位起点（也是切换瞬间）"},
		{"America/New_York", 2026, 3, 8, 2, 59, "2026-03-08T07:59:00Z", "，缺位末点"},
		{"Europe/London", 2026, 3, 29, 1, 30, "2026-03-29T01:30:00Z", "，英国 01:00→02:00 缺位中段"},
		{"Australia/Lord_Howe", 2026, 10, 4, 2, 15, "2026-10-03T15:45:00Z", "，半小时跳变的缺位中段"},
		{"Australia/Lord_Howe", 2026, 10, 4, 2, 0, "2026-10-03T15:30:00Z", "，半小时跳变的切换瞬间"},
		{"Pacific/Apia", 2011, 12, 30, 0, 0, "2011-12-30T10:00:00Z", "，整天不存在（跳过一天）"},
		{"Pacific/Apia", 2011, 12, 30, 12, 0, "2011-12-30T22:00:00Z", "，整天不存在的正午"},
		// ── 缺位起点恰好是本地零点/偏好时刻：Go 原生本来就对，防「guess 自身偏移」的实现 ──
		{"Asia/Tehran", 2016, 3, 21, 0, 0, "2016-03-20T20:30:00Z", "，伊朗 00:00→01:00 的切换瞬间"},
		{"Asia/Tehran", 2016, 3, 21, 0, 30, "2016-03-20T21:00:00Z", "，同一缺位的中段"},
		{"America/Santiago", 2026, 9, 5, 0, 0, "2026-09-05T04:00:00Z", "，智利 00:00→01:00 的切换瞬间"},
		{"America/Santiago", 2026, 9, 5, 0, 30, "2026-09-05T04:30:00Z", "，智利缺位中段（剧本日常落这里）"},
		{"Pacific/Chatham", 2026, 9, 27, 0, 45, "2026-09-26T12:00:00Z", "，查塔姆 00:45→01:45 的切换瞬间"},
		{"Pacific/Chatham", 2026, 9, 27, 1, 0, "2026-09-26T12:15:00Z", "，查塔姆缺位中段"},
		// ── 重复：两侧同取第一次 ──
		{"America/New_York", 2026, 11, 1, 1, 30, "2026-11-01T05:30:00Z", "，回拨重复取第一次（EDT）"},
		{"America/New_York", 2026, 11, 1, 1, 0, "2026-11-01T05:00:00Z", "，重复区间的起点"},
		// ── 正常时刻与固定偏移：不许被「修正」误伤 ──
		{"America/New_York", 2026, 3, 8, 3, 30, "2026-03-08T07:30:00Z", "，缺位之后的首个正常时刻"},
		{"America/New_York", 2026, 3, 8, 1, 30, "2026-03-08T06:30:00Z", "，缺位之前的最后正常时刻"},
		{"Asia/Shanghai", 2026, 10, 14, 7, 15, "2026-10-13T23:15:00Z", "，无 DST 的固定偏移"},
		{"UTC", 2026, 3, 8, 2, 30, "2026-03-08T02:30:00Z", "，UTC 本身"},
		{"Etc/GMT+5", 2026, 3, 8, 2, 30, "2026-03-08T07:30:00Z", "，POSIX 反向符号的固定区"},
	}
	for _, tc := range cases {
		zone, err := time.LoadLocation(tc.tz)
		if err != nil {
			t.Fatalf("LoadLocation(%q): %v——夹具与语料都用这些区名，缺 tzdata 时先红在这里", tc.tz, err)
		}
		got := wallClockToUTC(zone, tc.y, time.Month(tc.mo), tc.d, tc.h, tc.mi)
		if gotStr := got.UTC().Format("2006-01-02T15:04:05Z"); gotStr != tc.want {
			t.Errorf("wallClockToUTC(%s %04d-%02d-%02d %02d:%02d) = %s，Python fold=0 是 %s%s",
				tc.tz, tc.y, tc.mo, tc.d, tc.h, tc.mi, gotStr, tc.want, tc.why)
		}
	}
}

func TestPushMomentOnDSTGapDay(t *testing.T) {
	// 纽约用户的剧本日 2026-03-08（春季拨快）与偏好 02:30：这一支的 scheduled_at
	// 是直接进响应字节的（GET /journey/events 的 events[].scheduled_at）。
	user := JourneyUser{Timezone: "America/New_York", PushTimeRaw: "02:30"}
	day := time.Date(2026, 3, 8, 0, 0, 0, 0, time.UTC)
	got := PushMomentAt(user, day, loadUserZone(user.Timezone))
	if want := "2026-03-08T07:30:00Z"; got.Format("2006-01-02T15:04:05Z") != want {
		t.Errorf("缺位日的推送落点 = %s，Python 是 %s", got.Format(time.RFC3339), want)
	}

	// 秋季重复日 2026-11-01 的 01:30 出现两次，Python 的 fold=0 取第一次（EDT）⇒ 05:30Z。
	user.PushTimeRaw = "01:30"
	day = time.Date(2026, 11, 1, 0, 0, 0, 0, time.UTC)
	got = PushMomentAt(user, day, loadUserZone(user.Timezone))
	if want := "2026-11-01T05:30:00Z"; got.Format("2006-01-02T15:04:05Z") != want {
		t.Errorf("重复日的推送落点 = %s，Python 是 %s（01:30 出现两次，取 EDT 那一次）",
			got.Format(time.RFC3339), want)
	}
}

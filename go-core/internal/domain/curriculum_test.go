// N8 批 2 的域层单测：本地周窗口算术与周视图组装。
//
// 与 internal/httpapi/n8_window_test.go 的分工：那份是**跨语言**闸（899 条语料由 Python
// 现算，逐条比四个值），负责证明「Go 的口径 == 现行实现」；这一份是**单元**闸，
// 负责把容易被改坏的分支各自钉一条——尤其这三类：
//  1. 日历日与瞬间必须分开：monday/today 是纯日历日（用 UTC 承载），
//     start/end 才是「本地零点折算出的瞬间」。2026-10-14 修掉的真缺陷就是把
//     calendar 日当成瞬间返回（智利 2026-09-06 跳过 00:00–00:59，today 掉回 09-05）。
//  2. day_no % 7 的符号：Python 的 % 恒非负，Go 的 % 跟着被除数——历史脏值（负 day_no）
//     少这一步就永远不出卡。
//  3. 空 Days 是 [] 不是 nil：nil 序列化成 null，前端读 days.map 会炸。
//
// 期望值来源：go-core/testdata/n8_contract.json 的 week_window_corpus 同名行
// （即 Python 的 local_today/local_day_start_utc 实测输出），不是手算。
package domain

import (
	"testing"
	"time"
)

func mustZone(t *testing.T, name string) *time.Location {
	t.Helper()
	zone, err := time.LoadLocation(name)
	if err != nil {
		t.Fatalf("LoadLocation(%q) = %v——本机没有 IANA 时区数据库时这些用例不作数，"+
			"但生产镜像靠 time/tzdata 嵌入，见 backend/tests/test_n8_batch2_contract.py", name, err)
	}
	return zone
}

func parseUTC(t *testing.T, s string) time.Time {
	t.Helper()
	v, err := time.Parse(time.RFC3339, s)
	if err != nil {
		t.Fatalf("解析 %q: %v", s, err)
	}
	return v.UTC()
}

// currInstant ＝ Python `datetime.isoformat()` 在 µs==0 时的形状。
// 不用 time.RFC3339：Go 把 UTC 渲染成 "Z"，而两侧比的是 "+00:00" 那段文本
// （夹具里的 start/end 就是 Python 写出来的字面量）。
const currInstant = "2006-01-02T15:04:05-07:00"

type windowCase struct {
	name   string
	tz     string
	at     string // RFC3339 UTC
	monday string
	today  string
	start  string
	end    string
}

func TestCurriculumWindowCases(t *testing.T) {
	for _, c := range []windowCase{
		{
			name: "UTC 周内", tz: "UTC", at: "2026-10-14T14:45:30+00:00",
			monday: "2026-10-12", today: "2026-10-14",
			start: "2026-10-12T00:00:00+00:00", end: "2026-10-19T00:00:00+00:00",
		},
		{
			// 同一瞬间，UTC 还在周日（上一周），+14 已经是周一：
			// 这就是 CurriculumWindow 不能用 UTC 日历日推 monday 的原因。
			name: "Kiritimati 比 UTC 早一天", tz: "Pacific/Kiritimati", at: "2026-10-11T23:59:59+00:00",
			monday: "2026-10-12", today: "2026-10-12",
			start: "2026-10-11T10:00:00+00:00", end: "2026-10-18T10:00:00+00:00",
		},
		{
			name: "Niue 比 UTC 晚一天", tz: "Pacific/Niue", at: "2026-10-12T00:00:00+00:00",
			monday: "2026-10-05", today: "2026-10-11",
			start: "2026-10-05T11:00:00+00:00", end: "2026-10-12T11:00:00+00:00",
		},
		{
			// 2026-09-06 是春季拨快日：本地 00:00–00:59 不存在，04:00Z 已是当地 01:00。
			// 旧实现把「本地今天」当瞬间构造再 .UTC()，today 掉成 09-05、monday 掉成 08-24。
			name: "Santiago 缺位午夜", tz: "America/Santiago", at: "2026-09-06T04:00:00+00:00",
			monday: "2026-08-31", today: "2026-09-06",
			start: "2026-08-31T04:00:00+00:00", end: "2026-09-07T03:00:00+00:00",
		},
		{
			// 拨快正好落在周一：窗口左端点本身就是一个不存在的本地零点，
			// 两侧都按**切换前**偏移折（+03:30 ⇒ 03-20T20:30Z），右端点已是 +04:30。
			// 这一周只有 167 小时——所以断言比的是端点瞬间，不是时长。
			name: "Tehran 周一缺位午夜", tz: "Asia/Tehran", at: "2016-03-21T07:30:00+00:00",
			monday: "2016-03-21", today: "2016-03-21",
			start: "2016-03-20T20:30:00+00:00", end: "2016-03-27T19:30:00+00:00",
		},
		{
			// 非法时区名与空串：safe_zoneinfo 的回退结果是**按 UTC 算**，不是报错。
			name: "非法时区名回退 UTC", tz: "Not/AZone", at: "2026-10-11T23:59:59+00:00",
			monday: "2026-10-05", today: "2026-10-11",
			start: "2026-10-05T00:00:00+00:00", end: "2026-10-12T00:00:00+00:00",
		},
		{
			name: "空时区名回退 UTC", tz: "", at: "2026-10-11T23:59:59+00:00",
			monday: "2026-10-05", today: "2026-10-11",
			start: "2026-10-05T00:00:00+00:00", end: "2026-10-12T00:00:00+00:00",
		},
	} {
		t.Run(c.name, func(t *testing.T) {
			monday, start, end, today := CurriculumWindow(c.tz, parseUTC(t, c.at))
			if got := monday.Format("2006-01-02"); got != c.monday {
				t.Errorf("monday = %s，期望 %s", got, c.monday)
			}
			if got := today.Format("2006-01-02"); got != c.today {
				t.Errorf("today = %s，期望 %s", got, c.today)
			}
			if got := start.UTC().Format(currInstant); got != c.start {
				t.Errorf("start = %s，期望 %s", got, c.start)
			}
			if got := end.UTC().Format(currInstant); got != c.end {
				t.Errorf("end = %s，期望 %s", got, c.end)
			}
			// monday/today 承载在 UTC：调用方只许比 Y/M/D，把它们的偏移当信息用就是 bug 源头。
			if monday.Location() != time.UTC || today.Location() != time.UTC {
				t.Error("monday/today 必须以 UTC 承载日历日")
			}
		})
	}
}

func TestCivilDayStartUTCUsesPreTransitionOffset(t *testing.T) {
	// 缺位日的本地零点：Go 与 Python 都按**切换前**的偏移折（Tehran +03:30 ⇒ 20:30Z）。
	// 这条单独钉是因为它决定了 week 窗口的左端点包含哪一批行。
	zone := mustZone(t, "Asia/Tehran")
	got := civilDayStartUTC(zone, time.Date(2016, 3, 21, 0, 0, 0, 0, time.UTC)).UTC()
	if want := "2016-03-20T20:30:00+00:00"; got.Format(currInstant) != want {
		t.Errorf("civilDayStartUTC = %s，期望 %s", got.Format(currInstant), want)
	}
	// 秋季回拨落本地午夜的点在 2016 年的 Tehran 没有，换成夏令时内的普通日核对偏移：
	// +04:30 ⇒ 本地零点＝前一日 19:30Z。
	if plain := civilDayStartUTC(zone, time.Date(2016, 6, 20, 0, 0, 0, 0, time.UTC)); plain.Format(
		currInstant) != "2016-06-19T19:30:00+00:00" {
		t.Errorf("夏令时内的本地零点 = %s", plain.Format(currInstant))
	}
}

func TestWeekdayMondayZero(t *testing.T) {
	// Go: Sunday=0 … Saturday=6 → Python date.weekday(): Monday=0 … Sunday=6。
	// 2026-10-12 是周一，往后逐天走一圈：偏移 i 就是 Python 的 weekday()。
	monday := time.Date(2026, 10, 12, 0, 0, 0, 0, time.UTC)
	for i := 0; i < 7; i++ {
		d := monday.AddDate(0, 0, i)
		if d.Weekday() == time.Monday && i != 0 {
			t.Fatalf("基准日期不是周一：%v", d)
		}
		if got := weekdayMondayZero(d); got != i {
			t.Errorf("%v(%s) → %d，期望 %d", d.Weekday(), d.Format("2006-01-02"), got, i)
		}
	}
	if got := weekdayMondayZero(monday.AddDate(0, 0, 6)); got != 6 {
		t.Errorf("周日应为 6，实为 %d", got)
	}
}

func TestCurriculumSlotMatchesPythonModulo(t *testing.T) {
	// Python 的 % 结果恒非负；Go 的 % 跟着被除数符号。少这一步，负 day_no 的行
	// 会落到 bySlot[-1]，那一格永远不出卡，而 Python 会把它当周六出。
	for _, c := range []struct{ dayNo, want int }{
		{0, 0}, {1, 1}, {6, 6}, {7, 0}, {35, 0}, {37, 2}, {-1, 6}, {-7, 0}, {-8, 6},
	} {
		if got := curriculumSlot(c.dayNo); got != c.want {
			t.Errorf("curriculumSlot(%d) = %d，期望 %d", c.dayNo, got, c.want)
		}
	}
}

func card(id string, dayNo int, read bool) CurriculumCard {
	return CurriculumCard{ID: id, DayNo: dayNo, Payload: `{"title":"T"}`,
		MemoryRefs: "[]", HasReadAt: read}
}

func TestBuildCurriculumWeek(t *testing.T) {
	monday := time.Date(2026, 10, 12, 0, 0, 0, 0, time.UTC)
	wednesday := time.Date(2026, 10, 14, 0, 0, 0, 0, time.UTC)

	t.Run("空卡片返回空数组而不是 nil", func(t *testing.T) {
		week := BuildCurriculumWeek(nil, monday, wednesday)
		if week.Days == nil {
			t.Fatal("Days == nil：JSON 里是 null，Python 侧是 []")
		}
		if len(week.Days) != 0 || week.Unread != 0 {
			t.Fatalf("Days = %+v, Unread = %d", week.Days, week.Unread)
		}
		if week.WeekStart != "2026-10-12" {
			t.Errorf("week_start = %s", week.WeekStart)
		}
	})

	t.Run("未揭晓与已读都不计未读", func(t *testing.T) {
		week := BuildCurriculumWeek([]CurriculumCard{
			card("mon", 0, false), // 已揭晓、未读 ⇒ 计
			card("tue", 1, true),  // 已揭晓、已读 ⇒ 不计
			card("thu", 3, false), // 未揭晓 ⇒ 不计
			card("sun", 6, false), // 未揭晓 ⇒ 不计
		}, monday, wednesday)
		if week.Unread != 1 {
			t.Errorf("unread = %d，期望 1", week.Unread)
		}
		if len(week.Days) != 4 {
			t.Fatalf("days 条数 = %d，期望 4（缺格不出卡）", len(week.Days))
		}
		byID := map[string]CurriculumDay{}
		for _, d := range week.Days {
			byID[d.ID] = d
		}
		if !byID["mon"].Revealed || !byID["tue"].Revealed {
			t.Error("周一/周二应已揭晓")
		}
		if byID["thu"].Revealed || byID["sun"].Revealed {
			t.Error("周四之后的天不得标揭晓")
		}
		if !byID["tue"].Read || byID["mon"].Read {
			t.Error("read 位由 HasReadAt 搬运，读反了就会把已读卡再算进未读")
		}
		// date 是本地日历日：周一＝monday+0，周三＝monday+2。
		if byID["thu"].Date != "2026-10-15" || byID["mon"].Date != "2026-10-12" {
			t.Errorf("date 平移错了：thu=%s mon=%s", byID["thu"].Date, byID["mon"].Date)
		}
		// day_no 原样搬运（37 这种越界值也是响应内容）。
		if byID["sun"].DayNo != 6 {
			t.Errorf("day_no = %d", byID["sun"].DayNo)
		}
	})

	t.Run("揭晓边界含今天", func(t *testing.T) {
		// revealed = day <= today：今天那张卡算已揭晓（Python 的 date 比较是闭区间）。
		week := BuildCurriculumWeek([]CurriculumCard{card("wed", 2, false), card("thu", 3, false)},
			monday, wednesday)
		if week.Unread != 1 {
			t.Errorf("unread = %d，期望 1（今天算揭晓，明天不算）", week.Unread)
		}
	})

	t.Run("同格后写覆盖先写", func(t *testing.T) {
		// scheduled_at 正序传入，Python 的 by_day[idx] = e 是**后写覆盖**；
		// 顺序变了这里就得红——所以两条的 DayNo 故意不同（35 与 0 同槽）。
		week := BuildCurriculumWeek([]CurriculumCard{card("first", 0, false), card("later", 35, true)},
			monday, wednesday)
		if len(week.Days) != 1 {
			t.Fatalf("days = %d，期望 1（同槽只剩一张）", len(week.Days))
		}
		if week.Days[0].ID != "later" || week.Days[0].DayNo != 35 {
			t.Errorf("留下的是 %+v，期望 id=later day_no=35", week.Days[0])
		}
	})

	t.Run("负 day_no 落周六格", func(t *testing.T) {
		week := BuildCurriculumWeek([]CurriculumCard{card("neg", -1, false)}, monday, wednesday)
		if len(week.Days) != 1 || week.Days[0].Date != "2026-10-18" {
			t.Fatalf("负 day_no 没落到 i=6 的格子：%+v", week.Days)
		}
	})
}

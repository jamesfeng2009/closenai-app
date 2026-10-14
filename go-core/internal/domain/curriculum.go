// N8 批 2（curriculum 域）的周视图算术。
//
// 逐字对应 backend/services/curriculum_service.py:164-199 的 get_week：
// 本地周一 ⇒ [周一00:00, 下周一00:00) 的计划窗口 ⇒ 按 scheduled_at 正序取行 ⇒
// day_no % 7 落到周内格（同格后写覆盖先写）⇒ 0..6 依次出卡（缺格不出）。
//
// payload / memory_refs 两列在这里**只搬运不解析**：解析与再序列化属 HTTP 层的
// CPython 字节口径（internal/httpapi/curriculum_week.go），域层拿到的是文本列原文。
// 「这张卡读没读过」也是解析出来的，所以由调用方算好塞进 HasReadAt——域层不碰 JSON。
package domain

import "time"

const (
	// CurriculumKind 同 curriculum_service.py:52 的 KIND（journey_events.event_kind）。
	CurriculumKind = "curriculum"
	// CurriculumWeekDays 同 WEEK_DAYS=7；CurriculumListLimit 同
	// journey_repository.list_kind_between 的默认 limit=50（超出周窗的行取不到）。
	CurriculumWeekDays  = 7
	CurriculumListLimit = 50
)

// CurriculumUser 是 require_ai_consent 与 require_user 这两次读取要的三列，
// WHERE 与 user_repository.get_user 同一条（id + status='active'）。
// Found=false 表示 Python 侧的 40401；AIConsent=false（含列值为 NULL）表示 40303。
type CurriculumUser struct {
	ID        string
	Timezone  string
	Language  string
	AIConsent bool
	Found     bool
}

// CurriculumCard 是 journey_events 里的一张主题卡（文本列原样，未解析）。
type CurriculumCard struct {
	ID         string
	DayNo      int
	Payload    string
	MemoryRefs string
	HasReadAt  bool
}

// CurriculumEvent 是越权判定要的三个值（journey_repository.get 不过滤 user_id，
// 所以「不存在」「别人的」「不是主题卡」三种情形都由调用方合成同一条 403）。
type CurriculumEvent struct {
	ID      string
	UserID  string
	Kind    string
	Payload string
	Found   bool
}

// CurriculumDay 是视图里出的一张卡；Date 是本地日历日（Python 的 day.isoformat()）。
type CurriculumDay struct {
	ID         string
	Date       string
	DayNo      int
	Revealed   bool
	Read       bool
	Payload    string
	MemoryRefs string
}

// CurriculumWeek 是 get_week 的返回结构。Days 为空时是**空列表而不是 nil**——
// Python 侧 `days = []` 序列化成 `[]`，nil 会变成 `null`，前端两样都不认。
type CurriculumWeek struct {
	WeekStart string
	Days      []CurriculumDay
	Unread    int
}

// CurriculumWindow 返回本地周一、[周一, 下周一) 的 UTC 窗口与本地今天。
// now 必须是 UTC 当下（同 Python 的 datetime.now(ZoneInfo(tz))）。
//
// 返回的 monday/today 是**纯日历日**（用 UTC 承载，只比 Y/M/D），不是「本地零点」：
// 本地零点在 DST 缺位日根本不存在（智利 2026-09-06 的 00:00–00:59 直接跳过），
// 拿它当日历日比，等于让一次时钟解释错误改掉 week_start 与整周的 revealed。
// 窗口两端才是瞬间，由 civilDayStartUTC 单独折出来。
func CurriculumWindow(timezone string, now time.Time) (monday, start, end, today time.Time) {
	zone := loadUserZone(timezone)
	local := now.In(zone)
	today = time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, time.UTC)
	monday = today.AddDate(0, 0, -weekdayMondayZero(today))
	return monday, civilDayStartUTC(zone, monday),
		civilDayStartUTC(zone, monday.AddDate(0, 0, CurriculumWeekDays)), today
}

// civilDayStartUTC 把某个日历日的**本地零点**折成瞬间，口径＝Python 的
// `datetime.combine(day, time.min, tzinfo=tz).astimezone(utc)`（time_utils.py:36-44）。
// civil 只用它的 Y/M/D，自身承载在哪个时区无关。
func civilDayStartUTC(zone *time.Location, civil time.Time) time.Time {
	return wallClockToUTC(zone, civil.Year(), civil.Month(), civil.Day(), 0, 0)
}

// wallClockToUTC 把「zone 里的一个墙钟时刻」折成瞬间，口径＝Python 的 fold=0。
//
// Python 侧所有本地日历折算都写成 `datetime.combine(..., tzinfo=zone)`，而 PEP 495 的
// fold=0 对两类异常墙钟各有明确规定：**缺位**（春季拨快跳过去的那一段）用**切换前**的偏移，
// **重复**（秋季回拨走两遍的那一段）取**第一次**出现。实测（./.venv/bin/python 与
// go run 对拍，2026-10-14；批 3 的 push_moment_corpus 又补了伦敦/悉尼/豪勋爵岛三条重复日）：
//
//	重复：Go 原生**同样没有稳定口径**——同一个函数按区给出相反的两支
//	  America/New_York 2026-11-01 01:30 → 05:30Z（切换前 EDT）⇒ 与 Python 相同
//	  Europe/London      2024-10-27 01:30 → 01:30Z（切换后 GMT）⇒ 比 Python 晚一小时
//	  Australia/Sydney   2024-04-07 02:30 → 16:30Z（切换后 AEST）⇒ 同上
//	  Australia/Lord_Howe 2026-04-05 01:30 → 15:00Z ⇒ 比 Python(14:30Z) 晚半小时
//	正常：墙钟可逆 ⇒ 一致
//	缺位：Go 原生也不稳
//	  America/New_York 2026-03-08 02:30 → Go 06:30Z（切换后偏移），Python 07:30Z ⇒ 差一小时
//	  Europe/London   2026-03-29 01:30 → Go 01:30Z（切换前偏移）⇒ 恰好与 Python 相同
//	  Asia/Tehran     2016-03-21 00:30 → Go 21:00Z 相同；00:00（切换瞬间本身）两侧都 20:30Z
//	  Pacific/Apia    2011-12-30 00:00 → Go 12-29T10:00Z，Python 12-30T10:00Z（整天不存在）
//
// 所以这里**不依赖** time.Date 的猜测，改成按定义求「本地墙钟等于 W 的瞬间集合」：
// 在 W 附近 ±16h（覆盖 tzdata 全部合法 UTC 偏移）采出出现过的偏移，逐个回代检验
// `off(W−o) == o`（等价于该瞬间的本地墙钟就是 W）。集合非空时——
//   - 一个元素＝正常墙钟，答案唯一；
//   - 两个元素＝重复墙钟，取**最早**那一瞬＝切换前偏移＝fold=0 的「第一次出现」；
//
// 集合为空只可能是缺位（拨快跳过了 W），此时按切换前偏移重算：o1＝请求瞬间（把墙钟当
// unix）所在区的偏移，o2＝(墙钟−o1) 所在区的偏移，缺位时这两个瞬间必然分居切换两侧
// （证明：W−O_post < T ≤ W−O_pre），而前向跳变恒有 O_pre < O_post ⇒ min(o1,o2) 就是 O_pre。
//
// 采样粒度 30 分钟：只有「某个偏移在窗口内持续不到 30 分钟」才会漏掉它的候选瞬间，
// tzdata 里已知最短的偏移段是小时级；这一支的可证性由夹具 736 条语料守着
// （internal/httpapi/n8_pushmoment_test.go 逐条比 Python `_push_moment` 的现算结果）。
func wallClockToUTC(zone *time.Location, year int, month time.Month, day, hour, minute int) time.Time {
	wall := time.Date(year, month, day, hour, minute, 0, 0, time.UTC)

	var offsets []int
	for k := -32; k <= 32; k++ { // ±16h，30 分钟一步
		_, o := time.Unix(wall.Unix()+int64(k)*1800, 0).In(zone).Zone()
		if !containsInt(offsets, o) {
			offsets = append(offsets, o)
		}
	}
	earliest, found := int64(0), false
	for _, o := range offsets {
		at := wall.Unix() - int64(o)
		if _, check := time.Unix(at, 0).In(zone).Zone(); check != o {
			continue
		}
		if !found || at < earliest {
			earliest, found = at, true
		}
	}
	if found {
		return time.Unix(earliest, 0).UTC()
	}

	_, o1 := wall.In(zone).Zone()
	_, o2 := time.Unix(wall.Unix()-int64(o1), 0).In(zone).Zone()
	if o2 < o1 {
		o1 = o2
	}
	return time.Unix(wall.Unix()-int64(o1), 0).UTC()
}

func containsInt(xs []int, x int) bool {
	for _, v := range xs {
		if v == x {
			return true
		}
	}
	return false
}

// weekdayMondayZero 把 Go 的 Sunday=0 换到 Python date.weekday() 的 Monday=0。
func weekdayMondayZero(d time.Time) int {
	return (int(d.Weekday()) + 6) % 7
}

// curriculumSlot 是 day_no % 7。Python 的 % 对负数返回非负（-1 % 7 == 6），
// Go 的 % 跟着被除数符号——day_no 由 day_no_of() 生成为非负，但历史脏值不是保证，
// 少这一步会让负 day_no 的行落到 byDay[-1]，于是那一格永远不出卡而 Python 会出。
func curriculumSlot(dayNo int) int {
	slot := dayNo % CurriculumWeekDays
	if slot < 0 {
		slot += CurriculumWeekDays
	}
	return slot
}

// BuildCurriculumWeek 组周视图。cards 必须按 scheduled_at 正序传入：
// 同一格有多行时 Python 的 `by_day[idx] = e` 是**后写覆盖先写**。
func BuildCurriculumWeek(cards []CurriculumCard, monday, today time.Time) CurriculumWeek {
	bySlot := make(map[int]CurriculumCard, CurriculumWeekDays)
	for _, card := range cards {
		bySlot[curriculumSlot(card.DayNo)] = card
	}

	week := CurriculumWeek{WeekStart: monday.Format("2006-01-02"), Days: []CurriculumDay{}}
	for i := 0; i < CurriculumWeekDays; i++ {
		card, ok := bySlot[i]
		if !ok {
			continue
		}
		day := monday.AddDate(0, 0, i)
		// revealed = day <= today：两个本地日历日比大小，不比时刻。
		revealed := !day.After(today)
		week.Days = append(week.Days, CurriculumDay{
			ID: card.ID, Date: day.Format("2006-01-02"), DayNo: card.DayNo,
			Revealed: revealed, Read: card.HasReadAt,
			Payload: card.Payload, MemoryRefs: card.MemoryRefs,
		})
		if revealed && !card.HasReadAt {
			week.Unread++
		}
	}
	return week
}

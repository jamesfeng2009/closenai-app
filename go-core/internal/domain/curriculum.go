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
	return time.Date(civil.Year(), civil.Month(), civil.Day(), 0, 0, 0, 0, zone).UTC()
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

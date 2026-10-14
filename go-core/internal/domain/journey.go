// N8 批 3（journey 域）的剧本预写与推送窗口算术。
//
// 逐字对应 backend/services/journey_service.py 的三处：
//
//	SCRIPT_EVENTS（:63-70 的 D0–D7 时刻表，D4/D6 天然没有事件）
//	_push_moment（:229-243 的「用户本地偏好时刻 → UTC 瞬间」）
//	prefill/ensure_prefilled（:162-201 的幂等预写与「过去的剧本日置 skipped」）
//
// 这里只算**计划**，不碰库：每行的「已存在就改计划时刻」「改完再判 status 是否仍为
// pending 才 skip」都依赖读回来的行状态，那是 handler 的顺序（见 journey_events.go）。
package domain

import (
	"strings"
	"time"
	"unicode"
)

// JourneyListLimit 同 journey_repository.list_for_user 的默认 limit=50。
const JourneyListLimit = 50

// ScriptEvent 是 SCRIPT_EVENTS 的一格（kind 与相对 day0 的日序）。
type ScriptEvent struct {
	Kind   string
	Offset int
}

// ScriptEvents 逐字抄 journey_service.py:63-70 的六行；顺序也是 Python 的写入顺序
// （同一事务内的行序会影响 get_by_kind 无 ORDER BY 时的返回序，见批 3 的登记）。
var ScriptEvents = []ScriptEvent{
	{"day0_read", 0},
	{"day1_checkin_echo", 1},
	{"day2_first_outreach", 2},
	{"day3_weak_assert", 3},
	{"day5_stat", 5},
	{"day7_report", 7},
}

// JourneyUser 是 ensure_prefilled 与 prefill 读到的用户列。
// PushTimeRaw 保留**列原文**（含 NULL 与空串），因为 Python 的
// `user.push_time or "09:00"` 只看真假，NULL 与 "" 同一条分支。
// HasCreatedAt 对应 `if base is None: return` 那一格：现役迁移里 users.created_at
// 是 NOT NULL，但判空是 Python 的显式分支，缺这一步会让「没有基准日」的旧行
// 被零值时间当成 1970-01-01 而把整条剧本置成 skipped。
type JourneyUser struct {
	ID           string
	Timezone     string
	Language     string
	PushTimeRaw  string
	CreatedAt    time.Time
	HasCreatedAt bool
	Found        bool
}

// LanguageOrDefault 是 `user.language or "en"`：空串（含 NULL）落 "en"。
func (u JourneyUser) LanguageOrDefault() string {
	if u.Language == "" {
		return "en"
	}
	return u.Language
}

// JourneyRow 是 prefill 要落的一条计划。
type JourneyRow struct {
	Kind        string
	DayNo       int
	ScheduledAt time.Time
	// Past＝day0+offset 早于用户本地今天。Python 在这一格还要再读 event.status，
	// 只有 pending 才 skip；所以 Past 为真不等于一定写 skipped。
	Past bool
}

// JourneyPrefillPlan 复现 prefill 的循环体：day0 与 today 都是**用户本地日历日**，
// base 为 nil 时用「ANCHOR 那一刻的本地今天」当 day0（Python 的 base_day or local_today(...)）。
func JourneyPrefillPlan(user JourneyUser, base *time.Time, now time.Time) []JourneyRow {
	zone := loadUserZone(user.Timezone)
	local := now.In(zone)
	today := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, time.UTC)
	day0 := today
	if base != nil {
		b := base.In(zone)
		day0 = time.Date(b.Year(), b.Month(), b.Day(), 0, 0, 0, 0, time.UTC)
	}

	rows := make([]JourneyRow, 0, len(ScriptEvents))
	for _, ev := range ScriptEvents {
		day := day0.AddDate(0, 0, ev.Offset)
		rows = append(rows, JourneyRow{
			Kind:        ev.Kind,
			DayNo:       ev.Offset,
			ScheduledAt: PushMomentAt(user, day, zone),
			Past:        day.Before(today),
		})
	}
	return rows
}

// JourneyOwner 是 acknowledge 的归属判定面：repository.get 不过滤 user_id，
// 「不存在」与「别人的事件」合成同一条 40402（不泄露他人 ID 有效性）。
type JourneyOwner struct {
	ID     string
	UserID string
	Found  bool
}

// JourneyEventRow 是 list_for_user 的一行（文本列原样，parse_json 在 HTTP 层做）。
// HasSentAt 分开保存，因为 Python 侧 `sent_at` 为 None 时响应里是 null 而不是缺键。
type JourneyEventRow struct {
	ID           string
	DayNo        int
	Kind         string
	Status       string
	ScheduledAt  time.Time
	SentAt       time.Time
	HasSentAt    bool
	Acknowledged bool
	Payload      string
	MemoryRefs   string
}

// JourneyPrefill 是 prefill 循环体要落的一行：计划时刻、幂等键、负载文本，
// 以及「这一格是否已过去」（过去且 status 仍为 pending 才 skip）。
// Payload 是**调用方**按 CPython 的 `json.dumps(payload, ensure_ascii=False)`
// 默认分隔符折好的文本——域层不产字节（同 CurriculumCard.Payload 的口径）。
type JourneyPrefill struct {
	Kind        string
	DayNo       int
	ScheduledAt time.Time
	Payload     string
	Past        bool
}

// PushMomentAt 是 `_push_moment`：把用户偏好的「本地 HH:MM」贴到日历日 day 上再折成 UTC。
// zone 由调用方传（同一批行不必反复 LoadLocation）。
//
// 注意这不是「本地零点＋时分」的加法：DST 缺位的墙钟时刻（智利春季 00:00–00:59、
// 纽约春季 02:00–02:59）与重复的墙钟时刻（秋季回拨）都必须按 Python 的 fold=0 口径落点，
// 所以走 wallClockToUTC 而不是 duration 相加，也不是裸的 time.Date(loc)——后者对**严格落在
// 缺位区间内**的时刻会用切换后的偏移，与 Python 差一小时（2026-03-08 02:30 实测：
// Python 07:30Z / Go 原生 06:30Z）。push_time 是用户可写的，剧本日又由 created_at 推出来，
// 这一支不需要任何脏数据就能到达。
func PushMomentAt(user JourneyUser, day time.Time, zone *time.Location) time.Time {
	hour, minute := parsePushTime(user.PushTimeRaw)
	return wallClockToUTC(zone, day.Year(), day.Month(), day.Day(), hour, minute)
}

// PushMoment 是 `_push_moment(user, day)` 的入口形状：调用方只给「用户 + 日历日」，
// 时区名在这里按 safe_zoneinfo 的口径折出来（与域内其它处同一回退）。
// 导出的唯一理由是批 3② 的 push_moment_corpus 要按同一签名逐条比对（见
// internal/httpapi/n8_pushmoment_test.go）。
func PushMoment(user JourneyUser, day time.Time) time.Time {
	return PushMomentAt(user, day, loadUserZone(user.Timezone))
}

// defaultPushTime 是 Python 里那三个字面量：`or "09:00"` 的兜底与 except 分支的 9,0。
const (
	defaultPushHour   = 9
	defaultPushMinute = 0
)

// parsePushTime 复刻：
//
//	hh, mm = (user.push_time or "09:00").split(":")
//	hour, minute = min(max(int(hh), 0), 23), min(max(int(mm), 0), 59)
//	except (ValueError, IndexError): hour, minute = 9, 0
//
// 五个容易抄歪的点：
//   - 空串与 NULL 同一条 `or` 分支（不是「解析失败」分支）；
//   - "09"（只有一段）走的是 **ValueError: not enough values to unpack**，结果也是 9,0，
//     但 9:00 与 09:00 的落点在同一天上相同——列里有脏值时这条分支真的会被走到，所以留着；
//   - 段数必须**恰好**两段："9:30:00" 走的是 ValueError: too many values to unpack ⇒ 9,0，
//     不是「取前两段」的 9,30。这一支经 PATCH 到不了（`String(5)` 加 `\d{2}:\d{2}`
//     只允许 "HH:MM"），所以它是历史脏值/其它写入者的分支；push_moment_corpus 按
//     **解析器等价**钉住它，不宣称 API 可达；
//   - 不能用 strconv.Atoi：它不吃首尾空白、不吃 `+`、不吃下划线，也**只认 ASCII 数字**
//     ——见 pyIntClamp，那一条才是 CPython 的 int()；
//   - clamp 的顺序是 `min(max(x, lo), hi)`："-3" 先抬到 0 再谈上界，写成
//     max(min(x, hi), lo) 在 lo≠0 的分钟位上会给不同结果。
func parsePushTime(raw string) (int, int) {
	if raw == "" {
		raw = "09:00"
	}
	parts := strings.Split(raw, ":")
	if len(parts) != 2 {
		return defaultPushHour, defaultPushMinute
	}
	hour, ok := pyIntClamp(parts[0], 0, 23)
	if !ok {
		return defaultPushHour, defaultPushMinute
	}
	minute, ok := pyIntClamp(parts[1], 0, 59)
	if !ok {
		return defaultPushHour, defaultPushMinute
	}
	return hour, minute
}

// pyIntClamp 是 `min(max(int(s), lo), hi)`，逐条按 CPython 的 int(str) 口径来（实测口径，
// 不是猜测）：
//   - 首尾剥 Unicode 空白：CPython 认的那 25 个码位与 Go 的 unicode.IsSpace **完全同集合**
//     （0009-000D/0020/0085/00A0/1680/2000-200A/2028/2029/202F/205F/3000），
//     而 001C-001F 与 001A 两侧都不认——所以这里用 IsSpace 而不是 ASCII 表；
//   - 可选 + / - 号；
//   - 数字不限 ASCII：Nd 全类都算（全角１２、阿拉伯-印度 ٢、天城 १…），值取所属
//     10 连号 run 的偏移；
//   - 下划线只允许夹在两个数字之间（"1__0"、"_9"、"9_" 一律 ValueError）。
//
// 为什么不能只认 ASCII：push_time 的入口校验是 `re.fullmatch(r"\d{2}:\d{2}", v)`
// （backend/models/schemas.py:400），Python 的 `\d` 默认 Unicode 感知 ⇒ 「１２:３４」
// **今天就能过 PATCH /user/settings 落进列里**，不是历史脏值才有的分支。
// Go 不实现这一支就会与 `_push_moment`（journey_service.py:238）算出不同的
// scheduled_at 字节——那是 N8「契约不变」里最不该破的一条。
func pyIntClamp(s string, lo, hi int) (int, bool) {
	runes := []rune(s)
	for len(runes) > 0 && unicode.IsSpace(runes[0]) {
		runes = runes[1:]
	}
	for len(runes) > 0 && unicode.IsSpace(runes[len(runes)-1]) {
		runes = runes[:len(runes)-1]
	}
	if len(runes) == 0 {
		return 0, false
	}
	sign := 1
	if runes[0] == '+' || runes[0] == '-' {
		if runes[0] == '-' {
			sign = -1
		}
		runes = runes[1:]
	}
	if len(runes) == 0 {
		return 0, false
	}
	value := 0
	prevDigit := false
	for i, r := range runes {
		if r == '_' {
			// CPython 只让下划线出现在两个数字之间：前不接数字、后不接数字都算失败。
			if !prevDigit || i+1 >= len(runes) || digitValue(runes[i+1]) < 0 {
				return 0, false
			}
			prevDigit = false
			continue
		}
		dv := digitValue(r)
		if dv < 0 {
			return 0, false
		}
		value = value*10 + dv
		prevDigit = true
		if value > 1<<30 { // 只要知道它会被 clamp，就不必背着溢出风险算下去
			value = 1 << 30
		}
	}
	value *= sign
	if value < lo {
		return lo, true
	}
	if value > hi {
		return hi, true
	}
	return value, true
}

// digitValue 是 CPython 的十进制位值：ASCII 快路径 + Nd 全类，非数字回 -1。
func digitValue(r rune) int {
	if r >= '0' && r <= '9' {
		return int(r - '0')
	}
	lo, ok := ndRunStart(r)
	if !ok {
		return -1
	}
	// Unicode 保证每个十进制 run 恰为 10 个连续码位，且 run 之间码位连续时可并表
	// （实测 unicode.Nd.R32 里 1D7CE-1D7FF 就是把五个数学数字 run 并成了一条），
	// 所以偏移要 %10 才是位值。Nd 的 stride 恒为 1，非 1 直接判不可达。
	return int((r - lo) % 10)
}

// ndRunStart 返回 r 所属 Nd run 的起点（找不到＝不是 Nd）。
func ndRunStart(r rune) (rune, bool) {
	for _, rg := range unicode.Nd.R16 {
		if r >= rune(rg.Lo) && r <= rune(rg.Hi) && rg.Stride == 1 {
			return rune(rg.Lo), true
		}
	}
	for _, rg := range unicode.Nd.R32 {
		if r >= rune(rg.Lo) && r <= rune(rg.Hi) && rg.Stride == 1 {
			return rune(rg.Lo), true
		}
	}
	return 0, false
}

// LocalDateOfUTC 是 `_local_date(dt, safe_zoneinfo(tz))`：瞬间折到用户本地日历日
// （仍以 UTC 承载，只比 Y/M/D，与 CurriculumWindow 的口径一致）。
func LocalDateOfUTC(at time.Time, timezone string) time.Time {
	zone := loadUserZone(timezone)
	local := at.In(zone)
	return time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, time.UTC)
}

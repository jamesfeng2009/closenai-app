// speedate（pydantic-core 的日期时间解析器）的口径复刻，N8 契约逐字节回放的前提。
//
// 为什么不能直接用 Go 的 time.Parse：`occurred_at` 的**校验失败文案**就是 HTTP 400 响应体
// 的一部分，而 speedate 不是 RFC 3339 解析器——它有 7 类错误词、逐位置的字符判定、
// 「时间部任何失败一律塌缩成 unexpected extra characters」的收敛口径，
// 以及三种输入形态各自不同的错误类型。
// 下面每条规则都对应 go-core/testdata/n8_contract.json 的 occurred_at_corpus 里
// 至少一条实测（2026-10-14 由 scripts/gen_n8_contract_goldens.py 真读 pydantic 录制），
// 不是从文档推的。
package httpapi

import (
	"fmt"
	"math"
	"strconv"
	"strings"
)

// Python datetime 的硬界换算成 µs，用于时间戳越界判定（两端都是闭区间，实测
// '-62135596800000' 毫秒恰好落在 0001-01-01 且可用）。
const (
	minYearOneMicros  = int64(-62135596800000000) // 0001-01-01T00:00:00
	maxYear9999Micros = int64(253402300799999999) // 9999-12-31T23:59:59.999999
	microsPerSecond   = int64(1_000_000)
	microsPerMinute   = 60 * microsPerSecond
	microsPerHour     = 60 * microsPerMinute
	microsPerDay      = 24 * microsPerHour
	after9999Word     = "dates after 9999 are not supported as unix timestamps"
	before0000Word    = "dates before 0000 are not supported as unix timestamps"
	extraCharsWord    = "unexpected extra characters at the end of the input"
)

// speedateKind 决定 400 体里的 `type` 与 `msg` 前缀，三者由**输入形态**而非失败原因决定：
//
//	datetime_type              —— null/bool/数组/对象（没有 ctx）
//	datetime_from_date_parsing —— 字符串在 speedate 这一层就失败（含数字串越界）
//	datetime_parsing           —— JSON 数字越界，或字符串解析成功、转 Python datetime 才失败（year 0）
type speedateKind int

const (
	speedateType speedateKind = iota
	speedateFromDateParsing
	speedateParsing
)

func (k speedateKind) String() string {
	switch k {
	case speedateType:
		return "datetime_type"
	case speedateFromDateParsing:
		return "datetime_from_date_parsing"
	default:
		return "datetime_parsing"
	}
}

// Message 逐字对照 pydantic-core：datetime_type 无后缀，from_date_parsing 带 " or date"。
func (k speedateKind) Message(word string) string {
	switch k {
	case speedateType:
		return "Input should be a valid datetime"
	case speedateFromDateParsing:
		return "Input should be a valid datetime or date, " + word
	default:
		return "Input should be a valid datetime, " + word
	}
}

type speedateError struct {
	kind speedateKind
	word string // speedateType 时为空，且不产出 ctx
}

// pyDatetime 是解析结果：本地墙钟 µs + 可选偏移。
// 存墙钟而不是 UTC，因为 isoformat() 与落库口径都保留原偏移。
type pyDatetime struct {
	wallMicros int64
	offsetSec  int
	hasTz      bool
}

// UTCMicros 是中继侧真正要送的时刻；naive 值按 UTC 处理（与录制脚本的口径一致）。
func (d pyDatetime) UTCMicros() int64 {
	if !d.hasTz {
		return d.wallMicros
	}
	return d.wallMicros - int64(d.offsetSec)*microsPerSecond
}

// Isoformat 复刻 Python datetime.isoformat()：微秒为零时不写小数段，偏移写 ±HH:MM。
func (d pyDatetime) Isoformat() string {
	y, mo, day, hh, mm, ss, micro := civilFromMicros(d.wallMicros)
	out := fmt.Sprintf("%04d-%02d-%02dT%02d:%02d:%02d", y, mo, day, hh, mm, ss)
	if micro != 0 {
		out += fmt.Sprintf(".%06d", micro)
	}
	if d.hasTz {
		sign, offset := "+", d.offsetSec
		if offset < 0 {
			sign, offset = "-", -offset
		}
		out += fmt.Sprintf("%s%02d:%02d", sign, offset/3600, (offset%3600)/60)
	}
	return out
}

// ParseOccurredAt 覆盖 AnalyticsEvent.occurred_at 的取值面。
// 返回二者之一：可用的 pyDatetime，或带错误词的 speedateError。
func ParseOccurredAt(v *pyValue) (pyDatetime, *speedateError) {
	if v == nil {
		return pyDatetime{}, &speedateError{kind: speedateType}
	}
	switch v.kind {
	case pyString:
		return parseDateStringOrNumber(v.str)
	case pyInt:
		f, _ := strconv.ParseFloat(v.intLit, 64) // 溢出即 ±Inf，走数字越界
		return datetimeFromFloat(f)
	case pyFloat:
		return datetimeFromFloat(v.float)
	default:
		return pyDatetime{}, &speedateError{kind: speedateType}
	}
}

// datetimeFromFloat 处理 JSON 数字。实测三条分岔：
//   - |v| > 2e10 按毫秒（20000000000 本身仍按秒），且这条路径的 µs 是**精确**的；
//   - 秒路径的负分数有一条符号怪癖：second 用 floor，microsecond 却取 |round(frac)|
//     而不是补数，于是 -5e-07 秒解析成 23:59:59.000001（-999999µs）而不是 23:59:59.999999；
//     由 -5e-07 / -1.4e-06 / -1.5e-06 / -0.9999995 / -1.0000005 五条实测钉住；
//   - NaN 有独立错误词。
func datetimeFromFloat(f float64) (pyDatetime, *speedateError) {
	if math.IsNaN(f) {
		return pyDatetime{}, &speedateError{kind: speedateParsing, word: "NaN values not permitted"}
	}
	if f > 2e10 || f < -2e10 {
		micros := math.Round(f * 1000) // Go 的 Round 即 tie 远离零，与实测一致
		if micros > float64(maxYear9999Micros) || math.IsInf(micros, 1) {
			return pyDatetime{}, &speedateError{kind: speedateParsing, word: after9999Word}
		}
		if micros < float64(minYearOneMicros) || math.IsInf(micros, -1) {
			return pyDatetime{}, &speedateError{kind: speedateParsing, word: before0000Word}
		}
		return pyDatetime{wallMicros: int64(micros), hasTz: true}, nil
	}
	sec := math.Floor(f)
	micros := math.Abs(math.Round((f - math.Trunc(f)) * float64(microsPerSecond)))
	if micros >= float64(microsPerSecond) { // -0.9999995 实测落到 epoch
		micros -= float64(microsPerSecond)
		sec++
	}
	total := sec*float64(microsPerSecond) + micros
	if total > float64(maxYear9999Micros) {
		return pyDatetime{}, &speedateError{kind: speedateParsing, word: after9999Word}
	}
	if total < float64(minYearOneMicros) {
		return pyDatetime{}, &speedateError{kind: speedateParsing, word: before0000Word}
	}
	return pyDatetime{wallMicros: int64(total), hasTz: true}, nil
}

// parseDateStringOrNumber 的优先顺序是实测出来的：先试「整串都是数字」，
// 成功才按 unix 时间戳；否则走 ISO 形状。这解释了为什么 20 个 9 报 separator——
// 它超出 i64，落回日期解析后被当成 '9999' 年、第 5 位不是 '-'。
func parseDateStringOrNumber(s string) (pyDatetime, *speedateError) {
	if num, ok := matchNumericString(s); ok && num.intFitsI64() {
		return datetimeFromNumericString(num)
	}
	return parseISODate(s)
}

// ── ISO 日期串 ───────────────────────────────────────────────────────────────

func parseISODate(s string) (pyDatetime, *speedateError) {
	fail := func(word string) (pyDatetime, *speedateError) {
		return pyDatetime{}, &speedateError{kind: speedateFromDateParsing, word: word}
	}
	tooShort := func() (pyDatetime, *speedateError) { return fail("input is too short") }
	if len(s) < 10 { // 按字节判长：'١٢٣' 实测落在这里
		return tooShort()
	}
	year, ok := digitsValue(s[0:4])
	if !ok {
		return fail("invalid character in year")
	}
	if s[4] != '-' {
		return fail("invalid date separator, expected `-`")
	}
	month, ok := digitsValue(s[5:7])
	if !ok {
		return fail("invalid character in month")
	}
	if month < 1 || month > 12 {
		return fail("month value is outside expected range of 1-12")
	}
	if s[7] != '-' {
		return fail("invalid date separator, expected `-`")
	}
	day, ok := digitsValue(s[8:10])
	if !ok {
		return fail("invalid character in day")
	}
	if day < 1 || day > daysInMonth(year, month) {
		return fail("day value is outside expected range")
	}
	if len(s) == 10 {
		return newParsedDate(year, month, day, 0, 0, 0, 0, 0, false)
	}
	if c := s[10]; c != 'T' && c != 't' && c != ' ' {
		return fail(extraCharsWord)
	}
	rest := s[11:]
	if len(rest) < 5 {
		return fail(extraCharsWord)
	}
	hh, ok := digitsValue(rest[0:2])
	if !ok || hh > 23 || rest[2] != ':' {
		return fail(extraCharsWord)
	}
	mm, ok := digitsValue(rest[3:5])
	if !ok || mm > 59 {
		return fail(extraCharsWord)
	}
	rest = rest[5:]
	var ss, micro int
	if len(rest) > 0 && rest[0] == ':' {
		if len(rest) < 3 {
			return fail(extraCharsWord)
		}
		seconds, ok := digitsValue(rest[1:3])
		if !ok || seconds > 59 { // 实测 60 也不接受：没有闰秒
			return fail(extraCharsWord)
		}
		ss = seconds
		rest = rest[3:]
		if len(rest) > 0 && (rest[0] == '.' || rest[0] == ',') {
			digits := 1
			for digits < len(rest) && isDigit(rest[digits]) {
				digits++
			}
			if digits == 1 { // '...10.' 实测被拒
				return fail(extraCharsWord)
			}
			micro = fractionalMicros(rest[1:digits]) // 超出 6 位是**截断**，不是舍入
			rest = rest[digits:]
		}
	} else if len(rest) > 0 && (rest[0] == '.' || rest[0] == ',') {
		return fail(extraCharsWord) // '2026-10-01T08:09.5' 实测被拒：小数秒必须跟在秒后
	}
	if len(rest) == 0 {
		return newParsedDate(year, month, day, hh, mm, ss, micro, 0, false)
	}
	offset, ok := parseOffset(rest)
	if !ok {
		return fail(extraCharsWord)
	}
	return newParsedDate(year, month, day, hh, mm, ss, micro, offset, true)
}

// parseOffset 只接受三种形状（实测）：Z/z、±HH:MM、±HHMM。
// '+070'、'+7:00'、'+07:0'、'+07:00Z'、'Z '、'Zx' 一律落回 extra characters；
// ±HH ≤ 23、MM ≤ 59（'+24:00'、'-24:00'、'+07:60' 实测被拒，'-14:00' 可用）。
func parseOffset(rest string) (int, bool) {
	if rest == "Z" || rest == "z" {
		return 0, true
	}
	sign := 0
	switch rest[0] {
	case '+':
		sign = 1
	case '-':
		sign = -1
	default:
		return 0, false
	}
	body := rest[1:]
	var hh, mm string
	switch {
	case len(body) == 5 && body[2] == ':':
		hh, mm = body[0:2], body[3:5]
	case len(body) == 4:
		hh, mm = body[0:2], body[2:4]
	default:
		return 0, false
	}
	h, ok1 := digitsValue(hh)
	m, ok2 := digitsValue(mm)
	if !ok1 || !ok2 || h > 23 || m > 59 {
		return 0, false
	}
	return sign * (h*3600 + m*60), true
}

// fractionalMicros 把 1..N 位小数数字补齐/截断到 6 位 µs。
func fractionalMicros(digits string) int {
	if len(digits) < 6 {
		digits += strings.Repeat("0", 6-len(digits))
	}
	micro, _ := strconv.Atoi(digits[:6])
	return micro
}

func digitsValue(s string) (int, bool) {
	n := 0
	for i := 0; i < len(s); i++ {
		if !isDigit(s[i]) {
			return 0, false
		}
		n = n*10 + int(s[i]-'0')
	}
	return n, true
}

func isDigit(c byte) bool { return c >= '0' && c <= '9' }

func daysInMonth(year, month int) int {
	days := [...]int{31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31}
	if month == 2 && isLeapYear(year) {
		return 29
	}
	return days[month-1]
}

func isLeapYear(year int) bool {
	return year%4 == 0 && (year%100 != 0 || year%400 == 0)
}

// newParsedDate 承担「speedate 通过、Python 转换失败」那一格：
// year 0 只有这条路可达，实测类型是 datetime_parsing、文案 year 0 is out of range。
func newParsedDate(y, mo, d, hh, mm, ss, micro, offsetSec int, hasTz bool) (pyDatetime, *speedateError) {
	if y == 0 {
		return pyDatetime{}, &speedateError{kind: speedateParsing, word: "year 0 is out of range"}
	}
	return pyDatetime{
		wallMicros: microsFromCivil(y, mo, d, hh, mm, ss, micro),
		offsetSec:  offsetSec,
		hasTz:      hasTz,
	}, nil
}

// ── 数字串（unix 时间戳写法）────────────────────────────────────────────────

// numericString 是 '±整数[.小数]' 的分解。数字保留原文而不是先转 float：
// 实测 '-0.0000005'→0µs 而 '-0.0000015'→-1µs，浮点乘给不出这个口径（0.5 会变成 0.5000000000000001）。
type numericString struct {
	neg      bool
	intPart  string
	fracPart string
}

// matchNumericString 的接受面（实测）：'1.'、'.5'、'00'、'007'、'+1.5'、'-0'、'+0' 可用；
// '+'、'.'、'1e5'、'1.2.3'、'--1'、'1-'、'0x10'、' 1'、'١٢٣' 不可用。
func matchNumericString(s string) (numericString, bool) {
	out := numericString{}
	body := s
	if len(body) > 0 && (body[0] == '+' || body[0] == '-') {
		out.neg = body[0] == '-'
		body = body[1:]
	}
	intDigits, fracDigits, hasFrac, dotAt := 0, 0, false, -1
	for i := 0; i < len(body); i++ {
		switch {
		case isDigit(body[i]):
			if hasFrac {
				fracDigits++
			} else {
				intDigits++
			}
		case body[i] == '.' && !hasFrac:
			hasFrac, dotAt = true, i
		default:
			return numericString{}, false
		}
	}
	if intDigits == 0 && fracDigits == 0 {
		return numericString{}, false
	}
	if !hasFrac {
		out.intPart = body
		return out, true
	}
	out.intPart, out.fracPart = body[:dotAt], body[dotAt+1:]
	return out, true
}

// intFitsI64 是「还算不算数字」的分界（实测）：'9223372036854775807' 算，
// '9223372036854775808' 不算并落回日期解析报 separator；前导零不计入位数。
func (n numericString) intFitsI64() bool {
	digits := strings.TrimLeft(n.intPart, "0")
	switch {
	case len(digits) > 19:
		return false
	case len(digits) < 19:
		return true
	}
	return digits <= "9223372036854775807"
}

// datetimeFromNumericString 用十进制精确算 µs，并按 floor(x+1/2) 舍入。
//
// 舍入方向是实测出来的：+0.5µs→1 而 -0.5µs→0（舍向正无穷，既非四舍五入也非偶数舍入），
// 因此负数只在小数部分**严格大于** 0.5 时才进位。
//
// 已知发散点（2026-10-14 登记在 tasks.md，回放测用显式白名单跳过并断言白名单不失效）：
// |整数部分| 大到 µs 会溢出 i64、但仍在 i64 内时，CPython 走浮点回绕给出一个「看似合法」
// 的荒谬年份（'92233720368547758.7'→4892 年），而再大一位的 '922337203685477580.7'
// 又落回日期解析报 separator——Python 自身在这两处不自洽。Go 一侧统一按 i64 溢出
// 报「dates after 9999」，即两侧都是拒绝、只是错误词不同。
func datetimeFromNumericString(n numericString) (pyDatetime, *speedateError) {
	rangeErr := &speedateError{kind: speedateFromDateParsing,
		word: unixTimestampRangeWord(n.neg)}
	intMag := strings.TrimLeft(n.intPart, "0")
	millis := len(intMag) > 11
	if len(intMag) == 11 {
		millis = intMag > "20000000000" || (intMag == "20000000000" && strings.TrimLeft(n.fracPart, "0") != "")
	}
	if intMag == "" {
		intMag = "0"
	}
	seconds, err := strconv.ParseInt(intMag, 10, 64)
	if err != nil {
		return pyDatetime{}, rangeErr
	}
	scale, shift := microsPerSecond, 6
	if millis {
		scale, shift = 1000, 3
	}
	if seconds > maxYear9999Micros/scale {
		return pyDatetime{}, rangeErr
	}
	// 小数部分对 µs 的贡献只需前 shift+1 位：第 shift 位之后决定是否进位。
	micros := seconds*scale + n.fractionMicros(shift)
	if n.fractionCarry(shift, !n.neg) {
		micros++
	}
	if n.neg {
		micros = -micros
	}
	if micros > maxYear9999Micros {
		return pyDatetime{}, &speedateError{kind: speedateFromDateParsing, word: after9999Word}
	}
	if micros < minYearOneMicros {
		return pyDatetime{}, &speedateError{kind: speedateFromDateParsing, word: before0000Word}
	}
	return pyDatetime{wallMicros: micros, hasTz: true}, nil
}

// fractionHead 返回小数部分换算成 µs 的**截断**值与紧随其后的那一位（进位判据）。
// shift 是秒→µs（6）还是毫秒→µs（3）。
func (n numericString) fractionHead(shift int) (int64, byte, string) {
	if n.fracPart == "" {
		return 0, 0, ""
	}
	head := n.fracPart
	if len(head) > shift {
		tail := head[shift:]
		head = head[:shift]
		v, _ := strconv.ParseInt(head, 10, 64)
		return v, tail[0], tail[1:]
	}
	v, _ := strconv.ParseInt(head, 10, 64)
	for i := len(head); i < shift; i++ {
		v *= 10
	}
	return v, 0, ""
}

func (n numericString) fractionMicros(shift int) int64 {
	head, _, _ := n.fractionHead(shift)
	return head
}

// fractionCarry 实现「正数 ≥0.5 进位、负数 >0.5 才进位」。
func (n numericString) fractionCarry(shift int, positive bool) bool {
	_, digit, rest := n.fractionHead(shift)
	if digit == 0 {
		return false
	}
	if digit > '5' {
		return true
	}
	if digit < '5' {
		return false
	}
	return positive || strings.TrimLeft(rest, "0") != ""
}

func unixTimestampRangeWord(before bool) string {
	if before {
		return before0000Word
	}
	return after9999Word
}

// ── 历法换算 ─────────────────────────────────────────────────────────────────
//
// 用 Howard Hinnant 的 civil↔days 逆运算，覆盖 0001-01-01 .. 9999-12-31；
// µs 采用 floor 语义（负方向不向零截断）。

func microsFromCivil(year, month, day, hh, mm, ss, micro int) int64 {
	return int64(daysFromCivil(year, month, day))*microsPerDay +
		int64(hh)*microsPerHour + int64(mm)*microsPerMinute +
		int64(ss)*microsPerSecond + int64(micro)
}

func civilFromMicros(us int64) (year, month, day, hh, mm, ss, micro int) {
	days := us / microsPerDay
	rem := us % microsPerDay
	if rem < 0 {
		rem += microsPerDay
		days--
	}
	hh = int(rem / microsPerHour)
	rem -= int64(hh) * microsPerHour
	mm = int(rem / microsPerMinute)
	rem -= int64(mm) * microsPerMinute
	ss = int(rem / microsPerSecond)
	micro = int(rem - int64(ss)*microsPerSecond)
	year, month, day = civilFromDays(days)
	return year, month, day, hh, mm, ss, micro
}

func daysFromCivil(y, m, d int) int64 {
	if m <= 2 {
		y--
	}
	var era int64
	if y >= 0 {
		era = int64(y) / 400
	} else {
		era = (int64(y) - 399) / 400
	}
	yoe := int64(y) - era*400
	var mm int64 = int64(m) + 9
	if m > 2 {
		mm = int64(m) - 3
	}
	doy := (153*mm+2)/5 + int64(d) - 1
	doe := yoe*365 + yoe/4 - yoe/100 + doy
	return era*146097 + doe - 719468
}

func civilFromDays(days int64) (y, m, d int) {
	z := days + 719468
	var era int64
	if z >= 0 {
		era = z / 146097
	} else {
		era = (z - 146096) / 146097
	}
	doe := z - era*146097
	yoe := (doe - doe/1460 + doe/36524 - doe/146096) / 365
	y = int(yoe + era*400)
	doy := doe - (365*yoe + yoe/4 - yoe/100)
	mp := (5*doy + 2) / 153
	d = int(doy - (153*mp+2)/5 + 1)
	if mp < 10 {
		m = int(mp) + 3
	} else {
		m = int(mp) - 9
	}
	if m <= 2 {
		y++
	}
	return y, m, d
}

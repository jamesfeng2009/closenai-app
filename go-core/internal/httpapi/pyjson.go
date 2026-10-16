// CPython json 的**值语义**复刻，N8 契约逐字回放的前提。
//
// 为什么不能直接用 encoding/json：FastAPI 的 400 体会把请求里的 `input` 原样回显，
// 而那串字节是 `json.loads(json.dumps(errors))` 再 `JSONResponse(ensure_ascii=False,
// separators=(",", ":"))` 的产物——三处与 Go 默认行为不同：
//   - 对象**键序**＝文档顺序（Go 的 map 会排序），重复键后值覆盖前值但**位置不变**（Python dict）；
//   - 数字：整数保留任意精度字面量，浮点按 `repr()` 重排（1e16 写成 `1e+16`、
//     9999999999999998 写成 `9999999999999998.0`，与 Go `%v` 的阈值不同）；
//   - 字符串只转义 `"` `\` 与控制字符，**不**转义 `<` `>` `&` `/`（Go 默认转义）。
//
// 另外 JSONDecodeError 的文案与 char 位置也是契约的一部分（loc 末位是**字符**下标，
// 不是字节下标）。
package httpapi

import (
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"
	"unicode/utf16"
	"unicode/utf8"
)

type pyKind uint8

const (
	pyNull pyKind = iota
	pyBool
	pyInt
	pyFloat
	pyString
	pyArray
	pyObject
)

type pyMember struct {
	key string
	val *pyValue
}

// pyValue 是一个 JSON 值。intLit 保存整数字面量原文（规范化后），
// 这样 10**30 这类超出 int64 的数也能逐字回显。
type pyValue struct {
	kind    pyKind
	truthy  bool
	intLit  string
	float   float64
	str     string
	items   []*pyValue
	members []pyMember
}

var errNonFiniteJSON = errors.New("响应含 NaN/Infinity，CPython 侧同样是编码失败")

func pyStringValue(s string) *pyValue { return &pyValue{kind: pyString, str: s} }

func pyBoolValue(b bool) *pyValue { return &pyValue{kind: pyBool, truthy: b} }

func pyIntValue(n int) *pyValue { return &pyValue{kind: pyInt, intLit: strconv.Itoa(n)} }

// pyFloatValue 给的是**列里那个 float**（如 memories.conviction_score），
// 序列化走 pyFloatRepr（Python repr(float) 口径：0.0 不是 0）。
// encoding/json 会把 0.0 写成 0，那一格两侧不同形，所以这类字段一律走 pyValue。
func pyFloatValue(f float64) *pyValue { return &pyValue{kind: pyFloat, float: f} }

func pyNullValue() *pyValue { return &pyValue{kind: pyNull} }

func pyArrayValue(items ...*pyValue) *pyValue {
	return &pyValue{kind: pyArray, items: items}
}

// pyObjectValue 按给出顺序建对象；重复键沿用首次位置、取最后一次的值。
func pyObjectValue(pairs ...string) *pyValue {
	v := &pyValue{kind: pyObject}
	for i := 0; i+1 < len(pairs); i += 2 {
		v.set(pairs[i], pyStringValue(pairs[i+1]))
	}
	return v
}

func (v *pyValue) set(key string, val *pyValue) {
	for i := range v.members {
		if v.members[i].key == key {
			v.members[i].val = val
			return
		}
	}
	v.members = append(v.members, pyMember{key: key, val: val})
}

func (v *pyValue) get(key string) (*pyValue, bool) {
	for i := len(v.members) - 1; i >= 0; i-- {
		if v.members[i].key == key {
			return v.members[i].val, true
		}
	}
	return nil, false
}

func (v *pyValue) isNullish() bool { return v == nil || v.kind == pyNull }

// ── 解析 ─────────────────────────────────────────────────────────────────────

// pyJSONError 对应 CPython json.JSONDecodeError：msg 是 ctx.error 原文，
// pos 是**字符**下标（Python 在 str 上扫描，不在 bytes 上）。
type pyJSONError struct {
	msg string
	pos int
}

func (e *pyJSONError) Error() string { return fmt.Sprintf("%s (char %d)", e.msg, e.pos) }

// pyJSONLoads 复刻 C 加速版 json.loads 的可观察口径：
// 允许前导/尾随空白为 " \t\n\r"，允许 NaN/Infinity/-Infinity 字面量，
// 且**不接受**注释、单引号、前导零、多余逗号（数组尾逗号报 Expecting value）。
func pyJSONLoads(raw []byte) (*pyValue, *pyJSONError) {
	s := []rune(string(raw))
	idx := pySkipWS(s, 0)
	v, next, err := pyScanOnce(s, idx)
	if err != nil {
		return nil, err
	}
	if pySkipWS(s, next) != len(s) {
		return nil, &pyJSONError{"Extra data", pySkipWS(s, next)}
	}
	return v, nil
}

func pySkipWS(s []rune, i int) int {
	for i < len(s) {
		switch s[i] {
		case ' ', '\t', '\n', '\r':
			i++
			continue
		}
		return i
	}
	return i
}

func pyLit(s []rune, i int, lit string) bool {
	return i+len(lit) <= len(s) && string(s[i:i+len(lit)]) == lit
}

func pyScanOnce(s []rune, i int) (*pyValue, int, *pyJSONError) {
	if i >= len(s) {
		return nil, 0, &pyJSONError{"Expecting value", i}
	}
	switch c := s[i]; {
	case c == '"':
		return pyScanString(s, i)
	case c == '{':
		return pyScanObject(s, i)
	case c == '[':
		return pyScanArray(s, i)
	case pyLit(s, i, "null"):
		return pyNullValue(), i + 4, nil
	case pyLit(s, i, "true"):
		return &pyValue{kind: pyBool, truthy: true}, i + 4, nil
	case pyLit(s, i, "false"):
		return &pyValue{kind: pyBool}, i + 5, nil
	case pyLit(s, i, "NaN"):
		return &pyValue{kind: pyFloat, float: math.NaN()}, i + 3, nil
	case pyLit(s, i, "Infinity"):
		return &pyValue{kind: pyFloat, float: math.Inf(1)}, i + 8, nil
	case pyLit(s, i, "-Infinity"):
		return &pyValue{kind: pyFloat, float: math.Inf(-1)}, i + 9, nil
	case c == '-' || (c >= '0' && c <= '9'):
		return pyScanNumber(s, i)
	default:
		return nil, 0, &pyJSONError{"Expecting value", i}
	}
}

func pyScanNumber(s []rune, i int) (*pyValue, int, *pyJSONError) {
	start := i
	if i < len(s) && s[i] == '-' {
		i++
	}
	digitsFrom := i
	for i < len(s) && s[i] >= '0' && s[i] <= '9' {
		i++
	}
	if i == digitsFrom {
		return nil, 0, &pyJSONError{"Expecting value", start}
	}
	// 前导零只允许单独的 "0"（CPython 的 NUMBER_RE：0|[1-9][0-9]*）。
	if s[digitsFrom] == '0' && i-digitsFrom > 1 {
		i = digitsFrom + 1
	}
	isInt := true
	if i < len(s) && s[i] == '.' {
		j := i + 1
		for j < len(s) && s[j] >= '0' && s[j] <= '9' {
			j++
		}
		if j > i+1 {
			isInt, i = false, j
		}
	}
	if i < len(s) && (s[i] == 'e' || s[i] == 'E') {
		j := i + 1
		if j < len(s) && (s[j] == '+' || s[j] == '-') {
			j++
		}
		digits := j
		for j < len(s) && s[j] >= '0' && s[j] <= '9' {
			j++
		}
		if j > digits {
			isInt, i = false, j
		}
	}
	text := string(s[start:i])
	if !isInt {
		f, err := strconv.ParseFloat(text, 64)
		if err != nil {
			return nil, 0, &pyJSONError{"Expecting value", start}
		}
		return &pyValue{kind: pyFloat, float: f}, i, nil
	}
	if text == "-0" {
		text = "0" // Python int("-0") == 0，回显也是 0
	}
	return &pyValue{kind: pyInt, intLit: text}, i, nil
}

var pySimpleEscapes = map[rune]rune{
	'"': '"', '\\': '\\', '/': '/', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t',
}

func pyScanString(s []rune, begin int) (*pyValue, int, *pyJSONError) {
	var b strings.Builder
	i := begin + 1
	for i < len(s) {
		c := s[i]
		switch {
		case c == '"':
			return &pyValue{kind: pyString, str: b.String()}, i + 1, nil
		case c < ' ':
			return nil, 0, &pyJSONError{"Invalid control character at", i}
		case c == '\\':
			i++
			if i >= len(s) {
				return nil, 0, &pyJSONError{"Unterminated string starting at", begin}
			}
			if r, ok := pySimpleEscapes[s[i]]; ok {
				b.WriteRune(r)
				i++
				continue
			}
			if s[i] != 'u' {
				return nil, 0, &pyJSONError{`Invalid \escape`, i - 1}
			}
			escPos := i
			r1, ok := pyHex4(s, i+1)
			if !ok {
				return nil, 0, &pyJSONError{`Invalid \uXXXX escape`, escPos}
			}
			i += 5
			// 代理对：与 CPython 一样把 \uD800\uDC00 合成一个字符。
			if utf16.IsSurrogate(r1) && i+5 < len(s)+1 && s[i] == '\\' && s[i+1] == 'u' {
				if r2, ok2 := pyHex4(s, i+2); ok2 && utf16.IsSurrogate(r2) {
					if combined := utf16.DecodeRune(r1, r2); combined != utf8.RuneError {
						b.WriteRune(combined)
						i += 6
						continue
					}
				}
			}
			b.WriteRune(r1)
		default:
			b.WriteRune(c)
			i++
		}
	}
	return nil, 0, &pyJSONError{"Unterminated string starting at", begin}
}

func pyHex4(s []rune, i int) (rune, bool) {
	if i+4 > len(s) {
		return 0, false
	}
	var v rune
	for k := 0; k < 4; k++ {
		d := hexDigit(s[i+k])
		if d < 0 {
			return 0, false
		}
		v = v*16 + rune(d)
	}
	return v, true
}

func hexDigit(c rune) int {
	switch {
	case c >= '0' && c <= '9':
		return int(c - '0')
	case c >= 'a' && c <= 'f':
		return int(c-'a') + 10
	case c >= 'A' && c <= 'F':
		return int(c-'A') + 10
	}
	return -1
}

func pyScanObject(s []rune, begin int) (*pyValue, int, *pyJSONError) {
	obj := &pyValue{kind: pyObject}
	i := pySkipWS(s, begin+1)
	if i < len(s) && s[i] == '}' {
		return obj, i + 1, nil
	}
	for {
		if i >= len(s) || s[i] != '"' {
			return nil, 0, &pyJSONError{
				"Expecting property name enclosed in double quotes", i}
		}
		key, next, err := pyScanString(s, i)
		if err != nil {
			return nil, 0, err
		}
		i = pySkipWS(s, next)
		if i >= len(s) || s[i] != ':' {
			return nil, 0, &pyJSONError{"Expecting ':' delimiter", i}
		}
		val, next, err := pyScanOnce(s, pySkipWS(s, i+1))
		if err != nil {
			return nil, 0, err
		}
		obj.set(key.str, val)
		i = pySkipWS(s, next)
		if i < len(s) && s[i] == ',' {
			i = pySkipWS(s, i+1)
			continue
		}
		if i < len(s) && s[i] == '}' {
			return obj, i + 1, nil
		}
		return nil, 0, &pyJSONError{"Expecting ',' delimiter", i}
	}
}

func pyScanArray(s []rune, begin int) (*pyValue, int, *pyJSONError) {
	arr := &pyValue{kind: pyArray}
	i := pySkipWS(s, begin+1)
	if i < len(s) && s[i] == ']' {
		return arr, i + 1, nil
	}
	for {
		val, next, err := pyScanOnce(s, i)
		if err != nil {
			return nil, 0, err
		}
		arr.items = append(arr.items, val)
		i = pySkipWS(s, next)
		if i < len(s) && s[i] == ',' {
			i = pySkipWS(s, i+1)
			continue
		}
		if i < len(s) && s[i] == ']' {
			return arr, i + 1, nil
		}
		return nil, 0, &pyJSONError{"Expecting ',' delimiter", i}
	}
}

// ── 再编码 ───────────────────────────────────────────────────────────────────

// encodeTo 写出 JSONResponse 风格：紧凑分隔符、ensure_ascii=False、不转义 HTML 字符。
func (v *pyValue) encodeTo(b *strings.Builder) error {
	if v == nil {
		b.WriteString("null")
		return nil
	}
	switch v.kind {
	case pyNull:
		b.WriteString("null")
	case pyBool:
		if v.truthy {
			b.WriteString("true")
		} else {
			b.WriteString("false")
		}
	case pyInt:
		b.WriteString(v.intLit)
	case pyFloat:
		if math.IsNaN(v.float) || math.IsInf(v.float, 0) {
			// CPython 侧 allow_nan=False ⇒ 同样的编码失败，交给上层映射成 500。
			return errNonFiniteJSON
		}
		b.WriteString(pyFloatRepr(v.float))
	case pyString:
		pyWriteString(b, v.str)
	case pyArray:
		b.WriteByte('[')
		for i, item := range v.items {
			if i > 0 {
				b.WriteByte(',')
			}
			if err := item.encodeTo(b); err != nil {
				return err
			}
		}
		b.WriteByte(']')
	case pyObject:
		b.WriteByte('{')
		for i, m := range v.members {
			if i > 0 {
				b.WriteByte(',')
			}
			pyWriteString(b, m.key)
			b.WriteByte(':')
			if err := m.val.encodeTo(b); err != nil {
				return err
			}
		}
		b.WriteByte('}')
	}
	return nil
}

func (v *pyValue) json() (string, error) {
	var b strings.Builder
	if err := v.encodeTo(&b); err != nil {
		return "", err
	}
	return b.String(), nil
}

// pyWriteString 的转义集与 Python json.dumps(ensure_ascii=False) 一致：
// 只有 " \ 与 <0x20 的控制字符（\b\f\n\r\t 用短写，其余 \u00XX）；0x7f 原样输出。
func pyWriteString(b *strings.Builder, s string) {
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		default:
			if r < 0x20 {
				fmt.Fprintf(b, `\u%04x`, r)
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
}

// pyFloatRepr 是 Python repr(float) 的口径：最短可回读数字，
// 指数 < -4 或 >= 16 才用科学计数，且指数至少两位、带符号。
func pyFloatRepr(f float64) string {
	if math.Signbit(f) {
		return "-" + pyFloatRepr(-f)
	}
	sci := strconv.FormatFloat(f, 'e', -1, 64)
	mantissa, expText, _ := strings.Cut(sci, "e")
	exp, err := strconv.Atoi(expText)
	if err != nil {
		return sci
	}
	digits := strings.ReplaceAll(mantissa, ".", "")
	if exp < -4 || exp >= 16 {
		out := digits[:1]
		if len(digits) > 1 {
			out += "." + digits[1:]
		}
		return fmt.Sprintf("%se%+03d", out, exp)
	}
	if exp >= 0 {
		if len(digits) <= exp+1 {
			return digits + strings.Repeat("0", exp+1-len(digits)) + ".0"
		}
		return digits[:exp+1] + "." + digits[exp+1:]
	}
	return "0." + strings.Repeat("0", -exp-1) + digits
}

// CPython `str` 的两个文本操作复刻：`str.strip()`（无参）与 `str.upper()`。
//
// 为什么不能用 Go 标准库的等价物顶替（两条都是**实测**，不是「看起来不一样」）：
//
//	str.strip()  认 29 个码位，`unicode.IsSpace` 认 25 个——**差的正是 001C-001F 四个**。
//	str.upper()  会做全案映射（ß⇒SS、ﬁ⇒FI…），`strings.ToUpper` 只做简单映射（一对一）。
//
// 两者在本域都**经 API 可达**，不是历史脏值才碰得到的分支：
//   - `backend/models/schemas.py:511-513` 的 `_normalize_persona` 先 `v.strip()` 再
//     `.upper()` 再 `re.fullmatch(r"[A-Z]{4}")`；实测 `persona_type:"\x1cINTJ"` 在
//     Python 侧是 **200 + "INTJ"**，而按 `strings.TrimSpace` 写会得到 422。
//   - 同一支校验里 `persona_type:"ßß"` 实测是 **200 + "SSSS"**（SS 是两个 ASCII 大写字母，
//     正好凑满 `[A-Z]{4}`），按 `strings.ToUpper` 写同样是 422。
//
// 与批 3 的那一处空白口径**不是同一件事**：`pyIntClamp`（domain/journey.go:213）剥的是
// CPython `int()` 认的空白，实测不含 001C-001F，domain/journey_test.go:62-63 反向钉着那条。
// 两支函数各自有闸，不许合并成一支「通用 trim」。
package domain

import "strings"

// pyStripSpace 是 CPython `Py_UNICODE_ISSPACE` 的码位集合（实测：3.12.11 全码位穷举 29 个）。
// 001C-001F 在 Go 的 unicode.IsSpace 里是 false，在 Python 的 strip 里是 true。
func pyStripSpace(r rune) bool {
	switch r {
	case '\t', '\n', '\v', '\f', '\r', 0x1C, 0x1D, 0x1E, 0x1F, ' ', 0x85, 0xA0,
		0x1680, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000:
		return true
	}
	return r >= 0x2000 && r <= 0x200A
}

// PyTrimSpace 是 `s.strip()`（无参＝只剥首尾）。
func PyTrimSpace(s string) string {
	runes := []rune(s)
	for len(runes) > 0 && pyStripSpace(runes[0]) {
		runes = runes[1:]
	}
	for len(runes) > 0 && pyStripSpace(runes[len(runes)-1]) {
		runes = runes[:len(runes)-1]
	}
	return string(runes)
}

// pyUpperExpand 是「大写结果**全为 ASCII 字母**」的多字符展开映射。
//
// 实测口径：CPython 3.12.11 全码位穷举共 102 个码位的大写结果长度 >1，其中结果为 ASCII 的
// 恰好这 8 个。其余 94 个（希腊字母含 iota 的组合、DŽ/LJ/NJ 之类）展开后必带非 ASCII 字符，
// 而本域唯一调用点要求 `re.fullmatch(r"[A-Z]{4}")`——含非 ASCII 就必然不匹配，
// 于是「Python 抛 ValueError、Go 也判不匹配」两侧同形，不需要把它们搬进表里。
// 「不搬」不等于「不实现」：新增调用点时这条界要重新量，别照抄这张表当 str.upper() 的全集。
var pyUpperExpand = map[rune]string{
	0xDF:   "SS", // ß
	0xFB00: "FF", // ﬀ
	0xFB01: "FI", // ﬁ
	0xFB02: "FL", // ﬂ
	0xFB03: "FFI",
	0xFB04: "FFL",
	0xFB05: "ST", // ﬅ
	0xFB06: "ST", // ﬆ
}

// PyUpper 是 `s.upper()`：先查展开表，其余按简单映射逐位大写。
//
// 单码位那一支与 `unicode.ToUpper` 同源，包括两个「非 ASCII 字符大写后落进 ASCII」的
// 常见情形（实测 ı⇒I、ſ⇒S）——它们与 ASCII 字母一起构成 `[A-Z]{4}` 的全部可达来源，
// 由批 4② 的 persona_upper_corpus 逐条钉住（整个 BMP 里 upper 会变样的 130 个码位，
// Python 现算；其中 34 个重复若干次后能拼出合法 persona，ß 这一支要重复**两次**，
// 因为它的大写本身就是两个字符——夹具按 k∈{1,2,3,4} 找见证，见 n8PersonaWitness）。
func PyUpper(s string) string {
	if !strings.ContainsFunc(s, func(r rune) bool {
		_, ok := pyUpperExpand[r]
		return ok
	}) {
		return strings.ToUpper(s)
	}
	var b strings.Builder
	b.Grow(len(s) + 4)
	for _, r := range s {
		if exp, ok := pyUpperExpand[r]; ok {
			b.WriteString(exp)
			continue
		}
		b.WriteString(strings.ToUpper(string(r)))
	}
	return b.String()
}

// 批 4 的两支 CPython 文本复刻件各自的闸。
//
// 分工与 n8_replay_test.go 不同：那边比响应字节，这里钉的是**码位集合**——
// 集合只在「两侧认的空白/展开不一致」的那几个码位上出问题，而响应字节夹具不可能
// 把每个码位都跑一遍。Python 侧的同一批集合由 backend/tests/test_n8_batch4_contract.py
// 现算并与此处字面量比对（跨语言闸），所以这里写死的表不是「我以为的 Python」。
package domain

import (
	"strings"
	"testing"
	"unicode"
)

// pyStripCodes 是 CPython `str.strip()` 认的全部 29 个码位（3.12.11 全码位穷举实测）。
// 001C-001F 那四个正是 Go 的 unicode.IsSpace 不认的差集，也是本函数存在的理由。
var pyStripCodes = []rune{
	0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1C, 0x1D, 0x1E, 0x1F, 0x20,
	0x85, 0xA0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006,
	0x2007, 0x2008, 0x2009, 0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
}

func TestPyTrimSpaceFullCodeSet(t *testing.T) {
	for _, r := range pyStripCodes {
		pad := string([]rune{r})
		s := pad + "AB" + pad
		if got := PyTrimSpace(s); got != "AB" {
			t.Errorf("U+%04X：PyTrimSpace(%q) = %q，期望 \"AB\"", r, s, got)
		}
	}
	// 反向：Python 不剥的两个相邻码位。001A/001B 紧挨着 001C，抄成区间就错。
	for _, r := range []rune{0x1A, 0x1B, 0x21, 0x2B} {
		s := string([]rune{r}) + "AB"
		if got := PyTrimSpace(s); got != s {
			t.Errorf("U+%04X 不该被剥：得到 %q", r, got)
		}
	}
}

// TestPyTrimSpaceDivergesFromTrimSpace 把「为什么不能用标准库」钉成可失败的两条断言：
// 差集恰好只有 001C-001F 四个，两侧在其余 25 个码位上同形。
func TestPyTrimSpaceDivergesFromTrimSpace(t *testing.T) {
	diff := 0
	for _, r := range pyStripCodes {
		if !unicode.IsSpace(r) {
			diff++
			if r < 0x1C || r > 0x1F {
				t.Errorf("U+%04X 落在预期差集之外，码位集合口径变了", r)
			}
		}
	}
	if diff != 4 {
		t.Fatalf("Python 认而 unicode.IsSpace 不认的个数 = %d，期望 4（001C-001F）", diff)
	}
	if strings.TrimSpace("\x1cINTJ") != "\x1cINTJ" {
		t.Fatal("strings.TrimSpace 竟然剥掉了 001C：本函数的存在理由没了")
	}
	if PyTrimSpace("\x1cINTJ") != "INTJ" {
		t.Errorf(`PyTrimSpace("\x1cINTJ") = %q，期望 "INTJ"（实测 Python persona_type 出 200）`,
			PyTrimSpace("\x1cINTJ"))
	}
}

func TestPyTrimSpaceEdgeShapes(t *testing.T) {
	for _, tc := range []struct{ in, want string }{
		{"", ""},
		{"   ", ""},
		{"\x1c\x1d\x1e\x1f", ""},
		{"  \x1c \x1d  ", ""},
		{"\x00AB\x00", "\x00AB\x00"},
		{"AB\x00", "AB\x00"},
		{"\u3000AB\u3000", "AB"},
		{"\ufeffAB", "\ufeffAB"},
		{"\nAB\r", "AB"},
		{"\t AB \n", "AB"},
	} {
		if got := PyTrimSpace(tc.in); got != tc.want {
			t.Errorf("PyTrimSpace(%q) = %q，期望 %q", tc.in, got, tc.want)
		}
	}
}

// TestPyUpperAsciiExpansions 钉住那 8 个「大写结果全为 ASCII」的多码位展开。
// 其余 94 个展开后必带非 ASCII ⇒ 在 persona 的 [A-Z]{4} 上两侧同形（判负），
// 所以它们不进表；本测试同时钉住这条界：**表里有非 ASCII 展开就说明界画歪了**。
func TestPyUpperAsciiExpansions(t *testing.T) {
	for _, tc := range []struct {
		in, want string
	}{
		{"ß", "SS"},
		{"ßß", "SSSS"},
		{"ﬀ", "FF"},
		{"ﬁ", "FI"},
		{"ﬂ", "FL"},
		{"ﬃ", "FFI"},
		{"ﬄ", "FFL"},
		{"ﬅ", "ST"},
		{"ﬆ", "ST"},
		{"aß", "ASS"},
		{"ßa", "SSA"},
	} {
		if got := PyUpper(tc.in); got != tc.want {
			t.Errorf("PyUpper(%q) = %q，期望 %q", tc.in, got, tc.want)
		}
	}
	for r := range pyUpperExpand {
		if !isAllASCII(pyUpperExpand[r]) {
			t.Errorf("U+%04X 的展开 %q 含非 ASCII，不该进表（persona 界变了）", r, pyUpperExpand[r])
		}
	}
	if len(pyUpperExpand) != 8 {
		t.Fatalf("展开表长度 = %d，期望 8（实测 ASCII-only 展开的码位数）", len(pyUpperExpand))
	}
}

// TestPyUpperSimplePath 覆盖不走展开表的那一支：含 ı/ſ 这两个「非 ASCII 却映射进 ASCII」
// 的单码位——它们与 26 个 ASCII 字母一起构成 [A-Z]{4} 的全部可达来源（62 个，
// 由 persona_upper_corpus 逐条钉）。
func TestPyUpperSimplePath(t *testing.T) {
	for _, tc := range []struct{ in, want string }{
		{"intj", "INTJ"},
		{"ıNTJ", "INTJ"},
		{"ſ", "S"},
		{"ﬁntj", "FINTJ"}, // 展开＋简单映射混排
		{"ǆ", "Ǆ"},        // 单码位映射两侧同形（Python 的 upper 这里不展开）
		{"ẖ", "ẖ"},        // Python 展开成 H+U+0331（带非 ASCII），Go 的简单映射原样返回：两支都进不了 [A-Z]{4}
		{"中文", "中文"},
		{"", ""},
	} {
		if got := PyUpper(tc.in); got != tc.want {
			t.Errorf("PyUpper(%q) = %q，期望 %q", tc.in, got, tc.want)
		}
	}
	// strings.ToUpper 对 ß 给 "ß"（简单映射），这就是两支的分界。
	if strings.ToUpper("ß") != "ß" {
		t.Fatal("strings.ToUpper 竟然展开了 ß：本函数的存在理由没了")
	}
}

func isAllASCII(s string) bool {
	for _, r := range s {
		if r > 0x7F {
			return false
		}
	}
	return true
}

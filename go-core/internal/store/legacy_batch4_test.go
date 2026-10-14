// 推荐码随机源的单元测试（不需要库）。
//
// 这一支的「形状」是跨侧契约：backend/core/referral.py:13-16 的字符集与长度同时被
// 深链落地页、注册 `?ref=` 校验和 PDF 二维码复用，`REFERRAL_CODE_RE` 判非法的那批
// 字符永远不该被生成出来。集成侧（真库查重）在 legacy_batch4_integration_test.go。
package store

import (
	"strings"
	"testing"
)

func TestNewReferralCodeShape(t *testing.T) {
	seen := map[byte]bool{}
	for i := 0; i < 2000; i++ {
		code, err := newReferralCode()
		if err != nil {
			t.Fatalf("newReferralCode: %v", err)
		}
		if len(code) != referralCodeLen {
			t.Fatalf("长度 = %d，期望 %d（%q）", len(code), referralCodeLen, code)
		}
		for j := 0; j < len(code); j++ {
			if !strings.ContainsRune(referralAlphabet, rune(code[j])) {
				t.Fatalf("%q 含字母表外的字符 %q", code, code[j])
			}
			seen[code[j]] = true
		}
	}
	// 排除的四个数字与三个字母（0/1/8/9 与 I/L/O）一旦漏进生成就破坏「手抄不混」。
	for _, bad := range []byte{'0', '1', '8', '9', 'I', 'L', 'O'} {
		if strings.ContainsRune(referralAlphabet, rune(bad)) {
			t.Errorf("字母表里出现了易混字符 %q", bad)
		}
	}
	// 拒绝采样的界：256 mod 29 = 2，接受的字节是 [0,232)。
	if got := 256 - 256%len(referralAlphabet); got != 232 {
		t.Errorf("拒绝采样上界 = %d，期望 232", got)
	}
	if len(seen) != len(referralAlphabet) {
		t.Errorf("2000 码 ×8 位应当覆盖全部 %d 个字符，实际 %d 个",
			len(referralAlphabet), len(seen))
	}
}

// TestNewReferralCodeRoughlyUniform 是「无偏」这条的实现侧兜底：抽 30000 码（24 万字符），
// 每个字符的频次应当落在 ±30% 内。
// 用 `byte % 29` 的话前 256 mod 29=2 个字符会高出 29/28≈3.6%——这个偏差远小于本闸容差，
// 所以这里**不**宣称测得出取模；真正的判据是 newReferralCode 里那句拒绝采样本身。
// 本测试兜的是「随机源坏了 / 字母表错位 / 长度写歪」这类硬故障。
func TestNewReferralCodeRoughlyUniform(t *testing.T) {
	const codes = 30000
	counts := map[byte]int{}
	for i := 0; i < codes; i++ {
		code, err := newReferralCode()
		if err != nil {
			t.Fatal(err)
		}
		for j := 0; j < len(code); j++ {
			counts[code[j]]++
		}
	}
	draws := codes * referralCodeLen
	want := draws / len(referralAlphabet)
	for _, c := range referralAlphabet {
		got := counts[byte(c)]
		if got < want*7/10 || got > want*13/10 {
			t.Errorf("字符 %q 出现 %d 次，期望约 %d 次（±30%%）", c, got, want)
		}
	}
}

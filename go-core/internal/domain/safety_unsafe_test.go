// 批 4 · D-G17 的 Go 侧闸：SafetyStatus 有了第四态 unsafe，且两侧值集对得上。
//
// 这一条为什么要动契约：三态时代 banned/overreach/model 三个「非危机的违规面」只能落
// crisis，Go 读到的措辞就是「用户处在危险中」——拦是对的，说成危机是错的（危机会牵出
// 04 §21 的安全流程让位）。所以本文件钉三件事：
//  1. unsafe **必拦**（与 crisis 同码 safety_blocked），但措辞不得复述危机文案；
//  2. crisis 那一格的行为一个字节没动（这条是本批判据的**正证**，不是回归）；
//  3. proto 的枚举名 ↔ Go 的常量集双向对账，**只读 .proto 源、不读 gen**
//     ——拿生成物数生成物等于自证（留痕见工作单 §四 D-G17）。
package domain

import (
	"os"
	"regexp"
	"strings"
	"testing"
)

// protoEnumMembers 从**契约源**抓 SafetyStatus 的成员名。
// 路径相对测试运行目录（internal/domain）指向 contracts/proto。
var protoEnumMembers = regexp.MustCompile(`^\s*(SAFETY_STATUS_[A-Z_]+)\s*=\s*\d+;`)

func protoSafetyStatuses(t *testing.T) map[string]bool {
	t.Helper()
	src, err := os.ReadFile("../../../contracts/proto/closenai_common.proto")
	if err != nil {
		t.Fatalf("读不到 .proto 契约源：%v（这条闸的两面都在源上，读不到就不该算过）", err)
	}
	text := string(src)
	start := strings.Index(text, "enum SafetyStatus {")
	if start < 0 {
		t.Fatal(".proto 里找不到 enum SafetyStatus ⇒ 枚举改名了，两侧对账得跟着改")
	}
	end := strings.Index(text[start:], "}")
	if end < 0 {
		t.Fatal("SafetyStatus 的枚举块没有收尾")
	}
	out := map[string]bool{}
	for _, line := range strings.Split(text[start:start+end], "\n") {
		if m := protoEnumMembers.FindStringSubmatch(line); m != nil {
			out[m[1]] = true
		}
	}
	if len(out) == 0 {
		t.Fatal("SafetyStatus 一个成员都没抓到：正则失效会让这条闸永远绿")
	}
	return out
}

// TestSafetyStatusValueSetMatchesProto 钉值集双向：Go 的四个常量＝proto 去掉 UNSPECIFIED，
// 且字符串值等于 proto 名去掉前缀后的小写（`SAFETY_STATUS_UNSAFE` → `"unsafe"`）——
// 后半句是入站解码 `mappedSafetyStatus` 与 Python 回投 JSON 实际比的形状。
func TestSafetyStatusValueSetMatchesProto(t *testing.T) {
	proto := protoSafetyStatuses(t)
	goSide := map[SafetyStatus]bool{
		SafetyOK: true, SafetyCrisis: true, SafetyUnsafe: true, SafetyUnavailable: true,
	}
	if len(proto) != len(goSide)+1 {
		t.Fatalf("proto 有 %d 个 SafetyStatus（含 UNSPECIFIED），Go 侧常量 %d 个 ⇒ 两侧不是同一份值集：%v",
			len(proto), len(goSide), proto)
	}
	for name := range proto {
		if name == "SAFETY_STATUS_UNSPECIFIED" {
			continue
		}
		want := SafetyStatus(strings.ToLower(strings.TrimPrefix(name, "SAFETY_STATUS_")))
		if !goSide[want] {
			t.Errorf("proto 有 %s ⇒ Go 侧该有常量 %q，但没有（新增枚举值必须两侧同批）", name, want)
		}
	}
	for status := range goSide {
		if !proto["SAFETY_STATUS_"+strings.ToUpper(string(status))] {
			t.Errorf("Go 侧有 %q 但 proto 没有对应枚举值 ⇒ 这个态永远收不到，是假格子", status)
		}
	}
}

// TestGateSafetyBlocksUnsafeWithoutCrisisWording 第 6 关：unsafe 必拦，且不说成危机。
func TestGateSafetyBlocksUnsafeWithoutCrisisWording(t *testing.T) {
	c := okInsight()
	// 与危机用例同confidence：置信关在 safety 之前，低了会落在 confidence 而不是 safety。
	c.Confidence = 0.90
	c.Safety = &Safety{Status: SafetyUnsafe, Categories: []string{"治疗"}}
	out := run(t, c, newReader())
	assertReject(t, out, ReasonSafetyBlocked)
	if !strings.Contains(out.Detail, "违规内容") {
		t.Errorf("detail 没点出这是违规内容：%q", out.Detail)
	}
	// 危机文案的那句原话（gate.go 的 crisis 分支）不得出现在这条上：
	// 「说成危机」正是本批要修的缺陷，不是风格问题。
	if strings.Contains(out.Detail, "危机信号") {
		t.Errorf("unsafe 被说成了危机信号：%q", out.Detail)
	}
}

// TestGateCrisisStillUsesCrisisWording 是本批判据的**正证**：真危机一个字没动。
func TestGateCrisisStillUsesCrisisWording(t *testing.T) {
	c := okInsight()
	c.Confidence = 0.90
	c.Safety = &Safety{Status: SafetyCrisis, Categories: []string{"self_harm"}}
	out := run(t, c, newReader())
	assertReject(t, out, ReasonSafetyBlocked)
	if !strings.Contains(out.Detail, "危机信号") {
		t.Errorf("危机措辞被改掉了（本批不该动它）：%q", out.Detail)
	}
}

// TestGateUnknownSafetyStatusStillFailClosed 第四态加进来以后，第五种未知值仍得拦：
// 加值时最容易顺手把 default 分支写成放行。
func TestGateUnknownSafetyStatusStillFailClosed(t *testing.T) {
	c := okInsight()
	c.Confidence = 0.90
	c.Safety = &Safety{Status: SafetyStatus("definitely_safe")}
	assertReject(t, run(t, c, newReader()), ReasonSafetyUnavailable)
}

// TestProactiveGateUnsafeBlocksWithoutSafetyHandoff 主动触达第 5 关：unsafe 拦，
// 但**不让位给安全流程**（那是危机才有的待遇）。
func TestProactiveGateUnsafeBlocksWithoutSafetyHandoff(t *testing.T) {
	c := proacCandidate()
	c.Safety = &Safety{Status: SafetyUnsafe, Categories: []string{"越界措辞"}}
	reason, detail, stop := ProactiveGate{}.checkProactiveSafety(&c)
	if reason != ReasonSafetyBlocked || !stop {
		t.Fatalf("unsafe 必须拦下主动触达：reason=%q stop=%v", reason, stop)
	}
	if !strings.Contains(detail, "违规内容") {
		t.Errorf("detail 没点出违规内容：%q", detail)
	}
	if strings.Contains(detail, "让位给安全流程") {
		t.Errorf("unsafe 不该走危机的安全流程让位：%q", detail)
	}

	// 对照：危机那条仍说「让位」，本批没动它。
	c.Safety = &Safety{Status: SafetyCrisis, Categories: []string{"self_harm"}}
	reason, detail, stop = ProactiveGate{}.checkProactiveSafety(&c)
	if reason != ReasonSafetyBlocked || !stop || !strings.Contains(detail, "让位给安全流程") {
		t.Fatalf("危机措辞被改掉了（本批不该动它）：reason=%q detail=%q stop=%v", reason, detail, stop)
	}
}

// TestStaleThreeStateWordingIsGone 口径闸（批 4 的第五种形状：钉住那句文字）：
// 「不是三态之一」是 unsafe 加进来之前的原话，留在源里就是句假话，
// 而假话比缺话更难发现——它会一直绿着骗人。
func TestStaleThreeStateWordingIsGone(t *testing.T) {
	for _, f := range []string{"gate.go", "proactive_gate.go", "candidate.go"} {
		src, err := os.ReadFile(f)
		if err != nil {
			t.Fatalf("读 %s 失败：%v", f, err)
		}
		if strings.Contains(string(src), "不是三态之一") {
			t.Errorf("%s 里还留着「不是三态之一」⇒ SafetyStatus 已是四态", f)
		}
	}
}

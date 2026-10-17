// 批 3 D-G14 的调用点闸＋零调用方台账。
//
// 为什么这条闸必须存在（工作单 §三 D-G14 的原话口径）：domain 那批方法「实现了但没人调」
// 是台账 R6 的活——给门找调用方。上一批就发生过一次误判：把「在册没人调」一律当「漏接」，
// 于是有人想去给 `RecheckProactive` 硬造一个调用方，而它今天根本没有合法消费者。
// 所以这里把两侧都钉住：
//   - **已接的两件**（CleanupDeliveryRecords／RequestAITask）必须在**生产代码**里有引用点，
//     删掉接线就红——inbound／dispatch 自己的用例看不见 main，也看不见 Run 循环里的这一跳；
//   - **仍为零的五件**必须继续为零，且名字还在登记表里；哪天有人接上了，
//     这条会红并要求把台账改掉，而不是让「零调用方」这句口径悄悄失真。
//
// 台账本身由反射枚举 `*domain.Service` 的导出方法生成判据：加一个没人调的导出方法
// 而不登记，同样打红。这才是「零调用方」这件事的长期形状，而不是一份会过期的名单。
package main

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// zeroCallerLedger 是「本批刻意不接、留给 R6」的那五件。
//
// 每一件为什么不接（带出处，免得下一个人靠猜）：
//   - `RecordOutcome`／`RecordUserFeedback`：反馈面在前端已经有 UI，但写入端点归 R6 的
//     「谁产生这条反馈」裁决；现在接上就是往 canonical 写一批没有消费方的行。
//   - `PropagateEvidenceInvalidation`：证据失效的传播需要删除语义，而删除口径（软删还是
//     supersede）在 18 §13 没有唯一答案。
//   - `ReevaluateInsight`／`RecheckProactive`：两者的触发面是「洞察/主动消息的定时重算」，
//     那是 cron 面（批 1 G1 只把 journey 那一条排上了），没有排程就接门＝空转。
var zeroCallerLedger = map[string]string{
	"PropagateEvidenceInvalidation": "R6（删除语义未裁决）",
	"RecheckProactive":              "R6（重算排程未上）",
	"RecordOutcome":                 "R6（反馈写入端点未裁决）",
	"RecordUserFeedback":            "R6（反馈写入端点未裁决）",
	"ReevaluateInsight":             "R6（重算排程未上）",
}

// wiredThisBatch 是本批 D-G14 点名的两条「无争议的」，工作单逐字：
// 「`CleanupDeliveryRecords` 挂到既有周期面」「`RequestAITask` 在候选入库后由 inbound 侧发起」。
var wiredThisBatch = map[string]string{
	"CleanupDeliveryRecords": "cmd/go-api/main.go 传给 dispatch.Deps.Cleanup",
	"RequestAITask":          "internal/messaging/inbound 的 requestFollowUp",
}

// TestDomainServiceCallerLedger 数的是**剥掉注释与字符串之后**的引用点。
//
// 为什么要剥：`topology.go` 的 Document() 里有一句散文写着「入队口 `Service.RequestAITask`」，
// 不剥字符串就会把一句文档读成调用点 ⇒ 台账上「零调用方」的五件里，任何一件将来只在注释里
// 被提到就会假绿地变成「有调用方」。同理，注释里提名字也不该算。
func TestDomainServiceCallerLedger(t *testing.T) {
	root := filepath.Join("..", "..")
	methods := exportedMethods(t)
	if len(methods) == 0 {
		t.Fatal("反射枚举到 0 个导出方法 ⇒ 判据是空的，这条闸等于没跑")
	}
	refs := countProductionRefs(t, root, methods)

	for _, name := range methods {
		_, wired := wiredThisBatch[name]
		_, ledger := zeroCallerLedger[name]
		n := refs[name]
		switch {
		case wired && n == 0:
			t.Errorf("%s：本批接的门在生产代码里已经找不到引用点了（命中 %d）⇒ "+
				"接线被删了，而这个包自己的用例不会红，只有这条会", name, n)
		case !wired && !ledger && n == 0:
			t.Errorf("%s：零调用方却没登记在台账里 ⇒ 要么接上它、要么写进 zeroCallerLedger 并交代归哪个桶", name)
		case ledger && n > 0:
			t.Errorf("%s：台账说零调用方，实测在生产代码里有 %d 处引用 ⇒ 台账过期了，去改工作单/任务台账再回来", name, n)
		}
	}
	// 名单本身也要对得上：两侧并集必须正好覆盖全部导出方法，且互不重叠。
	for name := range wiredThisBatch {
		if !contains(methods, name) {
			t.Errorf("wiredThisBatch 里有 %s，但 *domain.Service 上已经没有这个方法 ⇒ 改名或删掉了，名单要跟着收", name)
		}
	}
	for name := range zeroCallerLedger {
		if !contains(methods, name) {
			t.Errorf("台账登记的 %s 在 *domain.Service 上已经不存在 ⇒ 这条要么已接线（去 wiredThisBatch），要么整个方法被删了", name)
		}
		if _, dup := wiredThisBatch[name]; dup {
			t.Errorf("%s 同时出现在「已接」和「仍为零」两张表里", name)
		}
	}
}

// TestMainWiresTheRetentionCleanup 是 D-G14-① 的装配面闸：只看 main.go 的源码形状。
//
// 三格各挡一种「看起来接了其实没接」：
//   - `Cleanup: candidateDomain.CleanupDeliveryRecords` ⇒ 真把唯一清理入口传给了 worker；
//   - `domain.NewService(canonical` 出现在 `dispatch.New(` **之前** ⇒ 传的是 canonical 的那个
//     Service（不是 auth 那个同名 `service`）；
//   - 不许出现 `CleanupEvery: env`／新的 `CLOSEN_*CLEANUP*` 键 ⇒ 节拍是包内默认，
//     不重演 D-G12「键存在但没人读」的假旋钮。
func TestMainWiresTheRetentionCleanup(t *testing.T) {
	src := readFile(t, filepath.Join(".", "main.go"))
	if !strings.Contains(src, "Cleanup: candidateDomain.CleanupDeliveryRecords") {
		t.Error("main.go 没把 CleanupDeliveryRecords 传给 dispatch.Deps.Cleanup ⇒ 清理又回到零调用方")
	}
	built := strings.Index(src, "domain.NewService(canonical")
	used := strings.Index(src, "dispatch.New(")
	if built < 0 || used < 0 {
		t.Fatalf("main.go 里找不到 domain.NewService(canonical 或 dispatch.New(（判据的锚没了）")
	}
	if built > used {
		t.Errorf("domain.NewService(canonical 在 dispatch.New 之后 ⇒ 出箱 worker 拿不到那个 Service")
	}
	lower := strings.ToLower(src)
	for _, banned := range []string{"closen_cleanup", "cleanupevery: env", "cleanupenv"} {
		if strings.Contains(lower, banned) {
			t.Errorf("main.go 出现了 %q ⇒ 清理节拍被做成了 env 键；D-G12 刚登记过「没人验的旋钮比没有旋钮更坏」", banned)
		}
	}
	// 入站那一侧不能被这次改动弄断：Domain 仍然是同一个 candidateDomain。
	if !strings.Contains(src, "Domain: candidateDomain,") {
		t.Error("inbound.Deps.Domain 不再是 candidateDomain ⇒ D-G14-② 的领域入口换了来源")
	}
}

// TestDispatchLoopActuallyCallsCleanup 盯 dispatch.go 自己：钩子字段有了但循环里不调用，
// 是这一格最省事的假接线形态（New 校验得过、Stats 全是零、没人会发现）。
func TestDispatchLoopActuallyCallsCleanup(t *testing.T) {
	src := readFile(t, filepath.Join("..", "..", "internal", "messaging", "dispatch", "dispatch.go"))
	code := goCodeOnly(src)
	if !strings.Contains(code, "r.maybeCleanup(ctx)") {
		t.Error("dispatch.Run 没在循环里调 maybeCleanup ⇒ Deps.Cleanup 是个没人调的字段")
	}
	if !strings.Contains(code, "r.deps.Cleanup(ctx)") {
		t.Error("maybeCleanup 没真的调用注入的钩子")
	}
	// 节拍不许来自 env：这一条把「包内默认」的决定写成断言。
	if strings.Contains(code, "EnvCleanup") || strings.Contains(src, "CLOSEN_CLEANUP") {
		t.Error("dispatch 为清理发明了 env 键 ⇒ 与 D-G12 的改判口径冲突")
	}
}

// ── 扫描辅助 ────────────────────────────────────────────────────────────────

func exportedMethods(t *testing.T) []string {
	t.Helper()
	typ := reflect.TypeOf((*domain.Service)(nil))
	out := make([]string, 0, typ.NumMethod())
	for i := 0; i < typ.NumMethod(); i++ {
		out = append(out, typ.Method(i).Name)
	}
	return out
}

// countProductionRefs 在**生产代码面**数每个方法的引用点：
// 排除 `*_test.go`（测试调用不是现网调用）、`gen/`（生成物里全是同名符号）、
// `tools/`（取证驱动器，工作单逐字「不算现网」）与 `internal/domain` 自己（自引用不算调用方）。
func countProductionRefs(t *testing.T, root string, methods []string) map[string]int {
	t.Helper()
	out := make(map[string]int, len(methods))
	for _, m := range methods {
		out[m] = 0
	}
	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			switch d.Name() {
			case "gen", "testdata", ".git", "node_modules", "domain", "tools":
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		code := goCodeOnly(string(readFile(t, path)))
		for _, m := range methods {
			// `.M` 后不接标识符字符：否则 `Admit` 会把 `AdmitEvidence` 也算进去。
			idx := 0
			for {
				i := strings.Index(code[idx:], "."+m)
				if i < 0 {
					break
				}
				at := idx + i
				end := at + len(m) + 1
				if end >= len(code) || !isIdentByte(code[end]) {
					out[m]++
				}
				idx = end
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("扫 %s 失败：%v", root, err)
	}
	return out
}

func isIdentByte(b byte) bool {
	return b == '_' || (b >= '0' && b <= '9') || (b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z')
}

// goCodeOnly 把注释与字符串字面量整段换成空格（保留换行，行号不至于全乱）。
//
// 为什么不直接用 `go/ast`：本包没有现成的解析脚手架，而这条判据要的只是
// 「这个名字在代码里出现过，不在散文里出现过」——一台四状态小机就够，
// 且它不需要 import 分析，因此不会被一个语法错误连坐整条闸。
// 三处都不能少：块注释、行注释（但 `://` 里的双斜杠在字符串内，已由字符串态先兜住）、
// 双引号串（带反斜杠转义）与反引号原始串。
func goCodeOnly(src string) string {
	var b strings.Builder
	b.Grow(len(src))
	type state int
	const (
		code state = iota
		lineComment
		blockComment
		dquote
		backtick
	)
	st := code
	for i := 0; i < len(src); {
		c := src[i]
		switch st {
		case code:
			if strings.HasPrefix(src[i:], "//") {
				st = lineComment
				b.WriteString("  ")
				i += 2
				continue
			}
			if strings.HasPrefix(src[i:], "/*") {
				st = blockComment
				b.WriteString("  ")
				i += 2
				continue
			}
			if c == '"' {
				st = dquote
				b.WriteByte(' ')
				i++
				continue
			}
			if c == '`' {
				st = backtick
				b.WriteByte(' ')
				i++
				continue
			}
			b.WriteByte(c)
			i++
		case lineComment:
			if c == '\n' {
				st = code
				b.WriteByte('\n')
			} else {
				b.WriteByte(' ')
			}
			i++
		case blockComment:
			if strings.HasPrefix(src[i:], "*/") {
				st = code
				b.WriteString("  ")
				i += 2
				continue
			}
			if c == '\n' {
				b.WriteByte('\n')
			} else {
				b.WriteByte(' ')
			}
			i++
		case dquote:
			if c == '\\' {
				b.WriteString("  ")
				i += 2
				continue
			}
			if c == '"' {
				st = code
			}
			if c == '\n' {
				b.WriteByte('\n') // 不该发生（Go 的双引号串不跨行），留着只是为了不把解析器写死
			} else {
				b.WriteByte(' ')
			}
			i++
		case backtick:
			if c == '`' {
				st = code
			}
			if c == '\n' {
				b.WriteByte('\n')
			} else {
				b.WriteByte(' ')
			}
			i++
		}
	}
	return b.String()
}

func readFile(t *testing.T, path string) string {
	t.Helper()
	src, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("读 %s 失败：%v ⇒ 这一条没有真的比过", path, err)
	}
	return string(src)
}

func contains(hay []string, needle string) bool {
	for _, h := range hay {
		if h == needle {
			return true
		}
	}
	return false
}

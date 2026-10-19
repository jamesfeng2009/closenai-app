// T6-01 处置选项 A 的调用方闸：`internal/aiclient` 登记为「内部能力，未启用」，
// 这句话今天能不能继续写在台账里，由这条测判——不是由人记得住。
//
// 为什么判据要做在 Go 侧而不是只写进 docs/feature-status.json：台账是**人填的状态**，
// 「谁是生产调用方」是**代码里的事实**，两者必须各自由一侧盯（先例：T0-05 的
// `feature_status.py --check` 与 `contracts/endpoints.md` 两侧各拦一个方向）。
// 只有一侧的话：改代码的人不知道要改台账，改台账的人以为自己在改事实。
//
// 分层口径与 cmd/go-api/domain_caller_ledger_test.go 一致：数的是**真实 import**
// （go/parser 读的是 AST，注释与字符串里的包名不会被读成调用点），
// 而「登记行消失」与「调用方冒出来」两条反向判据都配了正向证人——扫到空集不算绿。
package aiclient

import (
	"encoding/json"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

// importPathSuffix 是本包在所有调用方里长得一样的那一段。用后缀判而不是全等：
// module 路径改一次就让这条闸扫空集＝一条永久绿的假闸。
const importPathSuffix = "/go-core/internal/aiclient"

// ledgerEntry 是 docs/feature-status.json 的 internal_capabilities 里本包那一条。
type ledgerEntry struct {
	ID           string   `json:"id"`
	State        string   `json:"state"`
	ProbeTools   []string `json:"probe_tools"`
	ReviewBy     string   `json:"review_by"`
	ReviewAction string   `json:"review_action"`
}

type ledgerDoc struct {
	InternalCapabilities []ledgerEntry `json:"internal_capabilities"`
}

// callerKind 决定一个引用本包的 .go 文件算不算「生产调用方」。
//
// 三类豁免各有理由，且都不许扩大：
//   - 包内文件（含 aiclient_test.go 这些同包测试）：被测对象自己引用自己不叫调用方；
//   - `_test.go`：测试引用实现是常态，把它算成生产调用方等于「写个测试就能宣称上线」；
//   - `tools/`：值班与联调工具，`go run` 只在有人手跑时才存在，不是常驻进程的一部分
//     （Baseline §10.4 问的是「生产」调用方）。
func callerKind(rel string) string {
	switch {
	case strings.HasPrefix(rel, "internal/aiclient/"):
		return "self"
	case strings.HasSuffix(rel, "_test.go"):
		return "test"
	case strings.HasPrefix(rel, "tools/"):
		return "probe"
	default:
		return "production"
	}
}

// goCoreRoot / repoRoot：测试的工作目录是本包目录，两级回到 module 根、三级回到仓库根。
// 台账住在仓库根的 docs/ 下（与 Python 侧 scripts/feature_status.py 同一个路径口径），
// 两个根混用的代价是「找不到 JSON ⇒ 整条对账跳过」，所以各写一个函数而不是就地拼。
func goCoreRoot() string { return filepath.Join("..", "..") }
func repoRoot() string   { return filepath.Join("..", "..", "..") }

// goCoreRel 把 walk 根（go-core/）下的相对路径换成**仓库根**口径的写法，
// 台账与 docs/ 里的路径都按仓库根写；两套口径对不上时会得到「未登记」的假红。
func goCoreRel(rel string) string {
	return "go-core/" + filepath.ToSlash(rel)
}

// importersOfAiclient 返回「引用本包的 .go 文件 → 分类」与**扫描过的 .go 文件总数**。
// 那个计数是这条闸的正向证人：walk 根给错、SkipDir 名单漂了，命中集会静默变成空集，
// 而「零生产调用方」在空集上永远成立——本仓铁律：归零型闸要配数得出来的证人。
func importersOfAiclient(t *testing.T, root string) (map[string]string, int) {
	t.Helper()
	out := map[string]string{}
	scanned := 0
	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			switch d.Name() {
			case ".git", ".venv", "node_modules", "__pycache__", "dist", "build", ".next":
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") {
			return nil
		}
		scanned++
		rel, rerr := filepath.Rel(root, path)
		if rerr != nil {
			t.Fatalf("相对路径算不出来（walk 根给错了？）：%v", rerr)
		}
		rel = goCoreRel(rel)
		fset := token.NewFileSet()
		file, perr := parser.ParseFile(fset, path, nil, parser.ImportsOnly)
		if perr != nil {
			// 解析不了的 .go 文件不能默默跳过：跳过＝那个文件里的调用方永远看不见。
			t.Errorf("go/parser 读不动 %s：%v ⇒ 这条闸的调用方集合不完整，不能算绿", rel, perr)
			return nil
		}
		for _, imp := range file.Imports {
			if strings.HasSuffix(strings.Trim(imp.Path.Value, `"`), importPathSuffix) {
				out[rel] = callerKind(strings.TrimPrefix(rel, "go-core/"))
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walk %s：%v", root, err)
	}
	return out, scanned
}

func TestAiclientHasNoProductionCaller(t *testing.T) {
	importers, scanned := importersOfAiclient(t, goCoreRoot())

	var production, probe int
	for _, kind := range importers {
		switch kind {
		case "production":
			production++
		case "probe":
			probe++
		}
	}
	// 证人一：扫描面不是空的（go-core 里有上百个 .go 文件，扫到个位数＝根或 SkipDir 漂了）。
	if scanned < 50 {
		t.Fatalf("只扫到 %d 个 .go 文件 ⇒ walk 根不对，这条闸的「零调用方」是扫空集扫出来的", scanned)
	}
	// 证人二：确实有非生产引用者。没有它，「零生产调用方」与「整个包没人用」分不清，
	// 而台账 impl 里点名的那条值班工具也就没人核了。
	if probe == 0 {
		t.Errorf("扫到 %d 个 .go 文件却没有一个 tools/ 引用者 ⇒ 分类函数或台账口径漂了", scanned)
	}
	if production != 0 {
		var names []string
		for rel, kind := range importers {
			if kind == "production" {
				names = append(names, rel)
			}
		}
		sort.Strings(names)
		t.Errorf("internal/aiclient 出现了生产调用方（%d 处：%s）⇒ 台账那条 IMPLEMENTED 已过期："+
			"要么按 T6-01 选项 B 补降级测试与失败指标后升态，要么把这条闸与台账一起改掉，"+
			"不许留着「未启用」这句话继续绿", production, strings.Join(names, ", "))
	}
}

func TestAiclientLedgerEntryMatchesReality(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(repoRoot(), "docs", "feature-status.json"))
	if err != nil {
		t.Fatalf("读不到 docs/feature-status.json：%v", err)
	}
	var doc ledgerDoc
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("feature-status.json 不是合法 JSON：%v", err)
	}
	var entry *ledgerEntry
	for i := range doc.InternalCapabilities {
		if doc.InternalCapabilities[i].ID == "ai.grpc.client" {
			entry = &doc.InternalCapabilities[i]
		}
	}
	if entry == nil {
		t.Fatal("台账里没有 internal_capabilities/ai.grpc.client 这一条 ⇒ T6-01 的处置登记消失了。" +
			"要么这条接口已删除（那本包与这条测都要一起删），要么登记被误删")
	}
	if entry.State != "IMPLEMENTED" {
		t.Errorf("台账把 ai.grpc.client 标成 %q ⇒ 内部能力封顶 IMPLEMENTED（Baseline §15 末句／§10.4）",
			entry.State)
	}
	if !strings.Contains(entry.ReviewAction, "删除") {
		t.Errorf("review_action 没写「删除」：%q ⇒ 90 天到期动作被改成了空话", entry.ReviewAction)
	}

	// 台账点名的 probe_tools 与实测的非生产外部引用者必须**逐字同一批文件**：
	// 两侧不同源（一侧是 JSON 里的字符串，一侧是 go/parser 现读的 AST），所以这条不是恒等式。
	importers, _ := importersOfAiclient(t, goCoreRoot())
	ruleProbe := map[string]bool{}
	for rel, kind := range importers {
		if kind == "probe" {
			ruleProbe[rel] = true
		}
	}
	if len(ruleProbe) == 0 {
		t.Fatal("实测没有 tools/ 里的引用者 ⇒ 这条对账在扫空集")
	}
	for _, named := range entry.ProbeTools {
		if !ruleProbe[named] {
			t.Errorf("台账把 %s 登记成非生产调用方，但它今天并没有 import internal/aiclient ⇒ 改名或删了", named)
		}
		delete(ruleProbe, named)
	}
	if len(ruleProbe) > 0 {
		var left []string
		for rel := range ruleProbe {
			left = append(left, rel)
		}
		sort.Strings(left)
		t.Errorf("实测有 tools/ 引用者未登记进台账：%s ⇒ 「谁是调用方」这本账不全", strings.Join(left, ", "))
	}
}

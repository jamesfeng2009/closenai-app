// T6-02 的闸：切流矩阵的 path × method 约束（决策点 D-10／D-5）。
//
// 这六条测各判一件事，且都只认自己那条违例码（`matrix.go` 的 V* 常量）——
// 「Violations 非空」这种笼统断言谁都能撞红，打不出「这条变异咬的是这条判据」的结论。
//
// 为什么住在 internal/cutover 而不是 backend/tests：
// Python 侧那套 test_k8s_manifests.py 已经在做「清单 → 归属」的模拟，但它读的是
// server.go 的**文本**（`"GET /api/v1/mood/history"` 这种字符串出现没有）。
// 文本在、声明表里没了（或反过来），它会照样绿；这里读的是注册用的那张表，
// 而表与 mux 由同一段代码消费（httpapi/routes_test.go 钉它），所以三张真源第一次真正同源。
//
// 反向判据全部配了正向证人（[[feedback-gate-needs-positive-witness]]）：
// 扫空集会让人以为「path×method 全对齐」，所以每条测都先数一遍三张源的规模。
package cutover_test

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/cutover"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/httpapi"
)

const (
	ingressFile  = "deploy/k8s/50-ingress.yaml"
	caddyFile    = "deploy/compose/Caddyfile"
	contractFile = "contracts/endpoints.md"
	configFile   = "go-core/internal/config/config.go"
	mainFile     = "go-core/cmd/go-api/main.go"
	matrixFile   = "contracts/cutover-matrix.md"
)

func repoRoot() string { return filepath.Join("..", "..", "..") }

func mustRead(t *testing.T, rel string) string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(repoRoot(), rel))
	if err != nil {
		t.Fatalf("读不到 %s：%v ⇒ 三张真源少了一张，这条闸判不了任何东西", rel, err)
	}
	return string(raw)
}

type inputs struct {
	rows       []cutover.DomainRow
	rules      []cutover.ProxyRule
	plan       []cutover.GoRoute
	ops        []cutover.ContractOp
	configSrc  string
	assembly   map[string]bool
	violations []string
}

func load(t *testing.T) inputs {
	t.Helper()
	configSrc := mustRead(t, configFile)
	keys := cutover.ParseConfigKeys(configSrc)
	rules := append(
		cutover.ParseIngressRules(mustRead(t, ingressFile), ingressFile),
		cutover.ParseCaddyRules(mustRead(t, caddyFile), caddyFile)...)
	ops := cutover.ParseContractOps(mustRead(t, contractFile))
	plan := make([]cutover.GoRoute, 0, 32)
	for _, r := range httpapi.RoutePlan() {
		plan = append(plan, cutover.GoRoute{Method: r.Method, Path: r.Path, Domain: r.Domain})
	}
	rows := cutover.BuildRows(keys, rules, ops, plan)
	assembly := cutover.ParseAssemblyKeys(mustRead(t, mainFile))
	return inputs{
		rows: rows, rules: rules, plan: plan, ops: ops, configSrc: configSrc,
		assembly:   assembly,
		violations: cutover.Violations(rows, rules, plan, configSrc, assembly),
	}
}

// filterByCode 只留下那条判据自己的违例。
func filterByCode(v []string, code string) []string {
	var out []string
	for _, x := range v {
		if strings.HasPrefix(x, code+"：") {
			out = append(out, x)
		}
	}
	return out
}

// 证人：三张源都得是「有内容的集合」，否则下面每一条归零判据都会在空集上永远成立。
func TestMatrixInputsAreNonEmpty(t *testing.T) {
	in := load(t)
	if len(in.rows) != 8 {
		t.Errorf("矩阵应有 8 行（config.go 的 8 个 CLOSEN_CUTOVER_* 键一一对应），实得 %d ⇒ 键集解析漂了",
			len(in.rows))
	}
	if len(in.ops) < 90 {
		t.Errorf("从 contracts/endpoints.md 只解析出 %d 个契约操作（实测 100 上下）⇒ 登记表行形状变了，"+
			"这条闸的「path 上有几种方法」就读错了", len(in.ops))
	}
	if len(in.plan) < 19 {
		t.Errorf("httpapi.RoutePlan() 只有 %d 条 ⇒ 声明表被掏空或读错", len(in.plan))
	}
	domainRoutes := 0
	for _, r := range in.plan {
		if r.Domain != "" {
			domainRoutes++
		}
	}
	if domainRoutes < 13 {
		t.Errorf("声明表里带切流域键的路由只有 %d 条（实测 13）⇒ 域列丢了，矩阵会整列误判", domainRoutes)
	}
	bound := 0
	for _, r := range in.rules {
		if r.Backend == "go-api" {
			bound++
		}
	}
	if bound == 0 {
		t.Error("反代清单里一条指向 go-api 的 path 都没有 ⇒ N8 的切流机制在清单上不存在，本闸无从对账")
	}
	if len(in.assembly) != 8 {
		t.Errorf("cmd/go-api/main.go 的 cutover.New 里解析出 %d 个域键（应为 8）⇒ 装配表形状变了",
			len(in.assembly))
	}
}

// 判据①（D-10 本体）：任何被判给 go-api 的契约操作，Go 必须注册它。
func TestNoBoundContractOperationLacksGoRoute(t *testing.T) {
	in := load(t)
	if got := filterByCode(in.violations, cutover.VMissing); len(got) != 0 {
		t.Errorf("已上刀的 path 上有 Go 答不了的方法（%d 条：%s）⇒ path 匹配不看方法，"+
			"这些请求会打到没注册它们的 mux 上（405/404），与灰度百分比无关",
			len(got), strings.Join(got, "；"))
	}
}

// 判据②（D-5）：Prefix 一刀只许用在「方法齐全的域」上。
func TestPrefixRulesOnlyOnMethodCompleteDomains(t *testing.T) {
	in := load(t)
	got := filterByCode(in.violations, cutover.VPrefix)
	if len(got) != 0 {
		t.Errorf("有域用 Prefix 上刀但域内还有方法不全的 path（%s）⇒ 域里新增的端点会被自动搬进"+
			"go-api；这条域只许按端点列 Exact", strings.Join(got, "；"))
	}
	// mood 是「域级键 × 端点级 path」的样本：它今天就带一个没切的 POST /mood/checkin。
	var mood *cutover.DomainRow
	for i := range in.rows {
		if in.rows[i].Domain == "mood" {
			mood = &in.rows[i]
		}
	}
	if mood == nil {
		t.Fatal("矩阵里没有 mood 这一行 ⇒ D-5 那个样本消失了，这条闸判不到 Prefix/Exact 混用的坑")
	}
	if len(mood.Incomplete) == 0 {
		t.Error("mood 域现在方法齐全了？⇒ 若是 POST /mood/checkin 也接进 Go，请连同本条与 Ingress 注释一起改判")
	}
	for _, r := range mood.BoundRules {
		if r.PathType != "Exact" {
			t.Errorf("mood 的 %s 是 %s ⇒ 该域缺 POST /mood/checkin，只许 Exact 按端点列（D-5）",
				r.Path, r.PathType)
		}
	}
}

// 判据③：不可切的两个域（reflections／memories）今天必须**一条刀都没有**。
func TestMethodIncompleteDomainsAreNotBound(t *testing.T) {
	in := load(t)
	seen := 0
	for _, row := range in.rows {
		if row.Domain != "reflections" && row.Domain != "memories" {
			continue
		}
		seen++
		if len(row.BoundRules) != 0 {
			t.Errorf("%s 已上刀（%s）但这个域方法不全（缺：%s）⇒ D-10 那条「补齐写方法前不得切流」被绕过",
				row.EnvKey, joinRulePaths(row.BoundRules), strings.Join(row.Incomplete, "、"))
		}
		if row.Cuttable {
			t.Errorf("%s 被矩阵标成可切 ⇒ 它的写侧方法还在 py-api 那边，标可切等于教下一个人去上刀",
				row.EnvKey)
		}
		if !strings.Contains(row.Reason, "D-10") {
			t.Errorf("%s 的原因里没写 D-10：%q ⇒ 裁决出处断了，后来人看不出这条不是漏接线",
				row.EnvKey, row.Reason)
		}
	}
	if seen != 2 {
		t.Errorf("矩阵里 reflections／memories 两行只数到 %d 行 ⇒ 键集或归域函数漂了", seen)
	}
}

func joinRulePaths(rs []cutover.ProxyRule) string {
	parts := make([]string, 0, len(rs))
	for _, r := range rs {
		parts = append(parts, r.PathType+" "+r.Path)
	}
	return strings.Join(parts, "、")
}

// 判据④：每个放量键都要有实现格子（假旋钮），且键名要真的住在 config.go 里。
func TestEveryCutoverKeyHasImplementationAndAssembly(t *testing.T) {
	in := load(t)
	if got := filterByCode(in.violations, cutover.VPhantom); len(got) != 0 {
		t.Errorf("有放量键在 Go 声明表里没有任何路由：%s ⇒ 键能拨、面不存在", strings.Join(got, "；"))
	}
	if got := filterByCode(in.violations, cutover.VAssembly); len(got) != 0 {
		t.Errorf("装配面与键面不对账：%s ⇒ 缺格的语义是「当成 满额自答」，上刀那天键调回 0 也回滚不了",
			strings.Join(got, "；"))
	}
	if got := filterByCode(in.violations, cutover.VKeyDrift); len(got) != 0 {
		t.Errorf("矩阵的键列在 config.go 里找不到：%s", strings.Join(got, "；"))
	}
}

// 判据⑤：go-api 不对公网暴露（第二处反代出口不许抢在 Ingress 之前把 path 搬走）。
func TestPublicProxyNeverBindsGoApi(t *testing.T) {
	in := load(t)
	if got := filterByCode(in.violations, cutover.VPublic); len(got) != 0 {
		t.Errorf("%s ⇒ go-api 只应在集群内可达（方案 §12／T0-01）", strings.Join(got, "；"))
	}
	// 证人：Caddyfile 确实被解析到了（解析不出任何块＝这条测在扫空集）。
	caddyRules := 0
	for _, r := range in.rules {
		if strings.Contains(r.Source, "Caddy") {
			caddyRules++
		}
	}
	if caddyRules == 0 {
		t.Error("Caddyfile 一条 handle/reverse_proxy 都没解析出来 ⇒ 这条闸在扫空集，形状变了没人红")
	}
}

// 判据⑥：`contracts/cutover-matrix.md` 是生成物，必须与现账逐字节同源。
func TestCutoverMatrixMarkdownIsUpToDate(t *testing.T) {
	in := load(t)
	if got := filterByCode(in.violations, cutover.VEmpty); len(got) != 0 {
		t.Fatalf("%s", strings.Join(got, "；"))
	}
	want := cutover.Render(in.rows, in.violations, ingressFile, caddyFile, contractFile)
	got := mustRead(t, matrixFile)
	if got != want {
		t.Errorf("contracts/cutover-matrix.md 与现账不同源 ⇒ 重跑 `cd go-core && go run ./tools/cutovermatrix -write`"+
			"并把生成物一起提（前 200 字差异：%q）", firstDiff(got, want))
	}
	// 裁决必须写在文档里，而不是只写在判据里：人会读 md，不会读 Violations()。
	for _, needle := range []string{"不得", "D-10", "CLOSEN_CUTOVER_MEMORIES", "CLOSEN_CUTOVER_REFLECTIONS"} {
		if !strings.Contains(want, needle) {
			t.Errorf("生成的矩阵里没有 %q ⇒「补齐写方法前不得切流」这句话没落到文档上", needle)
		}
	}
}

func firstDiff(a, b string) string {
	for i := 0; i < len(a) && i < len(b); i++ {
		if a[i] != b[i] {
			lo := i - 80
			if lo < 0 {
				lo = 0
			}
			return a[lo:min(i+80, len(a))] + " ⇄ " + b[lo:min(i+80, len(b))]
		}
	}
	if len(a) != len(b) {
		return "长度不同：" + itoa(len(a)) + " vs " + itoa(len(b))
	}
	return ""
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var digits []byte
	for n > 0 {
		digits = append([]byte{byte('0' + n%10)}, digits...)
		n /= 10
	}
	return string(digits)
}

// 判据⑦（形状自证）：归域与匹配这两个函数本身也得有人核——
// 它们错了整张矩阵会「全绿但全错」，上面六条都建立在它们之上。
func TestDomainAndMatchHelpersBehave(t *testing.T) {
	if got := cutover.DomainOfPath("/api/v1/memories/{memory_id}"); got != "memories" {
		t.Errorf("归域把 /api/v1/memories/{memory_id} 归成了 %q", got)
	}
	if got := cutover.DomainOfPath("/health"); got != "" {
		t.Errorf("/health 不该归到任何切流域，实得 %q", got)
	}
	exact := cutover.ProxyRule{Path: "/api/v1/reflections", PathType: "Exact", Backend: "go-api"}
	// 这条就是 D-10 的最小样本：Exact 也只按 path 匹配，**方法不参与判断**。
	if !exact.Match("POST", "/api/v1/reflections") {
		t.Error("Exact 规则不抓 POST /api/v1/reflections ⇒ 与 Ingress 语义不符，矩阵会漏判 405")
	}
	if exact.Match("GET", "/api/v1/reflections/generate") {
		t.Error("Exact 抓到了子路径 ⇒ 它其实是 Prefix，矩阵会把没上刀的域算成已上刀")
	}
	prefix := cutover.ProxyRule{Path: "/api/v1/mood", PathType: "Prefix", Backend: "go-api"}
	if !prefix.Match("POST", "/api/v1/mood/checkin") {
		t.Error("Prefix /api/v1/mood 不抓 /api/v1/mood/checkin ⇒ 最长前缀语义错了")
	}
	if prefix.Match("GET", "/api/v1/moodx") {
		t.Error("Prefix 按字符而不是按段匹配 ⇒ 会把邻居域搬进 go-api")
	}
	if cutover.NormalizePath("/api/v1/curriculum/{event_id}/read") != "/api/v1/curriculum/{}/read" {
		t.Error("{param} 没归一 ⇒ 路径参数改名会打红切流闸（假红）")
	}
	// 空串方法（不限方法的兜底）不参与对账：它不该被算成「这个 path 的某个方法」。
	if regexp.MustCompile(`\{\w+\}`).MatchString(cutover.NormalizePath("/api/v1/memories")) {
		t.Error("归一函数在无参 path 上动了手脚")
	}
}

// 判据⑧（解析器的规则边界）：一条 path 规则的 pathType/backend 只许在它自己那八行里取。
//
// 为什么单独一条测：2026-10-19 的 M3 变异（把 mood 的 `Exact /api/v1/mood/history` 改成
// `Prefix /api/v1/mood`）在修 parser 之前**打不红**这条闸——八行前视窗口读进了下一条规则的
// `pathType: Exact`，把坏料又"改回"了 Exact。这类 bug 的形状是「判据在真坏料上绿」，
// 只有拿一段合成清单钉住边界，才不会靠"今天两条恰好都是 Exact"蒙对。
func TestIngressRuleBoundaryIsPerRule(t *testing.T) {
	src := "" +
		"          paths:\n" +
		"          - path: /api/v1/mood\n" +
		"            pathType: Prefix\n" +
		"            backend:\n" +
		"              service:\n" +
		"                name: go-api\n" +
		"                port:\n" +
		"                  name: http\n" +
		"          - path: /api/v1/mood/trend\n" +
		"            pathType: Exact\n" +
		"            backend:\n" +
		"              service:\n" +
		"                name: py-api\n" +
		"                port:\n" +
		"                  name: http\n"
	rules := cutover.ParseIngressRules(src, "synthetic")
	if len(rules) != 2 {
		t.Fatalf("合成清单解析出 %d 条规则（应为 2）⇒ 行正则漂了", len(rules))
	}
	if rules[0].PathType != "Prefix" || rules[0].Backend != "go-api" {
		t.Errorf("第一条规则解析成 %s/%s ⇒ 前视窗口越过了 `- path:` 边界，"+
			"下一条的 pathType/backend 会覆盖这一条（D-5 的 Prefix 判据从此咬不住）",
			rules[0].PathType, rules[0].Backend)
	}
	if rules[1].PathType != "Exact" || rules[1].Backend != "py-api" {
		t.Errorf("第二条规则解析成 %s/%s ⇒ 边界切错了方向，清单里的归属会被读串",
			rules[1].PathType, rules[1].Backend)
	}
	// 现账证人：真清单里 mood 那两条今天确实都是 Exact（不是靠合成样本自证）。
	live := cutover.ParseIngressRules(mustRead(t, ingressFile), ingressFile)
	mood := 0
	for _, r := range live {
		if r.Backend != "go-api" {
			continue
		}
		if cutover.DomainOfPath(r.Path) != "mood" {
			continue
		}
		mood++
		if r.PathType != "Exact" {
			t.Errorf("现账里 mood 的 %s 是 %s ⇒ 与 D-5 的口径不符（该域缺 POST /mood/checkin）",
				r.Path, r.PathType)
		}
	}
	if mood != 2 {
		t.Errorf("现账里指向 go-api 的 mood 规则数=%d（应为 2）⇒ Ingress 那两条刀变了", mood)
	}
}

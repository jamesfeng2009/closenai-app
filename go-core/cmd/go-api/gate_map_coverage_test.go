// 批 3 D-G12 的闸：config 里声明的每一个 CLOSEN_CUTOVER_* 域，都必须出现在
// cmd/go-api/main.go 传给 cutover.New 的那张 gate map 里。
//
// 为什么这条不变量非要有人盯：cutover.go 对**没有登记的域**判的是
// `if !ok || pct >= 100 { return true }`——「查不到键」和「切到 100%」同形。
// 于是 CLOSEN_CUTOVER_MEMORIES 那样只在 config 声明、没进 gate map 的键会变成**死开关**：
// handler 按域问闸时永远拿到 true，下一刀把 path 放进 Ingress 就是 Go 无视百分比全量自答，
// 运维把键调回 0 也回滚不了。ConfigMap 与 Ingress 那三面闸（backend/tests/
// test_n8_batch2_contract.py）盯的是「键在不在册、path 上没上刀」，看不见这张 map。
package main

import (
	"os"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// declaredDomain 抓 config.Load() 里的读数点：envInt("CLOSEN_CUTOVER_<域>", 0)。
var declaredDomain = regexp.MustCompile(`envInt\("CLOSEN_CUTOVER_([A-Z_]+)"`)

// gateMapEntry 抓 main.go 里那张 map 的一格："<域>": cfg.Cutover<域>。
var gateMapEntry = regexp.MustCompile(`"([a-z_]+)":\s*cfg\.Cutover([A-Z][A-Za-z]*)`)

func readGoFile(t *testing.T, rel string) string {
	t.Helper()
	src, err := os.ReadFile(rel)
	if err != nil {
		t.Fatalf("读 %s 失败：%v（这条闸的两面都在源文件上，读不到就不该算过）", rel, err)
	}
	return string(src)
}

// configPath 相对测试运行目录（cmd/go-api）指向 Go 侧唯一的 cutover 键声明处。
const configPath = "../../internal/config/config.go"

func lowerDomain(upper string) string {
	return strings.ToLower(strings.ReplaceAll(upper, "_", ""))
}

// TestEveryDeclaredCutoverDomainHasAGateMapEntry 判「声明 ⇒ 接线」这一向。
// 少一格不会编译报错、也不会让进程起不来，它只在**下一次上刀**那天咬人。
func TestEveryDeclaredCutoverDomainHasAGateMapEntry(t *testing.T) {
	configSrc := readGoFile(t, configPath)
	mainSrc := readGoFile(t, "main.go")

	declared := map[string]bool{}
	for _, m := range declaredDomain.FindAllStringSubmatch(configSrc, -1) {
		declared[lowerDomain(m[1])] = true
	}
	if len(declared) == 0 {
		t.Fatal("config.go 里一个 CLOSEN_CUTOVER_* 都没抓到：正则失效会让这条闸永远绿")
	}

	wired := map[string]bool{}
	for _, m := range gateMapEntry.FindAllStringSubmatch(mainSrc, -1) {
		wired[m[1]] = true
	}

	var missing []string
	for domain := range declared {
		if !wired[domain] {
			missing = append(missing, domain)
		}
	}
	sort.Strings(missing)
	if len(missing) > 0 {
		t.Fatalf("这些域在 config 有键、但没进 main.go 的 gate map：%v ⇒ 按 cutover.go 的 "+
			"`if !ok || pct >= 100` 它们会被当成 100%% 自答，键调到 0 也回滚不了。"+
			"修法是在 gate map 补一格（值取 cfg.Cutover<域>）", missing)
	}
}

// TestGateMapHasNoInventedDomain 判反方向：map 里的每一格都得有对应的声明键。
// 反过来少一格不会出事（问不到就没人在 Go 侧答），但它意味着有一张**没人能拨**的格子，
// 与本单否掉的那条「假旋钮」是同一形状，所以一并拦。
func TestGateMapHasNoInventedDomain(t *testing.T) {
	configSrc := readGoFile(t, configPath)
	mainSrc := readGoFile(t, "main.go")

	declared := map[string]bool{}
	for _, m := range declaredDomain.FindAllStringSubmatch(configSrc, -1) {
		declared[lowerDomain(m[1])] = true
	}

	var invented []string
	for _, m := range gateMapEntry.FindAllStringSubmatch(mainSrc, -1) {
		if !declared[m[1]] {
			invented = append(invented, m[1]+" (cfg.Cutover"+m[2]+")")
		}
	}
	sort.Strings(invented)
	if len(invented) > 0 {
		t.Fatalf("gate map 里这些格在 config 没有对应的 CLOSEN_CUTOVER_* 读数点：%v", invented)
	}
}

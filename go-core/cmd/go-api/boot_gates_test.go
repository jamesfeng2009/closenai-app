// T6-04 的装配证人：判据写对了不等于接上了线。
//
// 前面几票登记过一笔欠账（T4-03 结案行 ①、mutate_t402_gates.py:38）：
// `cmd/go-api` 的装配面本身没有逐格证人，`internalEnabled()` 那几格只有编译期兜着。
// 本票新加的两道闸同形——`CheckDBIsolation`／`CheckApproval` 的单测全绿，
// 而 main() 里一句没调，进程照样带病起来。所以这里钉的是**顺序与调用**，不是判据：
//  1. 库面隔离判在建连**之前**（判晚了就等于先连上生产库再决定拒绝）；
//  2. 审批位判在 `cutover.New` **之前**，且两者读**同一张** map（D-T604-10）；
//  3. 强制鉴权那一格接的是 IsProtected()（staging 同档，D-T604-3），不是 IsProduction()。
//
// 三条都是源码文本判据：main() 是进程入口，本机不能真起一个带生产 env 的进程来验
// （那正是 §验收 的两条靶，只能由变异脚本来打——见 scripts/mutate_t604_gates.py）。
package main

import (
	"os"
	"regexp"
	"strings"
	"testing"
)

func readSource(t *testing.T, rel string) string {
	t.Helper()
	src, err := os.ReadFile(rel)
	if err != nil {
		t.Fatalf("读 %s 失败：%v（读不到就不许算过）", rel, err)
	}
	return string(src)
}

func mustIndex(t *testing.T, src, needle, file string) int {
	t.Helper()
	at := strings.Index(src, needle)
	if at < 0 {
		t.Fatalf("%s 里找不到 %q：这一格换了写法，本闸已经在空转（不是绿，是瞎）", file, needle)
	}
	return at
}

// TestBootOrderIsolationBeforeConnect 钉 §验收 第一句落在装配面的哪一步。
func TestBootOrderIsolationBeforeConnect(t *testing.T) {
	src := readSource(t, "main.go")
	loaded := mustIndex(t, src, "cfg, err := config.Load()", "main.go")
	isolation := mustIndex(t, src, "cfg.RequireDBIsolation()", "main.go")
	secrets := mustIndex(t, src, "cfg.RequireAuthSecret()", "main.go")
	pool := mustIndex(t, src, "pgxpool.ParseConfig(dsn.Postgres(cfg.DatabaseURL))", "main.go")
	connected := mustIndex(t, src, "pgxpool.NewWithConfig(", "main.go")

	if !(loaded < isolation && isolation < pool && pool < connected) {
		t.Fatalf("装配顺序不对：Load=%d RequireDBIsolation=%d RequireAuthSecret=%d "+
			"ParseConfig=%d NewWithConfig=%d——隔离必须判在建连之前，"+
			"否则那一次连接本身已经进了 PG 的日志与连接计数",
			loaded, isolation, secrets, pool, connected)
	}
	if secrets > isolation {
		t.Errorf("缺密钥的拒启判在隔离之后：两条都该在建连前，先判哪条都要能单独拦住")
	}
	if !strings.Contains(src, `log.Fatalf("go-api: %v", err)`) {
		t.Error("隔离/审批的错误没有走 log.Fatalf（回 500 的启动失败等于没失败）")
	}
}

// TestApprovalSeesTheSameGateMap 钉 D-T604-10 那一句：
// 「审批看一份数、灰度闸看另一份数」等于谁都没看——这是本票自己否掉的形状。
func TestApprovalSeesTheSameGateMap(t *testing.T) {
	src := readSource(t, "main.go")

	constructed := regexp.MustCompile(`cutoverPercents\s*:?=\s*map\[string\]int\{`).FindAllString(src, -1)
	if len(constructed) != 1 {
		t.Fatalf("那张八格表构造了 %d 次（期望 1 次）：多出来的每一份都是一个可以各自漂移的真源",
			len(constructed))
	}
	approval := mustIndex(t, src, "cutover.CheckApproval(cfg.Environment, cutoverPercents, cfg.CutoverApproved)", "main.go")
	gate := mustIndex(t, src, "cutover.New(cfg.CutoverPyAPIURL, cutoverPercents)", "main.go")
	if approval > gate {
		t.Fatalf("审批位判在 cutover.New 之后：New 一旦先建好代理，没签字的放量也已经能答请求了")
	}

	// 表里八格都得同时被两个消费方读到（逐格证人，不靠人抄清单）。
	domains := regexp.MustCompile(`"([a-z_]+)":\s*cfg\.Cutover([A-Z][A-Za-z]*)`).FindAllStringSubmatch(src, -1)
	if len(domains) != 8 {
		t.Fatalf("gate map 抓到 %d 格，期望 8 格：%v", len(domains), domains)
	}
	for _, g := range domains {
		if !strings.Contains(src, "cfg.Cutover"+g[2]) {
			t.Errorf("域 %s 的读数点没接上", g[1])
		}
	}
}

// TestAuthTierWiredToIsProtected 钉 D-T604-3 的落点：接线处读的必须是 IsProtected()。
// 判据表本身由 internal/config 那侧守，这一条只防「表对了、线接错了」——
// 而接错的那一格是 staging 拿到 development 的设备身份透传（X-User-Id 当身份）。
func TestAuthTierWiredToIsProtected(t *testing.T) {
	src := readSource(t, "../../internal/httpapi/server.go")
	if !strings.Contains(src, "NewMiddleware(signer, cfg.IsProtected())") {
		t.Fatalf("server.go 的强制鉴权没接 IsProtected()：staging 会落到开发档")
	}
	if strings.Contains(src, "NewMiddleware(signer, cfg.IsProduction())") {
		t.Error("server.go 又读回 IsProduction() 了：这一格是本票改判的对象，不许悄悄改回去")
	}
}

// TestConfigReadsTheTwoNewKeys 钉两个新键的**变量名**逐字存在于 config.go 的读数点。
// 键名漂了的话 ConfigMap 那面对账会红，但这条更早、也更便宜。
func TestConfigReadsTheTwoNewKeys(t *testing.T) {
	src := readSource(t, "../../internal/config/config.go")
	for _, needle := range []string{
		`envString("CLOSEN_CUTOVER_APPROVED", "")`,
		`envList("PROD_DB_FINGERPRINTS")`,
	} {
		if !strings.Contains(src, needle) {
			t.Errorf("config.go 缺读数点 %s", needle)
		}
	}
	// 审批位是审批位、不是域键：它必须用 envString 读。
	// 用 envInt 读它会被 matrix.go／gate_map_coverage 那两面当成一个叫 approved 的域（D-T604-8）。
	if strings.Contains(src, `envInt("CLOSEN_CUTOVER_APPROVED"`) {
		t.Error("CLOSEN_CUTOVER_APPROVED 被当成灰度域读了（D-T604-8：它是审批位）")
	}
	if strings.Contains(src, `envInt("PROD_DB_FINGERPRINTS"`) {
		t.Error("PROD_DB_FINGERPRINTS 不该是整数读数点")
	}
}

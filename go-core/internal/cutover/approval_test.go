// T6-04 的审批闸（工单 §怎么做.4 点名的这个文件）。
//
// §验收 第二句逐字：「把切流键改 50 且无审批 ⇒ 启动失败」——
// 那条靶是 TestAcceptanceCutoverFiftyWithoutApprovalRefusesToBoot，
// 它跑的是真形状（production ＋一张只有一格非零的 map ＋ CLOSEN_CUTOVER_APPROVED 没设）。
// 其余各格钉的是这句话周围的形状：全 0 必须能起（否则现网默认第一天就绕闸）、
// 签了必须能起（否则这道闸只是一块砖）、staging 不要求签字（D-T604-7）。
package cutover

import (
	"os"
	"regexp"
	"sort"
	"strings"
	"testing"
)

func oneDomain(domain string, pct int) map[string]int {
	return map[string]int{domain: pct}
}

func TestAcceptanceCutoverFiftyWithoutApprovalRefusesToBoot(t *testing.T) {
	err := CheckApproval("production", oneDomain("analytics", 50), "")
	if err == nil {
		t.Fatal("切到 50% 而没人签字，进程却起来了：§验收 第二句没落地")
	}
	msg := err.Error()
	for _, want := range []string{"ENVIRONMENT=production", "CLOSEN_CUTOVER_ANALYTICS",
		"CLOSEN_CUTOVER_APPROVED"} {
		if !strings.Contains(msg, want) {
			t.Fatalf("报错要说清 %q（运维得知道改哪个键）：%s", want, msg)
		}
	}
	approval, ok := err.(*ApprovalError)
	if !ok {
		t.Fatalf("错误类型是 %T，运维按类型分流会落空", err)
	}
	if approval.Approved != "" || len(approval.Domains) != 1 || approval.Domains[0] != "analytics" {
		t.Errorf("ApprovalError 内容不对：%+v", approval)
	}

	// 显式设成 "0"（未签）与没设同向；"1" 才放行。
	for _, notSigned := range []string{"", "0"} {
		if err := CheckApproval("production", oneDomain("analytics", 50), notSigned); err == nil {
			t.Errorf("CLOSEN_CUTOVER_APPROVED=%q 也算签字了：只有 \"1\" 算", notSigned)
		}
	}
	if err := CheckApproval("production", oneDomain("analytics", 50), "1"); err != nil {
		t.Errorf("签了字还拒启，这道闸就成了没人愿意过的砖：%v", err)
	}
}

// TestAllZeroPercentsBootWithoutApproval 是本闸的**可用性**证人：
// 现网默认是八个键全 0（deploy/k8s/10-config.yaml:37-83），它必须照起。
// 这一格红了不等于更安全——它等于有人把整条校验注释掉。
func TestAllZeroPercentsBootWithoutApproval(t *testing.T) {
	percents := map[string]int{}
	for _, d := range []string{"analytics", "discovery", "curriculum", "journey",
		"onboarding", "mood", "reflections", "memories"} {
		percents[d] = 0
	}
	if err := CheckApproval("production", percents, ""); err != nil {
		t.Fatalf("八格全 0 却起不来：%v", err)
	}
	if err := CheckApproval("production", map[string]int{}, ""); err != nil {
		t.Fatalf("空表（一个切流域都没声明）却起不来：%v", err)
	}
}

// TestFullCutAlsoNeedsApproval 钉那一格「100 就不算放量」的直觉：
// 100% 是 Go 独自答整条域，比 50% 更需要签字，判据里没有 <100 的例外。
func TestFullCutAlsoNeedsApproval(t *testing.T) {
	for _, pct := range []int{1, 50, 100} {
		if err := CheckApproval("production", oneDomain("journey", pct), ""); err == nil {
			t.Errorf("%d%% 不要求审批：那 99%% 与 100%% 之间就没有闸了", pct)
		}
	}
}

// TestStagingDoesNotNeedApproval 是 D-T604-7 的后半句（备选是「staging 也要签」）。
// staging 的百分比只作用在它自己的库上，而它打到生产库那条路已由
// config/env_isolation.go 判成拒启；这里再要求签字只会把两道闸叠成一道没人能读的。
func TestStagingDoesNotNeedApproval(t *testing.T) {
	for _, env := range []string{"staging", "development"} {
		if err := CheckApproval(env, oneDomain("analytics", 50), ""); err != nil {
			t.Errorf("ENVIRONMENT=%s 不该被审批闸拦（D-T604-7）：%v", env, err)
		}
	}
}

// TestProdAliasSameTier 钉 `prod` 这个既有别名与 production 同档：
// config.IsProduction()/Python 的 ("production","prod") 早于本票就承认它，
// 审批闸只认逐字 production 会留下一个绕过入口（把键设成 prod 就没人签字也能放量）。
func TestProdAliasSameTier(t *testing.T) {
	if err := CheckApproval("prod", oneDomain("memories", 30), ""); err == nil {
		t.Fatal("ENVIRONMENT=prod 绕过了审批闸")
	}
	if err := CheckApproval("prod", oneDomain("memories", 30), "1"); err != nil {
		t.Fatalf("prod＋已签不该拒：%v", err)
	}
}

// TestEveryLiveDomainIsNamed 钉「报出来的是所有在放量的域，不是第一个碰到的」。
// map 的遍历序是随机的：只取第一条的实现会在某些运行里只报一个域，
// 运维照着改完那一个，剩下几个照旧在放量。
func TestEveryLiveDomainIsNamed(t *testing.T) {
	percents := map[string]int{
		"analytics": 50, "discovery": 0, "curriculum": 10,
		"journey": 100, "onboarding": 0, "mood": 1,
		"reflections": 0, "memories": 0,
	}
	for i := 0; i < 20; i++ {
		err := CheckApproval("production", percents, "0")
		approval, ok := err.(*ApprovalError)
		if !ok {
			t.Fatalf("第 %d 轮没拿到 *ApprovalError：%v", i, err)
		}
		got := append([]string(nil), approval.Domains...)
		if !sort.StringsAreSorted(got) {
			t.Fatalf("Domains 没排序（同一份配置两次启动报不同的样子）：%v", got)
		}
		want := []string{"analytics", "curriculum", "journey", "mood"}
		if strings.Join(got, ",") != strings.Join(want, ",") {
			t.Fatalf("放量的域报成 %v，期望 %v", got, want)
		}
		for _, d := range want {
			if !strings.Contains(err.Error(), EnvKeyOf(d)) {
				t.Errorf("报错里没点名 %s：%v", EnvKeyOf(d), err)
			}
		}
	}
}

// TestEveryConfiguredDomainNeedsApproval 是逐格证人：
// 八个 CLOSEN_CUTOVER_* 域**每一个**单独 > 0 都要触发审批闸。
// 域名单从 config.go 的读数点现推（与本包不同源），所以「新接一个域而忘了它也在审批面上」
// 这一格不会靠人抄清单抄漏。cmd/go-api 那条 gate_map_coverage 闸管的是
// 「声明 ⇒ 进 gate map」，这里管的是「声明 ⇒ 进审批面」——两张判据、同一个来源。
func TestEveryConfiguredDomainNeedsApproval(t *testing.T) {
	src, err := os.ReadFile("../../internal/config/config.go")
	if err != nil {
		t.Fatalf("读 config.go 失败：%v（取不到域名单就不该算过）", err)
	}
	re := regexp.MustCompile(`envInt\("CLOSEN_CUTOVER_([A-Z_]+)",\s*0\)`)
	groups := re.FindAllStringSubmatch(string(src), -1)
	if len(groups) < 8 {
		t.Fatalf("从 config.go 只推出 %d 个切流域：读数形状变了，本闸会空转", len(groups))
	}
	for _, g := range groups {
		domain := strings.ToLower(strings.ReplaceAll(g[1], "_", ""))
		if err := CheckApproval("production", oneDomain(domain, 50), ""); err == nil {
			t.Errorf("%s（%s）放量到 50%% 不要求审批", EnvKeyOf(domain), domain)
		}
		if err := CheckApproval("production", oneDomain(domain, 0), ""); err != nil {
			t.Errorf("%s 全 0 却被审批闸拦了：%v", EnvKeyOf(domain), err)
		}
	}
}

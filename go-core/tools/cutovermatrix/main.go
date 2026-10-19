// T6-02 生成物写手：`go run ./tools/cutovermatrix -write` 重导 contracts/cutover-matrix.md。
//
// 为什么用 Go 而不是 Python 脚本：判据本体住在 internal/cutover（Go 侧的注册面与灰度闸都在这），
// 用 Python 再实现一遍「path 匹配不看方法」就是两套口径——本仓的铁律是断言两侧不许同源，
// 但**生成**与**校验**必须同源：md 由这里的 Render 写，闸测由同一对 BuildRows/Render 重算比字节。
package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/cutover"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/httpapi"
)

// repoRoot：从当前工作目录往上找 `contracts/endpoints.md`。
// `go run ./tools/cutovermatrix` 的 cwd 是调用处（go-core/ 或 go-core/tools/cutovermatrix/），
// 写死 ".."／"../.." 会让其中一侧读不到清单——读不到就退，比按猜测的层级拼强。
func repoRoot() string {
	dir, err := os.Getwd()
	if err != nil {
		fmt.Fprintln(os.Stderr, "cutovermatrix: 拿不到工作目录：", err)
		os.Exit(1)
	}
	for i := 0; i < 8; i++ {
		if _, err := os.Stat(filepath.Join(dir, "contracts", "endpoints.md")); err == nil {
			return dir
		}
		dir = filepath.Dir(dir)
	}
	fmt.Fprintln(os.Stderr, "cutovermatrix: 往上八层都没有 contracts/endpoints.md ⇒ 不在仓库里跑？")
	os.Exit(1)
	return ""
}

func read(rel string) string {
	raw, err := os.ReadFile(filepath.Join(repoRoot(), rel))
	if err != nil {
		fmt.Fprintf(os.Stderr, "cutovermatrix: 读不到 %s：%v\n", rel, err)
		os.Exit(1)
	}
	return string(raw)
}

func main() {
	write := flag.Bool("write", false, "写 contracts/cutover-matrix.md（不给则只打印）")
	flag.Parse()

	rows, violations, md := build()
	if *write {
		if err := os.WriteFile(filepath.Join(repoRoot(), "contracts", "cutover-matrix.md"),
			[]byte(md), 0o644); err != nil {
			fmt.Fprintf(os.Stderr, "cutovermatrix: 写生成物失败：%v\n", err)
			os.Exit(1)
		}
		fmt.Printf("已生成 contracts/cutover-matrix.md（%d 个切流域）\n", len(rows))
		return
	}
	fmt.Print(md)
	if len(violations) > 0 {
		fmt.Fprintf(os.Stderr, "判据违例 %d 条（生成器不写脏矩阵，请先修）\n", len(violations))
		os.Exit(1)
	}
}

func build() ([]cutover.DomainRow, []string, string) {
	const (
		ingressFile  = "deploy/k8s/50-ingress.yaml"
		caddyFile    = "deploy/compose/Caddyfile"
		contractFile = "contracts/endpoints.md"
		configFile   = "go-core/internal/config/config.go"
		mainFile     = "go-core/cmd/go-api/main.go"
	)
	configSrc := read(configFile)
	keys := cutover.ParseConfigKeys(configSrc)
	rules := append(cutover.ParseIngressRules(read(ingressFile), ingressFile),
		cutover.ParseCaddyRules(read(caddyFile), caddyFile)...)
	ops := cutover.ParseContractOps(read(contractFile))
	plan := make([]cutover.GoRoute, 0)
	for _, r := range httpapi.RoutePlan() {
		plan = append(plan, cutover.GoRoute{Method: r.Method, Path: r.Path, Domain: r.Domain})
	}
	rows := cutover.BuildRows(keys, rules, ops, plan)
	violations := cutover.Violations(rows, rules, plan, configSrc,
		cutover.ParseAssemblyKeys(read(mainFile)))

	md := cutover.Render(rows, violations, ingressFile, caddyFile, contractFile)
	return rows, violations, md
}

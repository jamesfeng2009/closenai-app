// Command eventscontract 生成或校验 contracts/events.md（R4-①，D-R4-2）。
//
// 为什么要一台生成器而不是手写那份文档：本轮就抓到过一处手抄漂移
// （`contracts/rpc/ownership.md:182-183` 写的队列面与代码不一致）。契约文档一旦手抄，
// 它就变成第二事实源，而读的人无法分辨哪一份是旧的。
//
//	go run ./tools/eventscontract -write          # 写 ../contracts/events.md
//	go run ./tools/eventscontract -check          # 不一致就 exit 1（CI 用）
//	go run ./tools/eventscontract                 # 打到 stdout
//
// 事实源是 `internal/messaging/amqp/topology.go` 的两张表；本命令不添加任何判断。
package main

import (
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/amqp"
)

const relativeTarget = "contracts/events.md"

func main() {
	write := flag.Bool("write", false, "写进 "+relativeTarget+"（生成物，不要手改）")
	check := flag.Bool("check", false, "只比对：与磁盘上的生成物不一致就退出码 1")
	target := flag.String("out", "", "覆盖输出路径，默认向上找到仓库根的 "+relativeTarget)
	flag.Parse()

	path := *target
	if path == "" {
		root, err := repoRoot()
		if err != nil {
			fatal(err)
		}
		path = filepath.Join(root, relativeTarget)
	}

	want := amqp.Document()
	switch {
	case *write && *check:
		fatal(errors.New("-write 与 -check 互斥"))
	case *write:
		if err := os.WriteFile(path, []byte(want), 0o644); err != nil {
			fatal(err)
		}
		fmt.Printf("已写 %s（%d 字节）\n", path, len(want))
	case *check:
		got, err := os.ReadFile(path)
		if err != nil {
			fatal(fmt.Errorf("%s 不存在或读不出：%w ⇒ 跑 -write 生成，别手写", path, err))
		}
		if string(got) != want {
			fatal(fmt.Errorf("%s 与 topology.go 的表不一致 ⇒ 改了表没重跑生成器，"+
				"跑 go run ./tools/eventscontract -write", path))
		}
		fmt.Printf("%s 与生成结果逐字节一致\n", path)
	default:
		fmt.Print(want)
	}
}

// repoRoot 从当前工作目录向上找仓库根。判据用 contracts/ 目录**和** go-core/go.mod 同时存在：
// 单看 contracts/ 会在别的项目里撞库，单看 go.mod 会在 go-core/ 里就停下来（少一级）。
func repoRoot() (string, error) {
	dir, err := os.Getwd()
	if err != nil {
		return "", err
	}
	for {
		if isDir(filepath.Join(dir, "contracts")) && fileExists(filepath.Join(dir, "go-core", "go.mod")) {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", fmt.Errorf("从 %s 向上没找到仓库根（判据：同时有 contracts/ 与 go-core/go.mod）", dir)
		}
		dir = parent
	}
}

func isDir(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.IsDir()
}

func fileExists(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir()
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}

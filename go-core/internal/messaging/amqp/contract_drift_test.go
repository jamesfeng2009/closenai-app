// 生成物漂移闸（R4-①，D-R4-2）。
//
// contracts/events.md 由 `go run ./tools/eventscontract -write` 从本包的两张表生成。
// 这条测试的意义不是「文档存在」，而是**改了表忘了重跑生成器时 `go test ./...` 就红**——
// 手抄的契约文档一定会漂（本轮就抓到 `contracts/rpc/ownership.md:182-183` 那一处），
// 所以把它做成字节级比对，而不是"看起来差不多"。
package amqp

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const contractRelPath = "contracts/events.md"

func TestGeneratedContractMatchesTable(t *testing.T) {
	root, err := repoRootFromCwd()
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(root, contractRelPath)

	onDisk, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("%s 读不出：%v ⇒ 跑 `go run ./tools/eventscontract -write` 生成，不要手写", path, err)
	}
	want := Document()
	if string(onDisk) != want {
		t.Errorf("%s 与 topology.go 的表已漂移（磁盘 %d 字节 / 生成 %d 字节）⇒ "+
			"跑 `go run ./tools/eventscontract -write` 重生成，不要手改文档",
			path, len(onDisk), len(want))
	}
	// 两侧同源，所以「一致」本身可以是假绿：表被清空时两边一起空。
	// 这条下限保证漂移闸跑在一份真有内容的文档上。
	if len(want) < 2000 {
		t.Errorf("生成结果只有 %d 字节 ⇒ 拓扑表疑似被清空，比对通过也不代表契约还在", len(want))
	}
}

// repoRootFromCwd 从测试工作目录（本包目录）向上找仓库根。
// 判据用 contracts/ ＋ go-core/go.mod 同时存在：只认 contracts/ 会在别的项目里撞库。
func repoRootFromCwd() (string, error) {
	dir, err := os.Getwd()
	if err != nil {
		return "", err
	}
	for {
		if isDirectory(filepath.Join(dir, "contracts")) &&
			isRegularFile(filepath.Join(dir, "go-core", "go.mod")) {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", fmt.Errorf("从 %s 向上没找到仓库根（判据：同时有 contracts/ 与 go-core/go.mod）", dir)
		}
		dir = parent
	}
}

func isDirectory(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.IsDir()
}

func isRegularFile(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir()
}

// 契约文档里最容易「读成已交付」的两格，单独钉住：
// 一句话口径写歪，下一个人就会把 R5/R6 当收尾而不是缺口。
func TestContractStatesTheUnboundEventSideInPlainWords(t *testing.T) {
	doc := Document()
	for _, phrase := range []string{
		"R4 只是发射器",
		"没有消费者队列",
		"ErrNoConsumerBound",
		"生产者（谁下发任务）是 R6",
		// R4-③ 之后最容易误读的一格：入队口有了，但现网没人调 ⇒ 别写成「命令面已通」。
		"今天零调用者",
		// 键串的契约源在 domain，不在本文件：读文档的人据此知道改哪儿。
		"domain.AITaskKind.RoutingKey()",
	} {
		if !strings.Contains(doc, phrase) {
			t.Errorf("契约文档里没写 %q ⇒ 这份文档会被读成「异步面已通」", phrase)
		}
	}
}

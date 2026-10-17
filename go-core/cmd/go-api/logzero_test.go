// G-C2（工作单批 T-C）：归零闸＋正向证人。
//
// 判据：`internal/httpapi`、`internal/messaging`、`cmd/go-api` 三棵树的**非测试**源码里
// `log.Printf` 命中数为 0（日志面已整体切到 slog/obs）；同一次扫描里 `slog.` 命中数
// 必须 ≥ 固定基线——[[feedback-gate-needs-positive-witness]]：
// 只判归零的闸会把「把三行代码全删了」也读成绿。
// 变异 M-C2：往 httpapi 塞回一条 log.Printf ⇒ 本闸红。
//
// 范围取舍写在这里以免下批误读：`log.Fatalf`（启动失败路径）与 tools/ 不在闸内——
// 前者没有 ctx 可带、后者不是服务进程。
package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

var logZeroScope = []string{
	filepath.Join("..", "..", "internal", "httpapi"),
	filepath.Join("..", "..", "internal", "messaging"),
	".",
}

// slogBaseline 是批 T-C 结案实测值（13）。只准涨不准跌：跌说明有日志调用
// 被「删掉」而不是「改掉」，那不是迁移，是把可观测性删了。
const slogBaseline = 13

func TestPrintfZeroedSlogWitnessed(t *testing.T) {
	var printfHits []string
	slogCount := 0
	for _, dir := range logZeroScope {
		err := filepath.WalkDir(dir, func(path string, d os.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if d.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
				return nil
			}
			src, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			text := string(src)
			if n := strings.Count(text, "log.Printf"); n > 0 {
				printfHits = append(printfHits, path)
			}
			slogCount += strings.Count(text, "slog.")
			return nil
		})
		if err != nil {
			t.Fatalf("扫 %s 失败：%v（目录形状变了就要同步改 logZeroScope，不许默默跳过）", dir, err)
		}
	}
	if len(printfHits) > 0 {
		t.Errorf("这三棵树里不该再有 log.Printf（批 T-C 归零判据），命中：%v", printfHits)
	}
	if slogCount < slogBaseline {
		t.Errorf("slog. 命中数 %d < 基线 %d ⇒ 有日志调用被删掉而非改掉，归零闸失去正向证人",
			slogCount, slogBaseline)
	}
}

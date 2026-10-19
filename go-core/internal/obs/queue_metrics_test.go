// 异步指标行的闸（T2-05 §怎么做.2）。
//
// 这个文件判的四件事，每一件都对应一种「告警永远不会响」或「天天误响」的形态：
//  1. 三个指标名**逐字**出现在汇总行上（`queue_depth`／`oldest_message_age_seconds`／`dlq_total`）
//     ——告警规则是按这三个字面写的，改名等于把告警静默摘掉；
//  2. 正常态（全空）一次 tick 恰好**一条**日志，与工单那句「每 60s 打一条结构化日志」逐字一致；
//     积压时才按队列追加明细行；
//  3. `dlq_total` 只数终态面，`queue_depth` 数全部；
//  4. 读不到年龄时报「不知道」而不是 0 秒，但**年龄真的是 0 秒**（刚投进去的那条）时
//     必须报「知道是 0」——反过来的话最老消息这一格永远在「健康」与「不知道」之间抖动。
//
// 全程不碰 broker：取数走 `QueueReader` 函数端口，装配点在 cmd/go-api。
package obs

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func rows(v ...QueueStat) []QueueStat { return v }

func stats(q, kind string, depth, consumers int, age float64, known bool) QueueStat {
	return QueueStat{Queue: q, Kind: kind, Depth: depth, Consumers: consumers,
		OldestAgeSecs: age, OldestAgeKnown: known}
}

// ── 1／2：行的形状与条数 ────────────────────────────────

func TestHealthyTickEmitsExactlyOneLineWithThreeMetricNames(t *testing.T) {
	lines := capture(t, func() {
		err := tick(context.Background(), func(context.Context) ([]QueueStat, error) {
			return rows(
				stats("ai.observation.extract", "purpose", 0, 1, 0, false),
				stats("retry.30s.ai.observation.extract", "retry", 0, 0, 0, false),
				stats("dlq.ai.observation.extract", "dlq", 0, 0, 0, false),
			), nil
		})
		if err != nil {
			t.Fatalf("tick：%v", err)
		}
	})
	if len(lines) != 1 {
		t.Fatalf("队列全空时一次 tick 必须恰好一条日志，实际 %d 条（明细行只在有积压时打）", len(lines))
	}
	got := lines[0]
	if got["msg"] != "queue_metrics" {
		t.Fatalf("msg=%v（事件名就是 msg，Loki 的告警规则按它写）", got["msg"])
	}
	// 判据本体：三个指标名逐字在场（告警规则按这三个字面写，改名＝静默摘掉告警）。
	for _, name := range []string{"queue_depth", "oldest_message_age_seconds", "dlq_total"} {
		if _, ok := got[name]; !ok {
			t.Fatalf("汇总行少了 %q：%v", name, got)
		}
	}
	if got["queue_depth"] != float64(0) || got["dlq_total"] != float64(0) {
		t.Fatalf("全空时两个深度数应当是 0：%v", got)
	}
	if got["queues_scanned"] != float64(3) {
		t.Fatalf("扫了几个队列报错了：%v", got)
	}
}

func TestBacklogAddsOneDetailLinePerStuckQueue(t *testing.T) {
	lines := capture(t, func() {
		if err := tick(context.Background(), func(context.Context) ([]QueueStat, error) {
			return rows(
				stats("ai.observation.extract", "purpose", 0, 1, 0, false),
				stats("retry.30s.go.candidate.accept", "retry", 4, 0, 65, true),
				stats("dlq.go.candidate.accept", "dlq", 2, 0, 900, true),
				stats("dlq.ai.pattern.detect", "dlq", 1, 0, 0, true),
			), nil
		}); err != nil {
			t.Fatalf("tick：%v", err)
		}
	})
	// 1 条汇总 ＋ 3 条明细（每条有积压的队列各一行；空队列不打）。
	if len(lines) != 4 {
		t.Fatalf("行数=%d，应当 4：%v", len(lines), lines)
	}
	summary := lines[0]
	if summary["msg"] != "queue_metrics" {
		t.Fatalf("第一条必须是汇总行：%v", summary["msg"])
	}
	if summary["queue_depth"] != float64(7) { // 4+2+1（全部队列）
		t.Fatalf("queue_depth 该是全部队列的和=%v", summary["queue_depth"])
	}
	if summary["dlq_total"] != float64(3) { // 2+1（只数终态面）
		t.Fatalf("dlq_total 该只数 dlq 面=%v ⇒ 阶梯里的 4 条被算进来了", summary["dlq_total"])
	}
	// 最老的是那条 900 秒的终态消息，并且报出它在哪一格（值班要的是「去哪捞」）。
	if summary["oldest_message_age_seconds"] != float64(900) ||
		summary["oldest_age_queue"] != "dlq.go.candidate.accept" ||
		summary["oldest_age_known"] != true {
		t.Fatalf("最老年龄这格不对：%v", summary)
	}
	for _, l := range lines[1:] {
		if l["msg"] != "queue_metric" {
			t.Fatalf("明细行的 event 不是 queue_metric：%v", l)
		}
		if l["queue"] == nil || l["kind"] == nil || l["queue_depth"] == nil {
			t.Fatalf("明细行缺字段（Loki 按这三个字面查）：%v", l)
		}
	}
}

func TestAgeZeroIsKnownNotUnknown(t *testing.T) {
	// 反向抖动那一格：`0` 既可能是「知道很新鲜」也可能是「读不到」，靠 OldestAgeKnown 分开。
	lines := capture(t, func() {
		if err := tick(context.Background(), func(context.Context) ([]QueueStat, error) {
			return rows(stats("dlq.ai.pattern.detect", "dlq", 1, 0, 0, true)), nil
		}); err != nil {
			t.Fatalf("tick：%v", err)
		}
	})
	if lines[0]["oldest_age_known"] != true || lines[0]["oldest_message_age_seconds"] != float64(0) {
		t.Fatalf("年龄真的是 0 秒时该报「知道是 0」：%v", lines[0])
	}
	if lines[0]["oldest_age_queue"] != "dlq.ai.pattern.detect" {
		t.Fatalf("0 秒那条没报出队列名：%v", lines[0])
	}
}

func TestAgeUnknownIsReportedAsUnknown(t *testing.T) {
	lines := capture(t, func() {
		if err := tick(context.Background(), func(context.Context) ([]QueueStat, error) {
			// 深度不为 0 但年龄读不到（例如队列上有消费者）⇒ 不许冒充「0 秒＝健康」。
			return rows(stats("ai.insight.draft", "purpose", 3, 2, 0, false)), nil
		}); err != nil {
			t.Fatalf("tick：%v", err)
		}
	})
	if lines[0]["oldest_age_known"] != false {
		t.Fatalf("读不到年龄却报成知道：%v", lines[0])
	}
	// 没有已知年龄时**不打** oldest_age_queue 这一格：有值但为空的字段会被读成「在那一格」。
	if _, ok := lines[0]["oldest_age_queue"]; ok {
		t.Fatalf("不知道是谁却报了队列名：%v", lines[0])
	}
	// 积压本身照样报数（这条告警看的是「堵没堵」，与年龄读不读得到无关）。
	if lines[0]["queue_depth"] != float64(3) || lines[0]["dlq_total"] != float64(0) {
		t.Fatalf("深度数丢了：%v", lines[0])
	}
}

func TestEmptyTickHasNoQueueName(t *testing.T) {
	lines := capture(t, func() {
		if err := tick(context.Background(), func(context.Context) ([]QueueStat, error) {
			return nil, nil
		}); err != nil {
			t.Fatalf("tick：%v", err)
		}
	})
	if len(lines) != 1 || lines[0]["msg"] != "queue_metrics" ||
		lines[0]["queues_scanned"] != float64(0) {
		t.Fatalf("取到空清单也要出声（不然「巡检挂了」与「没队列」长得一样）：%v", lines)
	}
}

// ── 循环面：拒绝启动／读数失败不退 ──────────────────────

func TestNilReaderRefusesToStart(t *testing.T) {
	// 空转的观测面比没有观测面更坏：运维会把它读成「异步面在被巡检」。
	if err := RunQueueMetrics(context.Background(), nil); err == nil {
		t.Fatal("没给 QueueReader 时拒绝启动")
	} else if !strings.Contains(err.Error(), "QueueReader") {
		t.Fatalf("错误没说清缺的是哪一格：%v", err)
	}
}

func TestReadFailureIsLoggedAndLoopKeepsRunning(t *testing.T) {
	// broker 抖动那一格：这一条循环必须比 worker 活得久，它得继续报「读不到」。
	calls := make(chan int, 2)
	lines := capture(t, func() {
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		done := make(chan error, 1)
		go func() {
			done <- RunQueueMetrics(ctx, func(context.Context) ([]QueueStat, error) {
				// 计数走带缓冲通道而不是裸 int：这一格将来在 -race 下跑，
				// 跨 goroutine 读写一个 int 就是给 CI 加一条随机红。
				select {
				case calls <- 1:
					return nil, errors.New("拨不上 broker")
				default:
				}
				return rows(stats("dlq.ai.observation.extract", "dlq", 1, 0, 12, true)), nil
			})
		}()
		// 第一次 tick 是立即跑的；跑完就取消，不去等那 60s 的节拍。
		if n := <-calls; n != 1 {
			t.Errorf("第一次读数应当是失败那一格，实际第 %d 次", n)
		}
		cancel()
		if err := <-done; !errors.Is(err, context.Canceled) {
			t.Errorf("取消后应当回 context.Canceled，实际 %v", err)
		}
	})
	var sawFail, sawMetrics bool
	for _, l := range lines {
		switch l["msg"] {
		case "queue_metrics_read_failed":
			sawFail = true
			if !strings.Contains(stringOf(l["err"]), "拨不上") {
				t.Fatalf("失败行没带上原因（值班只能看到「没有指标」）：%v", l)
			}
		case "queue_metrics":
			sawMetrics = true
		}
	}
	if !sawFail {
		t.Fatalf("读失败没有出声：%v", lines)
	}
	if sawMetrics {
		t.Fatalf("只喂了一次失败却打出了汇总行（把「读不到」伪装成「都是 0」）：%v", lines)
	}
}

func TestDefaultCadenceIsSixtySeconds(t *testing.T) {
	// 节拍是包内常量、不是 env 键（D-G12：键存在但没人读＝假旋钮）。
	// 改这一格必须同时改告警窗口，所以这里按字面钉住。
	if QueueMetricsEvery() != 60*time.Second {
		t.Fatalf("节拍=%v，工单 §怎么做.2 逐字要的是 60s", QueueMetricsEvery())
	}
}

func stringOf(v any) string {
	s, _ := v.(string)
	return s
}

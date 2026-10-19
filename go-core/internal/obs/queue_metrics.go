// 异步面的读数打成一条结构化日志（T2-05 §怎么做.2，Baseline §13 要的
// 「积压、最老消息年龄、重试次数、DLQ 数量」）。
//
// 为什么是**日志**而不是 /metrics 端点：现网的抓取面是 Loki（k3s 上没有 Prometheus
// operator，`deploy/k8s/` 里也没有 ServiceMonitor），而「每 60s 一条 JSON 行」在 Loki 里
// 就是一条可告警的指标序列。多加一个 HTTP 端点要开端口、要鉴权、要在切流面登记，
// 换来的却是现网抓不到的东西。
//
// 为什么不认识 rabbitmq：取数走 `QueueReader` 这个函数端口，装配点在 `cmd/go-api`。
// obs 是日志面，它一旦认识 AMQP，`domain/arch_test.go` 那条「领域层不许认识 rabbitmq」
// 就只剩一半——日志面与消息面的边界是同一批人守的，别先在自己手里破。
//
// ⚠️ 节拍与开关都在装配点，不在这里：本文件只认「多久一次、怎么读、打成什么形状」。
// 每 60s 一条**汇总行**；汇总行之外，每条**有积压的**队列各打一行明细。
// 正常态（队列全空）一次 tick 恰好一行，这与工单那句「每 60s 打一条结构化日志」逐字一致；
// 出事时多出来的那些行不是噪声，是「哪一格堵了」这个唯一还需要的答案。
package obs

import (
	"context"
	"errors"
	"log/slog"
	"time"
)

// defaultQueueMetricsEvery 是巡检节拍。**不是 env 键**（D-G12 的「键存在但没人读＝假旋钮」）：
// 60s 与工单逐字一致，而真要改它的人应该改这里、并同步改告警窗口。
const defaultQueueMetricsEvery = 60 * time.Second

// QueueMetricsEvery 把包内默认暴露给装配点与测试（不让它们抄一个字面量）。
func QueueMetricsEvery() time.Duration { return defaultQueueMetricsEvery }

// QueueStat 是一个队列的一次读数。字段名与 `dlq.Summary` 的 JSON 标签同形，
// 这样工具输出与日志行在 Loki 里可以用同一条查询读。
type QueueStat struct {
	Queue          string  `json:"queue"`
	Kind           string  `json:"kind"` // purpose／retry／dlq
	Depth          int     `json:"queue_depth"`
	Consumers      int     `json:"consumers"`
	OldestAgeSecs  float64 `json:"oldest_message_age_seconds"`
	OldestAgeKnown bool    `json:"oldest_age_known"`
}

// QueueReader 取一轮全部队列的读数。取不到就回错误——本循环**不**把错误当成「都是 0」，
// 那会把「巡检挂了」伪装成「队列很健康」，而值班看的正是这一格。
type QueueReader func(ctx context.Context) ([]QueueStat, error)

// RunQueueMetrics 每 QueueMetricsEvery 巡检一轮并打日志，直到 ctx 取消。
//
// 三点取舍：
//   - **第一次立即跑**：进程刚起来那一轮的数字是部署取证要的（「上线后到底有没有积压」），
//     等 60s 才打第一条的话，一次滚动发布里最坏的那 60s 没有任何读数。
//   - 读失败**不退出**：broker 抖动时这条循环比 worker 活得久，它得继续报「读不到」，
//     而不是让观测面跟着一起消失。
//   - `read == nil` 时拒绝启动（回错误），不静默空转：空转的观测面比没有观测面更坏，
//     运维会把它读成「异步面在被巡检」。
func RunQueueMetrics(ctx context.Context, read QueueReader) error {
	if read == nil {
		return errors.New("queue metrics: 没给 QueueReader ⇒ 这一轮一次都不会读数，拒绝启动")
	}
	ticker := time.NewTicker(defaultQueueMetricsEvery)
	defer ticker.Stop()
	for {
		if err := tick(ctx, read); err != nil && ctx.Err() == nil {
			// 读不到也要出声：错误本身按一条日志打，不 panic、不退出循环。
			slog.Warn("queue_metrics_read_failed", "err", err.Error())
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}

// tick 跑一轮：取数 → 汇总 → 打行。分成这个函数是为了让测试能只跑一轮就断言形状
// （跑整条循环的测试要么等 60s，要么改节拍——节拍不是键，改不了）。
func tick(ctx context.Context, read QueueReader) error {
	rows, err := read(ctx)
	if err != nil {
		return err
	}
	total, dlqTotal, oldest, oldestQueue, oldestKnown := summarize(rows)
	// 汇总行：一条 tick 里**必定**只有这一行是无条件的（三个指标名逐字出现在这里）。
	// msg 就是事件名（`slog` 把它打成 JSON 的 `msg` 格），所以这里不再另写一个 `event` 属性：
	// 两处同义、其中一处必漂，而 Loki 的查询按 `msg` 写。
	attrs := []any{
		"queues_scanned", len(rows),
		"queue_depth", total,
		"dlq_total", dlqTotal,
		"oldest_message_age_seconds", oldest,
		"oldest_age_known", oldestKnown,
	}
	if oldestQueue != "" {
		attrs = append(attrs, "oldest_age_queue", oldestQueue)
	}
	slog.Info("queue_metrics", attrs...)

	for _, r := range rows {
		if r.Depth == 0 {
			continue // 正常态一行明细都不打（见文件头）
		}
		detail := []any{
			"queue", r.Queue, "kind", r.Kind,
			"queue_depth", r.Depth, "consumers", r.Consumers,
			"oldest_message_age_seconds", r.OldestAgeSecs,
			"oldest_age_known", r.OldestAgeKnown,
		}
		slog.Info("queue_metric", detail...)
	}
	return nil
}

// summarize 算出汇总行的四个数。口径写死在这里，只有一份：
//   - `queue_depth`＝**全部**队列的深度和（积压的总量，含阶梯与终态面）；
//   - `dlq_total`＝只加 `kind=="dlq"` 那一面（阶梯里的消息还没死，算进去会让告警
//     在正常瞬态抖动时也响，而这条告警的语义是「有消息需要人来判」）；
//   - `oldest_message_age_seconds`＝**已知**里最老的那个；一格的年龄都读不到时
//     回 0 且 `oldest_age_known=false`——「0 秒」在告警里是「很健康」，不能拿它冒充「不知道」。
func summarize(rows []QueueStat) (total, dlqTotal int, oldest float64, oldestQueue string, oldestKnown bool) {
	for _, r := range rows {
		total += r.Depth
		if r.Kind == "dlq" {
			dlqTotal += r.Depth
		}
		// 年龄为 0 也算「知道」（刚投进去的那条就是 0 秒），所以这里比的是**有没有已知**，
		// 不是 `> oldest`——用后者会把「全队列都很新鲜」误报成「读不到年龄」。
		if r.Depth > 0 && r.OldestAgeKnown && (!oldestKnown || r.OldestAgeSecs > oldest) {
			oldest, oldestQueue, oldestKnown = r.OldestAgeSecs, r.Queue, true
		}
	}
	return total, dlqTotal, oldest, oldestQueue, oldestKnown
}

// Package outbox 把 outbox_messages 的行搬到 broker（18 §2/§5/§6、17 §2、20 §5）。
//
// 本批**不引 AMQP**（裁决七-6 N7-7）：这里只有投递的编排面 + 一个 Publisher 接口。
// 依据是 21:106 的 Phase C「works without RabbitMQ」与 14:178-183「RabbitMQ 不是
// canonical 事件库」——队列换实现不动这一层，也不动 domain。
//
// 三条不可让的次序（都在这个包上钉住）：
//  1. 认领是一条自成一体的 SQL（FOR UPDATE SKIP LOCKED，18 §5），多 worker 不重复认领；
//  2. **只有 broker 确认之后**才置 published（18 §6、14 §11）；
//  3. 确认写入带租约守卫，0 行意味着这笔的认领权已被接管，按「我没发布成」计数，
//     不算成功也不算异常。
package outbox

import (
	"context"
	"fmt"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// lastErrorMax 是 outbox_messages.last_error 的列宽（db/schema/0003 实测 VARCHAR(512)）。
const lastErrorMax = 512

// Repository 是投递面的池面动作，由 internal/store.Canonical 实现。
//
// sqlc 生成物不出 internal/store（arch_test.go 的不变量 7 闸），所以这里只认
// domain 的投影类型。
type Repository interface {
	ClaimOutboxBatch(ctx context.Context, batchLimit, leaseSeconds int) ([]domain.OutboxClaim, error)
	// MarkOutboxPublished 返回 false＝租约守卫没放行（别的 worker 已接管）。
	MarkOutboxPublished(ctx context.Context, id string) (bool, error)
	MarkOutboxRetry(ctx context.Context, id string,
		retryAfter time.Duration, lastError string) (bool, error)
	MarkOutboxDeadLettered(ctx context.Context, id, lastError string) (bool, error)
}

// Envelope 是给 broker 的投递内容：由 outbox 行的列拼出，payload 不二次封装（N7-5）。
type Envelope struct {
	MessageID     string
	MessageType   string
	SchemaVersion int
	RoutingKey    string
	UserID        string
	CorrelationID string
	CausationID   string
	// TraceID 空串＝这一行产生于无 trace 的上下文；发布侧据此决定写不写 header。
	TraceID      string
	Payload      string
	AttemptCount int
}

// Publisher 把一条消息交给 broker。
//
// 契约：返回 nil 必须意味着 **broker 已确认**（18 §6、14 §11「not marked successfully
// delivered merely because a client library returned without error」）。返回 error
// 一律按可重试处理，重试上限由 outbox 自己的 attempt_count 兜（17 §9 的毒消息
// 不阻塞队列）。
type Publisher interface {
	Publish(ctx context.Context, msg Envelope) error
}

// Result 是一次 RunOnce 的计数面。
type Result struct {
	Claimed    int
	Published  int
	Retried    int
	DeadLetted int
	// Lost：发布成功但确认写入被租约守卫拒绝——消息可能已在 broker 上，
	// 由幂等消费面兜住重复（18 §12：at-least-once，不声称 exactly-once）。
	Lost int
	// Unrecorded：状态写回数据库失败。这一行的投递结果没落账，
	// 它会在租约到期后被重新认领，计数只用于暴露问题。
	Unrecorded int
}

// Worker 是 outbox 的投递编排。策略取自 domain.DeliveryPolicy（批大小/租约/阶梯/上限）。
type Worker struct {
	repo   Repository
	pub    Publisher
	policy domain.DeliveryPolicy
	// OnError 是给进程入口（cmd/…）的日志缝隙：本包不 import log，
	// 否则一个投递编排就绑死一种日志实现。nil＝静默，投递结果本身已进库。
	OnError func(claimID string, stage string, err error)
}

func NewWorker(repo Repository, pub Publisher, policy domain.DeliveryPolicy) *Worker {
	return &Worker{repo: repo, pub: pub, policy: policy}
}

// RunOnce 认领一批并逐条投递。
//
// 认领失败直接返回错误（库都连不上就没有「这一批」可言）；单条投递/写回失败只影响
// 那一条——一批里的毒消息不该让后面 49 条白等一轮租约。
func (w *Worker) RunOnce(ctx context.Context) (Result, error) {
	claims, err := w.repo.ClaimOutboxBatch(ctx, w.policy.BatchSize, w.policy.LeaseSeconds)
	if err != nil {
		return Result{}, fmt.Errorf("outbox: claim: %w", err)
	}
	result := Result{Claimed: len(claims)}
	for _, claim := range claims {
		if err := w.pub.Publish(ctx, EnvelopeOf(claim)); err != nil {
			w.fail(ctx, claim, err, &result)
			continue
		}
		confirmed, err := w.repo.MarkOutboxPublished(ctx, claim.ID)
		if err != nil {
			result.Unrecorded++
			w.report(claim.ID, "mark published", err)
			continue
		}
		if !confirmed {
			result.Lost++
			continue
		}
		result.Published++
	}
	return result, nil
}

// fail 按 14 §8 的阶梯退避，到上限转 dead_lettered（17 §9）。
//
// attempt_count 是**认领时已 ++** 的值，所以「第 attempt 次失败」之后要等的时长
// 由 DeliveryPolicy.RetryDelay(attempt) 给出；是否已经到头，也只看这一个数——
// 不在 SQL 里判级（07:183-186「repository 持久化领域决策，不做决策」）。
func (w *Worker) fail(ctx context.Context, claim domain.OutboxClaim,
	cause error, result *Result) {
	reason := truncate(cause.Error())
	if claim.AttemptCount >= w.policy.MaxAttempts {
		done, err := w.repo.MarkOutboxDeadLettered(ctx, claim.ID, reason)
		if err != nil {
			result.Unrecorded++
			w.report(claim.ID, "dead letter", err)
			return
		}
		if !done {
			result.Lost++
			return
		}
		result.DeadLetted++
		return
	}
	done, err := w.repo.MarkOutboxRetry(ctx, claim.ID,
		w.policy.RetryDelay(claim.AttemptCount), reason)
	if err != nil {
		result.Unrecorded++
		w.report(claim.ID, "mark retry", err)
		return
	}
	if !done {
		result.Lost++
		return
	}
	result.Retried++
}

func (w *Worker) report(claimID, stage string, err error) {
	if w.OnError != nil {
		w.OnError(claimID, stage, err)
	}
}

// Run 常驻轮询：认领到消息就立刻再来一批，空批按 poll 休息。
// ctx 取消时返回 ctx.Err()，认领失败不终止进程（库抖动是可恢复的）。
func (w *Worker) Run(ctx context.Context, poll time.Duration) error {
	if poll <= 0 {
		poll = time.Second
	}
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		result, err := w.RunOnce(ctx)
		if err != nil {
			w.report("", "claim", err)
		}
		if result.Claimed > 0 {
			continue
		}
		if err := sleep(ctx, poll); err != nil {
			return err
		}
	}
}

// EnvelopeOf 把认领行搬成 broker 投递内容。字段一一对应，不在这层加判断。
func EnvelopeOf(claim domain.OutboxClaim) Envelope {
	return Envelope{MessageID: claim.MessageID, MessageType: claim.MessageType,
		SchemaVersion: claim.SchemaVersion, RoutingKey: claim.RoutingKey,
		UserID: claim.UserID, CorrelationID: claim.CorrelationID,
		CausationID: claim.CausationID, TraceID: claim.TraceID, Payload: claim.Payload,
		AttemptCount: claim.AttemptCount}
}

// truncate 兜住 last_error 的 VARCHAR(512)：发布失败的原因可能是任意长度的
// broker 报文，超长截断比让它撞库更可解释（01 §25 的 Failure Containment 同向）。
func truncate(s string) string {
	if len(s) <= lastErrorMax {
		return s
	}
	return s[:lastErrorMax]
}

func sleep(ctx context.Context, d time.Duration) error {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

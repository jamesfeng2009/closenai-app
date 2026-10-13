// DeliveryPolicy 的数值面（N7-9/N7-10/N7-11）。
//
// 与 Evidence/Proactive/Outcome 同一口径（D-4/G-5）：V3 给了形的地方逐字取并标行号，
// V3 没给的地方只作待标定默认值，一律 env 可覆盖，标定后换部署环境变量、代码零改动。
package domain

import "time"

// DeliveryPolicy 是投递面的数值门限。
type DeliveryPolicy struct {
	// BatchSize：一次认领多少条（18 §5「claim batches」+ 20 §5 的 OUTBOX_BATCH_SIZE
	// 环境变量名）。V3 没给数，50 是待标定默认值。
	BatchSize int
	// LeaseSeconds：publishing 租约时长。available_at 一列两用（N7-2），所以它就是
	// 崩溃 worker 的孤儿行被重新认领的等待时间 ⇒ 必须显著大于一次发布超时，
	// 否则慢发布会被并发双投。V3 无量（18 §5 只给 SKIP LOCKED），120s 待标定。
	LeaseSeconds int
	// MaxAttempts：第几次之后转 dead_lettered（17 §9 毒消息不阻塞队列）。
	// V3 14 §8 的阶梯画到第三级就进 DLQ，那是**队列层级**不是投递次数上限；
	// 8 次是本项目给的次数闸（G-5 待标定），阶梯本身按 14 §8 原值。
	MaxAttempts int
	// RetryLadderSeconds：退避阶梯，逐字取 14 §8 的 retry.30s / retry.2m / retry.10m。
	// 超出长度沿用最后一档（「第 4 次起恒 10m」是 14 §8 图里 DLQ 之前的最后一档）。
	RetryLadderSeconds []int
	// SchemaVersion：写进 outbox.schema_version 的值（N7-5，payload 契约版本）。
	SchemaVersion int
	// OutboxRetentionDays / InboxRetentionDays：18 §13 的保留期。V3 没给天数，
	// 30/90 是待标定默认值；inbox 比 outbox 长，理由是 18 §13 的
	// 「旧重投必须仍能被识别为重复」——删早了二次业务副作用就回来了。
	OutboxRetentionDays int
	InboxRetentionDays  int
}

// DefaultDeliveryPolicy 按上面的出处给默认值。
func DefaultDeliveryPolicy() DeliveryPolicy {
	return DeliveryPolicy{
		BatchSize:           envInt("CLOSEN_OUTBOX_BATCH_SIZE", 50),
		LeaseSeconds:        envInt("CLOSEN_OUTBOX_LEASE_SECONDS", 120),
		MaxAttempts:         envInt("CLOSEN_OUTBOX_MAX_ATTEMPTS", 8),
		RetryLadderSeconds:  envInts("CLOSEN_OUTBOX_RETRY_SECONDS", []int{30, 120, 600}),
		SchemaVersion:       envInt("CLOSEN_OUTBOX_SCHEMA_VERSION", outboxSchemaVersion),
		OutboxRetentionDays: envInt("CLOSEN_OUTBOX_RETENTION_DAYS", 30),
		InboxRetentionDays:  envInt("CLOSEN_INBOX_RETENTION_DAYS", 90),
	}
}

// RetryDelay 给出「已经试过 attempt 次」之后要退避多久。attempt 取 outbox 行上
// **认领时已 ++** 的 attempt_count（1＝第一次投递尝试）。
//
// 最后一档之后恒取最后一档，MaxAttempts 的判定在调用方（worker），这里只管时长。
func (p DeliveryPolicy) RetryDelay(attempt int) time.Duration {
	ladder := p.RetryLadderSeconds
	if len(ladder) == 0 {
		return 0
	}
	if attempt < 1 {
		attempt = 1
	}
	index := attempt - 1
	if index >= len(ladder) {
		index = len(ladder) - 1
	}
	return time.Duration(ladder[index]) * time.Second
}

// OutboxLease 把 LeaseSeconds 表达成时长，供 worker 与测试共用一个换算点。
func (p DeliveryPolicy) OutboxLease() time.Duration {
	return time.Duration(p.LeaseSeconds) * time.Second
}

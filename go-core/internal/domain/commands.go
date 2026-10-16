// 命令面（R4-③）：Go 向 Python 下发「去做一次 AI 任务」的请求。
//
// 为什么不并进 `delivery.go:37-46` 那十条表：那张表的进门判据逐字是**两条同时成立**
// （`delivery.go:31-36`），其中第一条「权威模型内容变了」命令根本不满足——下发一条命令
// 只是请求别人算，本身不动权威态。所以命令有独立的一张表、独立的 exchange
// （`closenai.ai.tasks`），但**复用同一条 outbox**：租约、退避阶梯、死信与
// 「broker 确认之后才置 published」（18 §6、N7-10）全都已经在 outbox 那套里跑过测了，
// 给命令另起一条投递通道就是第二套口径——而丢一条命令的代价是「用户永远等不到那条洞察」，
// 不可接受（tasks.md:1136 逐字）。
//
// 追加的是**新事件类型**，十条既有事件的语义与路由一个字节没动 ⇒ additive。
//
// 依赖方向：本文件不 import 任何传输包（`arch_test.go` 的 G-7 闸）；
// exchange／队列名属 `internal/messaging/amqp/topology.go`，这里只给路由键与 message_type 的契约源。
package domain

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
)

// AITaskKind 是四类下发任务。值同时是路由键与 message_type 的中间段，
// 所以它的取值面就是契约面：加一类＝加一条路由＋一个队列（R4 表在 amqp 侧）。
type AITaskKind string

const (
	AITaskObservation AITaskKind = "observation"
	AITaskPattern     AITaskKind = "pattern"
	AITaskInsight     AITaskKind = "insight"
	AITaskProactive   AITaskKind = "proactive"
)

// AITaskKinds 固定枚举顺序：map 遍历无序会让生成的 `contracts/events.md` 每跑一次就漂一版。
var AITaskKinds = []AITaskKind{AITaskObservation, AITaskPattern, AITaskInsight, AITaskProactive}

// RoutingKey 是 AMQP 的 topic 键 `ai.<kind>.requested`。
//
// ⚠️ 命名出处：V3 14 篇只逐字给了**队列名**（14:51-54），`requested` 这套键名是本仓补齐的
// 命名法，已在 `contracts/rpc/ownership.md:88-97` 留痕。本方法是键串的**唯一**生成点，
// amqp 那张命令表引用它，不许在两侧各写一遍字面量。
func (k AITaskKind) RoutingKey() string { return "ai." + string(k) + ".requested" }

// MessageType 写进 `outbox_messages.message_type` 与 AMQP 的 `type` 属性（14 §5）。
// 下划线面与十条事件类型（`fact_admitted` 等）同形，好让消费端只看一个字段就能分类。
func (k AITaskKind) MessageType() string { return "ai_" + string(k) + "_requested" }

func (k AITaskKind) valid() bool {
	for _, one := range AITaskKinds {
		if one == k {
			return true
		}
	}
	return false
}

// AITaskRequest 是一条下发请求的入参面。
type AITaskRequest struct {
	Kind AITaskKind
	// UserID 是这条任务服务于哪个用户——outbox 行带它有两个用处：消费端按用户取上下文，
	// 以及 D-5 的 purge 能连待投消息一起清（`service.go:199-201` 的三列偏离）。
	UserID string
	// OperationID 是**稳定**的逻辑操作边界 id（18 §10），直接成为 outbox 行的 message_id：
	// 同一次请求重放必须给同一个值，重试**不**换新 id。幂等由
	// `outbox_messages.message_id` 的 UNIQUE 兜住——撞了就是调用方把两次操作当成了一次，
	// 仓储按 `service.go:199-201` 的口径不做静默忽略，错误照实传出来。
	OperationID string
	// CorrelationID 可选：一次 HTTP 请求／一轮 LLM 生成的相关标识。
	// 空则回落到 ctx 注入的 envelope（17 §6），再空回落到 OperationID 自指（链头）。
	CorrelationID string
	// Payload 是给 Python 的任务输入体，必须是合法 JSON。
	// 空串归一成 `{}`：与 `emit`（`delivery.go:148-151`）同口径，下游按 JSON 解码，空串不是合法 JSON。
	Payload string
}

// ErrUnknownAITaskKind 是「这一类任务不在契约里」的判定点。
// 宁可在这儿就拒：投出去也是一条无人路由的消息，最后停在死信里没人认领。
var ErrUnknownAITaskKind = errors.New("domain: 未知的 AI 任务类型")

// RequestAITask 在**调用方的决策事务内**入队一条命令（18 §2：写状态与插 outbox 同一笔 COMMIT）。
//
// 为什么不自己开事务（本包其它导出方法都自开）：那条路会让「决策」与「因它而发的命令」
// 分属两笔事务——决策 COMMIT 成功、命令入队失败 ⇒ 权威态变了却没人去算，而且 outbox
// 也补不回来（新事务里重放整笔决策就是第二次生效）。把 st 交给调用方，两件事要么一起成、
// 要么一起回滚，这是 outbox 模式唯一站得住的用法。
//
// 本方法**不写权威事件行**：命令不是「模型内容变了」，写进 personal_model_events
// 就等于把 delivery.go 那条进门判据反着用了。
func (s *Service) RequestAITask(ctx context.Context, st Store, req AITaskRequest) error {
	if err := validateAITaskRequest(req); err != nil {
		return err
	}
	payload := req.Payload
	if payload == "" {
		payload = "{}"
	}
	return st.EnqueueOutbox(ctx, OutboxRow{
		ID: s.newID(), MessageID: req.OperationID,
		MessageType:   req.Kind.MessageType(),
		SchemaVersion: s.gate.Policy.Delivery.SchemaVersion,
		RoutingKey:    req.Kind.RoutingKey(),
		UserID:        req.UserID,
		CorrelationID: s.correlationForCommand(ctx, req),
		CausationID:   causationFor(ctx),
		Payload:       payload,
	})
}

// correlationForCommand 与 `correlationFor`（事件行版）同一条优先级，只是命令没有
// 事件行可回落：链头用 OperationID 自指。
func (s *Service) correlationForCommand(ctx context.Context, req AITaskRequest) string {
	if req.CorrelationID != "" {
		return req.CorrelationID
	}
	if env, ok := EnvelopeFrom(ctx); ok && env.CorrelationID != "" {
		return env.CorrelationID
	}
	return req.OperationID
}

// validateAITaskRequest 只判「这一行能不能成为一条合法投递」，不判业务。
//
// 长度那两条不是防御性冗余：`db/schema/0003_outbox_messages.sql` 给 user_id VARCHAR(36)、
// message_id VARCHAR(128) 且全 NOT NULL。超限在今天是 COMMIT 那一刻的 22001，
// 报错里带着一个 SQLSTATE 而不带「哪个字段、为什么」——在这里判，调用方拿到的是能读的错。
func validateAITaskRequest(req AITaskRequest) error {
	if !req.Kind.valid() {
		return fmt.Errorf("%w: %q（契约面只有 %v）", ErrUnknownAITaskKind,
			string(req.Kind), kindStrings())
	}
	if req.UserID == "" {
		return errors.New("domain: AI 任务缺 user_id（outbox 行 NOT NULL，且 purge 靠它）")
	}
	if len(req.UserID) > 36 {
		return fmt.Errorf("domain: user_id 长 %d 超过列宽 36", len(req.UserID))
	}
	if req.OperationID == "" {
		return errors.New("domain: AI 任务缺 operation_id，它就是 message_id；空值等于取消消费侧幂等（18 §10）")
	}
	if len(req.OperationID) > 128 {
		return fmt.Errorf("domain: operation_id 长 %d 超过 message_id 列宽 128", len(req.OperationID))
	}
	if req.Payload != "" && !json.Valid([]byte(req.Payload)) {
		return fmt.Errorf("domain: AI 任务 payload 不是合法 JSON：%s → %s",
			req.Kind.MessageType(), req.Kind.RoutingKey())
	}
	return nil
}

func kindStrings() []string {
	out := make([]string, 0, len(AITaskKinds))
	for _, k := range AITaskKinds {
		out = append(out, string(k))
	}
	return out
}

// IsCommandRoutingKey 认「这条键是命令面的」。
//
// 存在的理由只有一个：`RoutingKeyFor` 的 ok=false 同时覆盖「不是事件」与「是命令」两种情形，
// 而这两件事的处置完全不同（前者只留事件行，后者是命令入队）。给消费端与闸一个可判的口径，
// 别让调用方去比对字符串前缀。
func IsCommandRoutingKey(routingKey string) bool {
	for _, k := range AITaskKinds {
		if k.RoutingKey() == routingKey {
			return true
		}
	}
	return false
}

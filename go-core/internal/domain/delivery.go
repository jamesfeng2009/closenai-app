// N7 投递面：Outbox 入队 + Inbox 幂等消费 + 保留期清理（V3 18 篇、14 §7/§8、17 §4–§9）。
//
// 口径（裁决七-6 N7）：**at-least-once + 幂等消费，不声称 exactly-once**（18 §12、
// V3 00 §6）。投递行与权威态变更钉在同一个事务里（18 §2 的 BEGIN/写状态/insert outbox/
// COMMIT），所以这里所有写入都走 domain.Store 的事务面；唯一发生在事务之外的是
// 「发布」本身——18 §6：broker 确认之前不得置 published。
//
// 依赖方向不变：本文件不 import 任何传输/驱动包（arch_test.go 的 G-7 闸），
// AMQP 属 O2，Worker 在 internal/messaging/outbox。
package domain

import (
	"context"
	"errors"
	"fmt"
	"time"
)

// outboxSchemaVersion 是投递 payload 的契约版本默认值（N7-5）。payload 取事件行
// Payload 的 JSON 原文；切 protobuf 属 O1，届时 additive 递增并由消费端按
// 14 §7「unsupported schema version」拒收。
const outboxSchemaVersion = 1

// outboxRouting 是「哪些领域事件要跨进程投递」的唯一事实源（N7-3）。
//
// 判据是**两条同时成立**，不是「版本 ++ 了就要投递」：
//  1. 权威态变更（19 §3 权威事务、19 §9「If canonical state changed」）；
//  2. V3 里真的存在跨进程消费面（14 §3 的队列、14 §4 的 routing key、17 §1 的
//     Python 消费者）。
//
// 左列是 personal_model_events.event_type（Go 侧已有事实），右列是 14 §4 的 topic
// routing key。**只有 pattern.created / pattern.updated 是 14:66-70 逐字**，其余按
// 同一命名法本地补齐（七-6 N7-3 已登记为本地口径，不是 V3 原文）。
// 反过来，candidate_* / unknown_admitted / proactive_* / target_completed /
// insight_revalidation_requested 都不进表：前者是「提案被拒/未定」，权威模型内容
// 没变（判据 1 不成立）；后者在 V3 里没有下游队列（判据 2 不成立）。
var outboxRouting = map[string]string{
	"fact_admitted":          "fact.created",
	"pattern_admitted":       "pattern.created",
	"pattern_reactivated":    "pattern.updated",
	"pattern_lifecycle":      "pattern.updated",
	"insight_admitted":       "insight.created",
	"insight_retracted":      "insight.retracted",
	"insight_expired":        "insight.expired",
	"outcome_recorded":       "outcome.recorded",
	"user_feedback_recorded": "feedback.recorded",
	"evidence_withdrawn":     "evidence.withdrawn",
}

// RoutingKeyFor 查事件类型的投递路由。ok=false 表示这个事件只留权威事件行，不入 outbox。
func RoutingKeyFor(eventType string) (string, bool) {
	key, ok := outboxRouting[eventType]
	return key, ok
}

// OutboxRow 是要写进 outbox_messages 的一行。字段面 = 18 §3 的十三列 + N7-1 的
// 三列偏离（user_id 供 D-5 purge、correlation_id/causation_id 供 17 §6/§7 链路追踪）
// + 批 T-B 的第四列偏离 trace_id（异步段续链，工作单 T-2）。
type OutboxRow struct {
	ID string
	// MessageID 是逻辑操作边界的稳定 ID（18 §10）：本项目取事件行 PK，
	// 所以「同一条权威事件」在重试里永远是同一个 message_id（18 §10 末句）。
	MessageID     string
	MessageType   string
	SchemaVersion int
	RoutingKey    string
	UserID        string
	CorrelationID string
	CausationID   string
	// TraceID 是产生这一行的请求 trace（空串＝后台上下文，无 trace 可续）。
	TraceID string
	Payload string
}

// OutboxClaim 是 worker 认领到的一条待投递消息（池面读投影，18 §5）。
// AttemptCount 是**认领时已 ++** 的值：1 表示「这是第一次投递尝试」。
type OutboxClaim struct {
	ID             string
	MessageID      string
	MessageType    string
	SchemaVersion  int
	RoutingKey     string
	UserID         string
	CorrelationID  string
	CausationID    string
	TraceID        string
	Payload        string
	AttemptCount   int
	LeaseExpiresAt time.Time
}

// ── 关联标识（17 §6 correlation / §7 causation）────────────────────────────
//
// Envelope 走 context 而不是函数参数：投递行是在事务深处由 emit 顺手写的，
// 把它塞进每个业务方法的签名会让关联标识变成 N 份重复参数，而漏传一次就断链。

// Envelope 承载一次跨进程调用的关联标识。
type Envelope struct {
	CorrelationID string
	// CausationID 是直接原因那条消息的 message_id；空串＝链头（无父）。
	CausationID string
}

type envelopeKey struct{}

// WithEnvelope 注入关联标识。已有的键值被覆盖——入站消息的 envelope 就是本事务
// 里所有出站行的因果上下文（N7-6）。
func WithEnvelope(ctx context.Context, env Envelope) context.Context {
	return context.WithValue(ctx, envelopeKey{}, env)
}

// EnvelopeFrom 取回注入的关联标识；没有注入时 ok=false（链头，不是错误）。
func EnvelopeFrom(ctx context.Context) (Envelope, bool) {
	env, ok := ctx.Value(envelopeKey{}).(Envelope)
	return env, ok
}

// correlationFor 按 N7-6 的优先级给出 correlation_id：
// ctx 注入 → 事件行的 generation_run_id（一次 LLM 生成的整条链天然同相关）→ 事件行 id（链头自指）。
func (s *Service) correlationFor(ctx context.Context, row EventRow) string {
	if env, ok := EnvelopeFrom(ctx); ok && env.CorrelationID != "" {
		return env.CorrelationID
	}
	if row.GenerationRunID != "" {
		return row.GenerationRunID
	}
	return row.ID
}

// causationFor 取入站消息的 message_id 作为直接原因；不在消费上下文里就是链头。
func causationFor(ctx context.Context) string {
	if env, ok := EnvelopeFrom(ctx); ok {
		return env.CausationID
	}
	return ""
}

// emit 是权威事件行的**唯一**写入入口：写事件行，并按 outboxRouting 决定要不要在同一
// 事务里补一行投递消息（18 §2）。
//
// 为什么是包装而不是把路由判断塞进 AppendEvent 的实现：store「只搬类型不做决策」
// （07:183-186），「这条事件要不要跨进程」是领域结论，必须留在 domain。
//
// payload 的空串归一只作用于投递行：事件行的 Payload 是既有断言的一部分（N3–N6），
// 改它会动别人的口径；而下游按 JSON 解码，空串不是合法 JSON。
func (s *Service) emit(ctx context.Context, st Store, row EventRow) error {
	if err := st.AppendEvent(ctx, row); err != nil {
		return err
	}
	routingKey, ok := RoutingKeyFor(row.EventType)
	if !ok {
		return nil
	}
	payload := row.Payload
	if payload == "" {
		payload = "{}"
	}
	return st.EnqueueOutbox(ctx, OutboxRow{
		ID: s.newID(), MessageID: row.ID, MessageType: row.EventType,
		SchemaVersion: s.gate.Policy.Delivery.SchemaVersion, RoutingKey: routingKey,
		UserID: row.UserID, CorrelationID: s.correlationFor(ctx, row),
		CausationID: causationFor(ctx), TraceID: TraceIDFrom(ctx), Payload: payload,
	})
}

// ── Inbox 幂等消费（18 §7/§8、14 §7、17 §4–§5）─────────────────────────────

// InboundMessage 是一条入站消息的幂等面。字段取自 18 §7（message_id/message_type/consumer）
// 加 17 §6 的 correlation。
//
// 入站消息自己的 causation_id 不在这里：本事务产出的每条投递行，其直接原因都是
// **这条入站消息本身**（msg.MessageID），而不是它的父消息——父链由 broker 的
// envelope 继续往上游带（N7-6）。
// payload 也不在这里：业务回调自己从消息体取，domain 不替消费者解析契约
// （16:114-126 的同一方向）。
type InboundMessage struct {
	MessageID     string
	MessageType   string
	Consumer      string
	CorrelationID string
}

// InboxRow 是 inbox_messages 的写入投影（18 §7 的字段面）。
type InboxRow struct {
	ID          string
	MessageID   string
	MessageType string
	Consumer    string
}

// InboxFailureRow 是永久拒绝的留痕（14 §7 第三类 ACK 的记账面）。
// 它不走 Store：写入发生在主事务**之外**（见 ConsumeIdempotent 的分道说明）。
type InboxFailureRow struct {
	ID          string
	MessageID   string
	MessageType string
	Consumer    string
	ErrorCode   string
}

// InboxStatus 是幂等行的当前终态（18 §7 的 status）。
type InboxStatus struct {
	Status       string
	AttemptCount int
	Found        bool
}

// ConsumeStatus 是本次消费的结论，直接映射 14 §7 的三种 ACK 语义。
type ConsumeStatus string

const (
	// ConsumeProcessed：本次真跑完并落库 ⇒ ACK（14 §7 第 1 项）。
	ConsumeProcessed ConsumeStatus = "processed"
	// ConsumeDuplicate：幂等命中，副作用已跳过 ⇒ ACK（14 §7 第 2 项）。
	ConsumeDuplicate ConsumeStatus = "duplicate"
	// ConsumeTransient：可恢复失败，整笔事务连 inbox 行一起回滚 ⇒ NACK/重投
	// （14 §7「Use only for transient failures」+ 18 §9 case 1「Message can be retried」）。
	ConsumeTransient ConsumeStatus = "transient"
	// ConsumeRejected：永久拒绝且已留痕 ⇒ ACK（14 §7 第 3 项「permanent rejection
	// was recorded according to policy」；不再重投，17 §9 毒消息不阻塞队列）。
	ConsumeRejected ConsumeStatus = "permanent_rejected"
)

// ConsumeResult 是消费结论。Ack 由返回值表达（17 §5：DB COMMIT 之后才 ACK），
// 消费者拿到 Ack==false 必须 NACK/重投，不能吞掉。
type ConsumeResult struct {
	Status ConsumeStatus
	Ack    bool
	Detail string
}

// ErrPermanentMessage 是「这条消息永远不该再投」的分类锚点（14 §7 Reject/DLQ 的
// 四类共用一个码面：非法 payload、不支持的 schema version、永久契约违规、
// 不可恢复的业务输入）。消费回调用它做 errors.Is 判定。
var ErrPermanentMessage = errors.New("domain: permanent message rejection")

// PermanentRejection 是消费回调给出的永久拒绝结论，Code 进 inbox.error_code。
// 它的 Is 方法让它自动满足 errors.Is(err, ErrPermanentMessage)，所以回调只需
// `return domain.PermanentRejection{Code: "..."}`，不必自己拼哨兵。
type PermanentRejection struct {
	Code   string
	Detail string
}

func (r PermanentRejection) Error() string {
	if r.Detail == "" {
		return "domain: permanent message rejection " + r.Code
	}
	return fmt.Sprintf("domain: permanent message rejection %s: %s", r.Code, r.Detail)
}

func (r PermanentRejection) Is(target error) bool { return target == ErrPermanentMessage }

// ConsumeIdempotent 按 18 §8 的事务形状消费一条消息：
//
//	BEGIN
//	  INSERT inbox           -- 0 行＝重复
//	  if duplicate: COMMIT / ACK / return
//	  业务副作用（fn，同一事务）
//	COMMIT → ACK
//
// 两个失败分支按 18 §9 分道：
//   - 瞬时失败：事务回滚，**inbox 行也一起消失**，重投即重来（18 §9 case 1）。
//     所以 DB 侧的 attempt_count 在瞬时路径上没有意义，重试上限归 broker
//     （14 §8 的 retry 阶梯 / 17 §9）；
//   - 永久拒绝：回滚之后在**主事务之外**补一条 failed 留痕（18 §8 的单事务带不走它），
//     然后 ACK 丢弃。
//
// 返回值两看：error 是失败原因（成功/重复时为 nil），ConsumeResult.Ack 才是
// broker 动作的依据——只看 err 会把「已留痕的永久拒绝」误当成要重投。
func (s *Service) ConsumeIdempotent(ctx context.Context, msg InboundMessage,
	fn func(context.Context, Store) error) (ConsumeResult, error) {
	if msg.MessageID == "" || msg.Consumer == "" {
		return ConsumeResult{}, errors.New(
			"domain: consume 需要 message_id 与 consumer（18 §7 的唯一键两面）")
	}
	if fn == nil {
		return ConsumeResult{}, errors.New("domain: consume 需要业务回调")
	}
	// 入站 envelope 决定本次事务里所有出站投递行的因果上下文（N7-6）：
	// correlation 沿用入站的值（没有就是这条消息自己开的链），
	// causation 固定是入站 message_id——本事务产出的每一行都由这条消息直接引起。
	correlation := msg.CorrelationID
	if correlation == "" {
		correlation = msg.MessageID
	}
	ctx = WithEnvelope(ctx, Envelope{CorrelationID: correlation, CausationID: msg.MessageID})

	var outcome struct {
		duplicate bool
		status    InboxStatus
	}
	err := s.db.InTx(ctx, func(ctx context.Context, st Store) error {
		claimed, err := st.InsertInbox(ctx, InboxRow{ID: s.newID(), MessageID: msg.MessageID,
			MessageType: msg.MessageType, Consumer: msg.Consumer})
		if err != nil {
			return err
		}
		if !claimed {
			// 重复投递：先看它的终态再决定（14 §7 把「已消费」和「已记录永久拒绝」
			// 都算 ACK，而 pending 意味着另一笔还开着事务——那既不是重复也不是终局）。
			state, err := st.GetInboxStatus(ctx, msg.MessageID, msg.Consumer)
			if err != nil {
				return err
			}
			outcome.duplicate, outcome.status = true, state
			return nil
		}
		if err := fn(ctx, st); err != nil {
			return err
		}
		return st.MarkInboxProcessed(ctx, msg.MessageID, msg.Consumer)
	})
	if err != nil {
		return s.classifyFailure(ctx, msg, err)
	}
	if outcome.duplicate {
		return s.duplicateVerdict(msg, outcome.status)
	}
	return ConsumeResult{Status: ConsumeProcessed, Ack: true,
		Detail: "副作用与幂等行同事务提交"}, nil
}

// duplicateVerdict 把「为什么这次是重复」翻成 ACK/NACK。
//
// pending 是唯一不能 ACK 的重复：行已存在但没人提交过它。当瞬时失败处理，
// 让 broker 按阶梯重投，比在数据库里猜另一笔的命运安全（17 §5 的 COMMIT→ACK 次序）。
func (s *Service) duplicateVerdict(msg InboundMessage, state InboxStatus) (ConsumeResult, error) {
	switch state.Status {
	case "processed":
		return ConsumeResult{Status: ConsumeDuplicate, Ack: true,
			Detail: fmt.Sprintf("message_id=%s 已消费（attempt=%d）：副作用跳过",
				msg.MessageID, state.AttemptCount)}, nil
	case "failed":
		return ConsumeResult{Status: ConsumeRejected, Ack: true,
			Detail: fmt.Sprintf("message_id=%s 此前已按策略记录永久拒绝：不再重投",
				msg.MessageID)}, nil
	default:
		return ConsumeResult{Status: ConsumeTransient, Ack: false,
			Detail: fmt.Sprintf("message_id=%s 处于 %q：另一笔消费尚未提交，按可重投处理",
				msg.MessageID, state.Status)}, nil
	}
}

// classifyFailure 按 14 §7 给回调失败分道，并把永久拒绝的留痕写到账上。
func (s *Service) classifyFailure(ctx context.Context, msg InboundMessage,
	cause error) (ConsumeResult, error) {
	var rej PermanentRejection
	if !errors.As(cause, &rej) {
		// 瞬时：主事务已回滚（inbox 行不在），重投就是重试（18 §9 case 1）。
		// 失败原因照原样带回，让调用方能记日志/指标——Ack==false 才是它该 NACK 的依据。
		return ConsumeResult{Status: ConsumeTransient, Ack: false,
			Detail: "可恢复失败，事务已回滚：按 NACK/重投处理"}, cause
	}
	code := sanitizeErrorCode(rej.Code)
	if err := s.db.RecordInboxFailure(ctx, InboxFailureRow{
		ID: s.newID(), MessageID: msg.MessageID, MessageType: msg.MessageType,
		Consumer: msg.Consumer, ErrorCode: code}); err != nil {
		// 留痕写不进去就不能声称「已按策略记录永久拒绝」——退回重投，
		// 让下一次投递再尝试记账，而不是把消息在无痕状态下丢掉。
		return ConsumeResult{Status: ConsumeTransient, Ack: false,
			Detail: fmt.Sprintf("永久拒绝但留痕失败（%v）：按可重投处理", err)}, cause
	}
	return ConsumeResult{Status: ConsumeRejected, Ack: true,
		Detail: fmt.Sprintf("永久拒绝已留痕 %s：%s", code, rej.Detail)}, cause
}

// sanitizeErrorCode 兜住 inbox.error_code 的 VARCHAR(64)（N2 DDL 实测列宽）。
// 码由消费回调给出，属系统边界，超长截断比让它撞库更可解释
// （01 §25 的 Failure Containment 同一方向）。
func sanitizeErrorCode(code string) string {
	const max = 64
	if code == "" {
		return "permanent_rejection"
	}
	if len(code) > max {
		return code[:max]
	}
	return code
}

// ── 保留期清理（18 §13）────────────────────────────────────────────────────

// DeliveryCleanupResult 是一次清理搬走了多少行。
type DeliveryCleanupResult struct {
	OutboxDeleted int64
	InboxDeleted  int64
	OutboxBefore  time.Time
	InboxBefore   time.Time
}

// CleanupDeliveryRecords 是投递记录的**唯一**清理入口（N7-11）。
//
// 只清已完成的投递：outbox 的 published、inbox 的 processed。pending/failed/
// publishing/dead_lettered 一律不动——18 §13 的「不要激进删除幂等记录」在这里的
// 落法就是：待办和死信留着排查，只有尘埃落定的行才按保留期搬走。
func (s *Service) CleanupDeliveryRecords(ctx context.Context) (DeliveryCleanupResult, error) {
	policy := s.gate.Policy.Delivery
	out := DeliveryCleanupResult{
		OutboxBefore: s.now().AddDate(0, 0, -policy.OutboxRetentionDays),
		InboxBefore:  s.now().AddDate(0, 0, -policy.InboxRetentionDays),
	}
	err := s.db.InTx(ctx, func(ctx context.Context, st Store) error {
		deleted, err := st.DeletePublishedOutboxBefore(ctx, out.OutboxBefore)
		if err != nil {
			return err
		}
		out.OutboxDeleted = deleted
		deleted, err = st.DeleteProcessedInboxBefore(ctx, out.InboxBefore)
		if err != nil {
			return err
		}
		out.InboxDeleted = deleted
		return nil
	})
	if err != nil {
		return DeliveryCleanupResult{}, err
	}
	return out, nil
}

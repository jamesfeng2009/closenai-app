// Publisher：把 outbox 认领到的一条消息投进 broker，并且**只在 broker 确认之后**才回 nil。
//
// 这条纪律有三个出处，缺一不可：
//   - 14 §11「not marked successfully delivered merely because a client library returned
//     without error」；
//   - 18 §6 与 outbox 自己的口径（`internal/messaging/outbox/outbox.go:51-59`：
//     返回 nil 必须意味着 broker 已确认）；
//   - N7-10（`tasks.md` 的既有登记）：确认之后才 MarkOutboxPublished，未确认则 outbox 仍是重试权威。
//
// 本层**不做重试**：nack／超时／连接断一律回 error，交给 outbox.Worker.fail 走
// domain.DeliveryPolicy 的阶梯（`outbox.go:124-153`）。在这里再写一套退避就是第二套口径。

package amqp

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	amqp091 "github.com/rabbitmq/amqp091-go"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/outbox"
)

// ContentTypeJSON 是 payload 的 MIME（D-R4-1）。
//
// ⚠️ 14 §5 逐字要求 `application/protobuf`，但本仓 outbox 的 payload 今天是**事件行的 JSON 原文**
// （`internal/domain/delivery.go:19-22` 的 N7-5，「切 protobuf 属 O1，届时 additive 递增」）。
// 给 JSON 挂 protobuf 的标签，等于让 R5 的 Python 消费面在 decode 那一刻才发现对面不是 protobuf——
// 把一个当场可判的错推成一次线上解码失败。所以这里如实写 JSON，O1 换协议时连着 schema_version 一起换。
const ContentTypeJSON = "application/json"

// 属性面（14 §5 的六项逐条落地；causation／user_id／attempt 走 headers，
// 因为 AMQP 标准属性里没有 causation 这一格）。
const (
	HeaderSchemaVersion = "x-schema-version"
	HeaderUserID        = "x-user-id"
	HeaderCausationID   = "x-causation-id"
	HeaderAttemptCount  = "x-attempt-count"
)

// 两类失败分开：
//   - ErrUnknownRoute：这个 routing key 不在本表里 ⇒ 属于「我们的部署面写错了」，
//     投出去也是无人路由，所以**一条字节都不发**；
//   - ErrNoConsumerBound：键是已知的十条事件路由之一，但 R4 期故意没绑队列（见
//     topology.go 的 EventQueueFor）⇒ 可重试，且失败原因进 outbox.last_error，
//     逼 R5/R6 必须把消费者绑上，而不是让消息被 broker 静默丢弃后记成「已投递」。
var (
	ErrUnknownRoute      = errors.New("amqp: routing key 不在拓扑表里")
	ErrNoConsumerBound   = errors.New("amqp: 该事件路由没有绑定消费者队列")
	ErrNegativeAck       = errors.New("amqp: broker 回了 nack")
	ErrConfirmTimeoutSet = errors.New("amqp: ConfirmTimeout 必须为正")
)

// Confirmer 是发布需要的通道面：一次发布＋一次确认，别的都不许在这一层做。
//
// 真实现是 ConfirmChannel（下面），它包 `*amqp091.Channel` 的
// PublishWithDeferredConfirmWithContext（channel.go:1589）＋ DeferredConfirmation.WaitContext
// （confirms.go:231）。单测用 fake，于是「nack 不置 published」这件事在无 broker 时也可判。
type Confirmer interface {
	// PublishConfirmed 返回 (acked, err)：acked=false 且 err==nil 表示 broker 明确 nack。
	PublishConfirmed(ctx context.Context, exchange, routingKey string, msg amqp091.Publishing) (bool, error)
}

// ConfirmChannel 是 Confirmer 的生产实现。
type ConfirmChannel struct{ Ch *amqp091.Channel }

// Open 把通道切到 confirm 模式（14 §11 的前提）。必须在第一次发布之前调用。
func (c *ConfirmChannel) Open() error { return c.Ch.Confirm(false) }

func (c *ConfirmChannel) PublishConfirmed(ctx context.Context, exchange, routingKey string,
	msg amqp091.Publishing) (bool, error) {
	dc, err := c.Ch.PublishWithDeferredConfirmWithContext(ctx, exchange, routingKey, false, false, msg)
	if err != nil {
		return false, err
	}
	return dc.WaitContext(ctx)
}

// Options 是发布面的数值。
type Options struct {
	// ConfirmTimeout 是**单条消息**等确认的预算。它必须显著小于 outbox 的
	// LeaseSeconds（domain/delivery_policy.go:37 默认 120s），否则一次慢发布会在
	// 租约到期后被第二个 worker 双投——那条约束写在策略侧，这里只做正值校验，
	// 不在此处发明第二套门限。
	ConfirmTimeout time.Duration
}

// Publisher 实现 outbox.Publisher（接口本体在 outbox.go:57）。
type Publisher struct {
	conf Confirmer
	opts Options
	// now 是为测试留的注入点：timestamp 属性要能在断言里对齐。
	now func() time.Time
}

func NewPublisher(conf Confirmer, opts Options) (*Publisher, error) {
	if opts.ConfirmTimeout <= 0 {
		return nil, ErrConfirmTimeoutSet
	}
	if conf == nil {
		return nil, errors.New("amqp: Confirmer 为 nil")
	}
	return &Publisher{conf: conf, opts: opts, now: func() time.Time { return time.Now().UTC() }}, nil
}

// Publish 投递一条 outbox 消息。
//
// 次序是钉死的：校验 → 定位 exchange → 组装属性 → 发布 → 等确认。
// 「等确认」这一步不许被优化成 fire-and-forget（变异自证 M1 就摘这一步）。
func (p *Publisher) Publish(ctx context.Context, msg outbox.Envelope) error {
	if err := validate(msg); err != nil {
		return err
	}
	exchange, err := exchangeFor(msg.RoutingKey)
	if err != nil {
		return fmt.Errorf("amqp: %s: %w", msg.RoutingKey, err)
	}

	pub := amqp091.Publishing{
		ContentType:   ContentTypeJSON,
		MessageId:     msg.MessageID,
		Type:          msg.MessageType,
		CorrelationId: msg.CorrelationID,
		DeliveryMode:  amqp091.Persistent, // 2＝持久（14 §5 delivery_mode = persistent）
		Timestamp:     p.now(),
		Headers: amqp091.Table{
			HeaderSchemaVersion: msg.SchemaVersion,
			HeaderUserID:        msg.UserID,
			HeaderCausationID:   msg.CausationID,
			HeaderAttemptCount:  msg.AttemptCount,
		},
		Body: []byte(msg.Payload),
	}

	// 每条一个独立预算：ctx 已由 worker 给到整体生命周期，这里收窄到单条确认，
	// 免得一次卡死的确认把整批 50 条的租约一起拖没（domain 策略那句「租约必须显著
	// 大于一次发布超时」就是从这儿来的）。
	cctx, cancel := context.WithTimeout(ctx, p.opts.ConfirmTimeout)
	defer cancel()

	acked, err := p.conf.PublishConfirmed(cctx, exchange, msg.RoutingKey, pub)
	if err != nil {
		return fmt.Errorf("amqp: publish %s: %w", msg.MessageID, err)
	}
	if !acked {
		return fmt.Errorf("amqp: %s → %w", msg.MessageID, ErrNegativeAck)
	}
	return nil
}

// validate 只判「这一行能不能成为一条合法消息」，不判业务。
// message_id 空 ⇒ 消费侧的 inbox 幂等键就没了（18 §10），那比失败更糟是「重复生效」。
func validate(msg outbox.Envelope) error {
	if msg.MessageID == "" {
		return errors.New("amqp: message_id 为空，消费侧无法幂等")
	}
	if msg.RoutingKey == "" {
		return errors.New("amqp: routing_key 为空")
	}
	if msg.MessageType == "" {
		return errors.New("amqp: message_type 为空")
	}
	if msg.Payload == "" {
		return errors.New("amqp: payload 为空")
	}
	if msg.SchemaVersion <= 0 {
		return fmt.Errorf("amqp: schema_version=%d 非法（必须为正，消费端按 14 §7 拒收不支持的版本）", msg.SchemaVersion)
	}
	return nil
}

// exchangeFor 定位这条路由该走哪个 exchange。
//
// 命令面与事件面**同一条 outbox、不同 exchange**（D-R4-3）：判据是
// `domain/delivery.go:31-36` 逐字写死的「权威模型内容变了」——命令不满足它，
// 所以命令键只出现在 CommandRoutes，绝不与那十条重叠（形状闸在 topology_test.go）。
//
// 已知事件路由在 R4 期一律 ErrNoConsumerBound：本表还没有为它们绑队列（原因逐字写在
// topology.go 的 EventBindingGap 注释里）。等 R5/R6 把事件队列加进 CommandRoutes 那样的
// 表行，这条路由就自动可投——**不需要**在本文件加第二个开关。
func exchangeFor(routingKey string) (string, error) {
	for _, r := range allRoutes() {
		// 带 * 的行是**绑定模式**不是投递键：把它当键发出去等于发一个字面量 "candidate.*"，
		// 所以可投递的键只认表里的精确键，通配行只参与拓扑声明。
		if r.RoutingKey == routingKey && !strings.Contains(routingKey, "*") {
			return r.Exchange, nil
		}
	}
	for _, key := range EventRoutingKeys() {
		if key == routingKey {
			return "", ErrNoConsumerBound
		}
	}
	return "", ErrUnknownRoute
}

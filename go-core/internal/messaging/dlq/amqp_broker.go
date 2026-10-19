// amqp091 适配层：把 Broker 那四个方法落到一条真通道上（T2-05）。
//
// 为什么单独一个文件：核心逻辑（dlq.go）只认端口，fake 就能把「peek 必须放回」
// 「purge 必须有确认串」这些判据判红；真通道的所有怪癖（confirm 要 DeferredConfirmation、
// basic.get 的 ok 是第二个返回值而不是错误、x-death 的元素类型是 amqp091.Table）
// 全压在这一层，读核心逻辑的人不需要同时读 amqp091 的签名。
package dlq

import (
	"context"
	"fmt"
	"time"

	amqp091 "github.com/rabbitmq/amqp091-go"
)

// Channel 是本层需要的 amqp091 通道面（*amqp091.Channel 逐字满足）。
// 抽成接口是为了让适配层自己也能在没有 broker 的时候被测到（fake 记录调用序列）。
type Channel interface {
	QueueInspect(name string) (amqp091.Queue, error)
	Get(queue string, autoAck bool) (amqp091.Delivery, bool, error)
	PublishWithDeferredConfirm(exchange, key string, mandatory, immediate bool,
		pub amqp091.Publishing) (*amqp091.DeferredConfirmation, error)
	QueuePurge(queue string, noWait bool) (int, error)
}

// BrokerAdapter 把 Channel 适配成 Broker。confirm 模式由调用方保证（本仓唯一的拨号口
// `amqp.Dial` 已经开好了，见 conn.go 的 `Session.Channel`），本层只负责「没确认就当失败」。
//
// 参数收的是接口而不是 `*amqp091.Channel`：这样 `tools/dlqreplay` 不需要认识 amqp091，
// 适配层自己也能在没有 broker 的时候测（fake 记录调用序列）。
type BrokerAdapter struct {
	ch Channel
	// confirmTimeout 是单条发布的确认预算。它不是 env 键：一个巡检工具需要的
	// 是「别永远挂着」，不是一个可调参数（同 inbound.reconnectWait 的取舍）。
	confirmTimeout time.Duration
}

func NewBrokerAdapter(ch Channel) *BrokerAdapter {
	return &BrokerAdapter{ch: ch, confirmTimeout: 10 * time.Second}
}

func (a *BrokerAdapter) Inspect(_ context.Context, queue string) (int, int, error) {
	q, err := a.ch.QueueInspect(queue)
	if err != nil {
		return 0, 0, fmt.Errorf("dlq: inspect %s: %w", queue, err)
	}
	return q.Messages, q.Consumers, nil
}

func (a *BrokerAdapter) Get(_ context.Context, queue string) (Message, bool, error) {
	d, ok, err := a.ch.Get(queue, false)
	if err != nil {
		return Message{}, false, fmt.Errorf("dlq: get %s: %w", queue, err)
	}
	if !ok {
		// 空队列是**结论**不是错误（与入站面同一口径）；调用方据此收尾。
		return Message{}, false, nil
	}
	headers := make(map[string]any, len(d.Headers))
	for k, v := range d.Headers {
		headers[k] = v
	}
	return Message{
		Body: append([]byte(nil), d.Body...), ContentType: d.ContentType,
		MessageID: d.MessageId, MessageType: d.Type, CorrelationID: d.CorrelationId,
		Timestamp: d.Timestamp, Headers: headers,
		DeliveryRoutingKey: d.RoutingKey, DeliveryExchange: d.Exchange,
		Settle: func(ack, requeue bool) error {
			if ack {
				return d.Ack(false)
			}
			return d.Nack(false, requeue)
		},
	}, true, nil
}

func (a *BrokerAdapter) Publish(ctx context.Context, exchange,
	routingKey string, m Outbound) error {
	pub := amqp091.Publishing{
		ContentType:   m.ContentType,
		MessageId:     m.MessageID,
		Type:          m.MessageType,
		CorrelationId: m.CorrelationID,
		DeliveryMode:  amqp091.Persistent, // 2＝持久：重放出来的那条不许比原件更容易丢
		Headers:       amqp091.Table(m.Headers),
		Body:          m.Body,
	}
	if !m.Timestamp.IsZero() {
		pub.Timestamp = m.Timestamp
	}
	deferred, err := a.ch.PublishWithDeferredConfirm(exchange, routingKey, false, false, pub)
	if err != nil {
		return fmt.Errorf("dlq: publish → %s/%s: %w", exchange, routingKey, err)
	}
	cctx, cancel := context.WithTimeout(ctx, a.confirmTimeout)
	defer cancel()
	acked, err := deferred.WaitContext(cctx)
	if err != nil {
		return fmt.Errorf("dlq: 等 %s 的 broker 确认: %w", m.MessageID, err)
	}
	if !acked {
		// 负确认：exchange 存在但键匹配不到任何绑定时 broker 也会走到这里。
		// 这正是 14 §10／R4-② 集成测跑实过的「静默丢弃」，所以它必须是错误而不是成功。
		return fmt.Errorf("dlq: %s → broker 负确认（exchange=%s key=%s 有没有绑定的认领者？）",
			m.MessageID, exchange, routingKey)
	}
	return nil
}

func (a *BrokerAdapter) Purge(_ context.Context, queue string) (int, error) {
	n, err := a.ch.QueuePurge(queue, false)
	if err != nil {
		return 0, fmt.Errorf("dlq: purge %s: %w", queue, err)
	}
	return n, nil
}

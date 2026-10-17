// 消费面（R5-②）。
//
// R4 只交付了发布面，本仓今天**没有任何** Go 侧消费代码——这个文件就是那条空缺。
// 它只做三件事：设 QoS、开始消费、把 `amqp091.Delivery` 换成 `inbound.Delivery`
// （端口面在 `internal/messaging/inbound`，进程接线只认那个端口，不认 amqp091）。
//
// 三个刻意的选择：
//   - **复用 Dial 出来的那条 confirm 通道**：AMQP 0-9-1 允许同一通道既在 confirm 模式又消费，
//     而入站面今天不需要发布（永久拒绝的留痕走 inbox，见 inbound.Runner.Handle），
//     所以不给它另开一条通道、也不在这里发明第二个 Publisher。
//   - **prefetch 必须为正**：0 在 AMQP 里是「不限」，那会让 broker 把整条队列一次性推给一个
//     单线程消费者，把背压整个关掉。
//   - **goroutine 里的发送可被 ctx 打断**：停机时如果卡在「往自己的 channel 投递」上，
//     进程就永远退不掉。这条被打断的消息**不 ack**，于是它留在 broker 里等下次启动重投——
//     这是 at-least-once 的正确形态，而「退得掉」比「一条消息少处理一次」重要。
package amqp

import (
	"context"
	"fmt"

	amqp091 "github.com/rabbitmq/amqp091-go"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/inbound"
)

// Consume 在既有会话上开始消费 queue。返回值是一条**只读**的交付通道，
// 通道在底层通道或连接关闭时关闭（amqp091 的行为），调用方据此判定「会话死了、该重拨」。
func (s *Session) Consume(ctx context.Context, queue string, prefetch int) (<-chan inbound.Delivery, error) {
	if prefetch <= 0 {
		return nil, fmt.Errorf("amqp: prefetch=%d 非法（0 在 AMQP 里＝不限，不许用这一档）", prefetch)
	}
	if queue == "" {
		return nil, fmt.Errorf("amqp: 消费需要一个队列名")
	}
	// global=false：这条 QoS 只管本通道上的未确认消息，不去动同一连接上的其他通道。
	if err := s.ch.Qos(prefetch, 0, false); err != nil {
		return nil, fmt.Errorf("amqp: qos prefetch=%d on %s: %w", prefetch, queue, err)
	}
	// consumerTag="" ⇒ broker 生成；autoAck=false ⇒ ack 权必须在调用方手里（17 §5 的 COMMIT→ACK）。
	raw, err := s.ch.Consume(queue, "", false, false, false, false, nil)
	if err != nil {
		return nil, fmt.Errorf("amqp: consume %s: %w", queue, err)
	}
	out := make(chan inbound.Delivery)
	go func() {
		defer close(out)
		for d := range raw {
			select {
			case out <- convert(d):
			case <-ctx.Done():
				// 不打扰这一条的 ack：它没被处理，也不该被确认，重投就是它的正确归宿。
				return
			}
		}
	}()
	return out, nil
}

// convert 把 amqp091 的交付搬进端口结构。幂等三件套（message_id/type/correlation_id）走
// 标准属性（14 §5 的六项），user_id／causation／attempt／trace（最后一项是批 T-B 加的）
// 走 headers——分派只有这两类，因为 AMQP 没有 causation／trace 这两格属性
// （见 publisher.go:35-42 的同一取舍）。
func convert(d amqp091.Delivery) inbound.Delivery {
	return inbound.Delivery{
		RoutingKey:    d.RoutingKey,
		MessageID:     d.MessageId,
		MessageType:   d.Type,
		CorrelationID: d.CorrelationId,
		ContentType:   d.ContentType,
		Headers:       d.Headers,
		Body:          d.Body,
		Settle: func(ack, requeue bool) error {
			if ack {
				return d.Ack(false)
			}
			return d.Nack(false, requeue)
		},
	}
}

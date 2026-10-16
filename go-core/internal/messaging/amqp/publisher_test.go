// 发布面的闸（R4-①）。
//
// 这个文件只证明一件事：**回 nil 等于 broker 已确认**，别的都不算。
// 所以断言的重心不在「发出去了」，而在三种「没确认」的形态（nack／通道错／超时）
// 各自都拿到 error，以及四条「根本不该发出一个字节」的输入（校验失败／未知键／
// 已知事件路由但没绑消费者）在 fake 上留下的调用计数是 0——
// 后一类一旦写歪，表现是 outbox 记了「已投递」而消息被 topic exchange 静默丢弃，
// 那是假账，Go 侧永远不会报错。
//
// 无 broker 全跑：Confirmer 是两方法的窄接口。真 broker 上的 ack／nack 归 R4-②。
package amqp

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	amqp091 "github.com/rabbitmq/amqp091-go"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/outbox"
)

type confirmCall struct {
	exchange, routingKey string
	pub                  amqp091.Publishing
}

// fakeConfirmer 记录每一次发布，并可分别置「broker 确认」「通道错误」「卡住不回」。
type fakeConfirmer struct {
	calls []confirmCall
	acked bool
	err   error
	// block＝等到 ctx 结束才返回：用来证明单条确认真有预算，而不是发完就回。
	block bool

	hasDeadline bool
	gotDeadline time.Time
}

func (f *fakeConfirmer) PublishConfirmed(ctx context.Context, exchange, routingKey string,
	msg amqp091.Publishing) (bool, error) {
	f.calls = append(f.calls, confirmCall{exchange: exchange, routingKey: routingKey, pub: msg})
	f.gotDeadline, f.hasDeadline = ctx.Deadline()
	if f.block {
		<-ctx.Done()
		return false, ctx.Err()
	}
	return f.acked, f.err
}

// envelope 是一条合法的命令投递行——命令面是 R4 期唯一真能投出去的一面（见 topology.go
// 的 EventBindingGap），所以「发得成」的用例都拿它当样本。
func commandEnvelope() outbox.Envelope {
	return outbox.Envelope{
		MessageID: "018f9e6c-0000-7000-8000-000000000001", MessageType: "ai_pattern_requested",
		SchemaVersion: 1, RoutingKey: "ai.pattern.requested", UserID: "user-7",
		CorrelationID: "corr-7", CausationID: "caus-7",
		Payload: `{"event_type":"pattern_admitted","user_id":"user-7"}`, AttemptCount: 1,
	}
}

func newTestPublisher(t *testing.T, conf Confirmer, timeout time.Duration) *Publisher {
	t.Helper()
	p, err := NewPublisher(conf, Options{ConfirmTimeout: timeout})
	if err != nil {
		t.Fatalf("NewPublisher: %v", err)
	}
	return p
}

func TestPublishConfirmedReturnsNilAndCarriesEveryV3Property(t *testing.T) {
	conf := &fakeConfirmer{acked: true}
	now := time.Date(2026, 10, 16, 3, 4, 5, 0, time.UTC)
	p := newTestPublisher(t, conf, time.Second)
	p.now = func() time.Time { return now }

	msg := commandEnvelope()
	if err := p.Publish(context.Background(), msg); err != nil {
		t.Fatalf("已确认的发布不该回错：%v", err)
	}
	if len(conf.calls) != 1 {
		t.Fatalf("发布次数=%d，应为 1", len(conf.calls))
	}
	call := conf.calls[0]

	if call.exchange != ExchangeAITasks || call.routingKey != "ai.pattern.requested" {
		t.Errorf("路由定位错：%s / %s", call.exchange, call.routingKey)
	}
	// 14 §5 的六项属性：message_id／type／content_type／correlation_id／timestamp／delivery_mode。
	if call.pub.MessageId != msg.MessageID {
		t.Errorf("message_id（消费侧 inbox 的幂等键）没带上：%q", call.pub.MessageId)
	}
	if call.pub.Type != msg.MessageType {
		t.Errorf("type=%q，应为 %q", call.pub.Type, msg.MessageType)
	}
	if call.pub.ContentType != ContentTypeJSON {
		t.Errorf("content_type=%q，应为 %q（D-R4-1：payload 是 JSON 原文，不许谎报 protobuf）",
			call.pub.ContentType, ContentTypeJSON)
	}
	if call.pub.CorrelationId != msg.CorrelationID {
		t.Errorf("correlation_id=%q，应为 %q（17 §6：一次用户动作全链共用）",
			call.pub.CorrelationId, msg.CorrelationID)
	}
	if !call.pub.Timestamp.Equal(now) {
		t.Errorf("timestamp=%v，应为 %v", call.pub.Timestamp, now)
	}
	if call.pub.DeliveryMode != amqp091.Persistent {
		t.Errorf("delivery_mode=%d，应为 2＝persistent（14 §5）；非持久意味着 broker 重启就丢，"+
			"而 outbox 已按确认置了 published", call.pub.DeliveryMode)
	}
	if string(call.pub.Body) != msg.Payload {
		t.Errorf("body 被二次封装了：%q ≠ payload %q（N7-5：payload 不再包一层 JSON）",
			call.pub.Body, msg.Payload)
	}
	for header, want := range map[string]any{
		HeaderSchemaVersion: msg.SchemaVersion, HeaderUserID: msg.UserID,
		HeaderCausationID: msg.CausationID, HeaderAttemptCount: msg.AttemptCount,
	} {
		if got := call.pub.Headers[header]; got != want {
			t.Errorf("headers[%s]=%v，应为 %v", header, got, want)
		}
	}
}

// 每条**精确**表路由都必须指向自己那一行的 exchange——命令面与候选面分开是 D-R4-3 的落点。
func TestEveryDeclaredRouteResolvesToItsOwnExchange(t *testing.T) {
	conf := &fakeConfirmer{acked: true}
	p := newTestPublisher(t, conf, time.Second)
	exact := 0
	for _, r := range allRoutes() {
		if strings.Contains(r.RoutingKey, "*") {
			continue // 通配行是绑定模式，不是投递键（见 exchangeFor）
		}
		exact++
		msg := commandEnvelope()
		msg.RoutingKey = r.RoutingKey
		if err := p.Publish(context.Background(), msg); err != nil {
			t.Fatalf("%s: %v", r.RoutingKey, err)
		}
		last := conf.calls[len(conf.calls)-1]
		if last.exchange != r.Exchange {
			t.Errorf("%s 发往 %s，表里写的是 %s", r.RoutingKey, last.exchange, r.Exchange)
		}
	}
	if exact != len(allRoutes())-1 {
		t.Errorf("可投递的精确键数=%d，表行=%d ⇒ 通配行的排除逻辑变了", exact, len(allRoutes()))
	}
}

// 通配键不许被当成投递键：把它发出去等于发一个字面量 "candidate.*"，
// 而 Python 侧按精确候选类型投才是这套绑定的用途（拓扑里绑，发布时拒）。
func TestWildcardBindingPatternIsNotPublishable(t *testing.T) {
	for _, r := range allRoutes() {
		if !strings.Contains(r.RoutingKey, "*") {
			continue
		}
		conf := &fakeConfirmer{acked: true}
		p := newTestPublisher(t, conf, time.Second)
		msg := commandEnvelope()
		msg.RoutingKey = r.RoutingKey
		if err := p.Publish(context.Background(), msg); !errors.Is(err, ErrUnknownRoute) {
			t.Errorf("通配行 %s 居然可投：%v", r.RoutingKey, err)
		}
		if len(conf.calls) != 0 {
			t.Errorf("通配行发了 %d 次", len(conf.calls))
		}
	}
}

// TestEveryDomainCommandKindIsPublishableToTaskExchange：`Service.RequestAITask` 会写进
// outbox 行的那四个键，在这一面必须逐字定位到 `closenai.ai.tasks`，一个都不落进事件面。
//
// 两端共用 `domain.AITaskKind` 这一个契约源，所以这里不需要在 amqp 包里 fake 整个
// domain.Store 也能钉住「入队口写的键＝投递面认的键」。反证有两种写法都会红：
// 只改 amqp 表 ⇒ 未知路由、0 次发布；只改 domain ⇒ 同样 0 次发布。
func TestEveryDomainCommandKindIsPublishableToTaskExchange(t *testing.T) {
	conf := &fakeConfirmer{acked: true}
	p := newTestPublisher(t, conf, time.Second)
	for _, kind := range domain.AITaskKinds {
		msg := commandEnvelope()
		msg.RoutingKey = kind.RoutingKey()
		msg.MessageType = kind.MessageType()
		if err := p.Publish(context.Background(), msg); err != nil {
			t.Fatalf("%s: %v", msg.RoutingKey, err)
		}
		last := conf.calls[len(conf.calls)-1]
		if last.exchange != ExchangeAITasks {
			t.Errorf("命令 %s 发往 %s，应为 %s（进事件账本就是假账）",
				msg.RoutingKey, last.exchange, ExchangeAITasks)
		}
		if last.pub.Type != kind.MessageType() {
			t.Errorf("type=%q，消费端按它分类任务，应为 %q", last.pub.Type, kind.MessageType())
		}
	}
	if len(conf.calls) != len(domain.AITaskKinds) {
		t.Errorf("发布次数=%d，任务类型有 %d 类", len(conf.calls), len(domain.AITaskKinds))
	}
}

// broker 明确 nack＝这一条没投递成功。18 §6 与 14 §11 要的就是这一步不许含糊。
func TestNegativeAckIsAnErrorNotASuccess(t *testing.T) {
	conf := &fakeConfirmer{acked: false}
	p := newTestPublisher(t, conf, time.Second)

	err := p.Publish(context.Background(), commandEnvelope())
	if err == nil {
		t.Fatal("nack 之后回了 nil ⇒ outbox 会把这一行记成 published，这是假账")
	}
	if !errors.Is(err, ErrNegativeAck) {
		t.Errorf("错误里丢了 ErrNegativeAck 这个可判的标记：%v", err)
	}
	if len(conf.calls) != 1 {
		t.Errorf("发布调用数=%d，应为 1（nack 是发出去之后才被拒，不是没发）", len(conf.calls))
	}
}

func TestChannelErrorPropagates(t *testing.T) {
	cause := errors.New("connection closed")
	conf := &fakeConfirmer{err: cause}
	p := newTestPublisher(t, conf, time.Second)

	err := p.Publish(context.Background(), commandEnvelope())
	if !errors.Is(err, cause) {
		t.Fatalf("通道错误没透传：%v", err)
	}
	if !strings.Contains(err.Error(), commandEnvelope().MessageID) {
		t.Errorf("错误里要带 message_id，否则 outbox.last_error 没法定位是哪一行：%v", err)
	}
}

// ── 「一个字节都不许发」的三类输入 ──────────────────────────────────────────

func TestUnknownRoutingKeyPublishesNothing(t *testing.T) {
	conf := &fakeConfirmer{acked: true}
	p := newTestPublisher(t, conf, time.Second)

	msg := commandEnvelope()
	msg.RoutingKey = "pattern.created.v2"
	err := p.Publish(context.Background(), msg)
	if !errors.Is(err, ErrUnknownRoute) {
		t.Fatalf("不在拓扑表里的键应回 ErrUnknownRoute，得到 %v", err)
	}
	if len(conf.calls) != 0 {
		t.Fatalf("未知键却发了 %d 次 ⇒ topic exchange 对无绑定的键会静默丢弃并回 ack", len(conf.calls))
	}
}

// 十条事件路由在 R4 期故意没绑队列（topology.go 的 EventBindingGap）。
// 这条闸的意义是「不许把无人消费记成已投递」：R5/R6 把队列绑上之前，
// 这些行只能在 outbox 里退避、并在 last_error 里持续提醒，不能被 ack 抹平。
func TestEventRoutesAreRefusedUntilAConsumerIsBound(t *testing.T) {
	keys := EventRoutingKeys()
	if len(keys) == 0 {
		t.Fatal("EventRoutingKeys 空了 ⇒ domain 那十条表和这层的连线断了")
	}
	for _, key := range keys {
		conf := &fakeConfirmer{acked: true}
		p := newTestPublisher(t, conf, time.Second)

		msg := commandEnvelope()
		msg.RoutingKey = key
		err := p.Publish(context.Background(), msg)
		if !errors.Is(err, ErrNoConsumerBound) {
			t.Errorf("%s：应回 ErrNoConsumerBound，得到 %v", key, err)
		}
		if len(conf.calls) != 0 {
			t.Errorf("%s：发了 %d 次，没人消费的队列一旦被 ack 就成了假账", key, len(conf.calls))
		}
	}
}

func TestInvalidEnvelopesNeverReachTheBroker(t *testing.T) {
	cases := []struct {
		name    string
		mutate  func(*outbox.Envelope)
		warning string
	}{
		{"空 message_id", func(m *outbox.Envelope) { m.MessageID = "" }, "message_id"},
		{"空 routing_key", func(m *outbox.Envelope) { m.RoutingKey = "" }, "routing_key"},
		{"空 type", func(m *outbox.Envelope) { m.MessageType = "" }, "message_type"},
		{"空 payload", func(m *outbox.Envelope) { m.Payload = "" }, "payload"},
		{"schema_version 为 0", func(m *outbox.Envelope) { m.SchemaVersion = 0 }, "schema_version"},
		{"schema_version 为负", func(m *outbox.Envelope) { m.SchemaVersion = -1 }, "schema_version"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			conf := &fakeConfirmer{acked: true}
			p := newTestPublisher(t, conf, time.Second)
			msg := commandEnvelope()
			tc.mutate(&msg)

			err := p.Publish(context.Background(), msg)
			if err == nil {
				t.Fatalf("%s 的行居然投了出去", tc.name)
			}
			if !strings.Contains(err.Error(), tc.warning) {
				t.Errorf("错误里没提 %s，运维面就没法判断撞了哪一列：%v", tc.warning, err)
			}
			if len(conf.calls) != 0 {
				t.Errorf("非法行发了 %d 次", len(conf.calls))
			}
		})
	}
}

func TestNewPublisherRejectsUnusableOptions(t *testing.T) {
	for _, timeout := range []time.Duration{0, -time.Second} {
		if _, err := NewPublisher(&fakeConfirmer{acked: true}, Options{ConfirmTimeout: timeout}); !errors.Is(err, ErrConfirmTimeoutSet) {
			t.Errorf("ConfirmTimeout=%v 应被拒（=0 就是永久阻塞），得到 %v", timeout, err)
		}
	}
	if _, err := NewPublisher(nil, Options{ConfirmTimeout: time.Second}); err == nil {
		t.Error("nil Confirmer 应在构造期就拒掉，而不是第一条消息投递时才炸")
	}
}

// ConfirmTimeout 必须真的作用在「等确认」那一步：卡住的确认要在预算内回 error。
// 这一条摘掉（变异 M1 的另一半）就是「一条卡死的确认把整批 50 条的租约一起拖没」。
func TestConfirmTimeoutCapsTheWait(t *testing.T) {
	conf := &fakeConfirmer{block: true}
	const budget = 20 * time.Millisecond
	p := newTestPublisher(t, conf, budget)

	start := time.Now()
	err := p.Publish(context.Background(), commandEnvelope())
	elapsed := time.Since(start)

	if err == nil {
		t.Fatal("确认卡住却回了 nil")
	}
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Errorf("应透传超时，得到 %v", err)
	}
	if elapsed > time.Second {
		t.Errorf("等了 %v，远超 %v 的预算 ⇒ 单条确认没有独立预算", elapsed, budget)
	}
	if !conf.hasDeadline {
		t.Error("传给 Confirmer 的 ctx 没有 deadline ⇒ 超时是假的")
	}
}

// ── worker × amqp 的联合闸：M1 的靶面 ───────────────────────────────────────
//
// outbox_test.go 用 fakePublisher 证明了「Publisher 回 error 就不置 published」；
// 这里补上另一半：**真的 amqp.Publisher 在 nack／超时下确实回 error**，
// 两半合起来才让「无 confirm 也置 published」这条变异有地方被打红。

type fakeRepo struct {
	claims       []domain.OutboxClaim
	publishedIDs []string
	retries      []retryWrite
	deadLetted   []string
}

type retryWrite struct {
	id         string
	retryAfter time.Duration
	lastError  string
}

func (r *fakeRepo) ClaimOutboxBatch(context.Context, int, int) ([]domain.OutboxClaim, error) {
	claims := r.claims
	r.claims = nil
	return claims, nil
}

func (r *fakeRepo) MarkOutboxPublished(_ context.Context, id string) (bool, error) {
	r.publishedIDs = append(r.publishedIDs, id)
	return true, nil
}

func (r *fakeRepo) MarkOutboxRetry(_ context.Context, id string,
	retryAfter time.Duration, lastError string) (bool, error) {
	r.retries = append(r.retries, retryWrite{id, retryAfter, lastError})
	return true, nil
}

func (r *fakeRepo) MarkOutboxDeadLettered(_ context.Context, id, _ string) (bool, error) {
	r.deadLetted = append(r.deadLetted, id)
	return true, nil
}

func workerPolicy() domain.DeliveryPolicy {
	return domain.DeliveryPolicy{BatchSize: 50, LeaseSeconds: 120, MaxAttempts: 8,
		RetryLadderSeconds: []int{30, 120, 600}, SchemaVersion: 1}
}

func claimFixture(attempt int) domain.OutboxClaim {
	return domain.OutboxClaim{ID: "outbox-row-1",
		MessageID: "msg-1", MessageType: "ai_pattern_requested", SchemaVersion: 1,
		RoutingKey: "ai.pattern.requested", UserID: "user-7", CorrelationID: "corr-7",
		CausationID: "caus-7", Payload: `{"user_id":"user-7"}`, AttemptCount: attempt}
}

func TestWorkerMarksPublishedOnlyAfterBrokerAck(t *testing.T) {
	repo := &fakeRepo{claims: []domain.OutboxClaim{claimFixture(1)}}
	w := outbox.NewWorker(repo, newTestPublisher(t, &fakeConfirmer{acked: true}, time.Second), workerPolicy())

	result, err := w.RunOnce(context.Background())
	if err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if result.Published != 1 || len(repo.publishedIDs) != 1 || repo.publishedIDs[0] != "outbox-row-1" {
		t.Fatalf("已确认的行没记成 published：%+v / %v", result, repo.publishedIDs)
	}
	if len(repo.retries) != 0 || len(repo.deadLetted) != 0 {
		t.Errorf("成功的行不该有退避或死信：%+v / %v", repo.retries, repo.deadLetted)
	}
}

func TestWorkerDoesNotMarkPublishedWhenBrokerNacks(t *testing.T) {
	repo := &fakeRepo{claims: []domain.OutboxClaim{claimFixture(1)}}
	w := outbox.NewWorker(repo, newTestPublisher(t, &fakeConfirmer{acked: false}, time.Second), workerPolicy())

	result, err := w.RunOnce(context.Background())
	if err != nil {
		t.Fatalf("单条投递失败不该让 RunOnce 报错：%v", err)
	}
	if len(repo.publishedIDs) != 0 {
		t.Fatalf("nack 之后仍置了 published（%v）⇒ 变异 M1 的靶面被写穿了", repo.publishedIDs)
	}
	if result.Retried != 1 || len(repo.retries) != 1 {
		t.Fatalf("nack 应走一次退避：result=%+v retries=%v", result, repo.retries)
	}
	if got := repo.retries[0].retryAfter; got != 30*time.Second {
		t.Errorf("第一次失败后退 %v，应为阶梯第一档 30s（14 §8）", got)
	}
	// last_error 必须能看出是 broker 拒的，而不是「通道都没连上」——运维面靠这一格分流。
	if !strings.Contains(repo.retries[0].lastError, ErrNegativeAck.Error()) {
		t.Errorf("last_error 里没留 nack 痕迹：%q", repo.retries[0].lastError)
	}
}

func TestWorkerDoesNotMarkPublishedWhenConfirmTimesOut(t *testing.T) {
	repo := &fakeRepo{claims: []domain.OutboxClaim{claimFixture(1)}}
	w := outbox.NewWorker(repo,
		newTestPublisher(t, &fakeConfirmer{block: true}, 10*time.Millisecond), workerPolicy())

	result, err := w.RunOnce(context.Background())
	if err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if result.Published != 0 || len(repo.publishedIDs) != 0 {
		t.Fatalf("确认超时却记成已投递：result=%+v published=%v", result, repo.publishedIDs)
	}
	if len(repo.retries) != 1 {
		t.Fatalf("超时应走退避，retries=%v", repo.retries)
	}
}

// 毒消息到头转死信（17 §9），而**死信那条路径同样不许顺手置 published**——
// outbox.go 的 fail 里这两次写回是分开的，这里钉住其中一条被走通、另一条没被走。
func TestWorkerDeadLettersAtAttemptCeilingWithoutPublishing(t *testing.T) {
	repo := &fakeRepo{claims: []domain.OutboxClaim{claimFixture(8)}}
	w := outbox.NewWorker(repo, newTestPublisher(t, &fakeConfirmer{acked: false}, time.Second), workerPolicy())

	result, err := w.RunOnce(context.Background())
	if err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if result.DeadLetted != 1 || len(repo.deadLetted) != 1 {
		t.Fatalf("第 8 次失败应转 dead_lettered：result=%+v dead=%v", result, repo.deadLetted)
	}
	if len(repo.publishedIDs) != 0 {
		t.Errorf("转死信的同时置了 published：%v", repo.publishedIDs)
	}
	if len(repo.retries) != 0 {
		t.Errorf("已到上限还写退避，毒消息就会永远退避不进 DLQ：%v", repo.retries)
	}
}

// 一批里的事件行（今天没有消费者）不能把同批的命令行一起拖坏，也不能被记成 published。
func TestUnconsumableEventRowDoesNotTouchItsNeighbour(t *testing.T) {
	event := claimFixture(1)
	event.ID = "outbox-row-event"
	event.RoutingKey = "pattern.created"
	command := claimFixture(1)

	repo := &fakeRepo{claims: []domain.OutboxClaim{event, command}}
	conf := &fakeConfirmer{acked: true}
	w := outbox.NewWorker(repo, newTestPublisher(t, conf, time.Second), workerPolicy())

	result, err := w.RunOnce(context.Background())
	if err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if result.Published != 1 || result.Retried != 1 {
		t.Fatalf("应一条成功一条退避：%+v", result)
	}
	if len(repo.publishedIDs) != 1 || repo.publishedIDs[0] != "outbox-row-1" {
		t.Errorf("published 记到了无人消费的事件行上：%v", repo.publishedIDs)
	}
	if len(conf.calls) != 1 {
		t.Errorf("broker 收到 %d 条，应为 1（事件行那条不许发出）", len(conf.calls))
	}
	if !strings.Contains(repo.retries[0].lastError, ErrNoConsumerBound.Error()) {
		t.Errorf("事件行的 last_error 要写明没消费者，好逼 R5/R6 绑队列：%q", repo.retries[0].lastError)
	}
}

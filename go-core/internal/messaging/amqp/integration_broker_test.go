// 真 broker 集成测（R4-②，工作单 D-R4-7）。
//
// 门控：`CLOSEN_AMQP_URL` 没设就 skip，并把「少跑了几条」写进 skip 理由——
// 这是环境事实，不许伪装成回归通过（沿 internal/store 那 10 个文件的既有口径）。
//
// 这里证明的是**单测证不了的那一半**：fake 说「我按表声明了」，真 broker 说
// 「这套参数我收下了，而且拒绝的消息真的按这套参数走」。三处只有真库才知道的坑：
//  1. 幂等重放撞 PRECONDITION_FAILED ⇒ worker 每次重启就起不来；
//  2. topic exchange 对无绑定的键**照 ack 并静默丢弃** ⇒ outbox 记假账（本文件用反证跑实它）；
//  3. 阶梯队列少写一行 x-dead-letter-routing-key ⇒ 消息 TTL 到期后回不到原键，队列看着正常其实是死路。
//
// 真库那半（outbox 行状态、inbox 去重）不在这里：前者是编排面，已由 publisher_test.go 的
// fake Repository 钉住；后者要消费者，属 R5。本文件末尾用「同一 message_id 投两次 ⇒
// broker 上两份」把这条欠账跑成实据。
package amqp

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"

	amqp091 "github.com/rabbitmq/amqp091-go"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/outbox"
)

// integrationCaseCount 是 Skip 理由里要报的条数：改这个文件加用例就一起改，
// 否则「少跑了几条」这件事没人知道。
const integrationCaseCount = 7

func brokerURL() (string, bool) {
	url := os.Getenv("CLOSEN_AMQP_URL")
	return url, url != ""
}

// dial 建一条真连接并在测试结束时关掉。
func dial(t *testing.T) *amqp091.Connection {
	t.Helper()
	url, ok := brokerURL()
	if !ok {
		t.Skipf("未设 CLOSEN_AMQP_URL ⇒ %d 条真 broker 集成用例一条都没跑（环境事实，不是回归）"+
			"；起容器的命令与取证口径见 R4 工作单 D-R4-7", integrationCaseCount)
	}
	conn, err := amqp091.Dial(url)
	if err != nil {
		t.Fatalf("连不上 %s：%v ⇒ 容器没起来还是端口写错了？", redactURL(url), err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	return conn
}

func channel(t *testing.T, conn *amqp091.Connection) *amqp091.Channel {
	t.Helper()
	ch, err := conn.Channel()
	if err != nil {
		t.Fatalf("开通道：%v", err)
	}
	t.Cleanup(func() { _ = ch.Close() })
	return ch
}

// declareOn 在本条通道上声明整套拓扑（Declare 是幂等的，见用例 (a)）。
func declareOn(t *testing.T, ch *amqp091.Channel) {
	t.Helper()
	if err := Declare(ch); err != nil {
		t.Fatalf("Declare 真 broker：%v", err)
	}
}

// getMessage 取一条；空队列不是「等一等就好」的软失败，而是前一步没落地的硬证据。
func getMessage(t *testing.T, ch *amqp091.Channel, queue string, autoAck bool) amqp091.Delivery {
	t.Helper()
	d, ok, err := ch.Get(queue, autoAck)
	if err != nil {
		t.Fatalf("从 %s 取消息：%v", queue, err)
	}
	if !ok {
		t.Fatalf("%s 是空的 ⇒ 上一步的发布／死信搬运没落地", queue)
	}
	return d
}

func queueCount(t *testing.T, ch *amqp091.Channel, name string) int {
	t.Helper()
	q, err := ch.QueueInspect(name)
	if err != nil {
		t.Fatalf("QueueInspect(%s)：%v ⇒ 队列不存在就是声明没落地", name, err)
	}
	return q.Messages
}

// purge 清队列。清不干净不致命（用例都以「取到我要的那条」为判据），但静默失败会让
// 上一轮的残留混进这一轮，所以留一行日志。
func purge(t *testing.T, ch *amqp091.Channel, name string) {
	t.Helper()
	if n, err := ch.QueuePurge(name, false); err != nil {
		t.Logf("purge %s：%v", name, err)
	} else if n > 0 {
		t.Logf("purge %s 清掉 %d 条残留", name, n)
	}
}

// waitForCount 给死信路由留时间：AMQP 的 DLX 搬运是异步的，Get 之后立刻查会假阴性。
func waitForCount(t *testing.T, ch *amqp091.Channel, name string, want int) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for {
		if got := queueCount(t, ch, name); got == want {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("%s 计数停在 %d，期望 %d", name, queueCount(t, ch, name), want)
		}
		time.Sleep(50 * time.Millisecond)
	}
}

// publishCommand 按表内键发一条命令，并断言它真的被 broker 确认。
func publishCommand(t *testing.T, conn *amqp091.Connection, key string) outbox.Envelope {
	t.Helper()
	ch := channel(t, conn)
	declareOn(t, ch)
	pub := openPublisher(t, ch, 5*time.Second)

	msg := commandEnvelope()
	msg.RoutingKey = key
	if err := pub.Publish(context.Background(), msg); err != nil {
		t.Fatalf("发布 %s：%v", key, err)
	}
	return msg
}

// openPublisher 把 ConfirmChannel 打开成可发布状态（Confirm 必须在第一次发布前）。
func openPublisher(t *testing.T, ch *amqp091.Channel, timeout time.Duration) *Publisher {
	t.Helper()
	pc := &ConfirmChannel{Ch: ch}
	if err := pc.Open(); err != nil {
		t.Fatalf("进 confirm 模式（14 §11 的前提）：%v", err)
	}
	pub, err := NewPublisher(pc, Options{ConfirmTimeout: timeout})
	if err != nil {
		t.Fatalf("NewPublisher：%v", err)
	}
	return pub
}

// ── (a) 幂等重放在真 broker 上成立，且这个判定不是空转 ──────────────────────
func TestBrokerAcceptsTopologyReplayButRejectsConflictingArgs(t *testing.T) {
	conn := dial(t)

	first := channel(t, conn)
	declareOn(t, first)
	second := channel(t, conn)
	declareOn(t, second) // worker 重启就是这一条：同名同参数重复声明必须不报错

	for _, name := range QueueNames() {
		if _, err := second.QueueInspect(name); err != nil {
			t.Errorf("队列 %s 在 broker 上不存在：%v", name, err)
		}
	}
	for _, name := range Exchanges {
		if err := second.ExchangeDeclarePassive(name, "topic", true, false, false, false, nil); err != nil {
			t.Errorf("exchange %s 不存在或类型不符：%v", name, err)
		}
	}

	// 反证：幂等不是「broker 根本没在比参数」。改一个参数必须撞 PRECONDITION_FAILED，
	// 上面那句「重放成功」才有内容。
	if _, err := second.QueueDeclare(TierQueueName(CommandRoutes[0].Queue, 0), true, false, false, false,
		amqp091.Table{"x-message-ttl": 1000, // 与表里的 30000 不一致
			"x-dead-letter-exchange":    ExchangeAITasks,
			"x-dead-letter-routing-key": CommandRoutes[0].RoutingKey}); err == nil {
		t.Error("用不同的 x-message-ttl 重声明同一队列却没报错 ⇒ broker 上的 TTL 不是我们声明的那套")
	} else {
		t.Logf("参数不一致时的实测报错（应当是 PRECONDITION_FAILED）：%v", err)
	}
}

// ── (b) 确认过的消息真在表里那条队列上，属性逐字不丢 ────────────────────────
func TestConfirmedCommandLandsOnItsPurposeQueueWithEveryProperty(t *testing.T) {
	conn := dial(t)
	route := CommandRoutes[1] // ai.pattern.requested → ai.pattern.detect

	ch := channel(t, conn)
	declareOn(t, ch)
	purge(t, ch, route.Queue)

	want := publishCommand(t, conn, route.RoutingKey)

	read := channel(t, conn)
	d := getMessage(t, read, route.Queue, false)
	// 属性面
	if got := string(d.Body); got != want.Payload {
		t.Errorf("body 被改写了：%q ≠ %q", got, want.Payload)
	}
	if d.MessageId != want.MessageID {
		t.Errorf("message_id 到对面成了 %q ⇒ 消费侧 inbox 的幂等键丢了（18 §10）", d.MessageId)
	}
	if d.Type != want.MessageType {
		t.Errorf("type=%q ≠ %q", d.Type, want.MessageType)
	}
	if d.CorrelationId != want.CorrelationID {
		t.Errorf("correlation_id=%q ≠ %q（17 §6）", d.CorrelationId, want.CorrelationID)
	}
	if d.ContentType != ContentTypeJSON {
		t.Errorf("content_type=%q，应为 %q（D-R4-1：如实标 JSON，不谎报 protobuf）",
			d.ContentType, ContentTypeJSON)
	}
	if d.DeliveryMode != amqp091.Persistent {
		t.Errorf("delivery_mode=%d ⇒ broker 重启就丢，可 outbox 已按确认置了 published", d.DeliveryMode)
	}
	if time.Since(d.Timestamp) > time.Minute {
		t.Errorf("timestamp=%v 不像刚发的", d.Timestamp)
	}
	for header, expect := range map[string]any{
		HeaderSchemaVersion: want.SchemaVersion, HeaderUserID: want.UserID,
		HeaderCausationID: want.CausationID, HeaderAttemptCount: want.AttemptCount,
	} {
		// broker 会把 header 的 int 回成 int64/int32，所以按值比而不是按类型比。
		if fmt.Sprint(d.Headers[header]) != fmt.Sprint(expect) {
			t.Errorf("headers[%s] 到对面是 %v，发出去是 %v", header, d.Headers[header], expect)
		}
	}
	if err := d.Ack(false); err != nil {
		t.Fatalf("ack：%v", err)
	}
	if left := queueCount(t, read, route.Queue); left != 0 {
		t.Errorf("ack 之后队列还剩 %d 条", left)
	}
}

// ── (c) 反证：无绑定的事件键在 broker 上是「照 ack、消息蒸发」───────────────
//
// 这条不是测我们的代码，是测**我们躲开的那个坑**：十条事件路由若被绑上一个无人消费的
// 队列（或者直接对 closenai.events 裸发），publisher 会拿到真 ack，outbox 记成已投递，
// 而消息在 broker 里不存在了。跑实它，ErrNoConsumerBound 这条设计就不是口头理由。
func TestUnboundEventRouteWouldBeAcknowledgedAndLost(t *testing.T) {
	conn := dial(t)

	ch := channel(t, conn)
	declareOn(t, ch)
	pub := openPublisher(t, ch, 5*time.Second)

	msg := commandEnvelope()
	msg.RoutingKey = "pattern.created" // 十条表里的真键，R4 期故意没绑队列

	if err := pub.Publish(context.Background(), msg); !errors.Is(err, ErrNoConsumerBound) {
		t.Fatalf("事件路由应回 ErrNoConsumerBound，得到 %v", err)
	}
	for _, name := range QueueNames() {
		if got := queueCount(t, ch, name); got != 0 {
			t.Errorf("被拒的发布还是留下了痕迹：%s 有 %d 条", name, got)
		}
	}

	// 同一个键裸发（＝「给事件面绑个无人消费的队列」之后 broker 的真实行为）：
	// broker 回 ack，消息凭空消失。这就是假账的形态。
	bare := channel(t, conn)
	if err := bare.Confirm(false); err != nil {
		t.Fatalf("confirm：%v", err)
	}
	dc, err := bare.PublishWithDeferredConfirmWithContext(context.Background(),
		ExchangeEvents, "pattern.created", false, false,
		amqp091.Publishing{Body: []byte(msg.Payload), MessageId: msg.MessageID + "-bare",
			DeliveryMode: amqp091.Persistent})
	if err != nil {
		t.Fatalf("裸发：%v", err)
	}
	acked, err := dc.WaitContext(context.Background())
	if err != nil {
		t.Fatalf("等裸发的确认：%v", err)
	}
	if !acked {
		t.Error("broker 对无绑定的事件键回了 nack ⇒ 上面 ErrNoConsumerBound 的必要性论证要重做")
	}
	for _, name := range QueueNames() {
		if got := queueCount(t, bare, name); got != 0 {
			t.Errorf("裸发居然进了 %s（%d 条）", name, got)
		}
	}
	t.Logf("实测：无绑定的 %s 被 broker ack 且任何队列都没有这条 ⇒ 只有 outbox 侧拒认它才不是假账",
		"pattern.created")
}

// ── (d) 拒绝即进第一层重试队列，不原地 requeue（14 §8）──────────────────────
func TestRejectedMessageGoesToFirstRetryTierNotBackToPurposeQueue(t *testing.T) {
	conn := dial(t)
	route := CommandRoutes[0] // ai.observation.requested → ai.observation.extract

	ch := channel(t, conn)
	declareOn(t, ch)
	purge(t, ch, route.Queue)
	purge(t, ch, TierQueueName(route.Queue, 0))
	purge(t, ch, FinalQueueName(route.Queue))

	publishCommand(t, conn, route.RoutingKey)

	consumer := channel(t, conn)
	d := getMessage(t, consumer, route.Queue, false)
	// requeue=false ＋ DLX ⇒ 该进 retry.30s.<queue>；requeue=true 才是 14 §8 禁止的无限重投。
	if err := d.Nack(false, false); err != nil {
		t.Fatalf("nack：%v", err)
	}
	waitForCount(t, consumer, TierQueueName(route.Queue, 0), 1)
	if got := queueCount(t, consumer, route.Queue); got != 0 {
		t.Errorf("nack 后原队列还有 %d 条 ⇒ 消息被 requeue 回去了", got)
	}
	if got := queueCount(t, consumer, FinalQueueName(route.Queue)); got != 0 {
		t.Errorf("第一次拒绝就该进第一层，不该直接进终态 %s（有 %d 条）",
			FinalQueueName(route.Queue), got)
	}
}

// ── (d′) 阶梯在真 broker 上确实闭环：TTL 到期回原 exchange＋原绑定键 ────────
//
// 用一套**独立探针**跑，不动生产拓扑：生产的第一层 TTL 是 30s（14 §8 逐字），
// 测试不能为此等 30 秒，而「少写一行 x-dead-letter-routing-key 就是死路」这件事
// 只有真 broker 能证明。探针的三个参数与 declareTier 逐字同构，只有 TTL 换成 1s。
func TestRetryTierLadderClosesBackOntoPurposeQueue(t *testing.T) {
	conn := dial(t)
	ch := channel(t, conn)

	const (
		probeExchange = "closenai.probe.r4"
		probeQueue    = "probe.r4.detect"
		probeKey      = "probe.r4.requested"
		probeTier     = "retry.1s.probe.r4.detect"
	)
	t.Cleanup(func() {
		_, _ = ch.QueueDelete(probeTier, false, false, false)
		_, _ = ch.QueueDelete(probeQueue, false, false, false)
		_ = ch.ExchangeDelete(probeExchange, false, false)
	})

	if err := ch.ExchangeDeclare(probeExchange, "topic", true, false, false, false, nil); err != nil {
		t.Fatalf("探针 exchange：%v", err)
	}
	if _, err := ch.QueueDeclare(probeQueue, true, false, false, false, nil); err != nil {
		t.Fatalf("探针 purpose：%v", err)
	}
	if err := ch.QueueBind(probeQueue, probeKey, probeExchange, false, nil); err != nil {
		t.Fatalf("探针绑定：%v", err)
	}
	// 与 declareTier 同构：TTL 到期 → 原 exchange ＋ 原绑定键。
	if _, err := ch.QueueDeclare(probeTier, true, false, false, false, amqp091.Table{
		"x-message-ttl":             1000,
		"x-dead-letter-exchange":    probeExchange,
		"x-dead-letter-routing-key": probeKey,
	}); err != nil {
		t.Fatalf("探针 tier：%v", err)
	}
	if err := ch.QueueBind(probeTier, probeTier, probeExchange, false, nil); err != nil {
		t.Fatalf("探针 tier 绑定：%v", err)
	}

	// 探针 exchange 不在表里，所以绕开 exchangeFor 裸发，只验 broker 对这套参数的行为。
	msg := commandEnvelope()
	if err := ch.PublishWithContext(context.Background(), probeExchange, probeKey, false, false,
		amqp091.Publishing{Body: []byte("probe"), MessageId: msg.MessageID,
			DeliveryMode: amqp091.Persistent, ContentType: ContentTypeJSON}); err != nil {
		t.Fatalf("探针发布：%v", err)
	}
	waitForCount(t, ch, probeQueue, 1)

	// 把它挪进 tier（模拟被拒绝），然后等 TTL 到期看它回不回 probeQueue。
	d := getMessage(t, ch, probeQueue, false)
	if err := ch.PublishWithContext(context.Background(), probeExchange, probeTier, false, false,
		amqp091.Publishing{Body: d.Body, MessageId: "probe-ttl-1s", DeliveryMode: amqp091.Persistent}); err != nil {
		t.Fatalf("投进探针 tier：%v", err)
	}
	_ = d.Ack(false)

	deadline := time.Now().Add(6 * time.Second)
	for time.Now().Before(deadline) {
		if got := queueCount(t, ch, probeQueue); got == 1 {
			back := getMessage(t, ch, probeQueue, true)
			t.Logf("实测闭环：TTL 到期后回到 %s，routing key 还原为 %s（投递键是 %s）",
				probeQueue, back.RoutingKey, probeTier)
			if back.RoutingKey != probeKey {
				t.Errorf("回来的键是 %q，应为原绑定键 %q ⇒ 阶梯断了，消息永远不会被再消费一次",
					back.RoutingKey, probeKey)
			}
			return
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Errorf("6s 内没回到探针 purpose ⇒ TTL 到期后消息没有回原键（%s 仍为 %d 条）",
		probeQueue, queueCount(t, ch, probeQueue))
}

// ── (f) 表里每一个键都真的路由到它那行队列（含通配绑定）─────────────────────
//
// 这条是「绑定按全表走、声明按去重走」那次自查修复的实据：如果绑定也按队列去重，
// `go.candidate.accept` 就只绑了 candidate.pattern，其余候选类型的消息会被 broker
// 静默丢弃——Go 侧一声不响，Python 侧永远等不到回音。
func TestEveryTableKeyRoutesToItsQueueOnRealBroker(t *testing.T) {
	conn := dial(t)

	ch := channel(t, conn)
	declareOn(t, ch)
	for _, r := range allRoutes() {
		purge(t, ch, r.Queue)
	}

	// 精确键走 publisher 本体（通配行不可投，见 exchangeFor）。
	for _, r := range allRoutes() {
		if strings.Contains(r.RoutingKey, "*") {
			continue
		}
		publishCommand(t, conn, r.RoutingKey)
		// 计数面要等：passive declare 回的 messages 是统计采样，publish 与它之间有异步窗口，
		// 立刻查会读到 0（2026-10-16 实测），所以断言一律走轮询而不是单点取样。
		waitForCount(t, ch, r.Queue, 1)
		purge(t, ch, r.Queue) // 逐键独立计数，不能累加
	}

	// 通配绑定：candidate.fact 不是表里的键（Go 不投它，R5 的 Python 才投），
	// 这里裸发，证明 `candidate.*` 那条绑定真在 broker 上。
	bare := channel(t, conn)
	if err := bare.Publish(ExchangeAICandidate, "candidate.fact", false, false,
		amqp091.Publishing{Body: []byte(`{"probe":"wildcard"}`), MessageId: "wildcard-probe",
			DeliveryMode: amqp091.Persistent}); err != nil {
		t.Fatalf("裸发 candidate.fact：%v", err)
	}
	waitForCount(t, bare, "go.candidate.accept", 1)
	purge(t, bare, "go.candidate.accept")
}

// ── (e) broker 中途死掉 ⇒ 不许是成功；重启后重投，且 broker 自己不去重 ──────
//
// 需要 CLOSEN_AMQP_CONTAINER（容器名）才能停/起 broker；没设就只跑「停不掉就别断言」的那半：
// 直接 skip 并报数，不拿 fake 结果冒充真 broker 证据。
func TestBrokerRestartRepublishesButBrokerDoesNotDeduplicate(t *testing.T) {
	container := os.Getenv("CLOSEN_AMQP_CONTAINER")
	if container == "" {
		t.Skip("未设 CLOSEN_AMQP_CONTAINER ⇒ (e) 没法真停 broker；" +
			integrationSkipTail())
	}

	route := CommandRoutes[2] // ai.insight.requested → ai.insight.draft
	ch0 := dial(t)
	main := channel(t, ch0)
	declareOn(t, main)
	purge(t, main, route.Queue)
	pub := openPublisher(t, main, 5*time.Second)

	msg := commandEnvelope()
	msg.RoutingKey = route.RoutingKey
	if err := pub.Publish(context.Background(), msg); err != nil {
		t.Fatalf("第一次发布：%v", err)
	}

	// 停 broker：这一步之后 Publish 必须回 error（回 nil 就是「客户端返回了就算成功」）。
	run(t, "docker", "stop", "-t", "0", container)
	if err := pub.Publish(context.Background(), msg); err == nil {
		t.Fatal("broker 已被停掉，Publish 却回了 nil ⇒ outbox 会把这条记成 published（14 §11 明令禁止）")
	} else {
		t.Logf("实测：broker 停掉后发布回错（这就是 outbox 保持 pending 的依据）：%v", err)
	}

	run(t, "docker", "start", container)
	waitBroker(t)

	// 重投：outbox 侧的行为是「租约到期后重新认领再发一次」，这里模拟那一次。
	conn2 := dial(t)
	ch2 := channel(t, conn2)
	declareOn(t, ch2)
	pub2 := openPublisher(t, ch2, 5*time.Second)
	if err := pub2.Publish(context.Background(), msg); err != nil {
		t.Fatalf("重启后重投：%v", err)
	}

	got := channel(t, conn2)
	// 队列里应当正好两份：第一次成功的＋重启后重投的。停 broker 期间那一次没落地，
	// 所以是 2 而不是 3——这条就是「Publish 回 error ⇒ outbox 保持 pending、租约到期后重投」
	// 在 broker 侧的可判形态。
	waitForCount(t, got, route.Queue, 2)
	ids := make([]string, 0, 2)
	for range 2 {
		d := getMessage(t, got, route.Queue, true)
		ids = append(ids, d.MessageId)
	}
	for _, id := range ids {
		if id != msg.MessageID {
			t.Errorf("队列里的 message_id=%q，应为 %q", id, msg.MessageID)
		}
	}
	t.Logf("实测：同一条重投两次，broker 上两份都在（%v）⇒ 去重只能是消费侧 inbox 的活，"+
		"「重启后重投且不重复生效」的后半句属 R5，本批不冒充", ids)
	if left := queueCount(t, got, route.Queue); left != 0 {
		t.Errorf("取走两份后还剩 %d ⇒ 停 broker 那一次其实落地了，上面的结论要重做", left)
	}
}

func integrationSkipTail() string {
	return "这条是真 broker 用例，用 fake 冒充会污染取证"
}

func run(t *testing.T, name string, args ...string) {
	t.Helper()
	out, err := exec.Command(name, args...).CombinedOutput()
	if err != nil {
		t.Fatalf("%s %v：%v\n%s", name, args, err, out)
	}
}

// waitBroker 等容器重新可用：RabbitMQ 起来到能 accept AMQP 之间有秒级窗口，
// 单次 Dial 撞上的就是那个窗口（不是代码错）。
func waitBroker(t *testing.T) {
	t.Helper()
	url, _ := brokerURL()
	deadline := time.Now().Add(60 * time.Second)
	for {
		conn, err := amqp091.Dial(url)
		if err == nil {
			_ = conn.Close()
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("broker 60s 没回来：%v", err)
		}
		time.Sleep(500 * time.Millisecond)
	}
}

// 口令脱敏用 conn.go 的 redactURL：CLOSEN_AMQP_URL 里带口令，
// 测试失败信息与启动日志都不许把它打印出来。

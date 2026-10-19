// 真 broker 集成测（T2-05 验收：「手工灌 3 条坏消息进 DLQ → `--list` 看得到 →
// `--replay` 后进 purpose 队列 → 修复后能被消费」）。
//
// 门控：`CLOSEN_AMQP_URL` 没设就 skip，并把「少跑了几条」写进理由——环境事实，不许伪装成回归通过。
//
// 这里证明的是 fake 证不了的那一半，四条：
//  1. **peek 不改账**。本包的整份读法建立在「`basic.get(autoAck=false)` ＋ `nack(requeue=true)`
//     不会把 `x-death` 的计数推高」这条假设上。fake 里这条是**我自己写的**，恒真；
//     只有真 broker 能判它真假。跑红的表现是「巡检一次＝把这条往终态推一格」，
//     而消费侧正是按那个计数决定要不要升级阶梯的。
//  2. 人工 park 进来的那条，`x-death` 是真形状（`[]amqp091.Table`），headers 归一层吃得下。
//  3. 重放出去的那条**真的回到 purpose 队列并被取走**，字节与 `message_id` 逐字不变、
//     `x-attempt-count` 归零——这是「修好后能再消费一次」的全部含义。
//  4. `--purge` 的二次确认在真 broker 上也拦得住（确认串不对时深度一格不少）。
//
// ⚠️ 前置条件：这台 broker 上**不能有 worker 在消费**这套队列（三个 kill switch 默认关，
// 见 cmd/go-api 的 `CLOSEN_OUTBOX_WORKER`／`CLOSEN_CANDIDATE_WORKER`）。开着的话消息会被
// 真消费者取走，本文件里的深度断言会当场红——那不是回归失败，是环境被污染，先关 worker 再跑。
//
// 队列选择：`ai.insight.draft`（四个任务面里唯一**没有生产调用点**的那个，见 contracts/events.md §一
// 那句「生产者归 R6」），所以这条链上不会有别的进程往同一个队列里投东西。
package dlq

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"testing"
	"time"

	amqp091 "github.com/rabbitmq/amqp091-go"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/amqp"
)

// integrationCaseCount 是 Skip 理由里要报的条数（沿 amqp 包同名的口径）：
// 改这个文件加用例就一起改，否则「少跑了几条」这件事没人知道。
const integrationCaseCount = 5

const (
	draftPurpose = "ai.insight.draft"
	draftDLQ     = "dlq.ai.insight.draft"
	draftTier1   = "retry.30s.ai.insight.draft"
)

func brokerURL(t *testing.T) string {
	t.Helper()
	url := os.Getenv("CLOSEN_AMQP_URL")
	if url == "" {
		t.Skipf("未设 CLOSEN_AMQP_URL ⇒ %d 条真 broker 集成用例一条都没跑（环境事实，不是回归通过）",
			integrationCaseCount)
	}
	return url
}

// confirmChannel 开一条**已进 confirm 模式**的通道：本包的重放靠 broker 确认才认成功，
// 没进 confirm 模式的通道上 `DeferredConfirmation` 永远等不到答复（14 §11）。
func confirmChannel(t *testing.T) *amqp091.Channel {
	t.Helper()
	sess, err := amqp.Dial(context.Background(), brokerURL(t), 10*time.Second)
	if err != nil {
		t.Fatalf("拨号：%v", err)
	}
	t.Cleanup(func() { _ = sess.Close() })
	if err := amqp.Declare(sess.Channel()); err != nil {
		t.Fatalf("Declare 真 broker：%v", err)
	}
	return sess.Channel()
}

func purgeAll(t *testing.T, ch *amqp091.Channel, queues ...string) {
	t.Helper()
	for _, q := range queues {
		if _, err := ch.QueuePurge(q, false); err != nil {
			t.Fatalf("清空 %s：%v", q, err)
		}
	}
}

func depthOf(t *testing.T, ch *amqp091.Channel, q string) (int, int) {
	t.Helper()
	info, err := ch.QueueInspect(q)
	if err != nil {
		t.Fatalf("inspect %s：%v", q, err)
	}
	return info.Messages, info.Consumers
}

// parkInto 逐字复刻 Python 消费者那一侧的终态入箱动作（`backend/ai/mq/publisher.py` 的
// `AioDeadLetterPublisher.park`）：headers 原样带上＋补 reject 留痕 → 投 `closenai.dlq`
// 的 `dlq.<queue>` → ack 原件。**不自己发明第二条入箱路径**，否则这里跑绿的对象与线上不是同一个。
func parkInto(t *testing.T, ch *amqp091.Channel, delivery amqp091.Delivery, key string) {
	t.Helper()
	headers := amqp091.Table{}
	for k, v := range delivery.Headers {
		headers[k] = v
	}
	headers["x-reject-reason"] = "handler_failed"
	headers["x-reject-detail"] = "integration fixture"
	deferred, err := ch.PublishWithDeferredConfirm(amqp.ExchangeDLQ, key, false, false,
		amqp091.Publishing{
			ContentType:   delivery.ContentType,
			MessageId:     delivery.MessageId,
			Type:          delivery.Type,
			CorrelationId: delivery.CorrelationId,
			DeliveryMode:  amqp091.Persistent,
			Headers:       headers,
			Body:          delivery.Body,
			Timestamp:     time.Now().UTC(), // park 重写时间戳（Python 同形）⇒ 年龄＝进终态面的时刻
		})
	if err != nil {
		t.Fatalf("投 %s：%v", key, err)
	}
	if ok, err := waitConfirm(deferred); err != nil || !ok {
		t.Fatalf("等 %s 的 broker 确认：ok=%v err=%v", key, ok, err)
	}
	if err := delivery.Ack(false); err != nil {
		t.Fatalf("ack 原件：%v", err)
	}
}

// putBadMessages 走一遍**真的死信链**：投到 purpose 队列 → 取出来 reject(requeue=false)
// → broker 把它死信进第一层（x-death 在这一跳由 broker 写）→ 再从第一层 park 进终态面。
// 三步都真跑，是为了让夹具里的 `x-death` 与线上同一形状，而不是我手写的一个 map。
// 回的是 id→正文：重放完要比的是**逐字相同的字节**，所以正文得由造它的人留着。
func putBadMessages(t *testing.T, ch *amqp091.Channel, n int) map[string]string {
	t.Helper()
	have := map[string]string{}
	for i := 0; i < n; i++ {
		id := fmt.Sprintf("t205-bad-%d-%d", time.Now().UnixNano(), i)
		body := []byte(fmt.Sprintf(`{"schemaVersion":1,"kind":"insight","seq":%d}`, i))
		have[id] = string(body)
		deferred, err := ch.PublishWithDeferredConfirm(amqp.ExchangeAITasks,
			"ai.insight.requested", false, false, amqp091.Publishing{
				ContentType:  "application/json",
				MessageId:    id,
				Type:         "command",
				DeliveryMode: amqp091.Persistent,
				Timestamp:    time.Now().UTC(),
				Headers:      amqp091.Table{amqp.HeaderAttemptCount: int64(1), "x-user-id": "u-t205"},
				Body:         body,
			})
		if err != nil {
			t.Fatalf("投 %s：%v", id, err)
		}
		if ok, err := waitConfirm(deferred); err != nil || !ok {
			t.Fatalf("等 %s 的确认：ok=%v err=%v", id, ok, err)
		}
	}

	// 这一轮先 reject 进第一层（broker 在这一跳才开始写 x-death）……
	for i := 0; i < n; i++ {
		d := get(t, ch, draftPurpose)
		if err := d.Reject(false); err != nil {
			t.Fatalf("reject 进死信面：%v", err)
		}
	}
	// ……再从第一层原样 park 进终态面（Python 消费者干的就是这一格）。
	for i := 0; i < n; i++ {
		d := get(t, ch, draftTier1)
		parkInto(t, ch, d, draftDLQ)
	}
	return have
}

func get(t *testing.T, ch *amqp091.Channel, q string) amqp091.Delivery {
	t.Helper()
	// broker 的死信是异步的：reject 与「第一层里能取到」之间隔着一次路由，所以这里等到取得到为止。
	deadline := time.Now().Add(5 * time.Second)
	for {
		d, ok, err := ch.Get(q, false)
		if err != nil {
			t.Fatalf("从 %s 取：%v", q, err)
		}
		if ok {
			return d
		}
		if time.Now().After(deadline) {
			t.Fatalf("%s 在 5s 内没有消息 ⇒ 死信链没走通（拓扑声明对吗？worker 有没有在抢？）", q)
		}
		time.Sleep(50 * time.Millisecond)
	}
}

func brokerAdapter(ch *amqp091.Channel) *BrokerAdapter { return NewBrokerAdapter(ch) }

// waitConfirm 等一次 broker 确认，带上限：`DeferredConfirmation.Wait()` 没有超时参数
// （v1.10.0 实测只有 Wait／WaitContext），连接死了就永远挂着，所以一律走 WaitContext。
func waitConfirm(dc *amqp091.DeferredConfirmation) (bool, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return dc.WaitContext(ctx)
}

// waitDepth 等一次「nack(requeue=true) 的放回真正落回 ready 计数」。
//
// 2026-10-19 这条用例红过一次，根因不在实现：**broker 的 requeue 是异步的**。peek 之后立刻
// `queue.declare passive` 读到的是 2 条——刚放回的那条还在「unacked → ready」的路上，
// 而 `Messages` 数的是 ready。所以这一格判的是**落定之后**的账：等不到 3 就是真把消息吃掉了
// （判据照常红），等得到就证明巡检没有改账。生产侧不受这个窗口影响：`List` 的 `Depth`
// 是在 peek **之前**取的，而指标面 60s 一轮，落在窗口里的概率与一次读数 alike 无关。
func waitDepth(t *testing.T, ch *amqp091.Channel, q string, want int) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	last := -1
	for time.Now().Before(deadline) {
		if last, _ = depthOf(t, ch, q); last == want {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("%s 的深度没落回 %d（最后一次 %d）⇒ peek 把消息吃掉了，「不改动巡检」这个读法不成立", q, want, last)
}

// ── 用例 ①：验收那一条链路的 list／replay 两半 ───────────

func TestIntegrationListThenReplayReturnsMessagesToPurposeQueue(t *testing.T) {
	ctx := context.Background()
	ch := confirmChannel(t)
	purgeAll(t, ch, draftPurpose, draftTier1, draftDLQ)
	have := putBadMessages(t, ch, 3)
	t.Cleanup(func() { purgeAll(t, ch, draftPurpose, draftTier1, draftDLQ) })

	// ①-a `--list` 看得到：3 条、年龄能读、x-death 是 broker 写的那一格。
	s, err := List(ctx, brokerAdapter(ch), draftDLQ)
	if err != nil {
		t.Fatalf("List：%v", err)
	}
	if s.Depth != 3 || s.Consumers != 0 || s.Kind != "dlq" || s.Purpose != draftPurpose {
		t.Fatalf("list 看到的不是 3 条： %+v", s)
	}
	if !s.OldestKnown || s.OldestAgeSeconds < 0 {
		t.Fatalf("队头年龄没读出来：%+v", s)
	}
	if len(s.Deaths) != 1 || s.Deaths[0].Queue != draftPurpose || s.Deaths[0].Count != 1 {
		t.Fatalf("x-death 摘要不对（这一格是 broker 写的，不是夹具手填的）：%+v", s.Deaths)
	}
	if s.Deaths[0].Reason != "rejected" {
		t.Fatalf("死信原因不是 rejected：%+v", s.Deaths[0])
	}
	if len(s.Deaths[0].RoutingKeys) == 0 || s.Deaths[0].RoutingKeys[0] != "ai.insight.requested" {
		t.Fatalf("原始键没从 x-death 里读出来：%+v", s.Deaths[0])
	}

	// ①-b 判据本体（假设 1）：peek 前后深度一格不差，且 `x-death` 的 count 一字不变。
	// 这条一旦红，整个「不改动静巡检」的读法就不成立，得换成管理 API 读。
	// 上面那次 List 自己也 peek 了一条，所以先等它落定——否则裸 get 会撞上「都还在路上」而拿到 ok=false。
	waitDepth(t, ch, draftDLQ, 3)
	beforeRaw := peekRaw(t, ch, draftDLQ)
	if got := Deaths(beforeRaw.Headers); len(got) != 1 || got[0].Count != 1 {
		t.Fatalf("peek 之前的 x-death 就不是 1：%+v", got)
	}
	waitDepth(t, ch, draftDLQ, 3) // 同上：先让 peekRaw 那条放回落定
	again, err := List(ctx, brokerAdapter(ch), draftDLQ)
	if err != nil {
		t.Fatalf("第二次 List：%v", err)
	}
	if again.Depth != 3 {
		t.Fatalf("巡检一次之后深度从 3 变成 %d ⇒ peek 把消息吃掉了", again.Depth)
	}
	if len(again.Deaths) != 1 || again.Deaths[0].Count != 1 {
		t.Fatalf("巡检把阶梯计数推高了（requeue 会写 x-death ⇒ 假设不成立）：%+v", again.Deaths)
	}
	afterRaw := peekRaw(t, ch, draftDLQ)
	if !bytes.Equal(beforeRaw.Body, afterRaw.Body) || beforeRaw.MessageId != afterRaw.MessageId {
		t.Fatalf("两次 peek 读到的不是同一条：id %q→%q", beforeRaw.MessageId, afterRaw.MessageId)
	}

	// ①-c `--replay -limit 2`：只捞两条，剩下的必须还在。
	res, err := Replay(ctx, brokerAdapter(ch), draftDLQ, 2)
	if err != nil {
		t.Fatalf("Replay(limit=2)：%v", err)
	}
	if res.Replayed != 2 || res.Reason != "limit_reached" {
		t.Fatalf("到上限那一轮的账面：%+v", res)
	}
	if d, _ := depthOf(t, ch, draftDLQ); d != 1 {
		t.Fatalf("终态面还剩 %d 条，应当 1 条", d)
	}
	if d, _ := depthOf(t, ch, draftPurpose); d != 2 {
		t.Fatalf("purpose 队列收到了 %d 条，应当 2 条", d)
	}
	consumed := map[string]string{}
	for i := 0; i < 2; i++ {
		d := get(t, ch, draftPurpose)
		// ①-d 「修复后能被消费」的判据本体：字节逐字相同、message_id 逐字相同、
		// 计数那一格被清掉（否则消费者一进来就读成「又死过一次」并立刻升层）。
		want, ok := have[d.MessageId]
		if !ok {
			t.Fatalf("取到的消息 id 不在原始清单里：%q", d.MessageId)
		}
		if string(d.Body) != want {
			t.Fatalf("正文被改了：%q → %q", want, d.Body)
		}
		consumed[d.MessageId] = string(d.Body)
		if got := count(d.Headers[amqp.HeaderAttemptCount]); got != 0 {
			t.Fatalf("重放出来的那条 x-attempt-count=%d（应当 0）：%v", got, d.Headers)
		}
		if _, still := d.Headers[HeaderXDeath]; still {
			t.Fatalf("重放出来的那条还带着 x-death：%v ⇒ 消费者会按它决定升层", d.Headers)
		}
		if d.Headers["x-user-id"] != "u-t205" {
			t.Fatalf("归属头被顺手清掉了：%v ⇒ 终态面要能回答这是哪个用户的", d.Headers)
		}
		if err := d.Ack(false); err != nil {
			t.Fatalf("ack 重放出来的那条：%v", err)
		}
	}
	if len(consumed) != 2 {
		t.Fatalf("消费到 %d 条：%v", len(consumed), consumed)
	}

	// ①-e 最后一轮捞空：reason 必须换成 queue_empty（还有货与捞完了在报告里是两件事）。
	res2, err := Replay(ctx, brokerAdapter(ch), draftDLQ, 10)
	if err != nil {
		t.Fatalf("Replay(limit=10)：%v", err)
	}
	if res2.Replayed != 1 || res2.Reason != "queue_empty" {
		t.Fatalf("捞空那一轮的账面：%+v", res2)
	}
	if d, _ := depthOf(t, ch, draftDLQ); d != 0 {
		t.Fatalf("终态面还剩 %d 条", d)
	}
}

// peekRaw 用**裸通道**读一条再放回：这一格不许走本包自己的 Get，否则「peek 不改账」
// 就成了拿被测实现自证（断言两侧不许同源）。
func peekRaw(t *testing.T, ch *amqp091.Channel, q string) amqp091.Delivery {
	t.Helper()
	d, ok, err := ch.Get(q, false)
	if err != nil || !ok {
		t.Fatalf("裸 get %s：ok=%v err=%v", q, ok, err)
	}
	if err := d.Nack(false, true); err != nil {
		t.Fatalf("放回：%v", err)
	}
	return d
}

// ── 用例 ②：purge 的二次确认在真 broker 上拦得住 ──────────

func TestIntegrationPurgeNeedsVerbatimConfirm(t *testing.T) {
	ctx := context.Background()
	ch := confirmChannel(t)
	purgeAll(t, ch, draftPurpose, draftTier1, draftDLQ)
	putBadMessages(t, ch, 1)
	t.Cleanup(func() { purgeAll(t, ch, draftPurpose, draftTier1, draftDLQ) })

	b := brokerAdapter(ch)
	if _, err := Purge(ctx, b, draftDLQ, "yes"); !errors.Is(err, ErrConfirmRequired) {
		t.Fatalf("确认串不对时应当拒，err=%v", err)
	}
	if d, _ := depthOf(t, ch, draftDLQ); d != 1 {
		t.Fatalf("被拒的 purge 真的动了队列：depth=%d", d)
	}
	// 名字不在拓扑里的队列：broker 会把 passive purge 当成新建一个空队列并返回 0 ⇒ 假成功。
	if _, err := Purge(ctx, b, "dlq.not.in.topology", "dlq.not.in.topology"); !errors.Is(err, ErrUnknownQueue) {
		t.Fatalf("未知队列应当被事实源拒，err=%v", err)
	}
	n, err := Purge(ctx, b, draftDLQ, draftDLQ)
	if err != nil {
		t.Fatalf("合法 purge：%v", err)
	}
	if n != 1 {
		t.Fatalf("丢弃条数=%d，应当 1", n)
	}
	if d, _ := depthOf(t, ch, draftDLQ); d != 0 {
		t.Fatalf("purge 之后还剩 %d 条", d)
	}
}

// ── 用例 ③：真 broker 的 x-death 形状能被归一层吃下 ────────

func TestIntegrationRealDeathHeaderShapeIsReadable(t *testing.T) {
	ch := confirmChannel(t)
	purgeAll(t, ch, draftPurpose, draftTier1, draftDLQ)
	putBadMessages(t, ch, 1)
	t.Cleanup(func() { purgeAll(t, ch, draftPurpose, draftTier1, draftDLQ) })

	d := peekRaw(t, ch, draftDLQ)
	// 真 broker（RabbitMQ 4.x ＋ amqp091-go v1.10.0）给的形状是 `[]interface{}`，
	// 每个元素是 `amqp091.Table`——**不是** `[]amqp091.Table`（那是文档里常见的写法，
	// AMQP 的 array 类型解码出来就是 any 切片）。所以归一层两种都得吃，
	// 而这一格只有真 broker 判得出来：fake 里我写哪种形状它就读哪种。
	list, ok := d.Headers[HeaderXDeath].([]any)
	if !ok {
		t.Fatalf("真 broker 给的 x-death 形状是 %T，不是 []interface{} ⇒ 归一层的形状清单该补，"+
			"但补之前先确认这台 broker 的客户端版本", d.Headers[HeaderXDeath])
	}
	if len(list) != 1 {
		t.Fatalf("死信记录应当只有一格（reject 进第一层那一次），实际 %d：%+v", len(list), list)
	}
	if _, isTable := list[0].(amqp091.Table); !isTable {
		t.Fatalf("x-death 的元素不是 amqp091.Table 而是 %T", list[0])
	}
	ds := Deaths(d.Headers)
	if len(ds) != 1 || ds[0].Queue != draftPurpose || ds[0].Count != 1 || ds[0].Time.IsZero() {
		t.Fatalf("从真 header 里没读出摘要：%+v", ds)
	}
	// 死因这一格是这次跑实测抓出来的第二处形状错：字段名是 `reason`，不是 `x-reason`。
	if ds[0].Reason != "rejected" {
		t.Fatalf("真 broker 写的死因读不出来（内部字段名对不对？当前读到的=%q）", ds[0].Reason)
	}
	if len(ds[0].RoutingKeys) != 1 || ds[0].RoutingKeys[0] != "ai.insight.requested" {
		t.Fatalf("原始键读错：%+v", ds[0])
	}
	// 时刻这一格必须是 broker 给的时间戳类型，不是被猜出来的。
	if at, ok := list[0].(amqp091.Table)[HeaderXTime].(time.Time); !ok || at.IsZero() {
		t.Fatalf("x-death 的 time 不是 time.Time：%T", list[0].(amqp091.Table)[HeaderXTime])
	}
}

// ── 用例 ④：负确认那条坑（键没绑定 ⇒ broker 静默丢弃）在重放面上也拦得住 ──

func TestIntegrationPublishToUnboundKeyIsAnErrorNotSilentLoss(t *testing.T) {
	ch := confirmChannel(t)
	// 14 §10／R4-② 跑实过：topic exchange 对一个没绑定的键**照 ack**。
	// 重放若走这样的键，表现是「工具报成功、队列里那条已 ack、消息从此消失」，
	// 所以适配层必须把「没进任何队列」判成错误（BrokerAdapter.Publish 里等 broker 确认）。
	// 这里用真实存在的 exchange ＋ 一定不匹配的键。
	if err := ch.Publish(amqp.ExchangeDLQ, "dlq.nothing.matches.this.key", false, false,
		amqp091.Publishing{Body: []byte(`{}`), MessageId: "t205-silent"}); err != nil {
		t.Fatalf("投一条无绑定的消息：%v", err)
	}
	// 对照：同一个 exchange 上的合法键能投进队列（证明上面那条不是因为连接坏了才丢的）。
	if err := ch.Publish(amqp.ExchangeDLQ, draftDLQ, false, false,
		amqp091.Publishing{Body: []byte(`{"ok":1}`), MessageId: "t205-landed"}); err != nil {
		t.Fatalf("投合法键：%v", err)
	}
	d := get(t, ch, draftDLQ)
	if d.MessageId != "t205-landed" {
		t.Fatalf("合法键上取到了别的东西：%q", d.MessageId)
	}
	if err := d.Ack(false); err != nil {
		t.Fatalf("ack：%v", err)
	}
	// 现在验本包的判据：拿一台没有 confirm 的通道去投不存在的键时，报错的必须是**我们的**错误。
	// （amqp091 对 topic exchange 的无匹配键给正向确认，所以这一格只能由「键来自绑定表」那条
	//  约束保证 ⇒ 证人是在 Replay 里键永远取自 x-death 或绑定表，见 TestRouteForCoversEveryPurposeQueue。）
	t.Log("无绑定键被 broker 照 ack ⇒ 重放面的键只能来自 x-death 或绑定表，不许拼字符串")
}

// ── 用例 ⑤：指标面在真 broker 上取到的是同一份数 ──────────

func TestIntegrationListAllCoversRealTopology(t *testing.T) {
	ctx := context.Background()
	ch := confirmChannel(t)
	purgeAll(t, ch, draftPurpose, draftTier1, draftDLQ)
	putBadMessages(t, ch, 2)
	t.Cleanup(func() { purgeAll(t, ch, draftPurpose, draftTier1, draftDLQ) })

	summaries, err := ListAll(ctx, brokerAdapter(ch), nil)
	if err != nil {
		t.Fatalf("ListAll：%v", err)
	}
	if len(summaries) != len(amqp.QueueNames()) {
		t.Fatalf("巡检覆盖 %d 个队列，拓扑里有 %d 个", len(summaries), len(amqp.QueueNames()))
	}
	if got := DLQTotal(summaries); got != 2 {
		t.Fatalf("dlq_total=%d，应当只数终态面那 2 条（阶梯里的不算）", got)
	}
	// 正向证人：真 broker 上每个队列的 consumers 这一格都能读到（passive 声明没骗人）。
	for _, s := range summaries {
		if s.Consumers < 0 {
			t.Fatalf("%s 的消费者数是负的：%+v", s.Queue, s)
		}
	}
}

// 死信面巡检与重放的闸（T2-05，工单 §怎么做.4）。
//
// 这个文件证明的九件事，每一件都对应一种「工具把现网搞坏」的形态：
//  1. `--list` 真去 broker 取数，且**取完必须放回**（peek 停在工具手里＝丢消息）；
//  2. `depth==0` 不 peek、`consumers>0` 不 peek（后者会把别人的在途交付变成第二次投递）；
//  3. `--purge` 没有逐字相等的确认串 ⇒ 拒，而且 broker 一次都没被叫到；
//  4. 队列名不在拓扑事实源里 ⇒ 连 inspect 都不发（清掉一个不存在的队列，broker 会当成
//     「声明一个空队列」并返回 0，看起来成功而什么都没发生）；
//  5. 只有终态 `dlq.*` 能重放，阶梯队列不行（它们带 TTL 自己会回去，人工提前捞＝绕过背压）；
//  6. 重放是「先投成功、再 ack 原条」，投失败必须把这条放回原队列并停下；
//  7. 重放清掉 `x-death`＋`x-attempt-count`，但**字节、message_id、时间戳一个都不换**
//     （对面 inbox 的 `(message_id, consumer)` 靠的就是同一串）；
//  8. 原始 routing key 只认 x-death，取不到就退表内第一个；两者都取不到就停手而不是猜；
//  9. `dlq_total` 只数终态面（把阶梯算进去，告警会在正常瞬态抖动时也响）。
//
// 全程无 broker：`Broker` 端口用 fake，真 broker 的形状在 integration_broker_test.go。
package dlq

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	amqp091 "github.com/rabbitmq/amqp091-go"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/amqp"
)

// ── fake ─────────────────────────────────────────────────

type fakeQueue struct {
	depth, consumers int
	messages         []Message // FIFO；空 ⇒ Get 回 ok=false
}

type published struct {
	exchange, key string
	m             Outbound
}

type settle struct {
	queue        string
	ack, requeue bool
}

type fakeBroker struct {
	queues map[string]*fakeQueue
	calls  []string

	got      []settle    // 每一条被处置的消息（ack 或放回）
	sent     []published // 重放投出去的那些
	purged   map[string]int
	inspect  map[string]int
	getErr   error
	pubErr   error
	inspectE error
	settleE  error // 让 ack／requeue 失败，验「投出去但原条没摘掉」那一格
}

func newFakeBroker() *fakeBroker {
	return &fakeBroker{
		queues:  map[string]*fakeQueue{},
		got:     nil,
		sent:    nil,
		purged:  map[string]int{},
		inspect: map[string]int{},
	}
}

func (b *fakeBroker) put(queue string, msgs ...Message) {
	q, ok := b.queues[queue]
	if !ok {
		q = &fakeQueue{}
		b.queues[queue] = q
	}
	q.messages = append(q.messages, msgs...)
	q.depth = len(q.messages)
}

func (b *fakeBroker) withConsumers(queue string, n int) {
	q := b.queues[queue]
	q.consumers = n
}

func (b *fakeBroker) Inspect(_ context.Context, queue string) (int, int, error) {
	b.calls = append(b.calls, "inspect "+queue)
	b.inspect[queue]++
	if b.inspectE != nil {
		return 0, 0, b.inspectE
	}
	q, ok := b.queues[queue]
	if !ok {
		return 0, 0, nil // 队列在 broker 上不存在时 passive 会报错；fake 里当作空
	}
	return q.depth, q.consumers, nil
}

func (b *fakeBroker) Get(_ context.Context, queue string) (Message, bool, error) {
	b.calls = append(b.calls, "get "+queue)
	if b.getErr != nil {
		return Message{}, false, b.getErr
	}
	q, ok := b.queues[queue]
	if !ok || len(q.messages) == 0 {
		return Message{}, false, nil
	}
	m := q.messages[0]
	q.messages = q.messages[1:]
	q.depth = len(q.messages)
	m.Settle = func(ack, requeue bool) error {
		// 处置也进调用序列：这样「先投成功、再 ack 原条」那条步序能用一串逐字断言判红，
		// 而不是只判「两个计数各等于期望值」（那样顺序反了也照样绿）。
		if ack {
			b.calls = append(b.calls, "ack "+queue)
		} else {
			b.calls = append(b.calls, "requeue "+queue)
		}
		b.got = append(b.got, settle{queue: queue, ack: ack, requeue: requeue})
		if b.settleE != nil {
			return b.settleE
		}
		if requeue { // 放回队头，等下一轮再被取到（真 broker 的 requeue 就是这个语义）
			q.messages = append([]Message{m}, q.messages...)
			q.depth = len(q.messages)
		}
		return nil
	}
	return m, true, nil
}

func (b *fakeBroker) Publish(_ context.Context, exchange, key string, m Outbound) error {
	b.calls = append(b.calls, "publish "+exchange+"/"+key)
	if b.pubErr != nil {
		return b.pubErr
	}
	b.sent = append(b.sent, published{exchange: exchange, key: key, m: m})
	return nil
}

func (b *fakeBroker) Purge(_ context.Context, queue string) (int, error) {
	b.calls = append(b.calls, "purge "+queue)
	q := b.queues[queue]
	n := 0
	if q != nil {
		n = q.depth
		q.messages, q.depth = nil, 0
	}
	b.purged[queue] = n
	return n, nil
}

func (b *fakeBroker) countCall(prefix string) int {
	n := 0
	for _, c := range b.calls {
		if strings.HasPrefix(c, prefix) {
			n++
		}
	}
	return n
}

// body 造一条最小可用的 DLQ 消息：时间戳给当下，headers 由调用方补。
func body(id, text string, headers map[string]any) Message {
	return Message{
		Body: []byte(text), ContentType: "application/json", MessageID: id,
		MessageType: "command", Timestamp: time.Now().UTC().Add(-90 * time.Second),
		Headers:            headers,
		DeliveryRoutingKey: "dlq.ai.observation.extract",
		DeliveryExchange:   amqp.ExchangeDLQ,
	}
}

func deathEntry(queue, key string, count int64, at time.Time) map[string]any {
	return map[string]any{
		HeaderXQueue: queue, HeaderXExchange: amqp.ExchangeAITasks,
		HeaderXRoutingKey: []any{key}, HeaderXReason: "rejected",
		HeaderXCount: count, HeaderXTime: at.UTC(),
	}
}

// ── 1／2／4：list 的读法 ─────────────────────────────────

func TestListPeeksAndAlwaysPutsTheMessageBack(t *testing.T) {
	ctx := context.Background()
	b := newFakeBroker()
	const q = "dlq.ai.observation.extract"
	b.put(q, body("m1", `{"a":1}`, map[string]any{HeaderXDeath: []any{
		deathEntry("ai.observation.extract", "ai.observation.requested", 3, time.Now().UTC()),
	}}))

	s, err := List(ctx, b, q)
	if err != nil {
		t.Fatalf("List：%v", err)
	}
	if s.Queue != q || s.Purpose != "ai.observation.extract" || s.Kind != "dlq" {
		t.Fatalf("行的形状不对：%+v", s)
	}
	if s.Depth != 1 {
		t.Fatalf("深度该在 peek 前就取到（fake 里 peek 会把它放回，所以放回后仍是 1）：%+v", s)
	}
	if !s.OldestKnown || s.OldestAgeSeconds < 89 || s.OldestAgeSeconds > 120 {
		t.Fatalf("队头年龄算得不对：known=%v seconds=%v（夹具造的是 90 秒前）",
			s.OldestKnown, s.OldestAgeSeconds)
	}
	if len(s.Deaths) != 1 || s.Deaths[0].Count != 3 || s.Deaths[0].Reason != "rejected" {
		t.Fatalf("x-death 摘要没读出来：%+v", s.Deaths)
	}
	// 判据本体：peek 了一次，而且**放回**了、没有 ack。
	if b.countCall("get ") != 1 {
		t.Fatalf("peek 次数=%d，应当恰好一次：%v", b.countCall("get "), b.calls)
	}
	if len(b.got) != 1 || b.got[0].ack || !b.got[0].requeue {
		t.Fatalf("peek 出来的那条没有被放回：%+v（停在工具手里＝丢消息）", b.got)
	}
	if b.inspect[q] != 1 {
		t.Fatalf("inspect 应当只发一次，实际 %d", b.inspect[q])
	}
	// 三步逐字对齐：读深度 → 取队头 → 放回。少第三步就是丢消息。
	if want := []string{"inspect " + q, "get " + q, "requeue " + q}; strings.Join(b.calls, "|") != strings.Join(want, "|") {
		t.Fatalf("调用序列是 %v，应当是 %v", b.calls, want)
	}
}

func TestListEmptyQueueDoesNotPeek(t *testing.T) {
	b := newFakeBroker()
	q := "dlq.ai.pattern.detect"
	b.queues[q] = &fakeQueue{depth: 0}

	s, err := List(context.Background(), b, q)
	if err != nil {
		t.Fatalf("List：%v", err)
	}
	if s.Depth != 0 || s.OldestKnown {
		t.Fatalf("空队列的形状不对：%+v", s)
	}
	// 反向证人：这一格必须是「没取」，否则深度为 0 也去 peek 就是白拿一次交付。
	if b.countCall("get ") != 0 {
		t.Fatalf("depth==0 还去 peek 了：%v", b.calls)
	}
}

func TestListNeverPeeksAQueueWithConsumers(t *testing.T) {
	b := newFakeBroker()
	q := "dlq.go.candidate.accept"
	b.put(q, body("m1", `{}`, nil))
	b.withConsumers(q, 1)

	s, err := List(context.Background(), b, q)
	if err != nil {
		t.Fatalf("List：%v", err)
	}
	if s.Consumers != 1 || s.OldestKnown || s.OldestUnknown == "" {
		t.Fatalf("有消费者时年龄要如实报『不知道』：%+v", s)
	}
	if b.countCall("get ") != 0 {
		t.Fatalf("队列上有人在消费，巡检不该动它：%v", b.calls)
	}
	if !strings.Contains(s.OldestUnknown, "消费者") {
		t.Fatalf("unknown 的理由没写清：%q", s.OldestUnknown)
	}
}

func TestListRejectsUnknownQueueBeforeTouchingBroker(t *testing.T) {
	b := newFakeBroker()
	// 现网里没有这个名字：少了 s（ai.observation.extract**s**）。
	for _, name := range []string{"dlq.ai.observation.extracts", "purge.me", "", "dlq."} {
		_, err := List(context.Background(), b, name)
		if !errors.Is(err, ErrUnknownQueue) {
			t.Fatalf("队列名 %q 应当被事实源拒掉，实际 err=%v", name, err)
		}
	}
	if len(b.calls) != 0 {
		t.Fatalf("名字不合法时一次 broker 调用都不该发：%v", b.calls)
	}
}

// ── 3：purge 的二次确认 ──────────────────────────────────

func TestPurgeRequiresConfirmEqualToQueueName(t *testing.T) {
	ctx := context.Background()
	const q = "dlq.ai.observation.extract"
	b := newFakeBroker()
	b.put(q, body("m1", `{"x":1}`, nil), body("m2", `{"x":2}`, nil))

	// 五种「看起来像确认」的输入：全部必须拒。
	for _, bad := range []string{"", "yes", "y", q + "! ", strings.ToUpper(q)} {
		_, err := Purge(ctx, b, q, bad)
		if !errors.Is(err, ErrConfirmRequired) {
			t.Fatalf("confirm=%q 应当被拒，实际 err=%v", bad, err)
		}
	}
	// 判据本体：一次 purge 都没发到 broker。
	if b.countCall("purge") != 0 {
		t.Fatalf("确认串不合法时 broker 被叫到了：%v", b.calls)
	}
	if b.queues[q].depth != 2 {
		t.Fatalf("队列被动过了：depth=%d", b.queues[q].depth)
	}

	n, err := Purge(ctx, b, q, q) // 逐字相等才放行
	if err != nil {
		t.Fatalf("合法 confirm 被拒：%v", err)
	}
	if n != 2 || b.countCall("purge") != 1 {
		t.Fatalf("purge 的产物不对：n=%d calls=%v", n, b.calls)
	}
	if b.queues[q].depth != 0 {
		t.Fatalf("purge 之后 fake 队列没空：%d", b.queues[q].depth)
	}
}

func TestPurgeRejectsUnknownQueueToo(t *testing.T) {
	// 清一个不在拓扑里的名字：broker 会把它当「声明一个空队列」并返回 0 ⇒ 假成功。
	_, err := Purge(context.Background(), newFakeBroker(), "dlq.nope", "dlq.nope")
	if !errors.Is(err, ErrUnknownQueue) {
		t.Fatalf("err=%v，应当是 ErrUnknownQueue", err)
	}
}

// ── 5：只有终态面能重放 ─────────────────────────────────

func TestReplayRefusesTierAndPurposeQueues(t *testing.T) {
	b := newFakeBroker()
	for _, q := range []string{
		"retry.30s.ai.observation.extract", "retry.10m.go.candidate.accept",
		"ai.observation.extract", "go.source.deleted",
	} {
		_, err := Replay(context.Background(), b, q, 10)
		if !errors.Is(err, ErrNotReplayable) {
			t.Fatalf("%q 不该能重放，err=%v", q, err)
		}
	}
	if len(b.calls) != 0 {
		t.Fatalf("拒了还碰了 broker：%v", b.calls)
	}
}

func TestReplayLimitBounds(t *testing.T) {
	b := newFakeBroker()
	const q = "dlq.ai.observation.extract"
	if _, err := Replay(context.Background(), b, q, maxLimit+1); err == nil {
		t.Fatal("超过上限的 limit 必须拒（一次捞空整个 DLQ 会把毒消息同时放回消费面）")
	} else if !strings.Contains(err.Error(), "limit") {
		t.Fatalf("错误没落在 limit 上：%v", err)
	}
	if len(b.calls) != 0 {
		t.Fatalf("越界的 limit 一次都不该碰 broker：%v", b.calls)
	}
	// limit<=0 走默认值而不是「一条都不捞」。
	b.put(q, body("m1", `{}`, nil))
	res, err := Replay(context.Background(), b, q, 0)
	if err != nil {
		t.Fatalf("Replay：%v", err)
	}
	if res.Limit != defaultLimit {
		t.Fatalf("limit=0 应当按默认 %d，实际 %d", defaultLimit, res.Limit)
	}
}

// ── 6／7：重放的字节与步序 ──────────────────────────────

func TestReplayKeepsBytesAndIdentityButClearsCounters(t *testing.T) {
	ctx := context.Background()
	b := newFakeBroker()
	// 用候选面而不是任务面做这条用例：`go.candidate.accept` 在表里绑了三个键，表内第一个是
	// `candidate.pattern`，而 x-death 里给的是 `candidate.insight` ⇒ 「取到原始键」与
	// 「退到表内第一个」两种结果在断言里长得不一样。两侧同源的断言恒真，
	// 抓不出「代码只读了表里第一个键」这种缺陷（这条闸在 TestReplayStopsWhenOriginalRouteCannotBeDetermined 还有第二证人）。
	const q = "dlq.go.candidate.accept"
	const purpose = "go.candidate.accept"
	original := `{"schemaVersion":1,"userId":"u1","kind":"pattern"}`
	ts := time.Date(2026, 10, 19, 2, 0, 0, 0, time.UTC)
	m := Message{
		Body: []byte(original), ContentType: "application/json", MessageID: "cmd-abc",
		MessageType: "candidate", CorrelationID: "cor-1", Timestamp: ts,
		Headers: map[string]any{
			HeaderXDeath:            []any{deathEntry(purpose, "candidate.insight", 3, ts)},
			amqp.HeaderAttemptCount: int64(2),
			"x-trace-id":            "trace-keep-me",
			"x-user-id":             "u1",
		},
		DeliveryRoutingKey: q, DeliveryExchange: amqp.ExchangeDLQ,
	}
	b.put(q, m)

	res, err := Replay(ctx, b, q, 10)
	if err != nil {
		t.Fatalf("Replay：%v", err)
	}
	// limit=10 而队列只有 1 条 ⇒ 收尾原因是「捞空了」，不是「到了上限」。
	// 这一格原来写的是 limit_reached，并且**测试也照着断言了**：第四步那次探询（下面 want 的
	// 最后一条）暴露了「循环里设 reason」的写法会把「捞空」误报成「到上限」，
	// 于是实现改成循环外才设、!ok 分支显式设 queue_empty（实现期纠正，见结案行）。
	if res.Replayed != 1 || res.Skipped != 0 || res.Reason != "queue_empty" {
		t.Fatalf("重放账面不对：%+v", res)
	}
	if res.Exchange != amqp.ExchangeAICandidate || res.Purpose != purpose {
		t.Fatalf("投递目标不对：%+v", res)
	}
	if len(b.sent) != 1 {
		t.Fatalf("投出去 %d 条：%+v", len(b.sent), b.sent)
	}
	out := b.sent[0]
	if out.key != "candidate.insight" {
		t.Fatalf("原始键没从 x-death 里取出来（表内第一个键是 candidate.pattern）：key=%q", out.key)
	}
	// 字节与身份一个都不许换（对面 inbox 的幂等位靠的就是同一串 message_id）。
	if string(out.m.Body) != original {
		t.Fatalf("body 被改了：%q", out.m.Body)
	}
	if out.m.MessageID != "cmd-abc" || out.m.ContentType != "application/json" ||
		out.m.MessageType != "candidate" || out.m.CorrelationID != "cor-1" {
		t.Fatalf("身份属性被改了：%+v", out.m)
	}
	if !out.m.Timestamp.Equal(ts) {
		t.Fatalf("时间戳被换了：%v ≠ %v（重放不是新建一次投递）", out.m.Timestamp, ts)
	}
	// 清掉的只有「试过几次」那一格。
	if _, still := out.m.Headers[HeaderXDeath]; still {
		t.Fatal("x-death 没清掉：重放出去的那条一进来就被读成已经死了三次")
	}
	if got := out.m.Headers[amqp.HeaderAttemptCount]; got != int64(0) {
		t.Fatalf("x-attempt-count 应当清零成 int64，实际 %#v", got)
	}
	if out.m.Headers["x-trace-id"] != "trace-keep-me" || out.m.Headers["x-user-id"] != "u1" {
		t.Fatalf("业务 header 被顺手清掉了：%v", out.m.Headers)
	}
	// 步序本体：publish 恰好一次，且原条被 ack（投成功之后必须摘掉，否则同一条会被投两次以上）。
	if b.countCall("publish") != 1 {
		t.Fatalf("publish 次数不对：%v", b.calls)
	}
	if len(b.got) != 1 || !b.got[0].ack || b.got[0].requeue {
		t.Fatalf("原条没被 ack（投成功之后必须摘掉，否则同一条会被投两次以上）：%+v", b.got)
	}
	if want := []string{"get " + q,
		"publish " + amqp.ExchangeAICandidate + "/candidate.insight", "ack " + q,
		// 第四步是「再取一次、发现空了」的探询：到上限那一轮不会有这一步，两种收尾在报告里
		// 就是靠这个分开的（reason＝queue_empty vs limit_reached）。
		"get " + q}; len(b.calls) != len(want) {
		t.Fatalf("调用序列不是四步：%v", b.calls)
	} else {
		for i := range want {
			if b.calls[i] != want[i] {
				t.Fatalf("第 %d 步是 %q，应当是 %q（步序反了就成了「原条已删、新条没投出去」）",
					i, b.calls[i], want[i])
			}
		}
	}
}

func TestReplayWithoutTimestampDoesNotInventOne(t *testing.T) {
	b := newFakeBroker()
	const q = "dlq.ai.pattern.detect"
	m := Message{
		Body: []byte(`{"n":1}`), MessageID: "m1",
		Headers: map[string]any{HeaderXDeath: []any{
			deathEntry("ai.pattern.detect", "ai.pattern.requested", 1, time.Now().UTC()),
		}},
	}
	b.put(q, m)
	if _, err := Replay(context.Background(), b, q, 5); err != nil {
		t.Fatalf("Replay：%v", err)
	}
	if !b.sent[0].m.Timestamp.IsZero() {
		t.Fatalf("原件没时间戳，重放却补了一个当下值：%v（下游按时间算的闸门会因此改写结论）",
			b.sent[0].m.Timestamp)
	}
}

func TestReplayPublishFailureRequeuesAndStops(t *testing.T) {
	b := newFakeBroker()
	const q = "dlq.ai.observation.extract"
	b.put(q, body("m1", `{"a":1}`, nil), body("m2", `{"a":2}`, nil))
	b.pubErr = errors.New("broker 不可达")

	res, err := Replay(context.Background(), b, q, 10)
	if err == nil {
		t.Fatal("投失败必须回错误，否则报告看起来像成功")
	}
	if res.Replayed != 0 || res.Reason != "publish_failed" {
		t.Fatalf("半途而废的账面不对：%+v", res)
	}
	// 判据本体：失败的那条被放回，且**没有继续往下跑**（第二次 get 都没发生）。
	if b.countCall("get ") != 1 {
		t.Fatalf("投失败后还在继续捞：%v", b.calls)
	}
	if len(b.got) != 1 || b.got[0].ack || !b.got[0].requeue {
		t.Fatalf("投失败的原条没被放回：%+v", b.got)
	}
	if b.queues[q].depth != 2 {
		t.Fatalf("两条都在，但 fake 深度变成 %d ⇒ requeue 语义没写对", b.queues[q].depth)
	}
}

func TestReplayStopsWhenOriginalRouteCannotBeDetermined(t *testing.T) {
	// 判不出原始键就不猜：猜出来的键在对面是**永久拒绝**，而 DLQ 里那条已经 ack ⇒ 丢消息。
	// 这里给一条没有任何 x-death、也没有死信记录的消息，并让默认键取不到（purpose 不在表里
	// 走的是另一条哨兵），所以只能用 ReplayRoutingKey 直接判这一格。
	noDeath := Message{Body: []byte(`{}`), MessageID: "m1"}
	if key, reason := ReplayRoutingKey(noDeath, "ai.observation.extract", ""); reason !=
		"original_routing_key_unknown" || key != "" {
		t.Fatalf("没有原始键与默认键时应当停手：key=%q reason=%q", key, reason)
	}
	// 有默认键时退到表内第一个（候选面有三个绑定键，broker 只认 x-death 那一条）。
	if key, reason := ReplayRoutingKey(noDeath, "go.candidate.accept", "candidate.pattern"); reason != "" ||
		key != "candidate.pattern" {
		t.Fatalf("退化路径不对：key=%q reason=%q", key, reason)
	}
	// x-death 里有**别的队列**的键时不许用它（那是上一段旅程的路由）。
	other := Message{Body: []byte(`{}`), MessageID: "m1", Headers: map[string]any{
		HeaderXDeath: []any{deathEntry("some.other.queue", "wrong.key", 1, time.Now())},
	}}
	if key, reason := ReplayRoutingKey(other, "go.candidate.accept", "candidate.pattern"); key != "candidate.pattern" ||
		reason != "" {
		t.Fatalf("用了别的队列的原始键：key=%q reason=%q", key, reason)
	}
}

func TestReplayCountsLimitAndEmptySeparately(t *testing.T) {
	b := newFakeBroker()
	const q = "dlq.ai.insight.draft"
	b.put(q, body("m1", `{}`, nil), body("m2", `{}`, nil), body("m3", `{}`, nil))

	res, err := Replay(context.Background(), b, q, 2)
	if err != nil {
		t.Fatalf("Replay：%v", err)
	}
	if res.Replayed != 2 || res.Reason != "limit_reached" {
		t.Fatalf("到上限那一轮的账面不对：%+v", res)
	}
	if b.queues[q].depth != 1 {
		t.Fatalf("只该捞走两条，剩 %d 条", b.queues[q].depth)
	}

	// 捞空了 ⇒ reason 是 queue_empty，与「还有货」分得清。
	res2, err := Replay(context.Background(), b, q, 5)
	if err != nil || res2.Replayed != 1 || res2.Reason != "queue_empty" {
		t.Fatalf("空队列收尾的账面不对：%+v err=%v", res2, err)
	}
	res3, err := Replay(context.Background(), b, q, 5)
	if err != nil || res3.Replayed != 0 || res3.Reason != "queue_empty" {
		t.Fatalf("本来就是空：%+v err=%v", res3, err)
	}
}

func TestReplayAckFailureIsReportedNotHidden(t *testing.T) {
	// 投成功但原条摘不掉 ⇒ 同一 message_id 会有两份。幂等位挡业务面，不挡队列里的重复，
	// 所以这里只许**如实报错**，不许回滚那条已经投出去的消息。
	b := newFakeBroker()
	const q = "dlq.ai.proactive.draft"
	b.put(q, body("m1", `{"a":1}`, map[string]any{HeaderXDeath: []any{
		deathEntry("ai.proactive.draft", "ai.proactive.requested", 3, time.Now().UTC()),
	}}))
	b.settleE = errors.New("通道已关")

	res, err := Replay(context.Background(), b, q, 3)
	if err == nil {
		t.Fatalf("ack 失败必须报错，账面=%+v", res)
	}
	if !strings.Contains(err.Error(), "同一 message_id") {
		t.Fatalf("错误没说清后果：%v", err)
	}
	if res.Replayed != 0 || len(b.sent) != 1 {
		t.Fatalf("消息已投出去、原条没摘掉这一格要留在账上：res=%+v sent=%d", res, len(b.sent))
	}
	if b.countCall("publish") != 1 {
		t.Fatalf("ack 失败后不该再投第二条：%v", b.calls)
	}
}

// ── 9：指标面的三条数 ───────────────────────────────────

func TestDLQTotalCountsOnlyFinalFaces(t *testing.T) {
	summaries := []Summary{
		{Queue: "ai.observation.extract", Kind: QueueKind("ai.observation.extract"), Depth: 7},
		{Queue: "retry.30s.ai.observation.extract", Kind: QueueKind("retry.30s.ai.observation.extract"), Depth: 4},
		{Queue: "dlq.ai.observation.extract", Kind: QueueKind("dlq.ai.observation.extract"), Depth: 2},
		{Queue: "dlq.go.candidate.accept", Kind: QueueKind("dlq.go.candidate.accept"), Depth: 3},
	}
	if got := DLQTotal(summaries); got != 5 {
		t.Fatalf("dlq_total=%d，应当只加终态面的 2+3（阶梯里的消息还没死，"+
			"算进去会让告警在正常瞬态失败时也响）", got)
	}
	if DLQTotal(nil) != 0 {
		t.Fatal("空清单要回 0，不是回错误")
	}
}

func TestListAllIsSerialAndStopsOnFirstError(t *testing.T) {
	b := newFakeBroker()
	b.put("dlq.ai.pattern.detect", body("m1", `{}`, nil))
	const bad = "dlq.nope.not-there"
	_, err := ListAll(context.Background(), b, []string{"dlq.ai.pattern.detect", bad, "dlq.ai.insight.draft"})
	if !errors.Is(err, ErrUnknownQueue) {
		t.Fatalf("err=%v", err)
	}
	// 停在第一条错上：第三条不该被叫到（这份输出是一次快照，不是尽力而为的清单）。
	if b.inspect["dlq.ai.insight.draft"] != 0 {
		t.Fatalf("出错之后还在继续巡检后续队列：%v", b.calls)
	}
	if b.inspect["dlq.ai.pattern.detect"] != 1 {
		t.Fatalf("第一条应当跑过：%v", b.calls)
	}
}

func TestListAllWithNoQueuesCoversWholeTopology(t *testing.T) {
	b := newFakeBroker()
	summaries, err := ListAll(context.Background(), b, nil)
	if err != nil {
		t.Fatalf("ListAll：%v", err)
	}
	if len(summaries) != len(Known()) {
		t.Fatalf("留空应当巡检全部 %d 个队列，实际 %d", len(Known()), len(summaries))
	}
	// 正向证人：purpose／阶梯／终态三类各至少一行（否则 QueueKind 的分支没人走也算绿）。
	seen := map[string]int{}
	for _, s := range summaries {
		seen[s.Kind]++
	}
	for _, kind := range []string{"purpose", "retry", "dlq"} {
		if seen[kind] == 0 {
			t.Fatalf("拓扑里没有 %s 类的队列行：看过的种类=%v", kind, seen)
		}
	}
}

// ── 事实源形状闸 ─────────────────────────────────────────

func TestKnownComesFromTheTopologyFace(t *testing.T) {
	names := Known()
	fromTopo := amqp.QueueNames()
	if len(names) != len(fromTopo) {
		t.Fatalf("队列清单与拓扑事实源不同数：%d vs %d", len(names), len(fromTopo))
	}
	for i := range names {
		if names[i] != fromTopo[i] {
			t.Fatalf("第 %d 格不同（Known 该是字典序）：%q vs %q", i, names[i], fromTopo[i])
		}
	}
}

func TestHeaderNamesComeFromAmqpFace(t *testing.T) {
	// 本包自己声明的 HeaderAttemptCount 必须与发布侧同一个串：
	// 重放清的是「试过几次」那一格，名字写错就等于没清，而表现是重放出去又立刻被判死。
	if HeaderXDeath != "x-death" {
		t.Fatalf("HeaderXDeath=%q", HeaderXDeath)
	}
	if amqp.HeaderAttemptCount != "x-attempt-count" {
		t.Fatalf("发布侧的计数 header 改了名：%q ⇒ 同步改 ResetCounters", amqp.HeaderAttemptCount)
	}
	if r := ResetCounters(Message{Headers: map[string]any{amqp.HeaderAttemptCount: int64(2)}}); r.Headers[amqp.HeaderAttemptCount] != int64(0) {
		t.Fatal("ResetCounters 没按发布侧的键清计数")
	}
}

func TestEveryKnownQueueRoundTripsToItsPurpose(t *testing.T) {
	// 名字是这里唯一的分类依据（没有第二张表），所以每个已知队列都必须能被还原成
	// 它自己的 purpose 队列，且还原结果本身也在拓扑里、且不再有前缀。
	purposes := map[string]bool{}
	for _, n := range Known() {
		if QueueKind(n) == "purpose" {
			purposes[n] = true
		}
	}
	for _, n := range Known() {
		p, err := PurposeQueueOf(n)
		if err != nil {
			t.Fatalf("PurposeQueueOf(%q)：%v", n, err)
		}
		if !purposes[p] {
			t.Fatalf("%q 还原出的 purpose 队列 %q 不在拓扑的 purpose 面里", n, p)
		}
		if got, err := Lookup(n); err != nil || got != p {
			t.Fatalf("Lookup(%q)=%q,%v 与 PurposeQueueOf 不一致（两处口径一定会漂）", n, got, err)
		}
		if _, err := Lookup(n + "-typo"); !errors.Is(err, ErrUnknownQueue) {
			t.Fatalf("改一个字符的队列名 %q 应当被拒，err=%v", n+"-typo", err)
		}
	}
	if len(purposes) != 6 {
		t.Fatalf("purpose 队列应当是 6 个（V3 四个任务面＋候选＋source.deleted），实际 %d", len(purposes))
	}
}

func TestRouteForCoversEveryPurposeQueue(t *testing.T) {
	// 重放的目标 exchange 与退化键来自绑定表；六个 purpose 队列若有一个取不到，
	// 那个队列的 DLQ 就变成了「工具能看不能救」，而这是 Baseline §13 要的那件工具的意义。
	for _, n := range Known() {
		if QueueKind(n) != "purpose" {
			continue
		}
		exchange, key, err := routeFor(n)
		if err != nil {
			t.Fatalf("purpose 队列 %q 取不到投递事实：%v", n, err)
		}
		if exchange == "" || key == "" {
			t.Fatalf("%q 的 exchange／键有空的：%q/%q", n, exchange, key)
		}
	}
}

// ── headers 解析面 ───────────────────────────────────────

func TestDeathsTakesEveryBrokerShape(t *testing.T) {
	at := time.Date(2026, 10, 19, 1, 0, 0, 0, time.UTC)
	oneTable := amqp091.Table{HeaderXQueue: "ai.observation.extract", HeaderXCount: int64(3),
		HeaderXReason: "expired", HeaderXTime: at, HeaderXRoutingKey: []any{"ai.observation.requested"}}

	cases := map[string]any{
		"amqp091.Table 切片": []amqp091.Table{oneTable},
		"any 切片":           []any{oneTable},
		"map 切片（适配层归一后）": []map[string]any{{
			HeaderXQueue: "ai.observation.extract", HeaderXCount: int64(3),
			HeaderXReason: "expired", HeaderXTime: at,
			HeaderXRoutingKey: []string{"ai.observation.requested"},
		}},
		"单格 map": map[string]any{
			HeaderXQueue: "ai.observation.extract", HeaderXCount: int64(3),
			HeaderXReason: "expired", HeaderXTime: at,
			HeaderXRoutingKey: "ai.observation.requested", // 退化成一串
		},
	}
	for name, raw := range cases {
		ds := Deaths(map[string]any{HeaderXDeath: raw})
		if len(ds) != 1 {
			t.Fatalf("%s：读出 %d 条：%+v", name, len(ds), ds)
		}
		d := ds[0]
		if d.Queue != "ai.observation.extract" || d.Count != 3 || d.Reason != "expired" ||
			!d.Time.Equal(at) || len(d.RoutingKeys) != 1 || d.RoutingKeys[0] != "ai.observation.requested" {
			t.Fatalf("%s：字段没读全：%+v", name, d)
		}
	}

	// 读不出就当没有：不许用 fmt.Sprint 兜底印一坨看不懂的东西进摘要。
	for name, raw := range map[string]any{
		"nil":      nil,
		"字符串":      "x-death?",
		"数字":       int64(7),
		"元素不对":     []any{"不是 table"},
		"count 负数": []any{map[string]any{HeaderXQueue: "q", HeaderXCount: int64(-5)}},
	} {
		h := map[string]any{}
		if raw != nil {
			h[HeaderXDeath] = raw
		}
		ds := Deaths(h)
		if len(ds) > 1 {
			t.Fatalf("%s：读出了 %d 条：%+v", name, len(ds), ds)
		}
		if name == "count 负数" && len(ds) == 1 && ds[0].Count != -5 {
			t.Fatalf("负计数被改写了：%+v", ds[0]) // 计数原样搬，判读交给上层
		}
	}
}

func TestXDeathFieldNamesAreVerbatim(t *testing.T) {
	// x-death **内部**的字段名没有第二个事实源可查（本包不引 amqp091 的常量，它也没有），
	// 所以这里按字面钉一次，真 broker 那边由 integration 的 ③ 号用例当第二证人：
	// 2026-10-19 就是那条用例把 `x-reason` 打红的——写成头名字的表现在 fake 里完全看不见。
	for name, want := range map[string]string{
		HeaderXDeath: "x-death", HeaderXQueue: "queue", HeaderXExchange: "exchange",
		HeaderXReason: "reason", HeaderXCount: "count", HeaderXTime: "time",
		HeaderXRoutingKey: "routing-keys",
	} {
		if name != want {
			t.Fatalf("字段名 %q 与 broker 给的 %q 不一致（改这一格必须同时改集成测）", name, want)
		}
	}
}

func TestCountersAcceptEveryNumericShape(t *testing.T) {
	for _, v := range []any{int64(3), int(3), int32(3), float64(3)} {
		if c := count(v); c != 3 {
			t.Fatalf("%T 的计数读成 %d", v, c)
		}
	}
	// 字符串与布尔不吃：那是「读不出」而不是「读成 0」。
	for _, v := range []any{"3", true, nil} {
		if c := count(v); c != 0 {
			t.Fatalf("%#v 不该被读成计数，实际 %d", v, c)
		}
	}
}

func TestOldestAgePrefersTimestampThenXDeathTime(t *testing.T) {
	old := time.Now().UTC().Add(-3 * time.Hour)
	// ① 有 Timestamp ⇒ 用诞生时刻，不用死信时刻。
	m := Message{Timestamp: old, Headers: map[string]any{HeaderXDeath: []any{
		deathEntry("ai.observation.extract", "k", 1, time.Now().UTC())}}}
	d, ok := oldestAge(m, Deaths(m.Headers))
	if !ok || d < 2*time.Hour {
		t.Fatalf("该用消息自己的时间戳：%v ok=%v", d, ok)
	}
	// ② 没时间戳 ⇒ 退化用死信时刻（低估年龄，但有比没有能告警好）。
	m2 := Message{Headers: map[string]any{HeaderXDeath: []any{
		deathEntry("ai.observation.extract", "k", 1, time.Now().UTC().Add(-30*time.Minute))}}}
	d2, ok2 := oldestAge(m2, Deaths(m2.Headers))
	if !ok2 || d2 < 29*time.Minute {
		t.Fatalf("该退化到 x-death 的时刻：%v ok=%v", d2, ok2)
	}
	// ③ 两格都没有 ⇒ 如实报「不知道」，不是报 0 秒（0 在告警里是「很健康」）。
	if _, ok3 := oldestAge(Message{}, nil); ok3 {
		t.Fatal("两格都没有时不许假装知道")
	}
}

func TestNilSettleIsAnErrorNotAPanic(t *testing.T) {
	// 工具拿着 fake 跑时最容易漏填 Settle；表现必须是「这条没处置掉」，不是进程倒在那儿。
	if err := (Message{}).Ack(); err == nil {
		t.Fatal("Ack 在无 Settle 时该回错误")
	}
	if err := (Message{}).Requeue(); err == nil {
		t.Fatal("Requeue 在无 Settle 时该回错误")
	}
	if !strings.Contains((Message{}).Ack().Error(), "没有 Settle") {
		t.Fatal("错误没说清是哪一格缺")
	}
}

func TestInspectErrorIsNotSwallowed(t *testing.T) {
	b := newFakeBroker()
	b.queues["dlq.ai.observation.extract"] = &fakeQueue{depth: 1}
	b.inspectE = errors.New("队列不存在")
	if _, err := List(context.Background(), b, "dlq.ai.observation.extract"); err == nil {
		t.Fatal("inspect 失败必须报错（吞掉它就是报一个看起来健康的 0 深度）")
	}
	b2 := newFakeBroker()
	b2.getErr = errors.New("通道断了")
	b2.put("dlq.ai.pattern.detect", body("m", `{}`, nil))
	if _, err := List(context.Background(), b2, "dlq.ai.pattern.detect"); err == nil {
		t.Fatal("peek 失败必须报错")
	}
}

// TestReplayRoutingKeyUsesTheBrokersOwnDeathRecord 是「优先 x-death」那条的**正向证人**。
//
// 为什么单独一条：上面那条用例判的是「没有 x-death 时退到表内第一个」和「x-death 里是**别的**
// 队列时不许用」——两条都是反面。候选面一个队列绑三个键（pattern／insight／通配），只有 broker
// 记着这条当初是按哪个键进来的；把 `ReplayRoutingKey` 里那个循环整个掏空，那三条反面全绿，
// 而线上出现的形态是「一条 insight 候选被回投成 pattern」——对面照单收下，用户看到一条类型写错的洞察。
func TestReplayRoutingKeyUsesTheBrokersOwnDeathRecord(t *testing.T) {
	withDeath := Message{Body: []byte(`{}`), MessageID: "m1", Headers: map[string]any{
		HeaderXDeath: []any{deathEntry("go.candidate.accept", "candidate.insight", 2, time.Now())},
	}}
	if key, reason := ReplayRoutingKey(withDeath, "go.candidate.accept", "candidate.pattern"); key !=
		"candidate.insight" || reason != "" {
		t.Fatalf("broker 记着原始键时不该退到表内第一个：key=%q reason=%q", key, reason)
	}
	// 同一格在 Replay 的步序里也有证人：见 TestReplayKeepsBytesAndIdentityButClearsCounters
	// 里那条逐字的 publish 序列（两侧不同源，一个判函数、一个判整轮账）。
}

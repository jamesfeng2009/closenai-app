// 拓扑面的闸（R4-①）。
//
// 这一份测的意义不是「代码跑通了」，而是**声明出来的东西逐字对上 V3**：
// exchange 名／队列名／阶梯 TTL／DLX 参数只要有一格写歪，broker 那边就是
// 「消息进了一个没人读的队列」或「reject 之后原地无限 requeue」——
// 两种都不会在 Go 侧报错，只会在现网表现为「洞察永远没有依据」。
//
// 无 broker 也能全跑：Declarer 是个三方法的窄接口，fake 记录调用序列。
// 真 broker 上的幂等重放归 R4-②（集成测 (a)）。
package amqp

import (
	"errors"
	"fmt"
	"strings"
	"testing"

	amqp091 "github.com/rabbitmq/amqp091-go"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

type declCall struct {
	op       string // exchange-declare / queue-declare / queue-bind
	name     string
	key      string // bind 用的 routing key
	exchange string // bind 的目标 exchange，或 declare 时的 kind
	args     amqp091.Table
}

type fakeDeclarer struct {
	calls  []declCall
	failOn string // 让某个 op＋name 的组合报错，验错误包装
}

func (f *fakeDeclarer) record(op, name, key, exchange string, args amqp091.Table) error {
	if f.failOn != "" && f.failOn == op+" "+name {
		return errors.New("PRECONDITION_FAILED")
	}
	f.calls = append(f.calls, declCall{op: op, name: name, key: key, exchange: exchange, args: args})
	return nil
}

func (f *fakeDeclarer) ExchangeDeclare(name, kind string, durable, autoDelete, internal, noWait bool, args amqp091.Table) error {
	if !durable {
		return fmt.Errorf("exchange %s 必须 durable（14 §13：投递面不能靠瞬态 exchange 兜）", name)
	}
	return f.record("exchange-declare", name, "", kind, args)
}

func (f *fakeDeclarer) QueueDeclare(name string, durable, autoDelete, exclusive, noWait bool, args amqp091.Table) (amqp091.Queue, error) {
	if err := f.record("queue-declare", name, "", "", args); err != nil {
		return amqp091.Queue{}, err
	}
	return amqp091.Queue{Name: name}, nil
}

func (f *fakeDeclarer) QueueBind(name, key, exchange string, noWait bool, args amqp091.Table) error {
	return f.record("queue-bind", name, key, exchange, args)
}

func (f *fakeDeclarer) find(op, name string) *declCall {
	for i := range f.calls {
		if f.calls[i].op == op && f.calls[i].name == name {
			return &f.calls[i]
		}
	}
	return nil
}

// ── 名单逐字对 V3 ──────────────────────────────────────────

func testSet(names ...string) map[string]bool {
	m := make(map[string]bool, len(names))
	for _, n := range names {
		m[n] = true
	}
	return m
}

// TestDeclaredExchangesAreExactlyV3List：exchange 面只准有 14:41-45 那四条。
// 多一条就是有人在 V3 之外发明 exchange（本批 D-R4-2 明令禁止）。
func TestDeclaredExchangesAreExactlyV3List(t *testing.T) {
	want := testSet("closenai.events", "closenai.ai.tasks", "closenai.ai.candidates", "closenai.dlq")
	got := testSet(Exchanges...)
	if len(got) != len(want) {
		t.Fatalf("exchange 条数 %d ≠ 4：%v", len(got), Exchanges)
	}
	for name := range want {
		if !got[name] {
			t.Fatalf("缺 exchange %s（14 §3 逐字）", name)
		}
	}
}

// TestPurposeQueuesAreExactlyV3List：五个 purpose queue 名逐字取 14:50-55。
func TestPurposeQueuesAreExactlyV3List(t *testing.T) {
	want := testSet("ai.observation.extract", "ai.pattern.detect", "ai.insight.draft",
		"ai.proactive.draft", "go.candidate.accept")
	got := map[string]bool{}
	for _, r := range routes() {
		got[r.Queue] = true
	}
	if len(got) != len(want) {
		t.Fatalf("purpose queue 条数 %d ≠ 5：%v", len(got), got)
	}
	for name := range want {
		if !got[name] {
			t.Fatalf("缺队列 %s（14 §3 逐字）", name)
		}
	}
}

// TestCommandRoutesGoToTaskExchangeNotEvents：命令面走 closenai.ai.tasks，
// 候选回投走 closenai.ai.candidates。这条是 D-R4-3 的落点：
// 「命令与事件同一条 outbox、不同 exchange」——写歪了就是命令进了事件账本。
func TestCommandRoutesGoToTaskExchangeNotEvents(t *testing.T) {
	for _, r := range CommandRoutes {
		if r.Exchange != ExchangeAITasks {
			t.Errorf("命令路由 %s 的 exchange 是 %s，应为 %s", r.RoutingKey, r.Exchange, ExchangeAITasks)
		}
	}
	for _, r := range CandidateRoutes {
		if r.Exchange != ExchangeAICandidate {
			t.Errorf("候选路由 %s 的 exchange 是 %s，应为 %s", r.RoutingKey, r.Exchange, ExchangeAICandidate)
		}
	}
}

// TestCommandRouteKeysComeFromDomain：命令面的键串只有 `domain.AITaskKind` 一个生成点。
//
// 这是 R4-③ 的「生产者写的键 ＝ 投递面认的键」那一条：入队口 `Service.RequestAITask`
// 把 kind 折成 `ai.<kind>.requested` 写进 outbox 行，本表按同一个函数声明队列与绑定。
// 两边各写字面量迟早漂（本轮就抓到 `contracts/rpc/ownership.md:182` 那一处同类漂移），
// 所以这里断言三件事：表行数＝枚举数、每行的键都在 domain 认得的集合里、
// 每个 kind 恰好对应 14:51-54 那一个队列。
//
// 后半段把它打到**真声明**上：`Declare()` 之后这条键必须绑在 `closenai.ai.tasks`，
// 而且不许出现在 `closenai.events` 上——命令进事件账本＝把「权威模型内容变了」
// 那条进门判据反着用（`domain/delivery.go:31-36`）。
func TestCommandRouteKeysComeFromDomain(t *testing.T) {
	if len(CommandRoutes) != len(domain.AITaskKinds) {
		t.Fatalf("命令表 %d 行，domain 枚举 %d 类 ⇒ 有人在两侧各加了一套",
			len(CommandRoutes), len(domain.AITaskKinds))
	}
	wantQueue := map[domain.AITaskKind]string{
		domain.AITaskObservation: "ai.observation.extract",
		domain.AITaskPattern:     "ai.pattern.detect",
		domain.AITaskInsight:     "ai.insight.draft",
		domain.AITaskProactive:   "ai.proactive.draft",
	}
	for _, r := range CommandRoutes {
		if !domain.IsCommandRoutingKey(r.RoutingKey) {
			t.Errorf("命令表里的键 %s 不是 domain 生成的（AITaskKind 是唯一生成点）", r.RoutingKey)
		}
		if r.Kind != KindCommand {
			t.Errorf("%s 的 Kind=%q，应为 %q", r.RoutingKey, r.Kind, KindCommand)
		}
	}
	for kind, queue := range wantQueue {
		hits := 0
		for _, r := range CommandRoutes {
			if r.RoutingKey == kind.RoutingKey() {
				hits++
				if r.Queue != queue {
					t.Errorf("%s 绑到队列 %s，14 §3 给的是 %s", r.RoutingKey, r.Queue, queue)
				}
			}
		}
		if hits != 1 {
			t.Errorf("任务类型 %s 在命令表里出现 %d 次，应为 1", kind, hits)
		}
	}

	f := &fakeDeclarer{}
	if err := Declare(f); err != nil {
		t.Fatalf("Declare: %v", err)
	}
	for kind, queue := range wantQueue {
		key := kind.RoutingKey()
		if c := f.findBind(queue, key, ExchangeAITasks); c == nil {
			t.Errorf("真声明里没有把 %s 绑到 %s(%s) ⇒ Python 收不到这类任务", key, queue, ExchangeAITasks)
		}
		for _, c := range f.calls {
			if c.op == "queue-bind" && c.key == key && c.exchange == ExchangeEvents {
				t.Errorf("命令键 %s 被绑进了事件面 %s（D-R4-3：命令与事件同 outbox、不同 exchange）",
					key, ExchangeEvents)
			}
		}
	}
}

// TestCommandAndEventRoutingKeysAreDisjoint：命令键与 domain 十条事件键**不许重叠**。
//
// 这是变异自证 M2 的靶子：只要有人把 `ai.*.requested` 塞进 `delivery.go` 那张十条表
// （或者反过来把事件键加进 CommandRoutes），这条立刻红。判据本身在
// `domain/delivery.go:31-36` 逐字写着「权威模型内容变了」，命令不满足它。
func TestCommandAndEventRoutingKeysAreDisjoint(t *testing.T) {
	events := map[string]bool{}
	for _, k := range EventRoutingKeys() {
		events[k] = true
	}
	if len(events) != 9 {
		t.Fatalf("十条事件类型应折叠成 9 个 distinct routing key（pattern_admitted/reactivated/lifecycle 三型共用两键），实得 %d：%v",
			len(events), EventRoutingKeys())
	}
	for _, r := range append(append([]Route{}, CommandRoutes...), CandidateRoutes...) {
		if events[r.RoutingKey] {
			t.Errorf("路由键 %s 同时出现在命令/候选面和 domain 十条里——两套路由并成一套了", r.RoutingKey)
		}
	}
}

// TestEventRoutesHaveNoQueueBound：R4 期十条事件路由在本表里没有队列（引线）。
// 加了事件队列的那天，这条会红，提醒改的人同时把 publisher 的账本口径过一遍。
func TestEventRoutesHaveNoQueueBound(t *testing.T) {
	for _, r := range append(append([]Route{}, CommandRoutes...), CandidateRoutes...) {
		for _, key := range EventRoutingKeys() {
			if r.RoutingKey == key {
				t.Fatalf("事件路由 %s 不该在 R4 期绑队列，见 topology.go 的 EventBindingGap", key)
			}
		}
	}
}

// ── 声明内容与参数 ────────────────────────────────────────

// TestDeclareCreatesPurposeRetryAndFinalQueues：每条 purpose 路由要有
// 本体＋三层显式重试队列＋一个终态死信队列 ⇒ 5×5＝25 个队列；
// 绑定 7（全表键）＋15（阶梯）＋5（终态）＝27 次；exchange 声明 4 次。
func TestDeclareCreatesPurposeRetryAndFinalQueues(t *testing.T) {
	f := &fakeDeclarer{}
	if err := Declare(f); err != nil {
		t.Fatalf("Declare: %v", err)
	}
	if len(QueueNames()) != 25 {
		t.Errorf("QueueNames 展开出 %d 个队列，应为 5 purpose ×（1＋3 层＋1 终态）＝25", len(QueueNames()))
	}
	declared := 0
	for _, c := range f.calls {
		if c.op == "queue-declare" {
			declared++
		}
	}
	if declared != 25 {
		t.Errorf("真声明了 %d 个队列，应为 25", declared)
	}
	if got := len(QueueNames()); got != declared {
		t.Errorf("QueueNames()=%d 与实际声明 %d 不一致：生成出来的 contracts/events.md 会和 broker 上的拓扑漂", got, declared)
	}

	// 绑定按**全表**走，声明按去重走：`go.candidate.accept` 一个队列有三个键，
	// 只绑第一个键的话 candidate.insight／candidate.fact 会被 broker 静默丢弃，
	// 而 Go 侧一声不响（2026-10-16 写 R4-② 时自查到的真 bug，这条是它的锚）。
	binds := 0
	for _, c := range f.calls {
		if c.op == "queue-bind" {
			binds++
		}
	}
	if want := len(allRoutes()) + len(routes())*len(RetryTiers) + len(routes()); binds != want {
		t.Errorf("绑定 %d 次，应为 %d（全表键 %d＋阶梯 %d＋终态 %d）", binds, want,
			len(allRoutes()), len(routes())*len(RetryTiers), len(routes()))
	}
	for _, r := range allRoutes() {
		if c := f.findBind(r.Queue, r.RoutingKey, r.Exchange); c == nil {
			t.Errorf("缺绑定 %s[%s]→%s", r.Queue, r.RoutingKey, r.Exchange)
		}
	}
}

// findBind 按（队列, 键, exchange）三元组找一次绑定：find 只按名字找，
// 而一个队列可以有多个绑定键，只按名字找就看不见「第二个键被去重吞掉」。
func (f *fakeDeclarer) findBind(name, key, exchange string) *declCall {
	for i := range f.calls {
		c := f.calls[i]
		if c.op == "queue-bind" && c.name == name && c.key == key && c.exchange == exchange {
			return &f.calls[i]
		}
	}
	return nil
}

// TestPurposeQueueDeadLettersIntoFirstRetryTier：reject 之后进 30s 层，
// 而不是原地 requeue（14 §8「Prefer explicit retry queues over unlimited immediate requeue」）。
func TestPurposeQueueDeadLettersIntoFirstRetryTier(t *testing.T) {
	f := &fakeDeclarer{}
	if err := Declare(f); err != nil {
		t.Fatalf("Declare: %v", err)
	}
	for _, r := range routes() {
		c := f.find("queue-declare", r.Queue)
		if c == nil {
			t.Fatalf("没声明 purpose 队列 %s", r.Queue)
		}
		if got := c.args["x-dead-letter-exchange"]; got != ExchangeDLQ {
			t.Errorf("%s 的 x-dead-letter-exchange = %v，应为 %s", r.Queue, got, ExchangeDLQ)
		}
		if got := c.args["x-dead-letter-routing-key"]; got != TierRoutingKey(r.Queue, 0) {
			t.Errorf("%s 的 x-dead-letter-routing-key = %v，应为 %s", r.Queue, got, TierRoutingKey(r.Queue, 0))
		}
	}
}

// TestRetryTiersMatchV3LadderAndReturnToOrigin：TTL 逐字 30s/2m/10m（14:130-139），
// 且每层到期后回**原 exchange＋原绑定键**——不设 x-dead-letter-routing-key 的话
// 死信用的键是 `retry.30s.<queue>`，匹配不到任何绑定，阶梯就断了（这条最容易写歪且不报错）。
func TestRetryTiersMatchV3LadderAndReturnToOrigin(t *testing.T) {
	f := &fakeDeclarer{}
	if err := Declare(f); err != nil {
		t.Fatalf("Declare: %v", err)
	}
	wantTTL := []int{30_000, 120_000, 600_000}
	for _, r := range routes() {
		for i, ttl := range wantTTL {
			name := TierQueueName(r.Queue, i)
			c := f.find("queue-declare", name)
			if c == nil {
				t.Fatalf("没声明重试队列 %s", name)
			}
			if c.args["x-message-ttl"] != ttl {
				t.Errorf("%s 的 x-message-ttl = %v，应为 %d（14 §8）", name, c.args["x-message-ttl"], ttl)
			}
			if c.args["x-dead-letter-exchange"] != r.Exchange {
				t.Errorf("%s 到期后应回 %s，实为 %v", name, r.Exchange, c.args["x-dead-letter-exchange"])
			}
			if c.args["x-dead-letter-routing-key"] != r.RoutingKey {
				t.Errorf("%s 到期后的路由键应为 %s，实为 %v", name, r.RoutingKey, c.args["x-dead-letter-routing-key"])
			}
		}
	}
}

// TestFinalDlqTerminates：终态死信队列不许再有 DLX，否则一条毒消息会在几个 DLQ 之间
// 无限搬家，谁都不报错，日志里也看不出来。
func TestFinalDlqTerminates(t *testing.T) {
	f := &fakeDeclarer{}
	if err := Declare(f); err != nil {
		t.Fatalf("Declare: %v", err)
	}
	for _, r := range routes() {
		c := f.find("queue-declare", FinalQueueName(r.Queue))
		if c == nil {
			t.Fatalf("没声明终态死信队列 %s", FinalQueueName(r.Queue))
		}
		for k := range c.args {
			if strings.HasPrefix(k, "x-dead-letter") {
				t.Errorf("%s 上还挂着 %v＝毒消息会搬家，终态面必须是终点", FinalQueueName(r.Queue), k)
			}
		}
	}
}

// TestDeclareIsIdempotentReplay：整张表重放两遍，调用序列必须逐字相同
// （真 broker 只在参数不一致时报 PRECONDITION_FAILED ⇒ 参数写歪的代价归 R4-② 的 (a)）。
func TestDeclareIsIdempotentReplay(t *testing.T) {
	first, second := &fakeDeclarer{}, &fakeDeclarer{}
	if err := Declare(first); err != nil {
		t.Fatalf("第一遍：%v", err)
	}
	if err := Declare(second); err != nil {
		t.Fatalf("第二遍：%v", err)
	}
	if len(first.calls) != len(second.calls) {
		t.Fatalf("两遍调用数不同：%d vs %d", len(first.calls), len(second.calls))
	}
	for i := range first.calls {
		if first.calls[i].op != second.calls[i].op || first.calls[i].name != second.calls[i].name ||
			first.calls[i].key != second.calls[i].key || first.calls[i].exchange != second.calls[i].exchange {
			t.Fatalf("第 %d 次调用漂了：%+v vs %+v", i, first.calls[i], second.calls[i])
		}
	}
}

// TestDeclareWrapsChannelError：声明失败要把队列名带进错误里。
// worker 起来就崩是最难查的形态（日志只有一句 exit 1），所以这里的判据是「错误里有名字」。
func TestDeclareWrapsChannelError(t *testing.T) {
	for _, r := range routes() {
		f := &fakeDeclarer{failOn: "queue-declare " + r.Queue}
		err := Declare(f)
		if err == nil {
			t.Fatalf("声明 %s 失败时 Declare 必须返回错误", r.Queue)
		}
		if !strings.Contains(err.Error(), r.Queue) {
			t.Errorf("错误里没有队列名，运维无从定位：%v", err)
		}
	}
}

// TestDescribeMentionsEveryQueueAndTier：生成器的渲染面必须覆盖每一条表行，
// 否则 contracts/events.md 会写成半套（半套的契约文档比没有更坏）。
func TestDescribeMentionsEveryQueueAndTier(t *testing.T) {
	out := Describe()
	for _, name := range Exchanges {
		if !strings.Contains(out, name) {
			t.Errorf("Describe() 少了 exchange %s", name)
		}
	}
	for _, r := range append(append([]Route{}, CommandRoutes...), CandidateRoutes...) {
		if !strings.Contains(out, r.RoutingKey) || !strings.Contains(out, r.Queue) {
			t.Errorf("Describe() 少了路由 %s→%s", r.RoutingKey, r.Queue)
		}
	}
	for _, ttl := range RetryTiers {
		if !strings.Contains(out, ttl.Suffix) {
			t.Errorf("Describe() 少了重试层级 retry.%s", ttl.Suffix)
		}
	}
	if !strings.Contains(out, "不绑队列") {
		t.Error("Describe() 没写「事件面不绑队列」这条引线——读文档的人会以为十条事件已经通了")
	}
}

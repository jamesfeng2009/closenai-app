// Package dlq 是死信面的人工巡检与重放（T2-05，Baseline §13 那条「永久错误进 DLQ
// 并提供重放工具；提供积压、最老消息年龄、重试次数、DLQ 数量等指标」）。
//
// 为什么这一层住在 internal 而不是只住在 tools/ 里：`tools/dlqreplay` 的 flag 面是给
// 人敲的，判据不能只有「命令行了」——`--purge` 少了 `--confirm` 必须拒、`--list` 必须
// 真去 broker 取数、重放必须清掉重试计数。这三条都要能在**不需要真 broker、不需要真终端**
// 的情况下判红，所以逻辑住在可测的包里，main 只做参数搬运。
//
// 端口形状沿用入站面那套（`inbound.Delivery` 的 `Settle` 闭包）：核心逻辑只认四个方法，
// fake 只需要填闭包，真 broker 的适配在 amqp_broker.go。领域层不许认识本包
// （`domain/arch_test.go` 的 rabbitmq 禁集），消费者也不通过这里回投。
//
// ⚠️ 三条不动声色的取舍，都在测试里各有证人：
//  1. **peek 不许改账**。`--list` 要知道最老消息多老，就得看一眼队头那条的
//     `Timestamp`。取一条再 `requeue=true` 放回去是本包唯一允许的读法：RabbitMQ 只在
//     **死信**（reject requeue=false／expired／maxlen）时写 `x-death`，requeue 不写，
//     所以这条读法不会把阶梯计数推高——这一条不是信文档，是真 broker 测里逐格断言的
//     （`integration_broker_test.go` 的「peek 前后 x-death 的 count 一字不变」）。
//  2. **有消费者的队列不许 peek**。队头那条可能正在别人手里，取了再塞回去会把一次
//     正常的在途交付变成第二次投递。所以 `consumers>0` 时年龄这一格直接判「不知道」，
//     而不是猜一个。DLQ 按拓扑设计本来就没有消费者（`declareFinalQueue` 无 DLX、
//     没有任何认领者），所以这一格在现网里是「能读」。
//  3. **重放必须换计数、不许换字节**。同一串 body、同一个 `message_id` 原样回投，
//     对面 inbox 的 `(message_id, consumer)` 才认得出这是**同一次逻辑操作**（18 §10）；
//     清掉的只有 `x-death` 与 `x-attempt-count`——那两格是「已经试过几次」的记账，
//     重放这个动作的含义正是「从头再给一次机会」。
package dlq

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/amqp"
)

// 三个哨兵：调用方（tools/dlqreplay 与 pytest 侧的进程闸）按码分叉，不按 Detail 文案分叉。
var (
	// ErrUnknownQueue：队列名不在这套拓扑里。判据来自 `amqp.QueueNames()`（唯一事实源），
	// 所以改了 topology 忘了改这里不可能发生——本包不认识任何写死的队列名。
	ErrUnknownQueue = errors.New("dlq: 队列名不在拓扑事实源里")
	// ErrConfirmRequired：`--purge` 没给逐字相等的确认串。清空队列是不可逆的，
	// 二次确认串必须是**队列名本身**，不能是一个可以照抄的固定词。
	ErrConfirmRequired = errors.New("dlq: purge 需要与队列名逐字相等的 --confirm")
	// ErrNotReplayable：阶梯队列上的消息不归重放工具。它们带着 TTL 自己会回到
	// purpose queue，人工提前捞出来等于同时存在两条「同一命令的第二次投递」，
	// 而其中一条没经过任何背压。
	ErrNotReplayable = errors.New("dlq: 只有终态 dlq.* 队列可以重放")
)

// HeaderAttemptCount 与 `amqp.HeaderAttemptCount` 同值（由 TestHeaderNamesComeFromAmqpFace 钉）。
const (
	HeaderXDeath = "x-death"
	// 下面五个是 **x-death 那一格内部的字段名**，不是消息头上的键。
	// 区别不是文字游戏：内部字段叫 `reason`，写成 `x-reason` 的表现是「死因这一格永远为空」，
	// 而这份摘要是值班判断「这条为什么死的」唯一的输入
	// （2026-10-19 真 broker 集成测跑实：`Reason` 那一格读不出来）。
	HeaderXReason     = "reason"
	HeaderXCount      = "count"
	HeaderXQueue      = "queue"
	HeaderXExchange   = "exchange"
	HeaderXRoutingKey = "routing-keys"
	HeaderXTime       = "time"

	// defaultLimit／maxLimit：一次重放的条数上下限。上限存在是因为「把整个 DLQ 一次捞空」
	// 在现网的表现是那批毒消息同时回到 purpose 队列、把消费者再打一遍——
	// 想全捞就多敲几轮，每轮之间人能看见中间状态。
	defaultLimit = 100
	maxLimit     = 10_000
)

// Message 是一条从队列里取出来的交付（端口结构，不含 amqp091 对象）。
//
// `Settle` 是唯一能处置它的出口：`Settle(true,false)`＝ack（重放成功后摘掉原条），
// `Settle(false,true)`＝requeue（peek 之后放回原处，或发布失败时把这条留给下一次）。
type Message struct {
	Body          []byte
	ContentType   string
	MessageID     string
	MessageType   string
	CorrelationID string
	Timestamp     time.Time
	Headers       map[string]any
	// DeliveryRoutingKey／DeliveryExchange 是**这次投递**的键与 exchange（进 DLQ 后
	// 就是 `dlq.<queue>`／`closenai.dlq`），不是原始那次的。原始键在 x-death 里，见 OriginalRoute。
	DeliveryRoutingKey string
	DeliveryExchange   string
	Settle             func(ack, requeue bool) error
}

// Settle 把 nil 闭包判成错误而不是 panic：工具拿着 fake 跑测试时最容易漏填这一格，
// 而漏填的表现必须是「这条没处置掉」，不是整个进程倒在那儿。
func (m Message) Ack() error     { return m.settle(true, false) }
func (m Message) Requeue() error { return m.settle(false, true) }

func (m Message) settle(ack, requeue bool) error {
	if m.Settle == nil {
		return errors.New("dlq: 这条消息没有 Settle，处置不了")
	}
	return m.Settle(ack, requeue)
}

// Outbound 是重放时要投出去的那一条：字节原样、身份原样，计数清零。
type Outbound struct {
	Body          []byte
	ContentType   string
	MessageID     string
	MessageType   string
	CorrelationID string
	Timestamp     time.Time
	Headers       map[string]any
}

// Broker 是本包需要的四件事，逐字对应 amqp091 的 channel 面（适配在 amqp_broker.go）。
//
// 每个方法都带 ctx：这条链上最坏的一次等待是 broker 黑洞时的 `basic.publish` 确认，
// 而工具是人敲的、Ctrl-C 必须真能停。没有 ctx 的端口形状会让「取消」这件事
// 只能在适配层造假。
type Broker interface {
	// Inspect 被动声明取深度与消费者数（`queue.declare passive` 的返回体）。
	Inspect(ctx context.Context, queue string) (depth, consumers int, err error)
	// Get 从队头取一条（`basic.get`，autoAck=false）。队列空 ⇒ ok=false 且不是错误。
	Get(ctx context.Context, queue string) (Message, bool, error)
	// Publish 投一条并等 broker 确认（confirm 模式由适配层负责）。
	Publish(ctx context.Context, exchange, routingKey string, m Outbound) error
	// Purge 清空队列并返回被丢弃的条数。
	Purge(ctx context.Context, queue string) (int, error)
}

// DeathEntry 是 `x-death` 里的一格：哪个队列、为什么、几次、什么时候。
type DeathEntry struct {
	Queue       string
	Exchange    string
	RoutingKeys []string
	Reason      string
	Count       int64
	Time        time.Time
}

// Summary 是一个队列的巡检结果（`--list` 的行形状，也是 JSON 输出的单元）。
type Summary struct {
	Queue     string `json:"queue"`
	Purpose   string `json:"purpose_queue"`
	Kind      string `json:"kind"` // purpose／retry／dlq
	Depth     int    `json:"depth"`
	Consumers int    `json:"consumers"`
	// OldestAgeSeconds 是队头那条的年龄；取不到时 Known=false 而不是报 0——
	// 「0 秒」在告警里是「很健康」，而它其实可能是「读不到」。
	OldestAgeSeconds float64      `json:"oldest_message_age_seconds"`
	OldestKnown      bool         `json:"oldest_age_known"`
	OldestUnknown    string       `json:"oldest_age_unknown,omitempty"`
	Deaths           []DeathEntry `json:"x_death,omitempty"`
}

// QueueKind 按队列名在拓扑里的位置分类（名字是唯一事实，没有第二张表）。
func QueueKind(name string) string {
	switch {
	case strings.HasPrefix(name, "dlq."):
		return "dlq"
	case strings.HasPrefix(name, "retry."):
		return "retry"
	default:
		return "purpose"
	}
}

// PurposeQueueOf 把任意队列名（purpose／retry.<tier>.*／dlq.*）还原成它的 purpose 队列名。
func PurposeQueueOf(name string) (string, error) {
	switch QueueKind(name) {
	case "purpose":
		return name, nil
	case "dlq":
		return strings.TrimPrefix(name, "dlq."), nil
	case "retry":
		// `retry.<suffix>.<queue>`：suffix 不含点，所以第三段起就是队列名。
		rest := strings.TrimPrefix(name, "retry.")
		i := strings.Index(rest, ".")
		if i < 0 {
			return "", fmt.Errorf("%w: %q", ErrUnknownQueue, name)
		}
		return rest[i+1:], nil
	}
	return "", fmt.Errorf("%w: %q", ErrUnknownQueue, name)
}

// Known 列出拓扑里全部队列名（含阶梯与终态面），按字典序。
// 事实源只有一个：`amqp.QueueNames()`。本包不写死任何一个队列名。
func Known() []string {
	names := amqp.QueueNames()
	sort.Strings(names)
	return names
}

// Lookup 校验队列名属于本套拓扑并回它的 purpose 队列；不在表里就 ErrUnknownQueue。
// 这一格判的是「运维敲错队列名」——清空一个不存在的队列，broker 会把它当
// 「声明一个空的新队列」（purge 返回 0），于是那次操作看起来成功而什么都没发生。
func Lookup(name string) (string, error) {
	purpose, err := PurposeQueueOf(name)
	if err != nil {
		return "", err
	}
	for _, q := range Known() {
		if q == name {
			return purpose, nil
		}
	}
	return "", fmt.Errorf("%w: %q（事实源里有 %d 个队列名，见 --list 不带参数）",
		ErrUnknownQueue, name, len(Known()))
}

// routeFor 找到 purpose 队列的绑定事实（exchange＋默认 routing key）。
// 一个队列可能绑三个键（候选面就是），所以这里只给「表里第一个」，
// 真正的原始键由 OriginalRoute 从 x-death 里取。
func routeFor(purpose string) (exchange, routingKey string, err error) {
	for _, r := range append(append([]amqp.Route{}, amqp.CommandRoutes...),
		amqp.CandidateRoutes...) {
		if r.Queue == purpose {
			return r.Exchange, r.RoutingKey, nil
		}
	}
	for _, r := range amqp.EventRoutes {
		if r.Queue == purpose {
			return r.Exchange, r.RoutingKey, nil
		}
	}
	return "", "", fmt.Errorf("%w: purpose 队列 %q 没有任何绑定事实", ErrUnknownQueue, purpose)
}

// List 巡检一个队列：深度、消费者数、队头年龄、x-death 摘要。
func List(ctx context.Context, b Broker, queue string) (Summary, error) {
	purpose, err := Lookup(queue)
	if err != nil {
		return Summary{}, err
	}
	depth, consumers, err := b.Inspect(ctx, queue)
	if err != nil {
		return Summary{}, fmt.Errorf("dlq: inspect %s: %w", queue, err)
	}
	s := Summary{Queue: queue, Purpose: purpose, Kind: QueueKind(queue),
		Depth: depth, Consumers: consumers}
	if depth == 0 {
		return s, nil
	}
	if consumers > 0 {
		// 取舍 2：有人在消费就不 peek。年龄这一格如实报「不知道」而不是猜。
		s.OldestUnknown = "队列上有消费者，巡检不去动在途的交付"
		return s, nil
	}
	msg, ok, err := b.Get(ctx, queue)
	if err != nil {
		return Summary{}, fmt.Errorf("dlq: peek %s: %w", queue, err)
	}
	if !ok {
		s.OldestUnknown = "inspect 说有深度，取的时候空了（并发消费或 broker 计数滞后）"
		return s, nil
	}
	// 无论后面成不成，peek 出来的这条都必须放回原处——留在本进程手里就是丢消息。
	defer func() { _ = msg.Requeue() }()
	s.Deaths = Deaths(msg.Headers)
	if age, known := oldestAge(msg, s.Deaths); known {
		s.OldestAgeSeconds = age.Seconds()
		s.OldestKnown = true
	} else {
		s.OldestUnknown = "这条消息既没有 Timestamp 也没有 x-death 的时刻"
	}
	return s, nil
}

// ListAll 逐个巡检；空清单 ⇒ 全部已知队列。
//
// 为什么不做并发：这些数字来自**同一条通道**上的串行请求，并发取数只会让
// 「深度在变」这件事更难解释，而这份输出是给人读的一次快照。
func ListAll(ctx context.Context, b Broker, queues []string) ([]Summary, error) {
	if len(queues) == 0 {
		queues = Known()
	}
	out := make([]Summary, 0, len(queues))
	for _, q := range queues {
		s, err := List(ctx, b, q)
		if err != nil {
			return out, err
		}
		out = append(out, s)
	}
	return out, nil
}

// DLQTotal 把 dlq.* 那几张队列的深度加总（指标面 `dlq_total` 的定义）。
//
// 只数终态面：阶梯里的消息「还没死」，把它们算进 DLQ 计数会让告警在正常的瞬态失败
// 抖动时也响，而那条告警要的语义是「有消息需要人来判」。
func DLQTotal(summaries []Summary) int {
	total := 0
	for _, s := range summaries {
		if s.Kind == "dlq" {
			total += s.Depth
		}
	}
	return total
}

// oldestAge 优先用消息自己的 Timestamp（发布侧写的是 `p.now()`，见 amqp/publisher.go），
// 退化才用 x-death 的时刻（那是「它什么时候被判死」而不是「它什么时候诞生」，会低估年龄，
// 所以只在没有别的可用信息时才用它，并在 Detail 里说明来源）。
func oldestAge(m Message, deaths []DeathEntry) (time.Duration, bool) {
	if !m.Timestamp.IsZero() {
		return time.Since(m.Timestamp.UTC()), true
	}
	for _, d := range deaths {
		if !d.Time.IsZero() {
			return time.Since(d.Time.UTC()), true
		}
	}
	return 0, false
}

// ReplayResult 是一次重放的账面。
type ReplayResult struct {
	Queue     string `json:"queue"`
	Purpose   string `json:"purpose_queue"`
	Exchange  string `json:"exchange"`
	Limit     int    `json:"limit"`
	Replayed  int    `json:"replayed"`
	Skipped   int    `json:"skipped"`
	Reason    string `json:"reason"`
	LastRoute string `json:"last_routing_key,omitempty"`
}

// Replay 从终态 DLQ 队列里最多捞 limit 条，原样回投到它的 purpose 队列。
//
// 步序是「先投成功、再 ack 原条」：反过来做就成了「原条已删、新条没投出去」，
// 那条消息从此在两处都不存在。中途任何一次失败都把那条 requeue 回去并**停止本轮**，
// 如实报 reason——继续往下跑会让同一批消息里越靠后的越容易被重复处置。
func Replay(ctx context.Context, b Broker, queue string, limit int) (ReplayResult, error) {
	purpose, err := Lookup(queue)
	if err != nil {
		return ReplayResult{}, err
	}
	if QueueKind(queue) != "dlq" {
		return ReplayResult{}, fmt.Errorf("%w: 收到 %q（阶梯队列会自己按 TTL 回去，purpose 队列本来就在消费面）",
			ErrNotReplayable, queue)
	}
	if limit <= 0 {
		limit = defaultLimit
	}
	if limit > maxLimit {
		return ReplayResult{}, fmt.Errorf("dlq: limit=%d 超过上限 %d（先确认要一次捞这么多）", limit, maxLimit)
	}
	exchange, defaultKey, err := routeFor(purpose)
	if err != nil {
		return ReplayResult{}, err
	}
	res := ReplayResult{Queue: queue, Purpose: purpose, Exchange: exchange, Limit: limit,
		Reason: "queue_empty"}
	for res.Replayed+res.Skipped < limit {
		msg, ok, err := b.Get(ctx, queue)
		if err != nil {
			return res, fmt.Errorf("dlq: get %s: %w", queue, err)
		}
		if !ok {
			// 走到这一格只可能是「循环还没到上限、队列先空了」⇒ 收尾原因是 queue_empty。
			// 到上限那一轮在循环外另说（下面）：那里意味着**还有货**，值班得决定要不要再来一轮。
			res.Reason = "queue_empty"
			return res, nil
		}
		key, reason := ReplayRoutingKey(msg, purpose, defaultKey)
		if reason != "" {
			// 判不出原始键就不猜：猜一个键会让候选面收到一条**类型对但键不对**的消息，
			// 而那条消息在对面是永久拒绝，DLQ 里则留下一条已经 ack 掉的原始条＝丢消息。
			if err := msg.Requeue(); err != nil {
				return res, fmt.Errorf("dlq: 放回取不出原始键的那条: %w", err)
			}
			res.Skipped++
			res.Reason = reason
			return res, nil
		}
		if err := b.Publish(ctx, exchange, key, ResetCounters(msg)); err != nil {
			if rerr := msg.Requeue(); rerr != nil {
				return res, fmt.Errorf("dlq: 投 %s 失败（%v），放回原队列也失败（%v）⇒ 这条现在在通道上，通道一关会自动回队",
					key, err, rerr)
			}
			res.Reason = "publish_failed"
			return res, fmt.Errorf("dlq: replay → %s/%s: %w", exchange, key, err)
		}
		if err := msg.Ack(); err != nil {
			// 已经投出去了，原条摘不掉的最坏结果是**同一次操作被投两次**——
			// 对面 inbox 的幂等位正是为这一格准备的，所以这里只如实报，不回滚。
			return res, fmt.Errorf("dlq: 回投成功但原条 ack 失败（%v）⇒ 同一 message_id 会有两份，"+
				"幂等位挡业务面，不挡队列里的重复", err)
		}
		res.Replayed++
		res.LastRoute = key
	}
	// 循环自然退出＝捞到了 limit 条，队列里可能还有货；这与「捞空了」是两种收尾，
	// 报告里必须分得清（后者值班不用再敲一轮，前者得决定要不要再来）。
	res.Reason = "limit_reached"
	return res, nil
}

// ReplayRoutingKey 决定重放回哪个键。
//
// 优先 x-death 里**与 purpose 队列对上**的那一格（原始 exchange＋routing-keys）：
// 一个队列可能绑多个键（候选面三个），只有 broker 记着这条当初是按哪个键进来的。
// 取不到就退到表里第一个键，再取不到就返回 reason 让调用方停下。
func ReplayRoutingKey(m Message, purpose, defaultKey string) (string, string) {
	for _, d := range Deaths(m.Headers) {
		if d.Queue != purpose && d.Queue != "" {
			continue
		}
		if len(d.RoutingKeys) > 0 && d.RoutingKeys[0] != "" {
			return d.RoutingKeys[0], ""
		}
	}
	if defaultKey != "" {
		return defaultKey, ""
	}
	return "", "original_routing_key_unknown"
}

// ResetCounters 把「试过几次」这一格清掉，其余字节与身份属性逐字保留。
func ResetCounters(m Message) Outbound {
	headers := make(map[string]any, len(m.Headers)+1)
	for k, v := range m.Headers {
		if k == HeaderXDeath || k == amqp.HeaderAttemptCount {
			continue
		}
		headers[k] = v
	}
	// 整数 0 而不是 "0"：发布侧写的就是 int（`amqp/publisher.go` 直接放 AttemptCount），
	// 读侧两种形态都吃，但两侧同型才不会让「谁改了写侧」这件事变成只有对面看得见。
	headers[amqp.HeaderAttemptCount] = int64(0)
	ts := m.Timestamp
	if ts.IsZero() {
		// 原始消息没带时间戳时**不补当下时间**：重放的含义是「把同一条再给一次」，
		// 补一个当下值等于给这条消息换了一个诞生时刻，下游按时间算的闸门会因此改写结论。
		ts = time.Time{}
	}
	return Outbound{Body: m.Body, ContentType: m.ContentType, MessageID: m.MessageID,
		MessageType: m.MessageType, CorrelationID: m.CorrelationID,
		Timestamp: ts, Headers: headers}
}

// Purge 清空一个队列，**必须**带上与队列名逐字相等的确认串。
//
// 为什么确认串要等于队列名而不是一个固定词：固定词能被抄（`--confirm yes` 在终端里
// 是上一条命令的复读），而队列名要人再敲一次**这次要毁掉的东西**。
func Purge(ctx context.Context, b Broker, queue, confirm string) (int, error) {
	if _, err := Lookup(queue); err != nil {
		return 0, err
	}
	if strings.TrimSpace(confirm) != queue {
		return 0, fmt.Errorf("%w：--confirm 要逐字填 %q，收到 %q", ErrConfirmRequired, queue, confirm)
	}
	n, err := b.Purge(ctx, queue)
	if err != nil {
		return 0, fmt.Errorf("dlq: purge %s: %w", queue, err)
	}
	return n, nil
}

// Deaths 从 headers 里取 `x-death` 摘要。
//
// broker 给的形态不止一种：`[]amqp091.Table`、`[]any`（每个元素是 Table）、
// 以及经适配层归一后的 `[]map[string]any`。这里三种都吃，其余一律当「没有死信记录」——
// 不做 `fmt.Sprint` 那种兜底：把一坨结构印成字符串，表现是「摘要里有一条看不懂的东西」，
// 值班据此判不了「这条为什么死的」。
func Deaths(headers map[string]any) []DeathEntry {
	// 形态归一在 headers.go（那里逐字列了 broker 会给的几种形状），
	// 本文件因此不需要认识 amqp091：核心逻辑只认 map。
	entries := deathEntries(headers[HeaderXDeath])
	if len(entries) == 0 {
		return nil
	}
	out := make([]DeathEntry, 0, len(entries))
	for _, e := range entries {
		d := DeathEntry{
			Queue:    str(e[HeaderXQueue]),
			Exchange: str(e[HeaderXExchange]),
			Reason:   str(e[HeaderXReason]),
			Count:    count(e[HeaderXCount]),
			Time:     timeOf(e[HeaderXTime]),
		}
		d.RoutingKeys = keys(e[HeaderXRoutingKey])
		out = append(out, d)
	}
	return out
}

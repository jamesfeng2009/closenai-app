// Package amqp 是 outbox 到 RabbitMQ 的投递面（V3 14 篇、17 §1/§2）。
//
// 本包只解决两件事：**拓扑怎么声明**与**一条消息怎么在 broker 确认之后才算发出去**。
// 重试阶梯／租约／死信计数一律不在这层做——那些是 outbox.Worker 与 domain.DeliveryPolicy
// 已有的面（`internal/messaging/outbox/outbox.go:93-153`），在这里重做一遍就是第二套口径。
//
// 事实源（D-R4-2）：本文件的两张表是**唯一**事实源，`contracts/events.md` 由
// `tools/eventscontract` 从表生成，不许手抄——手抄的契约文档一定会漂
// （本轮就抓到 `contracts/rpc/ownership.md:182` 那一处）。
package amqp

import (
	"fmt"
	"sort"
	"strings"

	amqp091 "github.com/rabbitmq/amqp091-go"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// 四个 exchange 逐字取 14:41-45（`ownership.md:85` 已核过同一处）。
// 队列名逐字取 14:50-55（`ownership.md:86`）。
const (
	ExchangeEvents      = "closenai.events"
	ExchangeAITasks     = "closenai.ai.tasks"
	ExchangeAICandidate = "closenai.ai.candidates"
	ExchangeDLQ         = "closenai.dlq"
)

// Exchanges 是声明用的 exchange 清单（全部 topic＋durable，理由见 14 §4 与 §13）。
var Exchanges = []string{ExchangeEvents, ExchangeAITasks, ExchangeAICandidate, ExchangeDLQ}

// RetryTiers 逐字取 14 §8 的 retry.30s / retry.2m / retry.10m（`14:130-139`）。
//
// ⚠️ 层级怎么升级：**不由 broker 静态决定**。purpose queue 的 x-dead-letter-routing-key
// 是一个写死的值（AMQP 没有「按 x-death 计数换路由」这种参数），所以阶梯升级靠**消费侧**
// 读 x-death 后改投对应层级的 key（R5 的活）。发布侧只提供队列，不发明第二套计数。
var RetryTiers = []struct {
	Suffix string
	TTL    int // 毫秒，写进 x-message-ttl
}{
	{"30s", 30_000},
	{"2m", 120_000},
	{"10m", 600_000},
}

// Route 是一条「路由 → 队列」的绑定事实：谁投、谁收、走哪个 exchange。
type Route struct {
	// RoutingKey 是 AMQP 的 topic routing key。
	RoutingKey string
	// Exchange 是这条路由走的 exchange。
	Exchange string
	// Queue 是 purpose queue 名（14 §3 逐字）。
	Queue string
	// Kind 分「命令」与「事件」两面：判据是 `delivery.go:31-36` 逐字写死的
	// 「权威模型内容变了」——命令不满足它，所以**不许**进 domain 那十条表（D-R4-3）。
	Kind string
	// Source 记下这条命名的出处，好让「本地补齐」和「V3 逐字」在同一条表行上分得清。
	Source string
}

// 命令面（Go → Python）。队列名 14 §3 逐字；routing key `ai.*.requested` 是**本地补齐**
// 的命名（V3 只给队列名：`grep -cE "requested|command" 14_MESSAGING_ARCHITECTURE.md` 实测 0），
// 这个区别已在 `ownership.md:88-89` 留痕，别在这儿把它写成 V3 原文。
//
// 键串来自 `domain.AITaskKind.RoutingKey()`（R4-③ 把它定成契约源）：生产侧入队与投递侧声明
// 必须读到同一个串，两边各写字面量迟早漂——`TestCommandRouteKeysComeFromDomain` 守这件事。
// `ai.insight.requested` 这一条在 `ownership.md:96` 明确「今天没有对应物、输入形状归 R6 判」，
// 但队列名是 V3 给的 ⇒ 本表声明队列与绑定；入队口 `Service.RequestAITask` 四类都能走，
// 谁真的下发 insight 归 R6 定。
var CommandRoutes = []Route{
	{RoutingKey: domain.AITaskObservation.RoutingKey(), Exchange: ExchangeAITasks, Queue: "ai.observation.extract",
		Kind: KindCommand, Source: "队列 14:51／键 ownership.md:94（本地补齐）"},
	{RoutingKey: domain.AITaskPattern.RoutingKey(), Exchange: ExchangeAITasks, Queue: "ai.pattern.detect",
		Kind: KindCommand, Source: "队列 14:52／键 ownership.md:95（本地补齐）"},
	{RoutingKey: domain.AITaskInsight.RoutingKey(), Exchange: ExchangeAITasks, Queue: "ai.insight.draft",
		Kind: KindCommand, Source: "队列 14:53／键 ownership.md:96，生产者归 R6"},
	{RoutingKey: domain.AITaskProactive.RoutingKey(), Exchange: ExchangeAITasks, Queue: "ai.proactive.draft",
		Kind: KindCommand, Source: "队列 14:54／键 ownership.md:97（本地补齐）"},
}

// 回投面（Python → Go）。`go.candidate.accept` 队列名 14:55 逐字；exchange 是
// 14 §3 的 `closenai.ai.candidates`。绑定键取 14 §4 逐字的 `candidate.pattern`／
// `candidate.insight`，其余 candidate.* 用同一条通配绑定覆盖（V3 未逐字列，本地补齐）。
var CandidateRoutes = []Route{
	{RoutingKey: "candidate.pattern", Exchange: ExchangeAICandidate, Queue: "go.candidate.accept",
		Kind: KindCandidate, Source: "键 14:69 逐字／队列 14:55 逐字"},
	{RoutingKey: "candidate.insight", Exchange: ExchangeAICandidate, Queue: "go.candidate.accept",
		Kind: KindCandidate, Source: "键 14:70 逐字／队列 14:55 逐字"},
	{RoutingKey: "candidate.*", Exchange: ExchangeAICandidate, Queue: "go.candidate.accept",
		Kind: KindCandidate, Source: "通配为本地补齐，覆盖 fact/evidence/proactive 家族（R5/R6）"},
}

const (
	KindCommand   = "command"
	KindCandidate = "candidate"
	KindEvent     = "event"
)

// Declarer 是声明拓扑需要的通道面。`*amqp091.Channel` 逐字满足这三个签名
// （channel.go:807/953/1304 实测），所以生产路径不需要适配器；
// 单测用一个记录调用序列的 fake，于是「声明了什么」这件事在无 broker 时也可判。
type Declarer interface {
	ExchangeDeclare(name, kind string, durable, autoDelete, internal, noWait bool, args amqp091.Table) error
	QueueDeclare(name string, durable, autoDelete, exclusive, noWait bool, args amqp091.Table) (amqp091.Queue, error)
	QueueBind(name, key, exchange string, noWait bool, args amqp091.Table) error
}

// Declare 按本表声明整套拓扑：exchange → 全部队列（purpose／阶梯／终态）→ 全部绑定。
//
// 为什么声明与绑定必须分两轮：purpose 队列的 DLX 键指向它自己的第一层重试队列，而
// `QueueBind` 打在未声明的队列上会被 broker 拒——2026-10-16 真 broker 实测
// `NOT_FOUND - no queue 'retry.30s.ai.insight.draft' in vhost '/'`。
// fake Declarer 不拒，所以这条次序只有 R4-② 的集成测抓得出来，这正是它存在的理由。
//
// 幂等性口径：**同名同参数重复声明是安全的**（AMQP 只在参数不一致时报 PRECONDITION_FAILED），
// 所以 worker 每次启动都重放这张表；R4-② 的集成测 (a) 就是把「重放两遍不报错」当判据跑在真 broker 上。
func Declare(ch Declarer) error {
	for _, name := range Exchanges {
		if err := ch.ExchangeDeclare(name, "topic", true, false, false, false, nil); err != nil {
			return fmt.Errorf("amqp: declare exchange %s: %w", name, err)
		}
	}
	for _, r := range routes() {
		if err := declarePurposeQueue(ch, r); err != nil {
			return err
		}
		for _, tier := range RetryTiers {
			if err := declareTierQueue(ch, r, tier); err != nil {
				return err
			}
		}
		if err := declareFinalQueue(ch, r); err != nil {
			return err
		}
	}
	// 本体绑定走**全表**而不是去重后的 routes()：`go.candidate.accept` 一个队列有三个键
	// （candidate.pattern／candidate.insight／candidate.*），按队列去重就等于只绑第一个键，
	// 另两类候选会被 broker 静默丢弃——2026-10-16 写集成测时自查发现，队列声明去重、绑定不去重。
	for _, r := range allRoutes() {
		if err := ch.QueueBind(r.Queue, r.RoutingKey, r.Exchange, false, nil); err != nil {
			return fmt.Errorf("amqp: bind %s(%s) to %s: %w", r.Queue, r.RoutingKey, r.Exchange, err)
		}
	}
	// 阶梯与终态面按队列去重：同一队列的三条键绑定不需要三份阶梯。
	for _, r := range routes() {
		// 拒绝的消息按键进第一层，消费侧按 x-death 换层就改投 retry.2m.*／retry.10m.*，
		// 判了死刑就投 dlq.<queue>。
		for i := range RetryTiers {
			name := TierQueueName(r.Queue, i)
			if err := ch.QueueBind(name, TierRoutingKey(r.Queue, i), ExchangeDLQ, false, nil); err != nil {
				return fmt.Errorf("amqp: bind retry queue %s: %w", name, err)
			}
		}
		if err := ch.QueueBind(FinalQueueName(r.Queue), FinalRoutingKey(r.Queue),
			ExchangeDLQ, false, nil); err != nil {
			return fmt.Errorf("amqp: bind dlq %s: %w", FinalQueueName(r.Queue), err)
		}
	}
	return nil
}

// allRoutes 是未去重的全表：声明队列时用 routes()（同名队列只声明一次），
// 建绑定时必须用它（一个队列可以有多个绑定键）。
func allRoutes() []Route {
	return append(append([]Route{}, CommandRoutes...), CandidateRoutes...)
}

// routes 把命令面与回投面合成一张待声明表，并去重同名队列
// （`go.candidate.accept` 有三条绑定，但队列只声明一次）。
func routes() []Route {
	seen := map[string]bool{}
	out := make([]Route, 0, len(CommandRoutes)+len(CandidateRoutes))
	for _, r := range append(append([]Route{}, CommandRoutes...), CandidateRoutes...) {
		if seen[r.Queue] {
			continue
		}
		seen[r.Queue] = true
		out = append(out, r)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Queue < out[j].Queue })
	return out
}

// declarePurposeQueue 只声明投递队列本体，绑定统一在 Declare 的第二轮里做（原因见那里）。
//
// 拒绝（requeue=false）的消息一律进 `closenai.dlq` 的 30s 层，**不再原地 requeue**
// （14 §8「Prefer explicit retry queues over unlimited immediate requeue」、§7 NACK 只给瞬态失败）。
func declarePurposeQueue(ch Declarer, r Route) error {
	args := amqp091.Table{
		"x-dead-letter-exchange":    ExchangeDLQ,
		"x-dead-letter-routing-key": TierRoutingKey(r.Queue, 0),
	}
	if _, err := ch.QueueDeclare(r.Queue, true, false, false, false, args); err != nil {
		return fmt.Errorf("amqp: declare queue %s: %w", r.Queue, err)
	}
	return nil
}

// declareTierQueue 声明显式重试队列：TTL 到期后回到**原 exchange＋原绑定键**，于是消息重新
// 进 purpose queue 被再消费一次。不设 x-dead-letter-routing-key 的话用的是消息当前的
// routing key（＝ `retry.30s.<queue>`，匹配不到任何绑定），所以这一行是这套阶梯能闭环的关键。
func declareTierQueue(ch Declarer, r Route, tier struct {
	Suffix string
	TTL    int
}) error {
	name := "retry." + tier.Suffix + "." + r.Queue
	args := amqp091.Table{
		"x-message-ttl":             tier.TTL,
		"x-dead-letter-exchange":    r.Exchange,
		"x-dead-letter-routing-key": r.RoutingKey,
	}
	if _, err := ch.QueueDeclare(name, true, false, false, false, args); err != nil {
		return fmt.Errorf("amqp: declare retry queue %s: %w", name, err)
	}
	return nil
}

// declareFinalQueue 声明终态死信队列：无 DLX（到这儿就停），给 R7 的 replay 工具当收件箱。
// 名字带队列名而不带键，是因为一条消息可能因不同键落到同一个队列的终态面。
func declareFinalQueue(ch Declarer, r Route) error {
	name := FinalQueueName(r.Queue)
	if _, err := ch.QueueDeclare(name, true, false, false, false, nil); err != nil {
		return fmt.Errorf("amqp: declare dlq %s: %w", name, err)
	}
	return nil
}

// TierQueueName 给第 i 层重试队列的名字（从 0 开始，逐字对应 RetryTiers）。
func TierQueueName(queue string, i int) string {
	return "retry." + RetryTiers[i].Suffix + "." + queue
}

// TierRoutingKey 给投进第 i 层重试队列时用的 routing key（走 `closenai.dlq`）。
// 消费侧按 x-death 计数换层时就自己算这个键，别在发布侧发明第二套（见 RetryTiers 的 ⚠️）。
func TierRoutingKey(queue string, i int) string {
	return TierQueueName(queue, i)
}

// FinalQueueName / FinalRoutingKey 是终态死信面。
func FinalQueueName(queue string) string { return "dlq." + queue }
func FinalRoutingKey(queue string) string {
	return "dlq." + queue
}

// EventRoutingKeys 把 domain 那十条表的键面取出来，供 publisher 区分
// 「这是已知事件路由（但今天没有消费者）」与「这键压根不在契约里」。
// 复用 domain 的事实源，不在这里抄第二份清单。
func EventRoutingKeys() []string {
	out := make([]string, 0, 10)
	seen := map[string]bool{}
	// domain 没导出那张 map 本体，只给了 RoutingKeyFor——所以这里用已知事件类型名查表，
	// 事件类型名面来自 `ownership.md` §2 与本包测试里的形状闸，双向钉住不漂。
	for _, eventType := range knownEventTypes {
		if key, ok := domain.RoutingKeyFor(eventType); ok && !seen[key] {
			seen[key] = true
			out = append(out, key)
		}
	}
	sort.Strings(out)
	return out
}

// knownEventTypes 是 personal_model_events 里「要跨进程投递」的事件类型清单，
// 与 `delivery.go:37-46` 的左列同集合。本包的形状闸断言：这张清单查出来的键
// 必须一条都不在 CommandRoutes／CandidateRoutes 里出现（否则命令面就污染了
// 「权威态变更」那条进门判据，也说明有人把两套路由并成一套）。
//
// ⚠️ EventBindingGap：这十条在 R4 期**只声明 exchange、不绑队列**，publisher 一律回 ErrNoConsumerBound。
// 这是故意留的引线，不是漏配：V3 没给它们任何下游队列（14 §3 那五个队列全是任务／候选面），
// 而 14 §13 写明 broker 不是权威事件库。给它们各配一个无人消费的队列，topic exchange 会照单
// ack 并把消息静默丢弃 ⇒ outbox 记成「已投递」，那是假账。要接上：像 CommandRoutes 那样
// 加表行即可，publisher 侧不需要第二个开关（口径同 `contracts/events.md` §四）。
var knownEventTypes = []string{
	"fact_admitted", "pattern_admitted", "pattern_reactivated", "pattern_lifecycle",
	"insight_admitted", "insight_retracted", "insight_expired",
	"outcome_recorded", "user_feedback_recorded", "evidence_withdrawn",
}

// QueueNames 展开整套拓扑里所有会被声明的队列名（含阶梯与终态面），给生成器与集成测用。
func QueueNames() []string {
	var names []string
	for _, r := range routes() {
		names = append(names, r.Queue)
		for i := range RetryTiers {
			names = append(names, TierQueueName(r.Queue, i))
		}
		names = append(names, FinalQueueName(r.Queue))
	}
	sort.Strings(names)
	return names
}

// Describe 把整套拓扑打成人读文本（生成 contracts/events.md 的唯一渲染点）。
func Describe() string {
	var b strings.Builder
	b.WriteString("## 一、exchange（14 §3 逐字，全部 topic＋durable）\n\n")
	b.WriteString("| exchange | 用途 |\n|---|---|\n")
	for _, name := range Exchanges {
		b.WriteString(fmt.Sprintf("| `%s` | %s |\n", name, exchangePurpose[name]))
	}

	b.WriteString("\n## 二、路由与 purpose 队列（队列名 14 §3 逐字）\n\n")
	b.WriteString("| exchange | routing key | purpose 队列 | 面 | 命名出处 |\n|---|---|---|---|---|\n")
	for _, r := range append(append([]Route{}, CommandRoutes...), CandidateRoutes...) {
		b.WriteString(fmt.Sprintf("| `%s` | `%s` | `%s` | %s | %s |\n",
			r.Exchange, r.RoutingKey, r.Queue, r.Kind, r.Source))
	}

	b.WriteString("\n## 三、每条 purpose 队列的伴生队列（14 §8 的显式重试阶梯）\n\n")
	b.WriteString("| purpose 队列 | 第 1 层 | 第 2 层 | 第 3 层 | 终态死信 |\n|---|---|---|---|---|\n")
	for _, r := range routes() {
		b.WriteString(fmt.Sprintf("| `%s` | `%s` | `%s` | `%s` | `%s` |\n", r.Queue,
			TierQueueName(r.Queue, 0), TierQueueName(r.Queue, 1),
			TierQueueName(r.Queue, 2), FinalQueueName(r.Queue)))
	}
	b.WriteString(fmt.Sprintf("\n共 %d 个队列（%d 条 purpose ×（1＋%d 层＋1 终态））。\n",
		len(QueueNames()), len(routes()), len(RetryTiers)))

	b.WriteString("\n阶梯参数（TTL 逐字 14:130-139）：\n\n")
	b.WriteString("| 层 | `x-message-ttl` | TTL 到期后死信去哪 |\n|---|---|---|\n")
	for _, tier := range RetryTiers {
		b.WriteString(fmt.Sprintf("| `retry.%s.*` | %d ms | 原 exchange ＋ 原绑定键（回 purpose 队列再消费一次）|\n",
			tier.Suffix, tier.TTL))
	}
	b.WriteString("\npurpose 队列的 `x-dead-letter-exchange` 是 `closenai.dlq`、" +
		"`x-dead-letter-routing-key` 是第一层 `retry.30s.<queue>`：拒绝（requeue=false）不再原地 requeue。\n")
	b.WriteString("\n⚠️ **层与层之间怎么升级不是 broker 决定的**：`x-dead-letter-routing-key` 是写死的一个值，" +
		"AMQP 没有「按 x-death 计数换路由」这种参数。所以升到 2m／10m 靠**消费侧**读 x-death 后改投对应层级的键" +
		"（R5 的活）；发布侧只提供队列，不发明第二套计数。\n")

	b.WriteString("\n## 四、事件面（domain 十条路由）：只声明 exchange，**不绑队列**\n\n")
	b.WriteString("publisher 对事件路由一律回 `ErrNoConsumerBound`（可重试，原因进 `outbox_messages.last_error`），" +
		"于是「投递成功」这一格在 R5/R6 把消费者绑上之前**不可能被写成真**。\n")
	b.WriteString("这不是漏配：14 §3 那五个队列全是 AI 任务／候选面，V3 没给十条事件任何下游队列，" +
		"而 14 §13 又写明 broker 不是权威事件库。给它们各配一个无人消费的队列，" +
		"topic exchange 会照单 ack 并把消息静默丢弃，outbox 就记成「已投递」——那是假账。\n")
	b.WriteString("要接上：照 `CommandRoutes` 的写法加表行即可，publisher 侧不需要第二个开关。\n\n")
	b.WriteString("已知事件 routing key（由 `domain/delivery.go` 的十条表折叠而来，本文件不抄第二份）：\n\n")
	b.WriteString("| event_type | routing key |\n|---|---|\n")
	for _, eventType := range knownEventTypes {
		if key, ok := domain.RoutingKeyFor(eventType); ok {
			b.WriteString(fmt.Sprintf("| `%s` | `%s` |\n", eventType, key))
		}
	}
	b.WriteString("\n去重后 distinct 键：" + strings.Join(EventRoutingKeys(), ", ") + "\n")
	return b.String()
}

// exchangePurpose 只服务于渲染：读者要知道每个 exchange 是干什么的。
var exchangePurpose = map[string]string{
	ExchangeEvents:      "领域事件（十条路由）——R4 期无消费者，见 §四",
	ExchangeAITasks:     "Go → Python 的命令面（`ai.*.requested`）",
	ExchangeAICandidate: "Python → Go 的候选回投面（`candidate.*`）",
	ExchangeDLQ:         "拒绝／重试阶梯的投递口，终态在 `dlq.<queue>`",
}

// Document 是给 contracts/events.md 的完整正文：生成声明＋口径头＋Describe()。
//
// 口径头里那句「十条事件今天没有消费者」是**故意放在最前面**的：这份文档如果被读成
// 「异步面已经通了」，R5/R6 就会被当成收尾工作而不是缺口，而现网的表现是
// N9 那条依据端点对用户永远返回空。
func Document() string {
	var b strings.Builder
	b.WriteString("# ClosenAI 异步拓扑契约（R4）\n\n")
	b.WriteString("> 本文件由 `go run ./tools/eventscontract -write` 从\n" +
		"> `go-core/internal/messaging/amqp/topology.go` 的两张表生成，**不要手改**。\n" +
		"> 校验：`go-core/internal/messaging/amqp/contract_drift_test.go` 断言本文件与生成结果逐字节一致\n" +
		"> ⇒ 改了表没重跑生成器，`go test ./...` 就红。\n" +
		"> 事实源：V3 `docs/14_MESSAGING_ARCHITECTURE.md` §3/§4/§8、`docs/17_GO_PYTHON_MESSAGING_RUNTIME.md` §1、\n" +
		"> `contracts/rpc/ownership.md` §3；命令面 routing key 的命名法与「本地补齐」标注见后者 :88-97。\n\n")
	b.WriteString("## 读这份文档之前必须先知道的一条\n\n")
	b.WriteString("**R4 只是发射器。**十条领域事件在 R4 期只有 exchange、**没有消费者队列**，\n" +
		"publisher 对它们一律回 `ErrNoConsumerBound`；命令面（`ai.*.requested`）的队列已声明、\n" +
		"入队口 `domain.Service.RequestAITask` 于 2026-10-17（批 3 · D-G14-②）拿到**第一个生产调用点**：\n" +
		"`internal/messaging/inbound` 在候选入库的同一笔 inbox 事务里请下一段（fact→pattern、pattern→insight）。\n" +
		"消费者与 candidate 回投已由 R5" +
		"接通（2026-10-16 结案：两侧消费者都在、互操作闸真跑过），\n" +
		"但**流量仍是零**，两个原因各自独立：\n" +
		"① 三个 kill switch 默认关 ⇒ 那个调用点在现网一次都跑不到；\n" +
		"② Python 侧的 handler 注册表仍为空（`backend/ai/mq/registry.py`）⇒ 真投出来的命令会被\n" +
		"`handler_unregistered` 打进死信。生产者（谁下发任务）这件事本身仍是 R6。\n" +
		"「异步面通了」的判据是 R6 之后一条洞察能跨进程走完全程，不是本表把 exchange 声明出来。\n\n" +
		"⚠️ 下面四个命令键串**不是在本文件生成的**，来自 `domain.AITaskKind.RoutingKey()`\n" +
		"（R4-③ 定的契约源）：入队侧写的键与投递侧声明的键必须是同一个串，改键只能改 domain。\n\n")
	b.WriteString(Describe())
	return b.String()
}

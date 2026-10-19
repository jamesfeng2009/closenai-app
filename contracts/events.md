# ClosenAI 异步拓扑契约（R4）

> 本文件由 `go run ./tools/eventscontract -write` 从
> `go-core/internal/messaging/amqp/topology.go` 的两张表生成，**不要手改**。
> 校验：`go-core/internal/messaging/amqp/contract_drift_test.go` 断言本文件与生成结果逐字节一致
> ⇒ 改了表没重跑生成器，`go test ./...` 就红。
> 事实源：V3 `docs/14_MESSAGING_ARCHITECTURE.md` §3/§4/§8、`docs/17_GO_PYTHON_MESSAGING_RUNTIME.md` §1、
> `contracts/rpc/ownership.md` §3；命令面 routing key 的命名法与「本地补齐」标注见后者 :88-97。

## 读这份文档之前必须先知道的一条

**R4 只是发射器。**十条领域事件在 R4 期只有 exchange、**没有消费者队列**，
publisher 对它们一律回 `ErrNoConsumerBound`；命令面（`ai.*.requested`）的队列已声明、
入队口 `domain.Service.RequestAITask` 于 2026-10-17（批 3 · D-G14-②）拿到**第一个生产调用点**：
`internal/messaging/inbound` 在候选入库的同一笔 inbox 事务里请下一段（fact→pattern、pattern→insight）。
消费者与 candidate 回投已由 R5接通（2026-10-16 结案：两侧消费者都在、互操作闸真跑过），
但**流量仍是零**，三个原因各自独立：
① 三个 kill switch 默认关 ⇒ 那个调用点在现网一次都跑不到；
② 第一段命令没有发射点：Python 侧已于 2026-10-18（T2-02）注册了 `observation` handler
   （`backend/ai/mq/handlers/observation.py`），但链表 `domain/task_chain.go` 的 `followUpAfter`
   只有 fact→pattern、pattern→insight 两条边——`ai.observation.requested` 是首段、没有前驱，
   而候选入库是生产侧唯一的调用点 ⇒ 今天没有任何人发第一条命令（发射点归 T2-03/T2-04）；
③ `pattern`／`insight`／`proactive` 三类命令仍没有 handler ⇒ 真投出来的命令会被
`handler_unregistered` 打进死信。生产者（谁下发任务）这件事本身仍是 R6。
「异步面通了」的判据是 R6 之后一条洞察能跨进程走完全程，不是本表把 exchange 声明出来。

⚠️ 下面四个命令键串**不是在本文件生成的**，来自 `domain.AITaskKind.RoutingKey()`
（R4-③ 定的契约源）：入队侧写的键与投递侧声明的键必须是同一个串，改键只能改 domain。

## 一、exchange（14 §3 逐字，全部 topic＋durable）

| exchange | 用途 |
|---|---|
| `closenai.events` | 领域事件（十条路由）——R4 期无消费者，见 §四；`source.deleted` 是本节之外唯一已绑的事件键（T2-06） |
| `closenai.ai.tasks` | Go → Python 的命令面（`ai.*.requested`） |
| `closenai.ai.candidates` | Python → Go 的候选回投面（`candidate.*`） |
| `closenai.dlq` | 拒绝／重试阶梯的投递口，终态在 `dlq.<queue>` |

## 二、路由与 purpose 队列（队列名 14 §3 逐字）

| exchange | routing key | purpose 队列 | 面 | 命名出处 |
|---|---|---|---|---|
| `closenai.ai.tasks` | `ai.observation.requested` | `ai.observation.extract` | command | 队列 14:51／键 ownership.md:94（本地补齐） |
| `closenai.ai.tasks` | `ai.pattern.requested` | `ai.pattern.detect` | command | 队列 14:52／键 ownership.md:95（本地补齐） |
| `closenai.ai.tasks` | `ai.insight.requested` | `ai.insight.draft` | command | 队列 14:53／键 ownership.md:96，生产者归 R6 |
| `closenai.ai.tasks` | `ai.proactive.requested` | `ai.proactive.draft` | command | 队列 14:54／键 ownership.md:97（本地补齐） |
| `closenai.ai.candidates` | `candidate.pattern` | `go.candidate.accept` | candidate | 键 14:69 逐字／队列 14:55 逐字 |
| `closenai.ai.candidates` | `candidate.insight` | `go.candidate.accept` | candidate | 键 14:70 逐字／队列 14:55 逐字 |
| `closenai.ai.candidates` | `candidate.*` | `go.candidate.accept` | candidate | 通配为本地补齐，覆盖 fact/evidence/proactive 家族（R5/R6） |
| `closenai.events` | `source.deleted` | `go.source.deleted` | event | 键与队列都是本地补齐（T2-06／B-08）：V3 14 §3 没有这一格，它要回答的是「来源行被删之后已入账证据怎么办」 |

## 三、每条 purpose 队列的伴生队列（14 §8 的显式重试阶梯）

| purpose 队列 | 第 1 层 | 第 2 层 | 第 3 层 | 终态死信 |
|---|---|---|---|---|
| `ai.insight.draft` | `retry.30s.ai.insight.draft` | `retry.2m.ai.insight.draft` | `retry.10m.ai.insight.draft` | `dlq.ai.insight.draft` |
| `ai.observation.extract` | `retry.30s.ai.observation.extract` | `retry.2m.ai.observation.extract` | `retry.10m.ai.observation.extract` | `dlq.ai.observation.extract` |
| `ai.pattern.detect` | `retry.30s.ai.pattern.detect` | `retry.2m.ai.pattern.detect` | `retry.10m.ai.pattern.detect` | `dlq.ai.pattern.detect` |
| `ai.proactive.draft` | `retry.30s.ai.proactive.draft` | `retry.2m.ai.proactive.draft` | `retry.10m.ai.proactive.draft` | `dlq.ai.proactive.draft` |
| `go.candidate.accept` | `retry.30s.go.candidate.accept` | `retry.2m.go.candidate.accept` | `retry.10m.go.candidate.accept` | `dlq.go.candidate.accept` |
| `go.source.deleted` | `retry.30s.go.source.deleted` | `retry.2m.go.source.deleted` | `retry.10m.go.source.deleted` | `dlq.go.source.deleted` |

共 30 个队列（6 条 purpose ×（1＋3 层＋1 终态））。

阶梯参数（TTL 逐字 14:130-139）：

| 层 | `x-message-ttl` | TTL 到期后死信去哪 |
|---|---|---|
| `retry.30s.*` | 30000 ms | 原 exchange ＋ 原绑定键（回 purpose 队列再消费一次）|
| `retry.2m.*` | 120000 ms | 原 exchange ＋ 原绑定键（回 purpose 队列再消费一次）|
| `retry.10m.*` | 600000 ms | 原 exchange ＋ 原绑定键（回 purpose 队列再消费一次）|

purpose 队列的 `x-dead-letter-exchange` 是 `closenai.dlq`、`x-dead-letter-routing-key` 是第一层 `retry.30s.<queue>`：拒绝（requeue=false）不再原地 requeue。

⚠️ **层与层之间怎么升级不是 broker 决定的**：`x-dead-letter-routing-key` 是写死的一个值，AMQP 没有「按 x-death 计数换路由」这种参数。所以升到 2m／10m 靠**消费侧**读 x-death 后改投对应层级的键（R5 的活）；发布侧只提供队列，不发明第二套计数。

## 四、事件面（domain 十条路由）：只声明 exchange，**不绑队列**

⚠️ 本节说的十条是 `personal_model_events` 那十条（`domain/delivery.go` 的表）。事件面上**已有**一个消费者队列的是另一个键：`source.deleted` → `go.source.deleted`（T2-06／B-08，见 §二那张表的 `event` 行）——它不在十条里，生产者是 Python 而不是 outbox。
publisher 对事件路由一律回 `ErrNoConsumerBound`（可重试，原因进 `outbox_messages.last_error`），于是「投递成功」这一格在 R5/R6 把消费者绑上之前**不可能被写成真**。
这不是漏配：14 §3 那五个队列全是 AI 任务／候选面，V3 没给十条事件任何下游队列，而 14 §13 又写明 broker 不是权威事件库。给它们各配一个无人消费的队列，topic exchange 会照单 ack 并把消息静默丢弃，outbox 就记成「已投递」——那是假账。
要接上：照 `CommandRoutes` 的写法加表行即可，publisher 侧不需要第二个开关。

已知事件 routing key（由 `domain/delivery.go` 的十条表折叠而来，本文件不抄第二份）：

| event_type | routing key |
|---|---|
| `fact_admitted` | `fact.created` |
| `pattern_admitted` | `pattern.created` |
| `pattern_reactivated` | `pattern.updated` |
| `pattern_lifecycle` | `pattern.updated` |
| `insight_admitted` | `insight.created` |
| `insight_retracted` | `insight.retracted` |
| `insight_expired` | `insight.expired` |
| `outcome_recorded` | `outcome.recorded` |
| `user_feedback_recorded` | `feedback.recorded` |
| `evidence_withdrawn` | `evidence.withdrawn` |

去重后 distinct 键：evidence.withdrawn, fact.created, feedback.recorded, insight.created, insight.expired, insight.retracted, outcome.recorded, pattern.created, pattern.updated

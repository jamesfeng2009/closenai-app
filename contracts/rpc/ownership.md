# 跨语言通信面归属表（R0 · D-9 落表）

> **这份表的作用只有一个**：R1 的 `.proto`、R2 的 Go client、R3 的 Python server、R4/R5 的队列，
> 每一行都必须能指回这里。指不出来的方法、队列、事件类型就**不该存在**——
> 这是 V3 `06:173` 那句反面约束在本仓的可执行形态，不是文档装饰。
>
> 裁决记录日期：2026-10-16。下方每个锚点都在**当天逐条 grep 过**（文末「复核记录」给实测计数），
> 行号会漂，所以每条都同时给了「文件 + 干什么的那句话」，不靠行号自证。

---

## 0. 判据（四条，互相制衡）

| # | 判据 | 出处（逐字） |
|---|---|---|
| 判据 1（正面） | 「确实需要**同步低延迟**才用 gRPC + Protobuf」，V3 逐条列了**五条**：embedding service（`06:167`）/ reranking service（`:168`）/ model capability check（`:169`）/ low-latency inference（`:170`）/ health-capability calls（`:171`） | V3 `06_SYSTEM_COMPONENTS_AND_BOUNDARIES.md:164-171` |
| 判据 2（反面） | 「**Do not introduce gRPC merely because both runtimes use Protobuf.**」 | V3 `06:173`（逐字整句） |
| 判据 3（异步清单） | 这八类**默认走 MQ**，不许往同步面挪：source processing、candidate generation、event propagation、pattern detection、insight drafting、proactive drafting、**asynchronous embeddings**（`06:161`）、reconciliation jobs | V3 `06:153-162` |
| 判据 4（事件面进门） | 只有「**权威模型内容变了**」的事件才进出箱路由表；提案被拒/未定、以及 V3 里没有下游队列的，都不进 | `go-core/internal/domain/delivery.go:31-36` 注释原文 |

判据 2 在本仓的落法＝**每个 gRPC 方法必须单独回答「为什么不能等 MQ 回投」**，答不出就改成 MQ（见 §1 第三列，那一列就是为这条判据存在的）。

⚠️ **判据 3 里有个坑，先说破**：`asynchronous embeddings` 与判据 1 的 `embedding service` **同时成立**且指向不同东西——
前者是批量/后台的向量化作业，后者是当场要向量的那次调用。本仓两处实测点正好各占一边
（`memory_service.py:465` 单条、写在用户提交路径上；`cron_service.py:1024` 批量、在漂移检测里）。
默认值＝**两者都先落在同一个 `Embed` 方法上**，理由是 cron 那处今天就是同步 `await`、没有需要解耦的等待方；
**改判条件**写在 §1 的脚注里，不在这里预设。

---

## 1. 面① — 同步 gRPC 方法（首批三个，多一个都不加）

| 方法 | 实测调用点（本仓今天真的在跑的地方） | 为什么不能等 MQ 回投（判据 2） | 失败面（跨进程后必须保持/必须改） |
|---|---|---|---|
| `Embed` | `backend/services/memory_service.py:465`（在 `_embed_or_none:462` 里；这个 helper 被四个调用点复用——`:163` 写入、`:356`/`:379` 检索词向量化、`:441` 改内容，AST 实测）、`backend/services/cron_service.py:1024`（`_mean_embedding`，批量取均值） | 记忆刚落库就要能被语义检索到，**而检索词本身也要当场向量化**（`:356`/`:379`）。等回投＝用户写完记录后有一段时间**搜不到自己刚写的那条**，而这条恰恰是他最会去搜的；查询侧等回投更是直接把搜索结果打掉 | **保持** fail-soft：今天两个调用点都 `except → return None`（不阻塞主流程、只少一个向量）。跨进程后不得变成硬失败——一次网络抖动不该让「记一条想法」失败；但**检索侧**拿不到向量＝那次搜索语义面失效，降级方向要在 R2 明确（词面检索兜底，不是空结果） |
| `ClassifySafety` | `backend/services/safety_service.py:356`（`check_text`，真正的模型调用在 `:375`，外面包着 `asyncio.wait_for`）；流式侧同一判定由 `SentenceGate` 按句调用 | 内容交到用户手上**之前**必须判完。等回投＝先把违规句发出去再补一刀，那不是安全闸 | **必须反过来**（D-8）：今天两侧都写明 fail-open——`check_text` 的 docstring `:360`「超时/异常/不可解析 → fail-open True + 日志」、`SentenceGate` 的 `:48`「检测异常：fail-open 放行该句」。跨进程后超时**不得等于放行** ⇒ fail-closed 或降级模板，落点在 R2 的 client |
| `StreamChat` | `backend/routers/chat.py:54-55`（`POST /chat/stream`）→ `backend/services/message_generator.py:122`（`llm.stream`） | 用户在线等 token。走 MQ 等于把首字延迟变成「排队 + 回投」两轮 | 首字延迟与背压要**一条实测数字**，不接受「应该差不多」（R2 验收项）；Go 侧 SSE 由 gRPC server streaming 透传＝裁决文档 §八 风险 3 的结案点 |

**脚注（Embed 的改判条件）**：若 `cron_service.py:1024` 那类批量向量化出现「一次几百条、把 gRPC 通道占满、
影响在线的 `Embed`/`StreamChat`」，就把它**挪去判据 3 的异步面**（`ai.*.tasks` 一条批量命令），
而不是给 gRPC 加超时特例。这条改判要带前后实测计数。

**面①的归属纪律**：Python 的 gRPC handler 只做 `decode → 调 backend/ai/* → encode → return`，
**业务分支零内嵌**（R3）。留痕照旧每次调用一行 `ai_generation_runs`
（O4 已交付，`backend/core/generation_run.py`），provider/model/purpose/tokens/latency/status 不因换协议而丢。

---

## 2. 面② — 异步**事件**路由（权威模型内容变了 ⇒ 出箱）

已在代码里的十条（`go-core/internal/domain/delivery.go:37-48`，左列＝`personal_model_events.event_type`，右列＝routing key）：

```
fact_admitted          → fact.created
pattern_admitted       → pattern.created
pattern_reactivated    → pattern.updated
pattern_lifecycle      → pattern.updated
insight_admitted       → insight.created
insight_retracted      → insight.retracted
insight_expired        → insight.expired
outcome_recorded       → outcome.recorded
user_feedback_recorded → feedback.recorded
evidence_withdrawn     → evidence.withdrawn
```

其中**只有** `pattern.created` / `pattern.updated` 是 V3 逐字（`14:67-68`），其余按同一命名法本地补齐＝已登记的本地口径（七-6 N7-3），不是 V3 原文。
⚠️ `delivery.go:32` 那行注释里写的「14:66-70」是**旧快照的行号**（V3 那份文件后来动过），以这里的 `14:67-68` 为准。

**待补的一条**：`evidence.accepted`。
V3 `14:66` 的 routing key 表里有它，Go 侧**没有对应事件类型**（全仓 `evidence.accepted|evidence_accepted` 实测 **0 命中**）。
它满足判据 4（`evidence` 是权威表，accept 就是内容变了）⇒ 归 R6 一起补，**不在这里预先发明**第二个键名。

**刻意不进表的五类**（判据 4 的两条否定支，`delivery.go:34-36` 原文；⚠️ 代码注释里把它们编号为「判据 1／判据 2」，本表另立四条判据，同一件事两个编号，别在对表时读串）：
`candidate_*`、`unknown_admitted`、`proactive_*`、`target_completed`、`insight_revalidation_requested`。
前两类是「提案被拒/未定」⇒ 权威内容没变；后三类在 V3 里没有下游队列。
⚠️ `insight_revalidation_requested` 是**已经在发的权威事件**
（`go-core/internal/domain/service_outcome.go:519`、`:634`），它「不发到 MQ」这件事的正当性来自判据 4 的第二支，
而不是来自它不存在——读表的人很容易在这里改错，所以写明白。

---

## 3. 面③ — 异步**命令**路由（Go 下发任务，Python 提案）

V3 给的是**exchange 与队列名**，不是命令路由键：
`14:41-45` 的拓扑四条 = `closenai.events` / `closenai.ai.tasks` / `closenai.ai.candidates` / `closenai.dlq`；
`14:50-55` 的 purpose-oriented 队列 = `ai.observation.extract`、`ai.pattern.detect`、`ai.insight.draft`、`ai.proactive.draft`、`go.candidate.accept`。

⇒ 本仓把这四条队列落成命令面时用的 routing key `ai.*.requested` **是本地补齐的命名**（V3 只给队列名，
`grep -cE "requested|command" 14_MESSAGING_ARCHITECTURE.md` 实测 **0**，注意要 `-E`：用普通 grep 传 `requested|command` 是在找字面量，恒 0 命中、等于没测）。这个区别必须留痕，
否则下一个人会以为它是 V3 原文而不去对表。

| 命令 | 目标队列（V3 逐字） | 下游 AI 工作（今天的进程内实现） | 为什么是命令而不是事件 |
|---|---|---|---|
| `ai.observation.requested` | `ai.observation.extract` | `memory_service.extract_and_store`（`:307`，模型调用在 `:322`）；同一族的还有 `_detect_conflict`（`:270`，调用在 `:283`） | 权威内容没变（判据 4 不成立），变的是「请求一个提案」。⚠️ 冲突判定今天由 Python 直接「新建＋旧行 supersede」，R5 要把它改成 candidate 回投 |
| `ai.pattern.requested` | `ai.pattern.detect` | `growth_service.generate_pattern_discovery`（`:728`，调用在 `:755`） | 同上，候选要过 N4 的算分门才进权威态 |
| `ai.insight.requested` | `ai.insight.draft` | **今天没有对应物**：`growth_service.generate_daily_insight:384` / `generate_weekly_insight:420` 那些「洞察」写的是 legacy 文案面，**不是** canonical insight | 这条队列名来自 V3，但本仓的 canonical insight 只能由 N4 九关产生 ⇒ 归 R6「命令面接生产者」时一起判，**不在这里预先发明**它的输入形状 |
| `ai.proactive.requested` | `ai.proactive.draft` | `message_generator.generate_proactive_message`（`:138`，调用在 `:240`）；追问支在 `generate_follow_up`（`:359`，调用在 `:390`） | 同上，出草稿不等于有资格发（N5 的频次/疲劳门在 Go） |

**投递方式（R4 的关键决定，先落在表上）**：命令与事件**共用同一条 outbox**，但**路由到不同 exchange**。
判据写在 `delivery.go:31-36`：进门条件是「权威模型内容变了」，命令不满足 ⇒
`ai.*.requested` **不进 §2 那张十条表**，而是在决策事务内 `EnqueueOutbox` 一条行、路由到 `closenai.ai.tasks`，
从而**复用** outbox 的租约 / 退避 / 死信与 publisher confirms（V3 `14:163-165`：「不该因为客户端库没报错就被标记成投递成功」⇒ 确认后才 `MarkOutboxPublished`）。
代价与理由：丢一条命令＝用户永远等不到那条洞察，这比多一张表贵。

回投面（Python → Go）：`go.candidate.accept` 队列 + Go 的 inbox 消费
（`inbox_messages` 表、`ConsumeIdempotent` 定义在 `go-core/internal/domain/delivery.go:267`）。
**现状实测**：`ConsumeIdempotent` 除定义与注释外**零调用方**（非测试 grep 只命中它自己那三行）
⇒ 这条队列今天单向都不存在，归 R5。

### 3.1 其余 15 个 LLM 调用点：默认**不跨进程边界**（这张表最容易骗人的一格）

生产侧 `llm.complete/stream/embed` 实测共 **24** 个调用点、分布在 **13** 个文件
（手段＝AST 逐 `Call` 节点计数，排除 `core/llm.py` 自身与 `tests/`。⚠️ 单纯 grep `llm\.(complete|stream|embed)` 会命中 **25** 行，
多出的那行是 `safety_service.py:36`——它在 `SentenceGate` 的 docstring「用法：」示例里，不是真调用。计数差就在这，别拿 grep 数当口径）。
扣账：§1 三个方法吃掉 **4** 个（`memory:465`、`cron:1024`、`safety:375`、`message_generator:122`），
§3 四条命令吃掉 **5** 个（`memory:283/322`、`growth:755`、`message_generator:240/390`），
**剩下 15 个都是「读路径的措辞生成」**：

| 调用点 | 所属函数（AST 解析所得，不是猜行号） | 今天怎么被触发 |
|---|---|---|
| `mood_service.py:183` / `:224` | `_interpret:156` / `_deep_interpretation:196` | Python 自己服务的端点 |
| `bedtime_service.py:307` | `_llm_or_fallback:305` | Python 自己服务的端点 |
| `curriculum_service.py:274` | `_plan_week:242` | **Python 自己的 cron**：`GET /cron/curriculum`（`routers/cron.py:174`，`:186` 处调用）→ `dispatch_curriculum_generation`（`cron_service.py:600`）→ `generate_weekly:126` → `_plan_week`。Go 侧只服务读（`server.go:96-97` 两条 curriculum 路由，`curriculum_*.go` 内模型调用 **0 命中**） |
| `enneagram_service.py:235`、`personality_service.py:395` | 都是 `_classify_free_answer`（`:226` / `:386`） | 测评答题路径 |
| `personality_service.py:257` / `:450` / `:515` | `refresh:212` / `_generate_profile_from_answers:421` / `generate_combo_report:470` | 画像与组合报告 |
| `growth_service.py:500` / `:783` / `:870` | `_generate_next_week_forecast:476` / `_generate_report_body:765` / `_llm_or_fallback:858` | 周报/预测正文 |
| `reflection_service.py:89` | `generate_draft:38` | 反思草稿 |
| `share_service.py:1185` | `_generate_tagline:1162` | 分享卡标语 |
| `push/copy.py:157` | `_llm_body:126` | cron 推送文案 |

**默认值：这 15 处不进首批任何一面。** 理由不是「来不及做」，而是判据 2 反过来用也成立——
它们今天**根本不跨进程**（FastAPI 端点绝大多数仍在 Python 侧服务：`server.go:91-120` 灰度开关下只有 **11 条**端点落到 Go，另有 `:88` insight evidence 一条不受灰度键管；98 条的分母见 §R 与 N8 各批登记），
在这里给它们预先造一条 gRPC 方法或一条 MQ 命令，等于**为一次还没发生的切流付两次网络往返**，
而 V3 明令不许「只因为两边都用 Protobuf 就上 gRPC」。

**改判的触发条件（写死在这里，免得下次口头通过）**：
1. 某个端点被 N8 切到 Go ⇒ 它那处生成**必须回到本表加一行**并单独回答判据 2，才允许出现新方法/新命令；
2. `push/copy.py:157` 这类 cron 文案是特例：R6 把 cron 从「Python 直发」改成「Go 门判定 + 下发命令」时，
   它随那条命令进面③（`ai.proactive.draft` 家族），**不需要**新 gRPC 方法；
3. 任何时候都不许用 `health/capability calls`（`06:171`）给文案生成开后门——它不是 capability check。

✅ **`curriculum_service.py:274` 已查实（2026-10-16，本批原来留的那句必查项到此结案）**：
这次模型调用由 **Python 自己的 cron 端点**触发——`GET /cron/curriculum`（`routers/cron.py:174`）
→ `dispatch_curriculum_generation`（`cron_service.py:600`）→ `generate_weekly:126` → `_plan_week:242`；
Go 侧的 curriculum 只有读面（`server.go:96` 的 `GET /api/v1/curriculum/week`、`:97` 的 `.../{event_id}/read`），
`curriculum_read.go` 与 `curriculum_week.go` 里模型调用**实测 0 命中**。
⇒ 判据 2 两侧都不成立：**面③不成立**（没有 Go 决策方，命令无从 `EnqueueOutbox`——是定时器叫 Python 干活，不是 Go 下发）；
**面①不成立**（没有在线等待方，每用户每周 1 次、跑在小时级 cron 里，等得起）。
所以它按 §3.1 的默认值留在 Python 进程内，**不进首批任何一面**。
它的改判条件与 `push/copy.py:157` 同一类：当且仅当 R6 把「生成」也纳入 Go 的门（要过频次/配额/权威写纪律）时，
才随那条命令进面③——**不预先给它造方法**。

⚠️ **顺带实测到的一条边界事实（本批不修，只留痕）**：curriculum 生成走的是 `journey_repository.schedule`
（`curriculum_service.py:146`），而 `journey_events` 这张表 Go 侧也在写
（`store/legacy_batch3.go:198` INSERT、`:173/:192/:204` UPDATE、`legacy_batch2.go:119` UPDATE）
⇒ 「Python 只提案、不写权威表」这条纪律（§5 第二条）在 curriculum 这条链上**今天并不成立**。
这不是通信面的决定，是归属面的欠账，登记为 R5/R6 开工前要对的一条：**先定 curriculum 卡的权威写权归谁，再谈它进哪一面**。

---

## 4. 表外（V3 允许，但本仓零调用点 ⇒ 不进首批）

| 项 | V3 出处 | 本仓实测 | 加进面①的门槛 |
|---|---|---|---|
| reranking service | `06:168` | 全仓 `rerank`（不分大小写、含 Go/Py）**0 命中** | 先有真实检索链路需要它，并给出「为什么不能等 MQ」那一句 |
| model capability check / health | `06:169`、`06:171` | 全仓 `capability` **0 命中** | 出现「运行时要问对面能干什么」的产品动作；不要用健康检查冒充 |
| 批量 embedding（判据 3 的 asynchronous embeddings，`06:161`） | `06:161` | `cron_service.py:1024` 是批量但今天同步 await | 见 §1 脚注的改判条件 |

---

## 5. 边界纪律（这张表不许改的东西）

- **Go 的领域层保持纯决策**：`internal/domain` 不得依赖 PostgreSQL / RabbitMQ / Protobuf / LLM
  （V3 `07:8` 逐字 "It must not depend on PostgreSQL, RabbitMQ, Protobuf or an LLM."，
  并见 `07:183-186`）⇒ `go-core/internal/domain/arch_test.go:61-68` 的 `transportImports` 黑名单
  （含 `google.golang.org/grpc`、`github.com/rabbitmq`）与 V3 **同源**，
  所以 R1/R2 加 gRPC 依赖时**不改这个闸**：proto 与 client 归 `internal/aiclient/`、`internal/messaging/amqp/`，
  domain 只收已解码的输入对象。这是本表预见到最容易做错的一格，写在前面。
- **Python 只提案，不写权威表**（O3 的物理落点）：三处直写点改造归 R5，含
  `memory_service.py:270` 的 `_detect_conflict`（现为「立即新建 + 旧行 supersede」→ 改 candidate 回投）。
- **对外只有 Go 的 HTTP/SSE**：gRPC **不出 Ingress**，只在集群内（R7 的 manifests 口径）。
- **依赖面实测**（本批 2026-10-16 复核）：`go-core/go.mod` 无 grpc、无 amqp；
  `backend/requirements.txt` 无 grpcio、无 aio-pika/pika ⇒ R1/R2/R4 各自要新增依赖，
  且都要过 D-7 那套「按实测计数 + 体积/内存前后差」的取证，不许口头通过。

---

## 6. 复核记录（写这份表当天跑的 grep，全部是**当天实测**而不是引用旧文）

| 断言 | 实测手段与结果 |
|---|---|
| gRPC 三方法各有真实调用点 | AST 逐 `Call` 计数：生产侧 `llm.complete/stream/embed` 共 **24** 个调用点 / **13** 个文件（排除 `core/llm.py` 与 `tests/`；grep 口径会给 25，多的一行是 `safety_service.py:36` 的 docstring 示例）。三条链分别命中 `memory_service.py:465`、`cron_service.py:1024`、`safety_service.py:375`（外层 `check_text` 在 `:356`）、`chat.py:54`→`message_generator.py:122` |
| rerank / capability 零调用点 | 原写法 `grep -rniE "rerank\|capability"`（py+go，排除 `_test`）＝**0**。<br>⚠️ **2026-10-16 更正取证手段（结论不变，但这一行当时不可复制执行）**：Markdown 表格里的竖线**必须**写成转义形式才不会断列，而那份转义粘进 shell 后，在 ERE（`grep -E`）下 `\|` 变成**字面竖线** ⇒ 搜的是 `"rerank\|capability"` 这一个字符串，本来就会 0 命中，等于没测。可复制的等价写法（改用多个 `-e`，一条竖线都没有）：<br>`grep -rniE -e rerank -e capability --include='*.py' --include='*.go' --exclude='*_test.py' --exclude='*_test.go' backend go-core` ＝ **6 命中**，逐文件核对全部在生成物里（`go-core/gen/closenai/v1/closenai_ai_rpc_grpc.pb.go` 4、`backend/gen/closenai_ai_rpc_pb2_grpc.py` 2，都是 R1 抄进 `*_pb*` 的契约注释）；再加 `--exclude-dir=gen` ＝ **0 命中**（2026-10-16 复跑）。⇒ 「不建第四个 gRPC 方法」的判据成立，但下一批复用这行时必须带上 gen 排除，否则数到的是自家生成物 |
| `evidence.accepted` 在 Go 不存在 | `grep "evidence.accepted\|evidence_accepted"`（go+py）＝**0** |
| `Admit*` / `ConsumeIdempotent` 无生产调用方 | 非测试 grep 只命中**定义处与注释**（`service_proactive.go:97`、`service_evidence.go:121`、`delivery.go:267`） |
| 只有 `cmd/go-api` | `ls go-core/cmd` ＝ `go-api` 一项；`internal/messaging/` 只有 `outbox/outbox.go` |
| `backend/ai/` 目录尚不存在 | `ls -d backend/ai` ＝ No such file（⇒ O0「AI 独立目录 + 边界闸」未做，R3 的落点先建在这里） |
| 出箱面骨架就位、只差 broker 实现 | `outbox.go` 实测行号：`Repository`:29、`Publisher`:57、`RunOnce`:93、`Run`:163（§R 旧文写的 `:76 Worker.Run` 已漂，**以这里为准**） |
| curriculum 的模型调用谁在调 | 链逐段命中：`routers/cron.py:174` → `cron_service.py:600`（`:619` 调 `generate_weekly`）→ `curriculum_service.py:126 → :141 → :274`；`grep -c "llm" curriculum_read.go curriculum_week.go`＝**0**；Go 侧 curriculum 路由两条都在读面（`server.go:96-97`） |
| 切流到底切了几条（决定 §3.1 默认值的前提） | `grep -nE 'mux\.HandleFunc\(' go-core/internal/httpapi/server.go` 逐条数：灰度开关下 **11** 条（`:91/:92/:96/:97/:101/:102/:108/:109/:118/:119/:120`）＋ `:88` insight evidence 不受灰度键管；`deploy/k8s/10-config.yaml` 的 `CLOSEN_CUTOVER_*` 七个键（`:26/:27/:31/:36/:43/:59/:60`）**全为 "0"** ⇒ 「未切流 ⇒ 不必跨进程」这条默认值今天成立 |
| V3 的每条锚点行号 | `nl -ba` 逐行读出后核对：异步八类＝`06:155-162`、同步五条＝`06:167-171`（引导句 `06:164-165`）、反面约束整句＝`06:173`、domain 纯度＝`07:8`；MQ 面＝四 exchange `14:42-45`、五队列 `14:51-55`、routing key 表 `14:65-70`（`evidence.accepted`:66、`pattern.created/updated`:67-68）、confirms 正文 `14:163-165`。⚠️ 本表初稿把 rerank/capability/health/asynchronous embeddings 各写成 `06:166/167/160`——**统一偏了 2 行**，现已按 `nl -ba` 实测改为 `06:168/169/171/161`；`07:183-186` 这句是 `arch_test.go:60` 注释里抄来的，本批未独立复核该行号（只复核了 `07:8` 逐字） |
| `journey_events` 写权今天分两家 | Python：`curriculum_service.py:146 journey_repository.schedule`；Go：`store/legacy_batch3.go:198/173/192/204`、`legacy_batch2.go:119`（grep `journey_events` + insert/update，排除测试） |

## 7. 本表验收判据（自查用，也是下一批开工前的门）

1. 面①每一行都能指回一个**本仓实测**的调用点——能（§1 第二列三行都是 file:line）。
2. 面②每一行都能指回一个既有事件类型或 V3 的 routing key——能（十条在代码里，`evidence.accepted` 指回 `14:66`）。
3. 面③每一行都能指回 V3 的一条队列，并说清它是命令不是事件——能（四条队列名逐字来自 `14:50-55`）。
4. 表外每一项都给了「什么时候才进来」——能（§4 第三列）。
5. **指不出的已从表里删掉**：本表初稿曾把 `ExtractFactCandidate`/`DraftInsight`/`DraftProactive` 当作 gRPC 方法，
   与判据 3、判据 4 自相矛盾，已按 §3 归命令面（这个纠正在 §R 顶部也有留名，不是新写的）。
6. **§3.1 那条默认值有前提，不是永久成立**：它靠的是「11 条端点虽已注册到 Go，但七个 `CLOSEN_CUTOVER_*` 键全为 0%」这次实测（§6「切流到底切了几条」那行）。
   任一域把百分比抬起来 ⇒ 那条链就**真的跨进程**了，必须先回到 §3.1 加一行并单独回答判据 2，**不许在代码里先出现新方法/新命令**。
7. 本表**留了两条欠账**（不是遗漏，是范围）：① 裁决文档 D 表的 D-9 行与 §八 风险 3 的结案标记落在 `closenai-prd` 子模块，按「脏子模块不入库」的既有口径单独收口；
   ② `journey_events` 今天两家都写（§3.1 末），归 R5/R6 开工前先定权威写权，本表不替它决定。

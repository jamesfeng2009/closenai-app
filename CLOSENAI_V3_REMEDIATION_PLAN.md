# CLOSENAI V3 整改方案（Baseline → 本仓落地）

> **输入基线：** `CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md`（V3.0-draft／2026-10-17）
> **审计基准：** `master@b2ceed5`（2026-10-17「批 T」结案）
> **审计日期：** 2026-10-18
> **配套台账：** `CLOSENAI_V3_TASK_BOARD.md`（任务状态与逐条实现方式）
> **本文件性质：** 方案与决策记录。**代码未因本文件改动**；所有行号均为本次实测。

---

## 0. 结论先行

### 0.1 三句话

1. **Baseline 的 P0「可达性整改」已经做完，不要重做。** journey 调度、Life Archive PDF 交付、隐私/条款页面、未接线 API 清理、推送开关、记忆接线等，在 2026-10-15～10-17 的三份工作单里已结案，代码可验证。
2. **真正的差距不在"接线"，而在"领域层已建好、产品面没接电 + 异步链路还没有编排"**。Go 侧的九关 Gate、六个反馈态、outcome 状态机、证据读模型全部实现且有测试；但 22 张 canonical 运行时表里只有 `insights` 一张有读端点（且不在冻结契约、前端不可达），Python 侧连只读 SQL 都没有；同时项目尚未部署，三进程与 broker 缺一份 compose 编排。
3. **整改不是重写，是三件事：** ① 给异步 AI 链路一个常驻宿主；② 把 `Source → Evidence → Insight → Feedback` 接通**一条**纵切；③ 补齐合规与治理缺口（导出、consent、静默时段、幂等约束、CI、契约台账）。

### 0.2 一句话前置

> **代码不是"跑不了"，是"还没编排"。** 目标形态已确定为 **VPS 单机（Docker Compose）+ 反向代理**，不是 Vercel：
> `deploy/Dockerfile.py-api`、`go-core/Dockerfile` 都在，`python -m backend.ai.mq.worker` 就是标准进程入口，
> `go-core/cmd/go-api` 也是常驻 HTTP worker。缺的只是 compose 编排、调度源迁移与发布验收。
>
> ⇒ Wave 2～Wave 4 **不被架构阻塞**。只要 `T0-01` 的本地 compose 起得来（PG + RabbitMQ + go-api + py-worker），
> 跨语言链路即可开发自测；生产上线由 `T0-02` 收口。
>
> ⚠️ 订正：本文初版把仓库里「现役生产＝Vercel + Supabase」（`功能缺口修复工作单-2026-10-17.md:14`）当成了事实，
> 因而误判为"serverless 无处跑 worker"。该句本身是失真口径，需一并订正（见 `T0-02`）。

### 0.3 证据标签

沿用 Baseline §0.1：`VERIFIED` / `PARTIAL` / `PLANNED` / `BLOCKED` / `MEASURED`。

---

## 1. 现状审计

### 1.1 已关闭的 Baseline P0（代码/配置面，**不要重做**）

> **重要口径**：下表 `VERIFIED` 指"代码、测试、配置声明中可核实"，不等于"线上已启用"。
> 由于本项目**尚未部署**（无生产环境），按 Baseline §15 的六态，当前所有能力最高只到 `WIRED` / `E2E_VERIFIED`，
> **没有任何一项是 `PRODUCTION_ENABLED`**。这也是 `T0-01`/`T0-02` 的由来。

| Baseline 要求 | 代码状态（§15 六态） | 生产状态 | 证据 |
|---|---|---|---|
| 主动消息定时任务 | `WIRED`（声明完整，未跑过） | 未部署 | `vercel.json:14-17` 已含 `/api/v1/cron/journey`（当前声明源）；`backend/tests/test_cron_schedule_coverage.py` 从装饰器推导覆盖；`T0-02` 迁到宿主 cron |
| Life Archive 付款后交付 | `E2E_VERIFIED` | 未部署 | `PaywallScreen.tsx:313-324` 已购档位出现导出按钮 → `getLifeArchivePdf` → `exportPdf`；`pdfExportWiring.test.ts` |
| 隐私政策／服务条款 | `WIRED` | 未部署 | `site/src/pages/privacy.astro`、`terms.astro`、`zh/*`；`backend/tests/test_legal_pages.py` |
| API 已实现但前端零引用 | `WIRED`（仅剩 2 条，均登记） | 未部署 | `clearPushToken`（`api.ts:606`）、`exportLifeArchive`（`api.ts:928`） |
| 记忆管理可查看/编辑/删除 | `WIRED` | 未部署 | `MemoriesScreen.tsx` 使用 list/create/get/version/delete 五条；仅 `/memories/search` 零消费者 |
| 主动消息可关闭、可设频率 | `WIRED` | 未部署 | `ProfileScreen.tsx:337-368`；`test_push_enabled_setting.py`、`test_push_frequency_setting.py` |
| 订阅购买/权益/恢复/退款 | `E2E_VERIFIED` | 未部署（未跑沙盒真单） | `iap_service.py:240-336`（verifyReceipt + 21007/21008）、`:147-213`（JWS x5c 钉扎）、`:585-605`（退款撤销）、`:464-515`（恢复购买） |
| 账户删除覆盖派生数据 | `E2E_VERIFIED` | 未部署 | `user_service.py:80-125` `PURGE_TARGETS` 覆盖 22 张 canonical 表 + outbox + push token + share_exports |
| AI 安全态四态 | `IMPLEMENTED` | 未部署 | `closenai_common.proto` + `candidate.go:25-33` 已含 `UNSAFE` |
| 埋点计数不说谎 | `IMPLEMENTED` | 未部署 | 批 4 已改 `relayed`/`dropped` 分离（`analytics_service.py`） |
| cron 覆盖有自动化闸 | `IMPLEMENTED` | 未部署 | `test_cron_schedule_coverage.py` + `scripts/mutate_g1_cron_schedule_gates.py` |

> ⚠️ 台账真实性提醒：`contracts/endpoints.md` 第 79/80 行的「前端调用」列与当前代码不一致（`GET /share/life-archive/pdf` 记为 `—`，实际 `PaywallScreen.tsx:317` 已调用；`POST /share/life-archive` 记 `api.ts:910`，实际在 `:928`）。原因是 `scripts/export_contract.py:181-183` 的扫描正则漏掉了**换行模板串 + `binary:true`** 出口。→ 任务 `T0-04`。

### 1.2 尚未成立的 Baseline 条款

| # | Baseline 条款 | 状态 | 现状证据 | 影响 |
|---|---|---|---|---|
| 1 | `Source` 落库（§6.1、§9.2） | `PLANNED` | `source_records` 表存在（`canonical.py:116`），除 purge 外零读写；`canonical.py:6-8` 明文「Python 不写这些表（除 purge）」 | 主链路起点缺失 |
| 2 | `Evidence` 可追溯（§7.3 不变量 1） | `PLANNED` | `evidence` 无生产写入调用方（`AdmitEvidence` 只被测试驱动）；唯一读端点 `GET /api/v1/personal-model/insights/{id}/evidence`（`go-core/internal/httpapi/server.go:94`）**不在冻结契约**且前端零调用 | 无证据支撑的洞察 |
| 3 | `Insight` 带依据/置信/版本 | `PARTIAL` | 现网洞察来自 `growth_records`（`growth_service.py:207`），响应仅 `level/content/period/related_memory_ids`；前端用 `services/evidence.ts:50-65` 把「当期记忆条数」当依据 | 用户看到的"依据"不是领域证据 |
| 4 | 用户可纠正洞察（§7.3 不变量 4/6） | `PARTIAL` | Go `RecordUserFeedback`（`service_outcome.go:306`）六态完整、`StatusForDerivedTarget:622-634` 已映射 confirmed/retracted/corrected/expired/dismissed；**HTTP 入口为 0**（`httpapi` 无 feedback 路由） | 纠错只改内存不改真值 |
| 5 | `Goal`（§6.1） | `PLANNED` | `personal_goals` 有表（`canonical.py:221`）与 SQL（`canonical.sql:216`），但 `InsertGoal` 在 `internal/domain` **0 命中**，store 只有 `CompleteGoal`（`canonical_repo.go:737`） | 目标能力不存在 |
| 6 | `Action`（计划/完成/跳过） | `PARTIAL` | 被降级为记忆标签 + journey 事件：`commitment_service.py:140-152`、`:171-212`；无 `goal_id`、无 skipped | 行动不可追踪 |
| 7 | `Outcome`（含负面/无变化） | `PARTIAL` | `outcomes` 有 `result`/`origin`（`canonical.py:488-502`），Go `RecordOutcome`（`service_outcome.go:47`）完整，**HTTP 入口为 0** | 结果不可记录 |
| 8 | `ProactiveTask` 幂等/可审计 | `PARTIAL` | `proactives` 有八态（`proactive.go:71-78`），但无 HTTP；现网投递靠 `FOR UPDATE` + 当日计数（`cron_service.py:390-408`），**无 DB 唯一约束** | 多实例下可能重复投递 |
| 9 | 同意可区分用途/版本/撤回 | `PARTIAL` | 无 `user_consents` 表；只有 `users.ai_consent`/`analytics_consent` 两个布尔（`models/user.py:46-48`） | 无法回答"同意的是哪一版" |
| 10 | 数据导出（§2.2-6、§14.2） | `PLANNED` | 无通用导出端点；唯一出口是受 `40304` 权益门的 Life Archive PDF（`share_service.py:863-880`） | 合规与可携带权缺口 |
| 11 | 主动消息静默时段（§14.3-2） | `PLANNED` | `grep quiet\|silent_hour\|do_not_disturb` 在 `backend` 零命中；`/user/settings` 无该字段（`schemas.py:349-360`） | 夜间打扰风险 |
| 12 | AI 输出是候选、证据由服务端校验（§11.2） | `PARTIAL` | Python 候选编码只校验 kind/安全态/字节数（`mq/contract.py:388-460`）；证据引用校验在 Go Gate（设计正确），但**注册表为空**（`mq/registry.py:59-92` 无 `register` 调用） | 候选永远产不出来 |
| 13 | gRPC 同步 AI 调用 | `PARTIAL` | 双侧实现齐全（`backend/ai/rpc/server.py:285`、`go-core/internal/aiclient/grpc.go`），Go 侧唯一调用方是取证工具 `tools/aiclientprobe` | 运行时不存在 |
| 14 | Outbox/Inbox 可靠性（§13） | `PARTIAL` | Go 侧生产者/消费者真接线（`main.go:150-214`），但 Python 无 handler ⇒ 命令全部进 DLQ；仓库无 compose/调度编排，worker 尚未有部署落点 | 异步链路未通电 |
| 15 | Go 新增 Domain 必须有调用方（§10.4） | `PARTIAL` | 零调用方：`RecordUserFeedback`、`RecordOutcome`、`ReevaluateInsight`、`RecheckProactive`、`PropagateEvidenceInvalidation`（台账 `cmd/go-api/domain_caller_ledger_test.go:36-40`） | 领域能力不上线 |
| 16 | CI 中跑格式/生成/破坏性检查（§12.2） | `PLANNED` | 全仓无 `.github/`、无 Makefile；所有闸靠人跑 | 回归无强制面 |
| 17 | PostgreSQL 是唯一事实源（§8.3） | `PARTIAL` | `vercel.json`（serverless + 12 crons）与 `deploy/k8s/`（3 Deployment + Ingress）并存；Supabase pooler 与集群 PG 两套假设；canonical DDL 在 `alembic` 与 `go-core/db/schema/` 各写一份 | 数据分叉风险 |
| 18 | 洞察状态机与 Baseline 枚举一致（§7.2） | `VERIFIED`（领域层） | `outcome.go:622-634` 输出 `confirmed/retracted/corrected/expired/dismissed`；`evidence_read.go:34` 可见集 `published/acknowledged/confirmed` | 只需接线 |

### 1.3 五个结构性差距

**D1｜双模型并存：legacy 服务用户，canonical 空转。**
`memories / chat_messages / growth_records / journey_events` 是今天真实跑的产品数据；`source_records / evidence / patterns / insights` 是 Baseline 的领域真值层。两者之间**没有任何桥**（`source_records` 是"薄指针"设计，`canonical.py:111-115` 明确只存 `source_table + source_id`，正是为这种投影准备的，但投影代码不存在）。

**D2｜异步链路有代码、缺编排。**
AMQP 拓扑（4 命令队列 + 候选队列 + 25 个重试/DLQ 队列）已声明并被 `contracts/events.md` 冻结；Go 的 outbox publisher、inbound consumer 已接线；Python 的 `pipeline`/`worker`/`publisher` 也已实现。缺的是：① 没有任何 compose/调度编排把三进程与一个 broker 起在一起；② Python handler 注册表为空；③ `vercel.json` 与 `deploy/k8s/` 两套声明并存，谁是真调度源没定稿。

**D3｜领域完整、契约缺位。**
Go 侧 `Admit`/`AdmitEvidence`/`RecordUserFeedback`/`RecordOutcome` 都有完整实现与单测，但 `httpapi/server.go:83-142` 只有 19 条路由，其中只有 1 条涉及 canonical（证据读）。客户端契约 `contracts/endpoints.md` 由 FastAPI 生成，Go 专有端点无法登记——于是"能做的事"和"能调到的接口"之间有一条制度性裂缝。

**D4｜合规面只做了一半。**
删除侧很完整（22 表 + outbox + push token）；但**导出**、**consent 版本化**、**静默时段**三项缺失。Baseline 把这三项列为 P0（§3、§14.2、§14.3），不是"上线后补"。

**D5｜治理缺强制面 + 部署形态未定稿。**
无 CI；契约台账有已知失真；canonical DDL 双写；`vercel.json`（12 条 cron）与 `deploy/k8s/`（6 个清单）两套部署声明并存，而实际目标是 VPS + Compose。更严重的是 `backend/tests/test_cron_schedule_coverage.py:4` 明确把 `vercel.json` 当"现役生产调度源"——一旦换到宿主 cron/systemd 而不同步改闸，G1 的 P0 闸会继续盯一个已退役的文件。这正是 Baseline §22 第一条风险"文档/代码/生产配置不一致"的现成样本。

---

## 2. 目标架构（本仓可运行版本）

### 2.1 运行时视图

```text
React Native / Expo  (App)
   │  HTTPS  /api/v1/*   ← 唯一客户端契约面（冻结，由 FastAPI 生成）
   ▼
VPS: Nginx / Caddy（TLS、限流、静态）
   │
   ├─ py-api:8000   Python FastAPI  ── legacy 业务 + BFF（纯转发，不含领域规则）
   │     ├─ memories / chat / mood / growth / journey / iap / share
   │     └─ /api/v1/insights|goals|actions → http://go-api:8080/internal/v3/*
   │                │  X-Service-Token + X-User-Id（来自 JWT，绝不来自客户端 body）
   │                ▼
   │          go-api:8080（常驻：HTTP）
   │                ├─ Domain Gate（九关）→ canonical 表（唯一写者）
   │                └─ inbound consumer（AMQP）→ Admit / AdmitEvidence
   │
   ├─ py-ai-worker    常驻：consume ai.observation.requested → candidate.evidence
   ├─ ai-rpc:50051    Python gRPC（Embed / ClassifySafety / StreamChat）
   └─ rabbitmq:5672   命令面 + 候选面 + 重试阶梯 + DLQ

PostgreSQL（Supabase 托管 或 同机容器）：legacy 表 + canonical 表，同一库、不同写者
调度：宿主 cron / systemd timer → `curl 127.0.0.1:8000/api/v1/cron/*`（`vercel.json` 退役）
```

内存预算（不含 PostgreSQL，按 4c8G 估算）：`py-api` 0.6G + `py-ai-worker` 0.4G + `ai-rpc` 0.3G + `go-api` 0.15G + `rabbitmq` 0.35G + Nginx 0.05G ≈ **1.9G**；若 PG 也同机再 +2G，建议 ≥8G 或继续用托管 Supabase。

### 2.2 写入边界（本方案的宪法）

| 数据面 | 唯一写者 | 读路径 | 禁止 |
|---|---|---|---|
| legacy 表（memories/chat/mood/growth/journey/…） | Python | Python | Go 直写（`users` 的 auth 兼容面除外，已存在） |
| canonical 表（source/evidence/fact/pattern/insight/goal/action/outcome/feedback） | **Go Domain** | Python 经 Go 内部 API；**不得直连 SQL** | Python 直写、AI 直写、批量 SQL 修补 |
| 权益与订阅 | Python（`user_entitlements`） | Python | 前端本地缓存参与判定（已由 `idleRepositoriesWiring.test.ts:140-158` 钉住） |

### 2.3 主链路数据流

```text
用户写一条记忆 / 反思 / 签到
        │  (Python 事务提交后)
        ▼
Python 发命令 ai.observation.requested { sourceRef: "memories:<id>" }
        ▼
Python AI worker: 读取原文 → 结构化 evidence 候选（不产洞察）
        ▼
candidate.evidence → Go inbound（inbox 幂等）
        ▼
Go: source_records upsert（幂等键 user_id+source_table+source_id）
    → AdmitEvidence（证据强度/角色/时间窗校验）
    → evidence 落库
        ▼
（下一段）ai.pattern.requested → candidate.pattern → Gate 九关 → patterns
        ▼
（下一段）ai.insight.requested → candidate.insight → Gate → insights(draft→published)
        ▼
前端 GET /api/v1/insights（BFF → Go）显示 statement + evidence + confidence
        ▼
用户点击反馈 → POST /api/v1/insights/{id}/feedback → Go RecordUserFeedback
        ▼
状态机改写（confirmed / corrected / retracted / expired / dismissed）+ user_feedback 留痕
        ▼
（可选）转成 Goal + Action → Outcome → 回到洞察质量评估
```

---

## 3. 关键决策（ADR）

### ADR-001｜客户端契约面唯一：Nginx/Caddy → Python FastAPI

- **决策：** 所有 App 可达端点继续由 FastAPI 提供（VPS 上由 Nginx/Caddy 反代到 `py-api:8000`），`contracts/endpoints.md` 仍是唯一客户端契约。新增 V3 能力也走 `/api/v1`。
- **理由：** 已发布 App 只认这份契约（`contracts/endpoints.md:8-10` 明文）；新增第二套 base URL 会带来双环境配置、CORS、证书、灰度四件事，而 Baseline 明确"Python FastAPI 作为现有客户端兼容入口暂时保留"（§8.3）。同机部署下 BFF→Go 走 compose 内部网络（`http://go-api:8080`），不出公网，延迟与不可达风险都可控。
- **代价：** 多一跳（Nginx → Python → Go）；Python 进程与 Go 进程需同时健康。
- **备选（后置）：** 由 Caddy 按 path 直接分流 `/api/v1/insights*|goals*` → Go，省掉一跳。代价是 `contracts/endpoints.md` 的生成与 `test_contract_freeze.py` 必须支持"端点归属"标注，且 Go 侧要独立完成 JWT 校验与 CORS。等 V3 端点占比过半再评估。
- **失效条件：** 当 Go 承载 >50% 读流量且经测量确认 BFF 成为瓶颈时，重评直连。

### ADR-002｜Go 只暴露 `/internal/v3/*`（服务间 API）

- **决策：** Go 不注册任何客户端可达路径；内部 API 前缀 `/internal/v3/`，鉴权用 `X-Service-Token` + 从 BFF 透传的 `X-User-Id`。Go 侧一律**不信任** body 中的 `user_id`。
- **理由：** ① 保持契约单一来源；② 避免 App 迁移；③ `contracts/endpoints.md` 的冻结核对（`test_contract_freeze.py`）不被 Go 路由打破。
- **代价：** Python BFF 必须"纯转发"，不得长业务逻辑（用架构闸钉住）。
- **失效条件：** Go 获得公网直连能力且 App 愿意按端点切换 base URL 时。

### ADR-003｜纵切范围：只做「反思/记忆 → 洞察」

- **决策：** 第一轮只把 `memories`（含反思保存、对话中沉淀的记忆）作为 `Source` 来源；`mood_checkins`、`chat_messages` 作为第二批来源。
- **理由：** Baseline §9.3「先选一条核心链路试点」；`memories` 已有 embedding、生命周期（`status=current/superseded/deleted`）、且有 UI 入口（`MemoriesScreen`），是最短可验证路径。
- **代价：** 其它域（growth_reports、personality、first_read）继续留在 legacy。
- **失效条件：** 纵切 E2E 通过且证据支持率有基线后，扩展来源。

### ADR-004｜Action 复用 `personal_commitments`，不新建 `actions` 表

- **决策：** 给 `personal_commitments` 增加 `goal_id`（可空）与 `skipped_at`，把"行动"定义为「挂在 Goal 下的 commitment」。Outcome 仍落 `outcomes` + `outcome_links`。
- **理由：** ① `commitments` 已有 title/description/status/due_at/completed_at，语义就是 Action（`canonical.py:250-270`）；② Baseline §9.3 明说"不要为了领域模型完整一次性迁移所有历史功能"；③ 新建表要同步 alembic + go schema + sqlc + 22 表对账闸，成本远大于收益。
- **代价：** Baseline 表名 `actions` 与实现名 `personal_commitments` 不一致，需在契约文档写清映射。
- **失效条件：** 出现"一个 Action 挂多个 Goal"或"Action 需要独立证据边"时再拆表。

### ADR-005｜合规能力不依赖 Go，先行交付

- **决策：** 通用数据导出、`user_consents`、静默时段三项全部在 Python 侧实现，不等 Wave 2。
- **理由：** 三项都不需要 canonical 写入；Baseline 将其列为 P0；App Store 提审与合规风险与领域整改无关。
- **代价：** `user_consents` 上线后需与未来 Go 侧 consent gate 对齐（预留 purpose 枚举）。
- **失效条件：** 无。

### ADR-006｜异步只用现有 AMQP 一套

- **决策：** 不引入数据库队列表、不引入 Redis Stream、不引入 Celery。broker 用**同机 RabbitMQ 容器**（compose service），不引外部托管。
- **理由：** 拓扑、重试阶梯、DLQ、inbox 幂等、跨语言契约测试都已就绪（`contracts/events.md`、`go-core/internal/messaging/**`）；同机部署免去公网暴露与额外账单。第二套队列＝第二套真相。
- **代价：** 一个额外进程与约 0.35G 内存；需配持久化卷与磁盘告警。
- **失效条件：** 机器规格不足以同时跑 API/worker/broker 时，再评估托管 broker（CloudAMQP 等）。

### ADR-007｜staging 与 prod 隔离；cutover 键保持 0

- **决策：** 用两套 compose project（`closenai-staging` / `closenai-prod`）+ 两个数据库（或两个 schema）+ 两套 `.env`。staging 的 `DATABASE_URL` 禁止指向生产库；`CLOSEN_CUTOVER_*` 在 E2E 通过且双签前保持 `"0"`。
- **理由：** 单机部署下最容易犯的错是"用同一套 compose 变量起两份进程连同一个库"，造成 Go 与 Python 双写 canonical（Baseline §22 风险 3）。`deploy/k8s/` 保留为未来编排参考，不再是 staging 的定义。
- **代价：** staging 需要独立数据；回放夹具需重录。
- **失效条件：** 引入正式集群编排（k8s/Nomad）后，改由 namespace/database 隔离。

---

## 4. 主链路落地细节

### 4.1 Source 投影

| 项 | 设计 |
|---|---|
| 触发点 | Python 写 `memories` 成功后（`memory_service.create_memory` 提交后）发 `ai.observation.requested` |
| 命令体 | 现有体只有 `taskType/sourceKind/sourceMessageId`（`task_chain.go:93-96`），**需扩 `sourceRef`**：`memories:<id>`（任务 `T2-01`） |
| 幂等键 | `source_records (user_id, source_table, source_id)` 已有唯一约束（`canonical.py:133`） |
| 失败重试 | 复用 AMQP 三级阶梯（30s/2m/10m）+ DLQ；Python 不自己重试 |
| 删除传播 | 用户删记忆 → Python 发 `source.deleted` → Go 标 `source_records.status='deleted'` + `PropagateEvidenceInvalidation`（现成，`service_outcome.go:492`） |
| 不做 | 不复制原文到 canonical（薄指针设计），原文仍在 `memories.content` |

### 4.2 Evidence 生成

- **输入：** source 指针（BFF/worker 用 `source_table + source_id` 回读原文）+ 允许的证据类型 + 时间窗。
- **输出（结构化候选）：** `{kind:"evidence", evidence_type, role, strength?, excerpt, observation, source_ref, model_id, prompt_version, schema_version}`。
- **服务端校验（Go）：** `source_record_id` 必须属于该用户且存在（`AdmitEvidence` 已实现）；`excerpt` 必须能在原文中定位（新增校验，见 `T2-04`）；`strength` 不得由模型自报为"置信概率"（只作排序权重）。
- **证据不足：** 返回空候选 + `insufficient_evidence` 原因，不强行生成。
- **可观测：** 每次调用记 `request_id / task_type / prompt_version / model_id / tokens / outcome`。

### 4.3 Insight 展示与纠错

- **可见状态：** `published / acknowledged / confirmed`（`evidence_read.go:34`），加上 `corrected / expired / dismissed` 供用户回看。
- **列表接口：** 返回 `statement / claim_type / confidence / status / evidence_count / published_at`，**不返回证据正文**（详情再拉）。
- **详情接口：** 返回逐条 evidence（`excerpt / evidence_type / role / strength / observed_at / source_type`）+ 关联 pattern。
- **反馈六态映射（已有实现，只接线）：**

| UI 动作 | `feedback_type` | insight 状态结果 |
|---|---|---|
| 准确 | `confirm` | `confirmed` |
| 部分准确 | `partially_confirm` | 不改状态（留痕） |
| 不准确 | `reject` | `retracted` |
| 我要纠正 | `correct`（带 `correction_text`） | `corrected` |
| 已经过时 | `outdated` | `expired` |
| 从模型中移除 | `forget_from_model` | `dismissed` |

- **纠错语义：** 原文不改写；`correction_text` 落 `user_feedback.correction_text`，并作为下一轮候选生成的负样本（`T2-02` 的 prompt 要读最近 feedback）。

### 4.4 Goal / Action / Outcome

| 概念 | 落点 | 状态机 |
|---|---|---|
| Goal | `personal_goals`（`title/description/status/target_at`） | `active → paused → active`、`active → completed/abandoned` |
| Action | `personal_commitments` + 新增 `goal_id`、`skipped_at` | `active(planned) → completed / skipped / cancelled` |
| Outcome | `outcomes` + `outcome_links(target_type='goal'|'commitment')` | `candidate → recorded → corrected/invalidated` |

- **创建 Goal 的前置：** `personal_goals.personal_model_id` 非空 ⇒ 先 `EnsurePersonalModel`（`canonical_repo.go:140`），已存在则复用。
- **Outcome 的 `result`：** 必须支持 `improved / no_change / worse / unclear`（Baseline §7.3-7：无变化与变差都是合法结果）。
- **幂等：** 同一 action 的重复"完成"请求返回首次结果（`If-Match`/版本号或 `operation_id`），不产生第二条 outcome。

### 4.5 主动消息

- **静默时段：** `settings` 增 `quiet_start`/`quiet_end`（`HH:MM`，本地时区），投递门在 `cron_service.py:445` 前插入判断；跨零点区间（22:00–08:00）必须支持。
- **幂等：** `chat_messages` 增 `idempotency_key`（`proactive:<user>:<local_date>:<slot>`）唯一索引；`journey_events` 增 `(user_id, event_kind, day_no)` 唯一索引（现仅仓库层查重，`journey.py:67-72`）。
- **审计：** 内部 `GET /internal/v3/proactives?user_id=&status=` 供排查；不开放给客户端。
- **失败处置：** pending 超过 N 小时自动 `expired`，不再长期抑制后续消息（Baseline §14.3 末句）。

---

## 5. 新增接口清单

### 5.1 客户端面（FastAPI BFF，`/api/v1`，进 `contracts/endpoints.md`）

| # | 方法 | 路径 | 请求 | 响应 `data` | 错误 |
|---|---|---|---|---|---|
| 1 | GET | `/api/v1/insights` | `status?`, `limit?`, `cursor?` | `{items:[…], next_cursor}` | 401 |
| 2 | GET | `/api/v1/insights/{id}` | — | `{id, statement, claim_type, confidence, status, published_at, evidence:[…], patterns:[…]}` | 404, 403 |
| 3 | POST | `/api/v1/insights/{id}/feedback` | `{type*, correction_text?}` | `{feedback_id, target_status, changed, detail}` | 400, 404, 409 |
| 4 | POST | `/api/v1/goals` | `{title*, description?, target_at?}` | `{id, status, …}` | 400 |
| 5 | GET | `/api/v1/goals` | `status?` | `{items:[…]}` | 401 |
| 6 | PATCH | `/api/v1/goals/{id}` | `{title?, description?, target_at?, status?}` | goal | 400, 404, 409 |
| 7 | POST | `/api/v1/goals/{id}/actions` | `{title*, due_at?}` | `{id, status:'planned', …}` | 400, 404 |
| 8 | PATCH | `/api/v1/actions/{id}` | `{status: completed\|skipped\|cancelled}` | `{id, status, completed_at}` | 404, 409 |
| 9 | POST | `/api/v1/actions/{id}/outcomes` | `{outcome_type*, result*, note?}` | `{id, status}` | 400, 404 |
| 10 | POST | `/api/v1/user/export` | `{}` | `{job_id, status:'queued'}` | 429 |
| 11 | GET | `/api/v1/user/export/{job_id}` | — | `{status, download_url?, expires_at?, error_code?}` | 404 |
| 12 | GET | `/api/v1/user/consents` | — | `[{purpose, version, granted_at, revoked_at}]` | 401 |
| 13 | POST | `/api/v1/user/consents` | `{purpose*, granted*, version*}` | consent row | 400 |
| 14 | PATCH | `/api/v1/user/settings` | 增 `quiet_start?`, `quiet_end?` | profile | 400 |

> 响应信封沿用 `core/response.py:17` 的 5 键（`code/message/data/trace_id/user_id`），不新增业务码；状态冲突用 `409`，找不到用 `404`，重复反馈用 `409`。

### 5.2 服务间面（Go，`/internal/v3`，不进客户端契约）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/internal/v3/insights` | 列表（`user_id` 来自头） |
| GET | `/internal/v3/insights/{id}` | 详情 + evidence + patterns |
| POST | `/internal/v3/insights/{id}/feedback` | 六态反馈 |
| GET/POST/PATCH | `/internal/v3/goals…` | 目标 CRUD |
| POST/PATCH | `/internal/v3/actions…` | 行动创建/完成/跳过 |
| POST | `/internal/v3/actions/{id}/outcomes` | 结果记录 |
| GET | `/internal/v3/proactives` | 内部审计 |
| POST | `/internal/v3/source-records` | （仅当不走 AMQP 的降级路径） |

**鉴权：** `X-Service-Token`（固定密钥，HS256 签一个短时 token 亦可）+ `X-User-Id`（BFF 从 JWT 解出）；Go 侧拒绝任何 body 内的 `user_id`（沿用 `mq/contract.py:281-288` 的 `foreign_user_id` 口径）。

---

## 6. 枚举映射（Baseline ↔ 本仓）

| Baseline | 本仓实现值 | 位置 | 备注 |
|---|---|---|---|
| Source: `received/processed/failed/deleted` | `source_records.status`: `active/deleted`（薄指针，无异步处理态） | `canonical.py:126` | 处理态由 outbox/inbox 承载 |
| Evidence: `candidate/accepted/rejected/superseded` | 只有落库态（ingest 即 accepted）；候选态在 MQ inbox | `canonical.py:143` | 不新增 status 列 |
| Fact: `candidate/active/superseded/rejected/deleted` | `personal_facts.status`: `candidate/active/invalidated/outdated` | `canonical.py:177`、`outcome.go:610-621` | `invalidated` 覆盖 rejected/deleted |
| Pattern: `candidate/active/needs_review/rejected/superseded` | `patterns.status` 同左（默认 `candidate`） | `canonical.py:284` | `needs_review` 由 Gate 写入 |
| Insight: `draft/ready/shown/confirmed/corrected/dismissed/expired` | `draft/validated/published/acknowledged/confirmed/retracted/corrected/expired/dismissed` | `outcome.go:622-634`、`evidence_read.go:34` | Baseline 的 `ready`≈`validated`；`shown`≈`acknowledged` |
| Goal: `active/paused/completed/abandoned/deleted` | 同左（默认 `active`） | `canonical.py:228` | 无 `deleted` 列，用 `abandoned` + 软删列（如需） |
| Action: `planned/in_progress/completed/skipped/cancelled` | `personal_commitments.status`: `active/completed/skipped/cancelled`（新增 `skipped_at`） | `canonical.py:257` | `in_progress` 本期不做 |
| Outcome: `recorded/retracted/superseded` | `candidate/recorded/corrected/invalidated` | `outcome.go:53-56` | `invalidated` 覆盖 retracted |
| Proactive: `scheduled/processing/sent/skipped/cancelled/failed` | `candidate/eligible/blocked/scheduled/sent/responded/expired/cancelled` | `proactive.go:71-78` | 映射见 `T5-04` |

> 原则：**不把 Baseline 的建议值直接复制进生产**（Baseline §7.2 末段）。上表是映射，不是迁移。

---

## 7. 验收与门禁

### 7.1 每个能力必须四件套

1. **入口**：前端可达（屏/按钮）；
2. **执行**：API → 服务 → 持久化；
3. **交付**：用户能看到结果（不是只写库）；
4. **失败恢复**：超时/无权限/重复请求/跨用户各有一条可重试路径。

缺任一，状态最高只能标 `IMPLEMENTED`，不能标 `WIRED`。

### 7.2 必须新增的闸（12 条）

| # | 闸 | 判据 |
|---|---|---|
| G-01 | 契约台账真实性 | `export_contract.py` 生成的每条「前端调用」必须指向真实调用点；行号漂移即红 |
| G-02 | Go 内部路由不泄露客户端面 | `httpapi` 只允许 `/internal/v3/*` + 已登记兼容路由 |
| G-03 | BFF 纯转发 | BFF 模块不得出现状态机/权益判断关键字 |
| G-04 | 证据可定位 | 每条 `evidence.excerpt` 必须能在 source 原文中 `strings.Contains` |
| G-05 | 洞察无证据不上线 | `insights.status IN (published,…)` ⇒ `insight_evidence` 非空 |
| G-06 | 纠错真落库 | 反馈后 `user_feedback` 有行且 `insights.status` 按矩阵变化 |
| G-07 | 跨用户隔离 | 列表/详情/反馈/目标/行动全部做「他人 id ⇒ 404」 |
| G-08 | 导出清单 ⊆ 删除清单 | `EXPORT_TARGETS ⊆ PURGE_TARGETS` |
| G-09 | 静默时段 | `quiet` 窗口内定时任务不投递（真库断言） |
| G-10 | 幂等 | 同一 `idempotency_key` 二次写入不产生第二行 |
| G-11 | 切流键与 gate map 双向对账 | 已有 `gate_map_coverage_test.go`，扩到新域 |
| G-12 | 无 CI 不合并 | `ci.yml` 五作业 + 契约幂等 + 生成物检查 + 台账同源 |

### 7.3 指标（先可审计，再谈目标）

| 指标 | 计算口径 | 采集点 |
|---|---|---|
| 证据支持率 | `published` 洞察中 `insight_evidence` 非空的比例 | Go 定时聚合 |
| 证据引用有效率 | 引用存在、属于该用户、原文可定位 | G-04 |
| 用户纠错闭环率 | 有 `correct` 反馈的洞察中状态变为 `corrected` 的比例 | `user_feedback` × `insights` |
| 洞察有帮助率 | `confirm` / (`confirm`+`reject`) | `user_feedback` |
| 行动跟进率 | 创建 action 后 `completed/skipped` 的比例 | `personal_commitments` |
| 主动消息关闭率 | 关掉 `push_enabled` 的用户 / 收到过推送的用户 | `users` × `chat_messages` |
| Life Archive 交付成功率 | 有 `user_entitlements` 的买断用户中成功下载的比例 | `share_exports` × 客户端回执 |
| 异步失败率 | DLQ 增量 / 命令总量 | RabbitMQ 管理面 |

---

## 8. 波次与退出标准

> 任务级拆分、状态与逐条实现方式见 `CLOSENAI_V3_TASK_BOARD.md`（33 项）。
> 进度用脚本看，不靠人肉数：`python scripts/task_board.py`；一致性用 `--check`（CI 第五个 job）。

| 波次 | 目标 | 任务 | 退出标准 |
|---|---|---|---|
| **W0（第 0–7 天）** | 把"能跑"变成"编排好" | `T0-01`…`T0-06` | 本地 compose 一键起（PG+RabbitMQ+go-api+py-worker）；CI 红一次；契约台账零失真；调度源单一 |
| **W1（第 0–14 天，与 W0 并行）** | 补齐 P0 合规面 | `T1-01`…`T1-04` | 导出/consent/静默可用；付费交付指标可见 |
| **W2（第 8–30 天）** | 让证据真实存在 | `T2-01`…`T2-05` | 一条记忆 → source → evidence 跨进程跑通；DLQ 可重放 |
| **W3（第 25–45 天）** | 让洞察可解释、可纠错 | `T3-01`…`T3-05` | 用户能看到依据并纠正；纠正真落库 |
| **W4（第 40–70 天）** | 让洞察变成行动与结果 | `T4-01`…`T4-04` | 洞察→行动→结果端到端一条；负面结果可记 |
| **W5（第 30–60 天）** | 主动消息与同意模型达标 | `T5-01`…`T5-05` | 关闭/静默/幂等三闸全绿 |
| **W6（第 60–90 天）** | 架构收敛 | `T6-01`…`T6-04` | 单一 DDL、单一事实源、staging 隔离 |

**并行原则：** W0 与 W1 完全并行；W2 的**开发**可在 `T0-01a`（本地 compose）完成后立即开始，**上线**才需要 `T0-01b`（VPS 生产编排）。任何任务都不再被"架构不可行"阻塞，只被"编排未完成"影响交付时点。

### 8.1 第一周并行分工（可直接派工）

| 人 | 立刻做 | 产出 |
|---|---|---|
| Python 后端 | `T1-01` 导出、`T5-02` 静默时段、`T5-03` 幂等约束、`T0-04` 台账修复 | 三个 P0 合规能力 + 台账不再说谎 |
| 前端 | `T1-03` 死封装处置、`T5-02` 免打扰 UI、`T3-04`/`T3-05` 界面骨架（先用 mock） | 合规入口可见；洞察证据 UI 可评审 |
| Go | `T2-01` proto 扩字段、`T2-03` source 投影、`T3-01`/`T3-02` 内部端点（本地 compose 联调） | 跨语言链路就绪 |
| 平台 | `T0-01a` 本地 compose、`T0-03` CI、`T0-02` 调度源迁移 | 开发底座 + 强制面 + 单一调度真相 |
| 负责人 | 签批 ADR-001/002/007；确认 VPS 规格与 DB 形态（托管 Supabase 或同机 PG） | 解除所有待决策项 |

> 排序理由：合规三项（导出/consent/静默）不等 Go，先交付即降低提审与投诉风险；Go 侧先在本地 compose 里跑通，避免等生产环境。

---

## 9. 风险登记

| 风险 | 影响 | 预防 | 验收证据 |
|---|---|---|---|
| 编排拖延（compose/调度未定） | W2 上线时点后移 | `T0-01a` 一周内完成，开发不等生产 | 本地 compose 一键起 + 健康检查 |
| 调度双真相（Vercel 与宿主 cron 并存） | 消息重复/漏发 | `T0-02` 单一调度源 + 闸改靶 | `test_cron_schedule_coverage.py` 读新源且红一次 |
| 单机内存不足 | 进程被 OOM kill | §2.1 内存预算；PG 托管优先 | 上线前 24h 压测观察 RSS |
| BFF 长成第二业务层 | 双实现、规则分叉 | G-03 架构闸；code review 清单 | BFF 单测只测转发与错误映射 |
| canonical 与 legacy 数据分叉 | 用户看到两套事实 | 单一写者表；删除传播；diff 巡检 | 每周对账脚本（source 数 = memory 数） |
| 证据生成成本失控 | LLM 账单 | 只对"值得提取"的记忆触发；批量窗口；预算上限 | 每用户每日调用数指标 |
| 反馈被当成埋点 | 纠错不生效 | 反馈写 `user_feedback` + 状态机，双写才算成功 | G-06 |
| 导出泄露他人数据 | 合规事故 | 导出任务用服务端 user 上下文；G-08 | 跨用户测试 |
| 迁移双写漂移 | 上线炸库 | 单一 DDL + 生成对账 + staging 预演 | `test_canonical_schema.py` 扩列级对账 |
| 无 CI 的回归 | 闸形同虚设 | `T0-03` 为所有后续任务前置 | PR 必须绿 |

---

## 10. 明确不做（本期）

- 不做 Multi-Agent、不引入第二套消息队列、不引入向量库（pgvector 已在用则维持现状）。
- 不重写全部 PRD；不迁移 growth/personality/first_read/share 到 canonical。
- 不做角色库、占星、算命、虚拟关系等级。
- 不在本期把全部流量切到 Go；`CLOSEN_CUTOVER_*` 保持 `"0"`。
- 不做 insight 的"置信概率"对外承诺；`confidence` 只作排序与内部质量指标。
- 不新增 `actions` 表（ADR-004）；不删除 `growth_records`。
- 不对已发布 App 做破坏性契约变更（`contracts/endpoints.md` 只增不改）。

---

## 附录 A：Baseline 章节 → 本仓任务映射

| Baseline 章节 | 本仓落点 | 说明 |
|---|---|---|
| §0 证据标签 | 本文件全文 | 所有结论带标签与出处 |
| §1–§2 定位与用户任务 | 不在本期改产品文案 | 定位不改，只补能力 |
| §3 P0 | 已完成项见 §1.1；剩余 `T1-01`（导出） | 其余 P0 已于 10-15～10-17 结案 |
| §3 P1 | `T2-*`、`T3-*`、`T4-*` | 跨时间事实/模式、纠错、行动跟进 |
| §3 P2 | 已有 Life Archive；`T1-04` 补监控 | 报告/PDF 不做新品类 |
| §4 付费基线 | 已有 IAP 全链路；`T1-04` | 收费承诺门禁 8 条中 7 条已满足 |
| §5 竞品错位 | 不在本期 | 已由定位文档承载 |
| §6 领域模型 | `T2-*`、`T3-*`、`T4-*` | 对象已建表，补链路 |
| §7 状态流转/不变量 | `T3-02`、`T4-02`、`T4-03`、`T2-04` | 领域层已实现，补入口与门禁 |
| §8 架构决策 | ADR-001/002/006/007 | Go 权威写、Python 兼容面 |
| §9 Schema 基线 | `T4-01`、`T5-01`、`T6-03` | 只补两列 + 一张同意表；DDL 去重 |
| §10 Go 边界 | `T2-03`、`T3-01/02`、`T4-02/03`、`T6-01` | 生产调用方门禁 |
| §11 AI Runtime | `T2-01`、`T2-02`、`T2-04` | 候选化 + 证据校验 + 版本 |
| §12 Protobuf | `T2-01`、`T0-04` | 先真实 API 再冻结 |
| §13 Outbox/Inbox | `T2-05`、`T6-01` | 复用现有 AMQP，不引第二套 |
| §14 身份/隐私/安全 | `T1-01`、`T1-02`、`T5-01`、`T5-02`、`T5-03` | 导出/consent/静默/幂等 |
| §15 功能状态模型 | `T0-05` | 六态台账自动化 |
| §16 验收矩阵 | `T0-05` + 各任务 DoD | 十项逐项落任务 |
| §17 P0 缺口 | 已完成 3/4；`T1-01` 补第 4 项 | 见 §1.1 |
| §18 测试基线 | `T0-03`、`T0-06` | CI + 变异 |
| §19 指标 | `T1-04`、§7.3 | 先可审计 |
| §20 路线图 | §8 波次 | 已按本仓依赖重排 |
| §21 变更管理 | 台账 §0 | 每个任务带四件套 |
| §22 风险登记 | §9 + `T6-03/04` | 双写/双部署/删除 |
| §23 冻结决策 | ADR-001…007 | 逐条对应 |
| §24 成功标准 | §7.1、§7.3 | 以"可验证"而非"已实现"判定 |

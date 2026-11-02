# ClosenAI 关键词增长任务实现方案（2026-11-01）

> **对应台账：** `docs/keyword-growth-task-board-2026-11-01.md`（31 个任务，26 TODO / 5 BLOCKED / 0 DONE）
> **本文用途：** 把台账里的每个任务落到「改哪个文件、加哪个端点、动哪张表、怎么验证、怎么回滚」。
> **本文不改台账状态。** 文末 §14 单独列出台账需要修订的地方，等确认后再改台账。
> **编写依据：** 2026-11-01 对当前仓库代码的逐文件核对；所有行号以当前工作区为准。

---

## 0. 结论先行

### 0.1 31 个任务怎么分

| 类型 | 任务 | 需要改运行时代码吗 |
|---|---|---|
| 纯文档 / 商店 / 法务 / 调研 | KG-01、KG-02、KG-03、KG-04、KG-05、KG-06、KG-07、KG-08、KG-09、KG-18、KG-19、KG-21、KG-22、KG-29 | 否（KG-19 可加一个只读的文档闸测试） |
| 前端为主 | KG-23、KG-24、KG-25、KG-27 | 是（不改 Python/Go 契约） |
| Python + 契约 | KG-26、KG-28、KG-30 | 是（每个 +1 个端点） |
| 管线 + 部署 | KG-31 | 是（Python emitter/handler、Go 链、k8s/compose） |
| 站点内容 | KG-10～KG-17 | 是（静态站点文件与生成脚本，不是 App 运行时） |

### 0.2 三个硬结论

1. **KG-31 不是「把 `CLOSEN_CUTOVER_REFLECTIONS` / `CLOSEN_CUTOVER_MEMORIES` 改成 1」就能完成的。**
   这两个域在 Ingress 上是「同一 path 挂 GET+POST」或「GET+POST+DELETE」，而 Ingress 的 path 匹配不看方法（决策点 D-10）。详见 §7.9。
2. **洞察链今天没有生产流量，不是切流问题，是链本身缺三截：**
   - 第一段 `ai.observation.requested` 没有生产发射点；
   - observation 产出的是 **evidence**，而 Go 的 `followUpAfter` 只认 `fact→pattern`；
   - Go 的 `Call` 把 `AdmitEvidence` 返回的记录 ID 丢掉了，所以下一段拿不到 `sourceRef`。
   详见 §7.1。
3. **KG-24 要改的不止台账点名的四行。** 付费墙的副标题、Pro+ 三项、`compareSample` 也有与真实权益不符的表述。详见 §5.2。

### 0.3 本文建议的实施顺序

| 批次 | 内容 | 理由 |
|---|---|---|
| 第 1 批 | KG-23、KG-24、KG-27 | 纯前端/文案，不碰契约，能最快消除虚假承诺与「统计不可见」 |
| 第 2 批 | KG-26、KG-30 | 各 +1 端点，无新表，能独立验收 |
| 第 3 批 | KG-28 | 三层次分析实验，依赖配额/证据校验，但不需要 Go 改动 |
| 第 4 批 | KG-31 | 管线 + 部署，风险最高，放最后；先 canary 再放量 |
| 并行 | KG-01/02/03/04/09/10～22/18/29 | 文档、法务、商店、站点内容，不阻塞代码批次 |

---

## 1. 编码前必须知道的现状

### 1.1 服务归属

| 能力 | 当前归属 | 关键位置 |
|---|---|---|
| 情绪签到 + AI 解读 | Python | `backend/routers/mood.py:27`、`backend/services/mood_service.py:94` |
| 情绪历史/趋势读 | Go（Python 保留回滚实现） | `go-core/internal/httpapi/mood_*.go`；`backend/routers/mood.py:57-77` 标了 deprecated |
| 反思草稿 / 保存 / 列表 | Python | `backend/routers/reflection.py:31-70` |
| 成长记录 / 仪表盘 | Python | `backend/services/growth_service.py:132`、`:516` |
| 洞察 / 目标 / 行动 / 结果 | Go（Python BFF 代理） | `backend/routers/v3.py:57-149` |
| 记忆写 | Python | `backend/services/memory_service.py:199` |
| 分享 PDF | Python | `backend/services/share_service.py:277`、`backend/routers/share.py:91` |
| AI 任务命令 / 候选 / 出站箱 | Go | `go-core/internal/domain/commands.go:90`、`go-core/internal/messaging/outbox/outbox.go` |
| AI handler / LLM | Python | `backend/ai/mq/worker.py:133`、`backend/ai/mq/handlers/observation.py` |

### 1.2 六个会改变方案的事实

1. **`outbox_messages` 已经存在，而且 Go 的出站工作器会发布 Python 写的行。**
   表结构与索引见 `backend/models/canonical.py:589-627`；迁移见
   `backend/alembic/versions/d9b1f3a5c7e9_add_outbox_messages.py`。
   `backend/services/user_service.py:135` 已把它登记进 `PURGE_TARGETS`。
   **结论：KG-31 的第一段发射器不需要新建表，也不需要走 HTTP 调 Go。**
2. **`ai_generation_runs` 已有 `purpose` / `status` / `created_at` 和 `(user_id, created_at)` 索引。**
   **结论：KG-28 的配额可以按 purpose 统计，不需要给 `growth_records` 加索引或新表。**
3. **`growth_records.metadata_json` 已存在。**
   见 `backend/models/growth.py:15-41` 与 `backend/repositories/growth_repository.py:27-51`。
   **结论：KG-26 的 prompt 溯源、KG-28 的保存为反思，都可以复用这一列。**
4. **漏斗事件枚举被测试钉死。**
   `frontend/src/services/funnel.ts:21-26` 的 `ONE_TIME_STEPS` 被
   `frontend/__tests__/funnel.test.ts:71-77` 逐项比对；调用点测试还会断言「每个文件只调一个 step」。
   **结论：KG-25 加事件必须同步改测试，且允许一个屏幕挂多个 step。**
5. **指标字典的闸只读第一张 `| 机器码 |` 表。**
   `backend/tests/test_metrics_export.py:67-82` 从第一个 `| 机器码 |` 开始读到第一行非表格为止；
   `:91` 断言恰好 7 行。
   **结论：KG-19 必须在文档后面另起一张表，不能改核心七格表。**
6. **i18n 测试要求 15 个语言键集完全一致。**
   `frontend/__tests__/i18nLocales.test.ts` 对 14 个非 en 语言做键集、非空、占位符比对。
   **结论：KG-24 删除/新增付费墙键，必须 15 个语言一起改。**

### 1.3 现网真实权益（写文案必须以这张表为准）

来源：`backend/config.py:193-200`、`backend/services/personality_service.py:563`、
`backend/services/user_service.py:198`、`backend/services/quota_service.py:32-53`。

| 权益 | Free | Pro | Pro+ |
|---|---|---|---|
| 统一池消息/天 | 5 | 100 | 1000 |
| 对话轮次/天 | 10 | 100 | 1000 |
| 性格画像刷新 | 共 3 次 | 5 次/月 | 999 次/月 |
| Streak 冻结券/月 | 1 | 3 | 3 |
| 周期报告 | 无 | 月/季/年 | 月/季/年 |
| Life Archive 导出 | 无 | 每月 1 次免费 | 每月 1 次免费 |
| 主动消息/天 | 1 | 1 | 1 |
| 365 天时间线 | 有 | 有 | 有 |

**没有**：无限对话、优先模型路由、无限刷新、每日多条主动消息。

---

## 2. 31 个任务的实现矩阵

> 缩写：`—`＝不动；`+1`＝新增 1 个端点；`文档`＝只产出文档/商店字段；`测试`＝新增或修改测试。

| ID | 交付类型 | 前端 | Python | Go | 契约/迁移 | 验证 |
|---|---|---|---|---|---|---|
| KG-01 | 文档 | — | — | — | — | 承诺清单 ≥30 条，逐条有位置/判定/负责人 |
| KG-02 | 法务 | — | — | — | — | 法务意见书/签字邮件归档 |
| KG-03 | 文档 | — | — | — | — | ADR，含五个功能的取/舍/重开条件 |
| KG-04 | 商店 | — | — | — | — | ASO 文案包，字符数脚本复核 |
| KG-05 | 文档 | — | — | — | — | 月度关键词表，≥2 个来源 |
| KG-06 | 商店 | — | — | — | — | ASC 提交截图 + 审核状态 |
| KG-07 | 商店 | — | — | — | — | Play Console 草稿/上线截图 |
| KG-08 | 文档 | 提供真实界面截图 | — | — | — | 6 帧脚本 + 每帧证据 |
| KG-09 | 文档 | — | — | — | — | 4 份 brief + 4 个提纲 |
| KG-10 | 站点 | — | — | — | — | 两页 200、canonical/hreflang、sitemap |
| KG-11 | 站点 | — | — | — | — | 一页 200、非医疗声明、内链 |
| KG-12 | 站点 | — | — | 复用 KG-26 JSON | — | 两页 200、30 条提示可追溯 |
| KG-13 | 站点 | — | — | — | — | 两页 200、法务批注 |
| KG-14 | 站点 | — | — | — | — | 两页 200、结果回访段落 |
| KG-15 | 站点 | — | — | — | — | 一页 200、临床/法务批注 |
| KG-16 | 站点 | — | — | — | — | 两页 200、来源+日期、法务 |
| KG-17 | 站点 | — | — | — | — | 爬虫：0 断链、0 孤儿页 |
| KG-18 | 文档 | — | — | — | — | 检查清单 + PR 模板 |
| KG-19 | 文档+测试 | — | — | — | — | 新增独立指标表 + 文档闸 |
| KG-20 | 前端 | `attribution.ts` | — | — | — | 单测：解析/清洗/PII 拒绝 |
| KG-21 | 平台 | — | — | — | — | GSC/Bing/百度验证截图、CSV |
| KG-22 | 文档 | — | — | — | — | 复盘模板 + 首份报告 |
| KG-23 | 前端+文案 | onboarding 15 语言、`site`、`web` | — | — | — | 禁用词闸 0 命中 + i18n 绿 |
| KG-24 | 前端+文案 | 付费墙 15 语言 + 数组 | — | — | — | 权益对照表 + 文案闸 |
| KG-25 | 前端+Python | 漏斗事件、下一步卡 | dashboard `next_step` | — | — | 5 个事件 + 5 个状态测试 |
| KG-26 | 前端+Python | 提示库面板 | 内容 JSON + 1 端点 | — | +1 端点 | 30 条完整性 + 保存溯源 |
| KG-27 | 前端 | 7/30 切换、入口、空态 | — | — | — | 7/30 渲染 + 空态测试 |
| KG-28 | 前端+Python | 深度选择器、证据卡 | 1 端点 + 配额 | — | +1 端点 | 证据子串校验 + 配额 + 危机 |
| KG-29 | 调研 | — | — | — | — | 5–10 人访谈纪要 |
| KG-30 | 前端+Python | quote 变体、入口 | 1 端点 + PDF | — | +1 端点 | 归属/非 AI 原话/删除 |
| KG-31 | Python+Go+部署 | 无 | emitter + 2 handler | 链改 + ID 回传 | 无新表 | 7 天流量 + 回滚演练 |

**契约计数：** 当前 `backend/tests/test_contract_freeze.py:105-108` 是 115 router + 1 app = 116。
KG-26/28/30 各 +1 ⇒ **118 router + 1 app = 119 行**。本文不新增 `GET /app-config`，KG-28 的开关走 dashboard 字段，避免多一个端点。

---

## 3. 全局决策 D0–D12

### D0｜站点唯一源：`web/` 是现网，`site/` 不是

- 现网 Cloudflare Pages 的源是 `web/`，部署脚本 `scripts/deploy_web.sh` / `scripts/deploy_pages.py` 的根目录都是 `web/`。
- `site/` 是未发布的 Astro 源，里面有 `AI companion` / `remembers everything` 的冲突文案（`site/src/i18n.ts:12-14`、`:79`、`:90-91`）。
- **决定：** 不部署 `site/`；英文页面放在 `web/en/<slug>/`，中文页面保持现有根路径不动。构建脚本从一份 JSON 内容源生成静态页，避免 `web/` 与 `site/` 两套英文内容。
- 英文页用 `hreflang`：`zh` → 现有根路径，`en` → `/en/...`，`x-default` → `/en/...`（美国目标）。
- 这条是 KG-10～KG-17 的**前置决策**；在 T7-05 未完成前只写草稿，不发布。

### D1｜新端点最小化

只加 3 个：

1. `GET /api/v1/reflections/prompts`（KG-26）
2. `POST /api/v1/mood/analysis`（KG-28）
3. `POST /api/v1/share/quote-card`（KG-30）

KG-28 的开关不单独做 `/app-config`，放进 `GET /growth/dashboard` 的 `data.features`；dashboard 是 Python 端点，不加契约行。

### D2｜不新建表

- 提示库：`backend/content/reflection_prompts.json`，不入库。
- 三层次分析：不落库；保存为反思时走 `POST /reflections`，落到 `growth_records`。
- 原话卡：不落卡片文件，按需生成 PDF；源被删则自然 40403。
- 管线：复用 `outbox_messages`、`inbox_messages`、`evidence`、`patterns`、`insights`。
- **不加任何 Alembic 迁移。** 如果后续确实需要索引，另立任务，不在 KG-26/28/30 里夹带。

### D3｜配额口径

- 现有 `daily_usage` 只算「主动消息 + 签到解读」（`backend/services/quota_service.py:55-62`）。
- KG-28 新增独立维度：`mood_analysis`，从 `ai_generation_runs` 按 `purpose` + `created_at` 统计，不挤占统一池。
- 新增配置（`backend/config.py` 配额段）：
  - `free_daily_mood_analyses: int = 1`
  - `pro_daily_mood_analyses: int = 20`
  - `pro_plus_daily_mood_analyses: int = 50`
- 只统计 `status != "error"` 的生成；失败不扣额度。

### D4｜证据必须能回到原文

KG-28 的 explore/deep 每条结论必须带原文引用。校验规则：

1. 模型返回的 `quote` 必须在候选原文里**逐字出现**（先做空白归一化）；
2. 不在原文里的引用一律丢弃；
3. explore 至少 1 条有效引用，deep 至少 2 条；
4. 不满足就降级：`safety_status="degraded"`，`pattern=null`，只返回可核对的事实，不补造。

### D5｜漏斗事件命名

在 `ONE_TIME_STEPS` 追加 5 个：

- `onboarding_completed`
- `first_reflection_saved`
- `first_evidence_insight`
- `first_action`
- `first_outcome`

保留现有 4 个，不重命名，避免历史数据断档。事件名统一由 `funnelEventName()` 加 `funnel_` 前缀。

### D6｜埋点属性不扩白名单

新增事件只用现有属性键：`feature`、`action`、`result`、`source`、`trigger`、`duration_ms`、`count`、`value`。
`frontend/src/services/analytics.ts:19-39`、`backend/services/analytics_service.py:36`、
`go-core/internal/domain/analytics.go` 三份白名单都不动。

### D7｜管线架构：Python 写 outbox，Go 发，Python 消费

```
Python 业务事务
  └─ memory/reflection 保存
       └─ 同 session 插一行 outbox_messages（status=pending）
Go outbox worker
  └─ 发布 ai.observation.requested
Python MQ worker
  └─ observation handler → candidate.evidence
Go candidate worker
  └─ AdmitEvidence → 拿到 evidence.id
       └─ 同 inbox 事务写 ai.pattern.requested（sourceRef=evidence:<id>）
Python MQ worker
  └─ pattern handler → candidate.pattern
Go candidate worker
  └─ Admit(pattern) → 拿到 pattern.id
       └─ 写 ai.insight.requested（sourceRef=patterns:<id>）
Python MQ worker
  └─ insight handler → candidate.insight
Go candidate worker
  └─ Admit(insight) → 发布为已发布洞察（现有 gate）
```

**关键点：** 第一段没有候选可指，`sourceKind` / `sourceMessageId` 写空串，`sourceRef` 指向记忆或反思原文。

### D8｜任务来源表与候选来源表要分开

现在 `SOURCE_REF_TABLES` / `sourceRefPattern` 同时承担两件事：

1. 命令体 `sourceRef` 的形状与启用面；
2. 候选 `source_table` 的准入面（Go 的 `AdmissibleSourceTables` 与 `sourceOwnerQueries`）。

KG-31 要新增 `evidence` / `patterns` 作为**任务来源**，但它们不是候选来源，不能塞进第 2 件事。
**决定：**

- 保留 `SOURCE_REF_TABLES` / `sourceRefPattern` / `enabledSourceTables` 作为**候选来源**口径，值不动；
- 新增 `TASK_SOURCE_REF_TABLES`（Python）与 `enabledTaskSourceTablesByStage`（Go），按 taskType 限定：
  - `TASK_TYPE_OBSERVATION_EXTRACT` → `{memories, reflections}`
  - `TASK_TYPE_PATTERN_DETECT` → `{evidence}`
  - `TASK_TYPE_INSIGHT_DRAFT` → `{patterns}`
- `parse_command` 先判六表形状，再判「这张表是否属于这个 taskType」。

这样不会破坏 `backend/tests/test_ai_mq_contract.py:536-567` 的三方同值闸。

### D9｜切流与管线是两件事

- **管线开量**：KG-31，靠 `CLOSEN_AI_TASK_EMITTER`、`CLOSEN_AI_PIPELINE_ROLLOUT`、三个 worker 开关。
- **Go 读切流**：`CLOSEN_CUTOVER_REFLECTIONS` / `MEMORIES`，受 D-10 阻塞，**不在 KG-31 的验收里**。
- KG-31 结案时只能写「AI 候选链已生产开量」，不能写「reflections/memories 已由 Go 接管」。

### D10｜灰度三档

| 档 | 配置 | 人群 |
|---|---|---|
| 0 关 | `CLOSEN_AI_PIPELINE_ROLLOUT=0` | 无人 |
| 1 内测 | `CLOSEN_AI_PIPELINE_ALLOWLIST=u1,u2` | 点名用户 |
| 2 比例 | `CLOSEN_AI_PIPELINE_ROLLOUT=5/20/50/100` | 按 `sha256(user_id) % 100` 取模 |
| 3 全量 | `100` | 全部 |

回滚只改配置，不删数据。

### D11｜失败恢复

- 记忆/反思保存：outbox 插入放在 **savepoint** 里；插入失败不回滚业务行，记 `ai_task_enqueue_failed` 日志与计数，由补数脚本重试。
- 任务 ID 确定性派生：同一来源永远同一个 `message_id`，重试不会产生第二条。
- 候选 ID 确定性派生：现有 `candidate_message_id()` 已保证。
- 分析接口：前端请求中禁用按钮；后端记录 `ai_generation_runs`，重复请求受配额约束。
- PDF：源被删/越权一律 40403，不生成半张卡。

### D12｜文案闸要有正向证人

`scripts/check_claims.py` 只扫指定根目录（`frontend/src`、`web`、`site`、`docs/aso`、`docs/content`），
不扫台账与历史文档。闸必须带 `--self-test`：临时写一个含禁用词的文件，断言退出码为 1；
否则「扫空集永远绿」不算通过。

---

## 4. 无代码任务：怎么交付

### 4.1 KG-01 承诺审计

产出 `docs/keyword-claims-audit-2026-11.md`，至少 30 条。每条六列：

| 位置 | 原文 | 风险类型 | 判定 | 改法 | 负责人 |
|---|---|---|---|---|---|

风险类型固定四类：绝对化记忆、陪伴平台语言、医疗边界、无证据预测。
必须覆盖：

- `frontend/src/i18n/locales/en.ts:432` 的 `step1Tagline`
- `frontend/src/i18n/locales/en.ts:436` 的 `step1Feature2Desc`（「AI finds patterns… before you spiral」）
- `web/index.html:527` 的「成长陪伴」
- `site/src/i18n.ts:12-14`、`:79`、`:90-91`

### 4.2 KG-02 法务包

四份材料，归档到 `docs/legal/keyword-content-review-2026-11.*`：

1. MBTI/Enneagram 用词说明；ASO 保守版用 `16 types`。
2. Rosebud / Day One 对比页的 nominative fair use 说明。
3. 隐私政策与实际配置对照：不训练、可导出、可删除。
4. 非医疗声明：AI 内容不构成诊断/治疗/法律/财务建议。

在签字前，KG-13/15/16 只能写草稿。

### 4.3 KG-03 ADR

`docs/adr/2026-11-keyword-feature-scope.md`，五个决定：

| 功能 | 决定 | 重开条件 |
|---|---|---|
| 每日情绪记录 | 保留，提升可见性 | — |
| 三层次分析 | 做小实验，同一个 AI、不同深度、必须带证据 | 4 周读数不达标即停 |
| 温柔陪伴 AI 角色 | 不做 | 定位变更 + 法务/安全评审 |
| 匿名树洞/社区 | 不做，改为私人未寄出的信研究 | KG-29 访谈证明私密出口不够 |
| 金句卡 | 只做用户原话卡，不做 AI 金句库 | 不重开 |

### 4.4 KG-04 ASO 文案包

`docs/aso/en-US-2026-11.md`。用台账里的标题/副标题/关键词字段，另加一个校验脚本：

- `scripts/check_aso_copy.py` 读一个 JSON（`docs/aso/en-US-2026-11.json`），断言：
  - 标题 ≤30、副标题 ≤30、关键词 ≤100；
  - 关键词字段不含标题/副标题已有词；
  - 不含禁用词 `therapy|treat|diagnose|remembers everything|AI companion|best|#1`。
- 脚本带 `--self-test`，用一条超长标题证明会红。

### 4.5 KG-05 / KG-06 / KG-07 / KG-08

- KG-05：`docs/aso/keyword-tracker-2026-11.md`，40+ 词，每词有量/来源/日期/意图/落点/保留或淘汰。
- KG-06：ASC 提交截图、审核状态、A/B 配置；被拒时写「改文案/申诉/回滚」三条路径。
- KG-07：Play Console 草稿或上线截图；短描述 ≤80 字符；无医疗/绝对化。
- KG-08：`docs/aso/screenshot-brief-2026-11.md`，6 帧；首帧不用「remembers everything」；至少一帧讲「结果回访」。

### 4.6 KG-09 / KG-10～KG-17 站点

内容简报放 `docs/content/briefs-2026-11/`，页面草稿放 `docs/content/drafts/`。
每页的固定结构：

1. 一个具体问题；
2. 一个原创模板/例子；
3. 来源与日期；
4. 非医疗声明；
5. 至少 2 条内链 + 1 个 CTA；
6. canonical / hreflang / sitemap 行。

页面清单：

| 任务 | URL（英文） | 中文 URL | 主 CTA |
|---|---|---|---|
| KG-10 | `/en/ai-journal`、`/en/journal-app` | `/ai-journal`、`/journal-app` | 下载/等待名单 |
| KG-11 | `/en/mood-tracker` | `/mood-tracker` | 下载/等待名单 |
| KG-12 | `/en/self-reflection`、`/en/journal-prompts` | `/self-reflection`、`/journal-prompts` | Reflect 功能/等待名单 |
| KG-13 | `/en/self-discovery`、`/en/personality-test` | `/self-discovery`、`/personality-test` | 查看画像如何生成 |
| KG-14 | `/en/self-improvement`、`/en/goal-tracker` | `/self-improvement`、`/goal-tracker` | 下载/等待名单 |
| KG-15 | `/en/overthinking` | `/overthinking` | 反思提示库 |
| KG-16 | `/en/compare/rosebud-alternative`、`/en/compare/day-one-alternative` | 同名中文 | 下载/等待名单 |

**D0 前置：** 这些页面在 `web/` 里用 `scripts/build_seo_pages.py` 从 JSON 生成，不部署 `site/`。
生成脚本负责：title/description/H1/canonical/hreflang/JSON-LD/sitemap 行；同一份 JSON 不允许出现两个 H1。
没有 App Store 链接时，CTA 显示「等待名单」或「暂未开放」说明，不做假按钮。

### 4.7 KG-18 内容质量闸

`docs/keyword-content-gate.md` + PR 模板（`.github/pull_request_template.md` 若不存在则新建）。
清单至少 12 项：具体问题、原创模板、来源/日期、作者/主体、非医疗、无虚假案例、无批量同义词、内链、canonical、hreflang、CTA、可回滚。

### 4.8 KG-19 指标字典

**不要改 `docs/metrics-dictionary.md:18-27` 的核心七格表。** 在文件末尾另起：

```markdown
## 关键词渠道七格（KG-19）

| 关键词码 | 中文 | 定义 | 来源 | 窗口 | 分群 | 负责人 | 查询入口 | 数据质量检查 |
|---|---|---|---|---|---|---|---|---|
| `search_reach` | 曝光/搜索点击 | ... | GSC / Bing / 百度 | 周 | 语言/国家/页面 | GROWTH | ... | ... |
...
```

七格固定为：

1. `search_reach`（曝光/搜索点击）
2. `store_page_view`
3. `install`
4. `first_value`（首次签到或首次反思）
5. `first_evidence_insight`
6. `first_action`
7. `due_outcome`

新增 `backend/tests/test_keyword_funnel_doc.py`：

- 用 `| 关键词码 |` 定位**第二张**表；
- 断言 7 行、每行 9 列非空、无 `TBD/待补/—` 占位；
- 断言第 4～7 格能指回现有表：`growth_records`、`insights+insight_evidence`、`personal_commitments`、`outcomes`。

这样不会碰 `test_metrics_export.py` 的第一张表闸。

### 4.9 KG-21 / KG-22 / KG-29

- KG-21：`docs/seo/search-console-baseline-2026-11/`，验证截图、sitemap 提交记录、基线 CSV。
- KG-22：`docs/growth/monthly-review-2026-12.md`，20–30 个问题、8 周停/改/扩阈值、首份结论。
- KG-29：`docs/research/unsent-letter-2026-12.md`，5–10 人访谈；决定做/不做；不做写重开条件。

---

## 5. 前端为主的任务

### 5.1 KG-23 绝对化记忆承诺整改

#### 改哪里

| 位置 | 现状 | 改法 |
|---|---|---|
| `frontend/src/i18n/locales/en.ts:432` | `It remembers everything you tell it` | `It remembers what you choose to share` |
| `frontend/src/i18n/locales/en.ts:433-434` | `It remembers you` / `Every check-in and conversation...` | `It remembers what you share` / `The check-ins and conversations you choose to keep shape its understanding of you` |
| `frontend/src/i18n/locales/en.ts:435-436` | `See your blind spots` / `AI finds patterns... before you spiral` | `See what your records show` / `Look for recurring themes across your own entries — and decide what they mean` |
| `frontend/src/i18n/locales/en.ts:437-438` | `Grows with you` / `Your emotional timeline...` | 保留标题；描述改为 `Your timeline builds from your entries — look back and see what changed` |
| `frontend/src/i18n/locales/zh.ts:425` | `你说过的每一句，它都记得` | `你选择分享的，它才记得` |
| 其余 13 个 locale 的 `step1Tagline` | 绝对化翻译 | 按同一语义翻译；i18n 测试只保证键齐，不保证语义，必须人工复核 |
| `site/src/i18n.ts:12-14` | `AI companion` / `remembers everything` | `AI journal` / `It remembers <em>what you choose to share</em>.` / `A private journal that looks back with you` |
| `site/src/i18n.ts:79` | footer `remembers everything` | 同上 |
| `site/src/i18n.ts:90-91` | meta 里的绝对化 | 同上 |
| `web/index.html:527` | `成长陪伴` | 按 KG-01 结论改为 `成长记录` 或 `自我觉察工具`；这是现网中文页，改前先截图 |

#### 不改什么

- 不改 `site/` 的部署状态；`site/` 仍然不发布。
- 不改 `web/` 的现有中文 URL。
- 不把「remembers everything」做成产品卖点。

#### 闸

`scripts/check_claims.py`：

- 扫描根：`frontend/src`、`web`、`site`、`docs/aso`、`docs/content`。
- 禁用模式：`remembers everything`、`remembers every conversation`、`remembers every chat`、`remembers it all`、`AI companion`（营销文案）、`AI friend`。
- 白名单：`docs/keyword-growth-task-board-2026-11-01.md`、`docs/keyword-claims-audit-2026-11.md`、历史品牌文档；白名单逐条写原因。
- `--self-test`：临时文件写 `remembers everything`，断言退出码 1。
- CI 增加一步 `python scripts/check_claims.py`。

#### 测试

- `frontend/__tests__/claimsCopy.test.ts`：读 15 个 locale，断言 `step1Tagline` 不含 `everything|every conversation|每一句`。
- `frontend/__tests__/i18nLocales.test.ts` 保持绿。
- `scripts/check_claims.py --self-test` 输出记录。

#### 回滚

文案回滚 = 还原 15 个 locale 的 `step1Tagline` 与 3 条 feature 文案；不涉及数据。
现网 `web/` 回滚 = Cloudflare Pages 回退到上一个部署。

---

### 5.2 KG-24 付费墙价值文案改写

#### 5.2.1 先列全部虚假/不准确表述

| 位置 | 现状 | 为什么不对 | 改法 |
|---|---|---|---|
| `en.ts:260` `paywall.subtitle` | `Unlock unlimited chat & deeper insights` | 没有无限对话；洞察未生产化 | `More room to reflect, and reports that look back` |
| `en.ts:272` `featureUnlimitedChat` | `Unlimited AI conversations` | Free 10 轮/天，Pro 100，Pro+ 1000 | 删除；改 `featureDailyMessages` = `Up to 100 messages a day` |
| `en.ts:273` `featurePriorityLLM` | `Priority model responses` | 没有优先路由 | 删除；改 `featureReports` = `Monthly, quarterly and yearly reports` |
| `en.ts:274` `featurePersonalityRefresh` | `More personality refreshes` | 模糊；Pro 实际 5 次/月 | 改 `featureRefreshes` = `5 personality refreshes a month` |
| `en.ts:275` `featureTimeline` | `365-day growth timeline` | 365 天时间线免费也有 | 删除；改 `featureArchive` = `1 Life Archive export a month` |
| `en.ts:291` `plusFeatureChat` | `100 AI conversations per day` | Pro+ 实际 1000/天 | 改 `plusFeatureHighVolume` = `Up to 1,000 messages a day` |
| `en.ts:292` `plusFeatureRefresh` | `Unlimited personality refreshes` | 实际 999 次/月 | 改 `plusFeatureRefreshes` = `Up to 999 personality refreshes a month` |
| `en.ts:293` `plusFeatureProactive` | `Multiple proactive messages per day` | 实际 1 条/天 | 删除 |
| `en.ts:280` `compareSample` | `Night entries 5 days in a row — you write more honestly at night.` | 把未生产化的模式发现写成产品结论 | `A month of entries can show what changed — and what stayed the same.` |

#### 5.2.2 新的权益数组

`frontend/src/screens/PaywallScreen.tsx:75-89`：

```ts
const PRO_FEATURES = [
  'paywall.featureDailyMessages',
  'paywall.featureReports',
  'paywall.featureRefreshes',
  'paywall.featureArchive',
] as const;

const PRO_PLUS_FEATURES = [
  'paywall.plusFeatureAllPro',
  'paywall.plusFeatureHighVolume',
  'paywall.plusFeatureRefreshes',
] as const;
```

#### 5.2.3 15 个语言怎么改

- 删除 6 个旧键：`featureUnlimitedChat`、`featurePriorityLLM`、`featureTimeline`、`plusFeatureChat`、`plusFeatureRefresh`、`plusFeatureProactive`。
- 新增 7 个键：`featureDailyMessages`、`featureReports`、`featureRefreshes`、`featureArchive`、`plusFeatureHighVolume`、`plusFeatureRefreshes`；`plusFeatureAllPro` 保留。
- 15 个 locale 必须同时增删，否则 `i18nLocales.test.ts` 红。
- 每个新键的中文/英文必须能指回 §1.3 的一行；不能指回的文案不许上。

#### 5.2.4 测试

- `frontend/__tests__/paywallCopy.test.ts`：
  - 读 `en.ts` 的 `paywall` 段，断言不含 `unlimited|priority|multiple proactive|365-day`；
  - 断言 `featureDailyMessages` 的值包含 `100`，`plusFeatureHighVolume` 包含 `1,000`；
  - 用一条注入的假文案证明会红。
- 现有 `PaywallScreen` 渲染测试若断言 4 个 Pro+ 项，改为 3 个或按数组长度断言。

#### 5.2.5 回滚

文案回滚 = 还原 15 个 locale 的 `paywall` 段；`PRO_FEATURES` / `PRO_PLUS_FEATURES` 还原到旧数组。无数据/契约影响。

---

### 5.3 KG-25 首日价值路径

#### 5.3.1 漏斗事件

`frontend/src/services/funnel.ts:21-26` 的 `ONE_TIME_STEPS` 追加：

```ts
export const ONE_TIME_STEPS = [
  'first_read_opened',
  'first_record_created',
  'first_checkin',
  'week1_report_opened',
  'onboarding_completed',
  'first_reflection_saved',
  'first_evidence_insight',
  'first_action',
  'first_outcome',
] as const;
```

调用点：

| 事件 | 位置 | 触发条件 |
|---|---|---|
| `onboarding_completed` | `frontend/src/screens/OnboardingScreen.tsx:124` 的 `finish` 成功后 | onboarding 答案已落库 |
| `first_reflection_saved` | `frontend/src/screens/ReflectScreen.tsx:369` 的 `saveReflection` 成功后 | 保存成功，不是草稿生成 |
| `first_evidence_insight` | `frontend/src/screens/InsightsScreen.tsx` 的 `loadInsights` 返回后 | 任一 `evidence_count > 0` |
| `first_action` | `frontend/src/screens/InsightsScreen.tsx:385` 的 `createAction` 成功后 | 创建成功 |
| `first_outcome` | `frontend/src/screens/GrowthRecordsScreen.tsx:206` 的 `postActionOutcome` 成功后 | 结果提交成功 |

#### 5.3.2 测试要怎么改

`frontend/__tests__/funnel.test.ts`：

- `:71-77` 的精确数组断言改成 9 个 step。
- `:137` 的「四个节点互不干扰」改成按 `ONE_TIME_STEPS.length`。
- `:174-186` 的 `oneTimeSites` 从「文件 → 一个 step」改成「文件 → step 数组」：
  - `OnboardingScreen` → `onboarding_completed`
  - `ReflectScreen` → `first_record_created`、`first_reflection_saved`
  - `InsightsScreen` → `first_evidence_insight`、`first_action`
  - `GrowthRecordsScreen` → `first_outcome`
  - 原有 `FirstReadCardScreen`、`HomeScreen`、`Week1ReportScreen` 不变。
- `:244-266` 的全仓扫描仍会校验「没有枚举外的 step」；新 step 必须有调用点。
- `:261-265` 的「五段台阶 + 三个分享物料都真的被调用」改为遍历全部 step。

#### 5.3.3 下一步卡（服务端权威）

不新增端点，在 `GET /growth/dashboard` 的 `data` 里加：

```json
{
  "next_step": {
    "key": "checkin|reflection|insight|action|outcome|done",
    "title": "Record how today feels",
    "screen": "Home|Reflect|Insights|GrowthRecords"
  }
}
```

Python `GrowthService.get_dashboard`（`backend/services/growth_service.py:132`）增加判定：

1. 今天无签到 → `checkin`
2. 近 7 天无 `reflection` → `reflection`
3. 无「已发布且有 evidence」的洞察，且 `insight_pipeline_enabled` → `insight`
4. 无进行中的 `personal_commitments` → `action`
5. 有到期未回填结果的行动 → `outcome`
6. 其余 → `done`

数据来源：

- 签到：`mood_repository.get_today`
- 反思：`growth_repository.list_recent(record_type='reflection', days=7, limit=1)`
- 洞察：`Insight` + `InsightEvidence`（canonical，Python 已可读）
- 行动：`PersonalCommitment`
- 结果：`Outcome`

`insight_pipeline_enabled` 来自 `backend/config.py`，默认 `False`；KG-31 灰度 >0 时置 `True`。
**这样 KG-25 可以先上线，不会把用户引到一个「等洞察」的死胡同。**

`backend/models/schemas.py:149-167` 的 `GrowthDashboardResponse` 加：

```python
class NextStep(BaseModel):
    key: str
    title: str
    screen: str

class GrowthDashboardResponse(BaseModel):
    ...
    next_step: Optional[NextStep] = None
```

FE 在 `HomeScreen` 渲染一张卡，点击 `navigation.navigate(next_step.screen)`。
网络失败/字段缺失时不渲染，不新增大流程。

#### 5.3.4 失败恢复

- 保存反思失败：保留草稿、显示重试，不清空输入。
- 空内容：保存按钮禁用。
- 重复提交：请求中禁用按钮；后端 `saveReflection` 不产生重复门闸事件（`trackFunnelOnce` 本身幂等）。
- 洞察/行动接口失败：下一步卡不前进，仍停在当前 step。

#### 5.3.5 测试与证据

- pytest：`test_dashboard_next_step.py` 覆盖 6 个状态；每个状态断言 `next_step.key`。
- jest：`nextStepCard.test.ts` 覆盖有/无 `next_step`、点击导航。
- 5 名内部用户走通记录：`docs/growth/first-value-path-2026-11.md`，附事件表；没有真实用户时写「未验证」，不伪造。
- 依赖修订：KG-25 应加 `KG-31（验证闸）`；否则「首条有证据洞察」在现网不可达。见 §14。

---

### 5.4 KG-27 情绪统计可见性

#### 5.4.1 现状审计

| 位置 | 现状 |
|---|---|
| `frontend/src/screens/HomeScreen.tsx:60` | `HEAT_DAYS=14`，14 天热力图 |
| `frontend/src/screens/InsightsScreen.tsx:916` | 7 天折线来自 `timeline.days` |
| `frontend/src/screens/InsightsScreen.tsx:910` | 90/365 热力图切换 |
| `frontend/src/services/api.ts:453` | `getMoodTrend(days=14)`，Go 支持 1–90 |

#### 5.4.2 改法（纯前端）

1. `InsightsScreen` 增加 `moodRange: 7 | 30`，调用 `getMoodTrend(moodRange)`。
   - 7 天、30 天都用 Go 的 `/mood/trend`，不引入第二个时间源。
   - `labels` 为空或 `values` 全 0 → 显示空态「先签到一次」+ 按钮回 Home。
2. `ProfileScreen` 增加「情绪统计」入口：
   ```ts
   navigation.navigate('Insights', { initialSection: 'mood' });
   ```
   `InsightsScreen` 读 `route.params.initialSection` 滚到情绪区。
3. `ExploreScreen` 若已有情绪卡，补一个同样的入口；没有就不硬加。
4. 不新建情绪数据模型，不改服务端契约。

#### 5.4.3 测试

- jest：`moodStats.test.ts` 覆盖 7/30 切换、空态、有数据态。
- Go 侧已有 1–90 的测试，不新增。
- 截图：空态/7 天/30 天各一张，放 `docs/growth/mood-stats-audit-2026-11.md`。

#### 5.4.4 边界

- 365 天热力图仍走 `GET /growth/timeline`，不与 `/mood/trend` 混用。
- 文案不写「诊断/治疗」；用「情绪记录」。

---

## 6. Python + 契约任务

### 6.1 KG-26 反思提示库（30 条）

#### 6.1.1 数据源

新建 `backend/content/reflection_prompts.json`：

```json
{
  "version": "2026-11-1",
  "clusters": [
    {"key": "reflection_method", "title": {"en": "How to reflect", "zh": "怎么反思"}},
    {"key": "long_term_understanding", "title": {"en": "Understanding over time", "zh": "长期理解"}},
    {"key": "action_outcome", "title": {"en": "Action and outcome", "zh": "行动与结果"}},
    {"key": "ai_credibility", "title": {"en": "Checking AI insights", "zh": "核对 AI 洞察"}}
  ],
  "prompts": [
    {
      "id": "rp-001",
      "cluster": "reflection_method",
      "title": {"en": "What actually happened?", "zh": "实际发生了什么？"},
      "question": {"en": "Write one thing that happened today, without judging it.", "zh": "写下今天发生的一件事，先不评价。"},
      "why": {"en": "Separating events from judgments makes the next step clearer.", "zh": "把事件和评价分开，下一步才看得清。"},
      "follow_up": {"en": "What evidence supports your first judgment?", "zh": "你的第一判断有什么依据？"},
      "function": "reflect"
    }
  ]
}
```

约束：

- 30 条，四簇 8/8/7/7。
- `id` 唯一、稳定，不许按顺序重排。
- `function` ∈ `reflect|mood|goal`。
- en/zh 必填；其他语言暂不放进内容 JSON，接口回退 en 并显式返回 `language_used`。
- 不含医疗/诊断/治疗词；不复制外部提示清单。

#### 6.1.2 校验与接口

新建 `backend/services/prompt_library.py`：

- 启动时加载并校验：30 条、簇计数、id 唯一、en/zh 非空、`function` 枚举、无禁用词。
- `list(cluster=None, language="en")` 返回按簇分组的数据；语言不是 en/zh 时用 en 并返回 `fallback=true`。

`backend/routers/reflection.py` 增加：

```python
@router.get("/reflections/prompts")
async def list_prompts(
    cluster: Optional[str] = Query(None),
    language: str = Depends(get_request_language),
):
    return success(prompt_library.list(cluster=cluster, language=language))
```

响应：

```json
{
  "version": "2026-11-1",
  "language_used": "en",
  "fallback": false,
  "total": 30,
  "clusters": [{"key": "reflection_method", "count": 8, "title": "..."}],
  "prompts": [{"id": "rp-001", "cluster": "reflection_method", "title": "...", "question": "...", "why": "...", "follow_up": "...", "function": "reflect"}]
}
```

> **2026-11-01 实交付勘误（KG-26）**：路径不是 `GET /reflections/prompts`，而是
> **`GET /api/v1/reflection-prompts`**（顶层路径，不挂在 `/reflections` 下面），
> 并因此新建 `backend/routers/reflection_prompts.py` 而不是往 `reflection.py` 加一条。
> 根因：Ingress 按域 `pathType: Prefix` 切流，`reflections` 在 `PATHLESS_KEYS`（键先行、path 不上刀）里，
> 这条静态内容路径会在放量那一刀跟着搬去 Go，而 Go 无内容面 ⇒ 上线当天静默 404。
> 判据 `test_k8s_manifests.py::test_cutover_keys_without_ingress_path_stay_unrouted`（两步变异实测：
> 改装饰器不红，重跑 `export_contract.py` 后才红——所以路径改动必须连生成器一起跑）。
> 函数名实交付为 `prompt_library.list_prompts(...)`。证据：`docs/growth/prompt-library-2026-11.md` §6A。

#### 6.1.3 保存溯源

`backend/models/schemas.py:184-187` 的 `ReflectionCreate` 扩展：

```python
class ReflectionCreate(BaseModel):
    content: str = Field(..., max_length=4000)
    related_memory_ids: Optional[List[str]] = None
    prompt_id: Optional[str] = None
    prompt_version: Optional[str] = None
    source: Literal["manual", "prompt", "draft", "mood_analysis"] = "manual"
```

`ReflectionResponse` 增加 `prompt_id: Optional[str]`、`source: str`。

> **2026-11-01 实交付勘误（KG-26）**：这一格**没做**——`ReflectionResponse` 保持
> `id／content／related_memory_ids／created_at` 四格不变，溯源只进写侧
> （`ReflectionCreate` 三字段 → `growth_records.metadata_json` ＋ 簇名进 `tags`）。
> 根因：`reflections` 是待切流域，响应形状与 Go 的 `reflectionPayload` 字节对齐，由
> `test_n8_batch5_contract.py::test_reflections_falsy_split_exact_type_and_limit_match_go` 钉字段序；
> Python 单方面加两格＝给下一刀留一个「Go 没有这两格」的静默降级。
> 按提示统计保存率现在从**行**里取，不从 API 取。要读出提示来源，得 Go 侧同批改 `metadata_json`
> 解析＋重录字节夹具（归 N8 切流域那一票，不在 KG-26）。证据：`docs/growth/prompt-library-2026-11.md` §6B。

`reflection_service.save`（`backend/services/reflection_service.py:106`）：

- 如果带 `prompt_id`：
  - 必须存在；
  - `prompt_version` 必须等于当前库版本，否则 `40001`；
  - `source` 只能是 `prompt`。
- 写入 `growth_records.metadata_json`：
  ```json
  {"source":"prompt","prompt_id":"rp-001","prompt_version":"2026-11-1"}
  ```
- `tags` 加簇名，便于成长记录筛选。

#### 6.1.4 前端

- 新组件 `frontend/src/components/PromptLibrarySheet.tsx`：
  - 打开时 `GET /reflections/prompts`；
  - 四簇 tab；
  - 选中后把 `question` 填进反思输入框，记录 `prompt_id/version`；
  - 保存时 `saveReflection` 带这三个字段。
- `ReflectScreen` 增加入口「需要提示？」。
- `api.ts` 增加 `listReflectionPrompts(cluster?)`；`saveReflection` 签名增加 `promptMeta?`。
- 埋点：`feature='prompt_library'`, `action='open|select|save'`, `result='ok|error'`，不传提示正文。

#### 6.1.5 站点复用

`scripts/export_prompts.py` 从同一个 JSON 生成 `docs/content/drafts/journal-prompts.*` 或 `web` 构建数据。
**禁止**在站点里手写第二份 30 条。

#### 6.1.6 测试

- `backend/tests/test_prompt_library.py`：30 条、簇计数、id 唯一、en/zh 非空、禁用词、接口过滤。
- `backend/tests/test_reflection_prompt_save.py`：带 prompt 保存、版本不符 40001、不存在的 prompt 40001、metadata 落库。
- `frontend/__tests__/promptLibrary.test.ts`：选择/保存/失败态。
- 契约计数 +1：`test_contract_freeze.py:105` 改 116，`:108` 改 117（此时只有 KG-26 落地）。

#### 6.1.7 回滚

- 接口回滚：删路由 + 前端入口下线；已保存的反思仍在 `growth_records`，`metadata_json` 多一个字段不影响读取。
- 内容回滚：`reflection_prompts.json` 回到上一版本；`prompt_version` 不匹配的旧草稿保存会 40001，前端提示重新选择。

---

### 6.2 KG-28 三层次情绪分析

#### 6.2.1 接口

`backend/routers/mood.py` 增加：

```python
@router.post("/mood/analysis")
async def mood_analysis(
    request: MoodAnalysisRequest,
    db: AsyncSession = Depends(get_db),
    language: str = Depends(get_request_language),
    user_id: str = Depends(get_current_user),
    service: MoodService = Depends(get_mood_service),
):
    await user_service.require_ai_consent(db, user_id)
    return success(await service.analyze(db, user_id, request, language))
```

请求：

```json
{
  "depth": "light|explore|deep",
  "checkin_id": "uuid（可选；缺省取今天）",
  "focus": "可选，≤200 字符",
  "request_id": "可选，客户端幂等键"
}
```

响应：

```json
{
  "analysis_id": "uuid",
  "depth": "explore",
  "created_at": "2026-11-01T12:00:00Z",
  "summary": "...",
  "pattern": {"description": "...", "confidence": 0.72},
  "alternatives": ["...", "..."],
  "evidence": [
    {"source_type": "memory|mood_checkin|growth_record", "source_id": "...", "excerpt": "...", "occurred_at": "..."}
  ],
  "next_step": {"text": "...", "action": "reflect|checkin|goal|none"},
  "safety_status": "ok|degraded|crisis",
  "generation_run_id": "...",
  "quota": {"used": 2, "limit": 20}
}
```

#### 6.2.2 三种深度

| 深度 | 输入 | 输出 | 最大 token | 证据要求 |
|---|---|---|---|---|
| `light` | 今天的签到 + 备注 | 一句话回应 + 一个问题 | 120 | 0 条；有就带 |
| `explore` | 今天签到 + 近 7 天记录（≤8 条） | 1 个可能模式 + 1 条替代解释 | 300 | ≥1 条逐字引用 |
| `deep` | 今天签到 + 近 30 天记忆/记录（≤20 条） | 时间线 + 2 种解释 + 1 个下一步 | 600 | ≥2 条独立引用 |

**同一个 AI、同一套数据，不做三个人格。** `depth` 只控制上下文窗口、输出结构和 token 上限。

#### 6.2.3 证据校验

新建 `backend/services/mood_analysis_service.py`（或在 `mood_service.py` 内加方法）：

1. 收集候选原文：
   - 今天的 `mood_checkins.note`；
   - `memory_repository.list` 最近 N 条（`generation_run_id IS NULL` 优先，避免自引用）；
   - `growth_repository.list_recent` 最近 N 条。
2. 每条原文分配 `source_type` / `source_id`，截断到 500 字符。
3. 调 LLM，要求 JSON：
   ```json
   {
     "summary": "...",
     "pattern": {"description": "...", "quote": "...", "confidence": 0.0},
     "alternatives": ["..."],
     "next_step": {"text": "...", "action": "reflect|checkin|goal|none"}
   }
   ```
4. 校验 `quote` 是某条原文的逐字子串（空白归一化）；不匹配就丢弃该条证据。
5. `explore` 证据 <1 或 `deep` 证据 <2 → 降级：
   - `pattern=null`；
   - `summary` 只写可核对的事实；
   - `safety_status="degraded"`；
   - 不补造解释。
6. `safety_service.check_text_tri`：危机 → 返回危机资源文案，`safety_status="crisis"`，不生成模式。

#### 6.2.4 配额

`backend/services/quota_service.py` 增加：

```python
MOOD_ANALYSIS_PURPOSES = (
    "services.mood_analysis.light",
    "services.mood_analysis.explore",
    "services.mood_analysis.deep",
)

async def mood_analysis_used_today(self, db, user) -> int:
    day_start = local_day_start_utc(user.timezone)
    return await ai_generation_run_repository.count_since(
        db, user.id, day_start, purposes=MOOD_ANALYSIS_PURPOSES, statuses=("ok", "fallback")
    )

async def is_mood_analysis_quota_exceeded(self, db, user) -> bool:
    return await self.mood_analysis_used_today(db, user) >= self._mood_analysis_limit(user.tier)
```

如果没有现成的 `ai_generation_run_repository`，在 `backend/repositories` 新增一个只读查询，直接用 `select(func.count()).where(...)`；不新建表。

配置（`backend/config.py` 配额段）：

```python
free_daily_mood_analyses: int = 1
pro_daily_mood_analyses: int = 20
pro_plus_daily_mood_analyses: int = 50
mood_analysis_depths_enabled: bool = False
```

#### 6.2.5 开关

`GET /growth/dashboard` 的 `data` 增加：

```json
{"features": {"mood_analysis_depths": true, "insight_pipeline_enabled": false}}
```

- `mood_analysis_depths` 来自 `settings.mood_analysis_depths_enabled`。
- 关时：`POST /mood/analysis` 返回 `40402`（不暴露功能存在），前端不显示选择器。
- 开时：Home 签到成功页显示三档卡片。

#### 6.2.6 前端

- 新组件 `frontend/src/components/MoodAnalysisSheet.tsx`：
  - 三档卡片：轻松 / 探索 / 深潜；
  - 每档一句说明 + 证据要求；
  - 结果区：summary、pattern、alternatives、evidence 卡；
  - 证据卡点击 → 打开对应记忆/记录详情；
  - 「这不像我」→ 打开 Reflect，预填 `这不像我：` + 用户输入，保存为反思；
  - 「保存为反思」→ `saveReflection(content, null, {source:'mood_analysis'})`。
- `HomeScreen` 在 `onMoodPress` 成功后，如果 `features.mood_analysis_depths` 为真，显示入口。
- `api.ts` 增加 `analyzeMood(body)`。
- 埋点：
  - `mood_analysis_requested`（`feature='mood_analysis'`, `action=depth`）
  - `mood_analysis_completed`（`result=ok|degraded|crisis`, `duration_ms`）
  - `mood_analysis_evidence_opened`（`count`）
  - `mood_analysis_corrected`
  - `mood_analysis_saved`
  - **不传用户原文、不传 evidence excerpt。**

#### 6.2.7 测试

- `backend/tests/test_mood_analysis.py`：
  - 三档参数校验；
  - 无 AI 同意 → 40303；
  - 无今日签到 → 40402；
  - 配额超限 → 40301；
  - 引用逐字命中 → 保留；不命中 → 丢弃；
  - explore 1 条证据通过；deep 1 条证据降级；
  - 危机文案；
  - 开关关 → 40402；
  - 分析事件不含原文。
- `frontend/__tests__/moodAnalysis.test.ts`：三档选择、证据卡、纠正、保存、失败重试。
- 契约计数 +1：`test_contract_freeze.py` 从 116 改 117（若 KG-26 已落地）。

#### 6.2.8 幂等与残余风险

- `request_id` 在进程内 TTL 缓存 10 分钟；同一用户同一 `request_id` 返回同一结果。
- 多副本下进程内缓存不共享，重复请求可能多扣一次配额；前端请求中禁用按钮 + 配额兜底。
- 不在本期引入 Redis；4 周实验读数后再决定是否要持久幂等。

#### 6.2.9 回滚

- `mood_analysis_depths_enabled=0`：入口消失，端点 40402。
- 不需要删数据（分析未落库）。
- 已保存为反思的记录保留，按普通反思处理。

---

### 6.3 KG-30 用户原话金句卡

#### 6.3.1 接口

`backend/routers/share.py` 增加：

```python
@router.post("/share/quote-card")
async def quote_card_pdf(
    req: QuoteCardRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ShareService = Depends(get_share_service),
):
    pdf = await service.export_quote_card_pdf(db, user_id, req.source_type, req.source_id)
    return StreamingResponse(
        iter([pdf]),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="closenai_quote_card.pdf"'},
    )
```

请求：

```json
{"source_type": "memory|reflection", "source_id": "uuid"}
```

#### 6.3.2 只允许「用户自己的话」

| 来源 | 允许条件 | 拒绝 |
|---|---|---|
| memory | `memory_repository.get` 命中；`generation_run_id IS NULL`；`status == 'current'`；未删除 | AI 抽取的记忆（`generation_run_id` 非空）→ 40403 |
| reflection | `growth_records.record_type='reflection'`；`user_id` 匹配；content 非空 | `daily_insight` / `weekly_summary` / `mood_analysis` → 40402 |

`backend/repositories/growth_repository.py` 增加：

```python
async def get_owned(self, db, user_id, record_id, record_type):
    result = await db.execute(
        select(GrowthRecord).where(
            GrowthRecord.id == record_id,
            GrowthRecord.user_id == user_id,
            GrowthRecord.record_type == record_type,
        )
    )
    return result.scalar_one_or_none()
```

`share_service.export_quote_card_pdf`：

1. 按上表校验；
2. 取 `content`，截断到 280 字符（卡片可读）；
3. 标题用 `copy.t("share", "quote.title", language)`；
4. 正文：
   - 引用原文；
   - 日期（用户本地时区）；
   - 「来自你的记录」/「Saved from your own records」；
   - 如果反思的 `metadata_json.source == "draft"`，改为「Edited and saved by you」，不冒充原创；
5. `_render_pdf` 复用现有品牌区（水印 + QR + 短链）；
6. 不落卡片文件；源被删后自然 40403。

需要在 `backend/core/copy` 的 share 文案里加 2–3 个键（en/zh）；不加 15 语言也可以，因为 PDF 文案走用户语言回退，但必须写清回退。

#### 6.3.3 授权与删除

- **明示授权：** 每次导出前，前端显示预览 + 「分享」按钮；用户点「分享」才请求 PDF。
- **可关闭：** 预览页可取消；不做自动分享。
- **可删除：** 源记忆/反思删除后，再次导出 40403；已生成的 PDF 在用户设备上，无法回收——文案要写明「已保存到相册/文件的卡片不会自动删除」。
- **不做全局开关列：** 不新增 `users.share_quotes_enabled`，避免迁移；如果后续需要，另立任务。

#### 6.3.4 前端

- `frontend/src/types/index.ts:683` 的 `ShareCardVariant` 增加 `'quote'`。
- `ShareCardScreen.tsx:33` 的 `VARIANTS` 增加 `'quote'`。
- 路由参数扩展：
  ```ts
  ShareCard: {
    variant?: ShareCardVariant;
    memory_id?: string;
    source_type?: 'memory' | 'reflection';
    source_id?: string;
    quote_text?: string;
    quote_date?: string;
  }
  ```
- `quote` 变体：
  - 用 `quote_text` / `quote_date` 本地预览；
  - 导出时 `postQuoteCardPdf(source_type, source_id)`；
  - 成功/失败走现有 `exportPdf` + `pdfFailureKey`。
- 入口：
  - 反思卡片「分享原话」；
  - 记忆详情「分享原话」；
  - 不新增列表页。
- 漏斗：
  - `FUNNEL_SOURCES` 增加 `'quote'`；
  - `trackFunnelShare('share_tapped'|'share_completed', 'quote')`；
  - 更新 `frontend/__tests__/funnel.test.ts:75-77` 与 `:263` 的 source 列表。

#### 6.3.5 测试

- `backend/tests/test_quote_card.py`：
  - 自己的 memory/reflection → PDF 非空；
  - 别人的 id → 40403；
  - AI 记忆 → 40403；
  - 已删除记忆 → 40403；
  - `daily_insight` → 40402；
  - `source_type` 非法 → 40001。
- `frontend/__tests__/quoteCard.test.ts`：预览、导出、取消、失败提示。
- 契约计数 +1。

#### 6.3.6 回滚

- 删路由 + 前端入口下线；已生成的 PDF 在用户设备上，不影响服务端。
- 不改表，不需要数据回滚。

---

## 7. KG-31：AI 候选链生产开量

> 这一节是整份方案里唯一会改 Go 领域链、会动部署的文件。先读 §1.2 与 §7.9。

### 7.1 今天为什么是零流量

```
Go RequestAITask ──(无生产调用方)──> outbox
Python MQ worker ── 只注册 observation
observation handler ── 产出 candidate.evidence
Go inbound:
  - AdmitEvidence 返回 ID，但 Call 丢弃
  - admittedKind 只认 fact/pattern/insight，evidence 直接 FollowUpSkipped
  - followUpAfter 只有 fact→pattern、pattern→insight
```

四个断点：

1. 没有第一段发射点；
2. observation 产 evidence，链却从 fact 开始；
3. `Call` 丢掉 admitted ID，下一段拿不到 `sourceRef`；
4. pattern/insight handler + prompt 不存在。

### 7.2 目标链

```
memories/reflections
  → ai.observation.requested
  → candidate.evidence
  → AdmitEvidence（拿到 evidence.id + occurred_at）
  → ai.pattern.requested（sourceRef=evidence:<id>）
  → candidate.pattern
  → Admit(pattern)（拿到 pattern.id + occurred_at）
  → ai.insight.requested（sourceRef=patterns:<id>）
  → candidate.insight
  → Admit(insight) → 现有 gate → published
```

### 7.3 阶段 A：Python 事务内写 outbox（第一段发射）

#### 7.3.1 新文件

`backend/services/ai_task_outbox.py`：

```python
OBSERVATION_TASK_TYPE = "TASK_TYPE_OBSERVATION_EXTRACT"
OBSERVATION_ROUTING_KEY = "ai.observation.requested"
OBSERVATION_MESSAGE_TYPE = "ai_observation_requested"

def observation_message_id(source_ref: str) -> str:
    return "task-" + hashlib.sha256(
        f"task|observation|{source_ref}".encode("utf-8")
    ).hexdigest()[:40]

def build_observation_body(source_ref: str, occurred_at: datetime) -> str:
    body = {
        "taskType": OBSERVATION_TASK_TYPE,
        "sourceKind": "",
        "sourceMessageId": "",
        "sourceRef": source_ref,
        "sourceOccurredAt": _rfc3339(occurred_at),
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

async def enqueue_observation(
    db, *, user_id: str, source_ref: str, occurred_at: datetime, trace_id: str = ""
) -> bool:
    message_id = observation_message_id(source_ref)
    db.add(OutboxMessage(
        message_id=message_id,
        message_type=OBSERVATION_MESSAGE_TYPE,
        schema_version=1,
        routing_key=OBSERVATION_ROUTING_KEY,
        user_id=user_id,
        correlation_id="",
        causation_id="",
        trace_id=trace_id,
        payload=build_observation_body(source_ref, occurred_at),
        status="pending",
        attempt_count=0,
        available_at=utc_now(),
    ))
    await db.flush()
    return True
```

`_rfc3339` 输出 `2026-11-01T12:00:00Z`；Go 的 `time.Parse(time.RFC3339)` 接受。

#### 7.3.2 接入点

- `backend/services/memory_service.py:199` `create_memory`：
  - 在 `memory_repository.create` 之后、返回之前；
  - 条件：
    - `settings.ai_task_emitter_enabled`；
    - `user.ai_consent`；
    - `len(content) >= 30`；
    - `generation_run_id is None`（不让 AI 自己喂自己）；
    - `source == "self"`；
    - `rollout.is_enabled(user_id)`。
  - 用 `async with db.begin_nested():` 包住 outbox 插入：
    - 失败只回滚 savepoint，记 `ai_task_enqueue_failed`，不阻断记忆保存；
    - 由 `scripts/backfill_ai_tasks.py` 补。
- `backend/services/reflection_service.py:106` `save`：
  - 保存成功后，`source_ref = f"reflections:{record.id}"`；
  - 条件同上（反思没有 `source` 字段，默认允许；`generation_run_id` 不拦，因为用户保存即认可）；
  - 记录 `occurred_at = record.created_at`。

#### 7.3.3 开关

`backend/config.py` 增加：

```python
ai_task_emitter_enabled: bool = False
ai_pipeline_rollout_percent: int = 0
ai_pipeline_allowlist: str = ""  # 逗号分隔 user_id
```

`backend/services/ai_rollout.py`：

```python
def is_enabled(user_id: str) -> bool:
    if not settings.ai_task_emitter_enabled:
        return False
    allow = {x.strip() for x in settings.ai_pipeline_allowlist.split(",") if x.strip()}
    if user_id in allow:
        return True
    if not (0 <= settings.ai_pipeline_rollout_percent <= 100):
        return False
    bucket = int(hashlib.sha256(user_id.encode()).hexdigest(), 16) % 100
    return bucket < settings.ai_pipeline_rollout_percent
```

#### 7.3.4 为什么不用 Go 发射

记忆/反思写都在 Python；如果让 Go 发射，要么加跨服务 HTTP（引入新失败面），要么把写迁到 Go（D-10 不允许）。
Python 直接写同一张 `outbox_messages`，Go 出站工作器会发布，是当前架构下最短的路径。

#### 7.3.5 补数脚本

`scripts/backfill_ai_tasks.py`：

- 扫最近 N 天 `memories` / `growth_records`（reflection），
- 找没有对应 outbox `message_id` 的行，
- 重新 `enqueue_observation`；
- `--dry-run` 只打印计数；
- 幂等：`message_id` 唯一，重复插入会被唯一约束挡住，脚本捕获后跳过。

### 7.4 阶段 B：Python pattern/insight handler

#### 7.4.1 新文件

| 文件 | 作用 |
|---|---|
| `backend/ai/prompts/pattern.py` | pattern 提示词、JSON 解析、校验 |
| `backend/ai/prompts/insight.py` | insight 提示词、JSON 解析、校验 |
| `backend/ai/mq/handlers/pattern.py` | pattern handler（端口注入） |
| `backend/ai/mq/handlers/insight.py` | insight handler（端口注入） |
| `backend/services/ai_pattern.py` | 组合根：EvidenceLoader/Quota/Safety/LLM 端口 |
| `backend/services/ai_insight.py` | 组合根：PatternLoader/EvidenceLoader/Quota/Safety/LLM 端口 |
| `backend/services/ai_pipeline.py` | 统一注册 observation + pattern + insight |

#### 7.4.2 pattern handler

输入：`sourceRef=evidence:<id>`。

步骤：

1. `EvidenceLoader.load(user_id, id)`：
   - 强制 `user_id` 归属；
   - 读不到 → ACK + `source_missing`；
   - 读得到 → 拿 `excerpt/evidence_type/occurred_at/source_table/source_id`。
2. `EvidenceLoader.list_for_user(user_id, window_days=30, limit=20)`：
   - 只取同用户、`status` 有效的证据；
   - 用于找重复/时间关系。
3. `NegativeSampleLoader`：最近 20 条 `correct/reject` 反馈。
4. `Quota`：`ai_pattern_daily_cap`（默认 20/用户/天），超限 ACK + `pipeline_cap`。
5. 调 LLM，要求 JSON：
   ```json
   {
     "patterns": [
       {
         "pattern_type": "temporal_rhythm|recurring_context|...",
         "description": "...",
         "quote": "逐字引用",
         "evidence_ids": ["...", "..."],
         "confidence": 0.0
       }
     ]
   }
   ```
6. 校验：
   - `pattern_type` 在允许集合；
   - `evidence_ids` ⊆ 输入，且 ≥2 条独立证据；
   - `quote` 是某条 evidence 的逐字子串；
   - `confidence >= settings.pattern_min_confidence`（默认 0）；
   - 禁止 `attachment_style|personality_type|diagnosis|trauma_type`。
7. 生成 `fingerprint = sha256(pattern_type + sorted(evidence_ids))[:32]`，不采信模型自报。
8. 返回 `Proposal(kind="pattern", body={...}, item_key=fingerprint)`。
   - v1 每命令最多 1 条模式；多条时每个 `item_key` 必填。
9. 没产出 → `None`（ACK，不重投）。

#### 7.4.3 insight handler

输入：`sourceRef=patterns:<id>`。

1. `PatternLoader.load(user_id, id)`；读不到 → ACK + `source_missing`。
2. 加载该 pattern 的证据 + 最近 30 天同用户证据（≤20）。
3. 加载最近 7 天已发布洞察（避免重复）。
4. LLM JSON：
   ```json
   {
     "insights": [
       {
         "statement": "...",
         "claim_type": "observation|comparison|trend|recurrence|change|goal_progress|behavioral_sequence",
         "pattern_ids": ["..."],
         "evidence_ids": ["...", "..."],
         "confidence": 0.0,
         "quote": "逐字引用"
       }
     ]
   }
   ```
5. 校验：
   - `claim_type` 在允许集合；
   - `pattern_ids` 包含来源 pattern；`evidence_ids` ⊆ 输入；
   - 至少 2 条独立证据；
   - `confidence >= 0.70`；
   - 敏感结论必须带 hedge 词（复用 Go gate 的 `HedgeTerms` 口径，Python 侧也判一次）；
   - `quote` 逐字命中；
   - `safety_service.check_text_tri`。
6. 返回 `Proposal(kind="insight", body={...}, item_key=sha256(statement)[:16])`。
7. 没产出 → `None`。

#### 7.4.4 安全与成本

- pattern/insight 的 LLM purpose：
  - `services.ai_pattern.detect`
  - `services.ai_insight.draft`
- `ai_generation_runs` 记录 `status`、`latency_ms`、`tokens`，用于 7 天报告。
- 内部配额：
  - `ai_pattern_daily_cap: int = 20`
  - `ai_insight_daily_cap: int = 10`
  - 超限 ACK，不 requeue（否则永久撞）。
- 禁止把 `sourceRef` 指向别的用户的证据/模式；loader 必须带 `user_id`。

#### 7.4.5 注册

`backend/services/ai_pipeline.py`：

```python
def build_registry(registry: Registry) -> Registry:
    registry.register("observation", make_observation_handler(...))
    registry.register("pattern", make_pattern_handler(...))
    registry.register("insight", make_insight_handler(...))
    return registry
```

`backend/ai/mq/worker.py:133` 改为导入 `ai_pipeline.build_registry`。
`CLOSEN_MQ_WORKER=0` 时仍然不构造 registry，零副作用。

### 7.5 阶段 C：Go 链改

#### 7.5.1 `Call` 回传 admitted ID

`go-core/internal/messaging/inbound/candidate_decode.go:122`：

```go
type CallResult struct {
    Kind       domain.Kind
    ID         string
    OccurredAt time.Time
}

type Call func(ctx context.Context, d Domain) (CallResult, error)
```

- `decodeCandidate`：`res, err := dm.Admit(ctx, c)` → `CallResult{Kind: kind, ID: res.ID, OccurredAt: res.OccurredAt}`。
- `decodeEvidence`：`res, err := dm.AdmitEvidence(ctx, c)` → `CallResult{Kind: domain.KindEvidence, ID: res.ID, OccurredAt: res.OccurredAt}`。
- `decodeProactive`：`CallResult{Kind: domain.KindProactive, ID: res.ID, OccurredAt: res.OccurredAt}`；没有下一段。
- `Handle` 把 `CallResult` 传给 `requestFollowUp`。

需要给 `domain.Result` 与 `domain.EvidenceResult` 加 `OccurredAt time.Time`：

- `service.go:319` 的 `Admit` 在 `writeAccepted` 时设 `OccurredAt = time.Now().UTC()`（模式行的创建时刻）。
- `service_evidence.go:125` 的 `AdmitEvidence` 设 `OccurredAt = c.OccurredAt`（来源原文的发生时刻）。

#### 7.5.2 `followUpAfter`

`go-core/internal/domain/task_chain.go:43-46` 改为：

```go
var followUpAfter = map[Kind]AITaskKind{
    KindEvidence: AITaskPattern,
    KindPattern:  AITaskInsight,
}
```

- `KindEvidence` 是「链上的 kind」，不是 `Candidate.Kind`；加到 `candidate.go` 时注释清楚。
- `KindFact` 仍可 `Admit`，但不再有下一段；因为 observation 产 evidence，不再产 fact。

#### 7.5.3 `admittedKind`

`go-core/internal/messaging/inbound/inbound.go:570` 增加 evidence：

```go
var admittedKinds = map[string]domain.Kind{
    "candidate.fact":     domain.KindFact,
    "candidate.pattern":  domain.KindPattern,
    "candidate.insight":  domain.KindInsight,
    "candidate.evidence": domain.KindEvidence,
}
```

`CandidateKinds`（`candidate_decode.go:110`）保持只含 fact/pattern/insight，因为它用于“体内自报 kind”的校验；evidence 没有 kind 字段。

#### 7.5.4 `requestFollowUp`

`inbound.go:592` 改为：

```go
func (r *Runner) requestFollowUp(ctx context.Context, st domain.Store, d Delivery, admitted domain.CallResult) error
```

- `next, ok := domain.NextAITaskForKind(admitted.Kind)`；
- 根据 `admitted.Kind` 生成 `sourceRef`：
  - `KindEvidence` → `"evidence:" + admitted.ID`；
  - `KindPattern` → `"patterns:" + admitted.ID`；
- `sourceOccurredAt = admitted.OccurredAt.Format(time.RFC3339)`；
- `TaskRequestBody(next, admitted.Kind, d.MessageID, sourceRef, sourceOccurredAt)`；
- `DeriveTaskOperationID(next, d.MessageID)` 不变。

#### 7.5.5 任务来源表白名单

`task_chain.go` 保留：

- `sourceRefPattern`（四表，候选来源口径）；
- `enabledSourceTables`（`memories: true`，候选来源口径）。

新增：

```go
var taskSourceRefPattern = regexp.MustCompile(
    `^(memories|reflections|mood_checkins|chat_messages|evidence|patterns):[0-9a-f-]{36}$`,
)

var enabledTaskSourceTablesByStage = map[AITaskKind]map[string]bool{
    AITaskObservation: {"memories": true, "reflections": true},
    AITaskPattern:     {"evidence": true},
    AITaskInsight:     {"patterns": true},
}

func ValidateTaskSourceRef(stage AITaskKind, sourceRef, sourceOccurredAt string) error
```

- `TaskRequestBody` 调用 `ValidateTaskSourceRef(stage, ...)`。
- 保留 `ValidateSourceRef` 给旧测试/候选面；不要删，避免一次改太多。
- `sourceRef` 为空仍然合法（但第一段和 follow-up 都应非空）。

#### 7.5.6 Python 对应

`backend/ai/mq/contract.py` 增加：

```python
TASK_SOURCE_REF_PATTERN = re.compile(
    r"^(memories|reflections|mood_checkins|chat_messages|evidence|patterns):[0-9a-f-]{36}$"
)

TASK_SOURCE_REF_TABLES: Mapping[str, frozenset[str]] = {
    "TASK_TYPE_OBSERVATION_EXTRACT": frozenset({"memories", "reflections"}),
    "TASK_TYPE_PATTERN_DETECT": frozenset({"evidence"}),
    "TASK_TYPE_INSIGHT_DRAFT": frozenset({"patterns"}),
}

def validate_task_source_ref(task_type, source_ref, source_occurred_at): ...
```

`parse_command` 在 `validate_source_ref` 之后调用 `validate_task_source_ref(expected_task_type, ...)`。
**不要改** `SOURCE_REF_PATTERN` / `SOURCE_REF_TABLES`，否则 `test_ai_mq_contract.py:536-567` 的三方同值闸会红。

#### 7.5.7 observation handler 支持 reflections

`backend/services/ai_observation.py` 的 `load_source` 增加：

- `memories` → `memory_repository.get`
- `reflections` → `growth_repository.get_owned(record_type='reflection')`

返回统一结构：`content`、`occurred_at`、`source_id`、`source_table`。
`MIN_CONTENT_CHARS=30`、`already_extracted` 逻辑不变。

### 7.6 阶段 D：部署与开关

#### 7.6.1 k8s

`deploy/k8s/10-config.yaml` 增加：

```yaml
CLOSEN_MQ_WORKER: "0"
CLOSEN_OUTBOX_WORKER: "0"
CLOSEN_CANDIDATE_WORKER: "0"
CLOSEN_AI_TASK_EMITTER: "0"
CLOSEN_AI_PIPELINE_ROLLOUT: "0"
CLOSEN_AI_PIPELINE_ALLOWLIST: ""
CLOSEN_MOOD_ANALYSIS_DEPTHS: "0"
CLOSEN_FREE_DAILY_MOOD_ANALYSES: "1"
CLOSEN_PRO_DAILY_MOOD_ANALYSES: "20"
CLOSEN_PRO_PLUS_DAILY_MOOD_ANALYSES: "50"
```

新建 `deploy/k8s/35-py-ai-worker.yaml`：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: py-ai-worker
  namespace: closenai
spec:
  replicas: 1
  selector:
    matchLabels: {app: py-ai-worker}
  template:
    metadata:
      labels: {app: py-ai-worker}
    spec:
      containers:
        - name: py-ai-worker
          image: ghcr.io/jamesfeng2009/closenai-api:0.1.0
          command: ["python", "-m", "backend.ai.mq.worker"]
          envFrom:
            - configMapRef: {name: closenai-config}
            - secretRef: {name: closenai-runtime}
          resources:
            requests: {cpu: 100m, memory: 128Mi}
            limits: {cpu: 500m, memory: 512Mi}
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: {drop: ["ALL"]}
```

`go-api` 已经在 `10-config.yaml` 的 `envFrom` 里，`CLOSEN_OUTBOX_WORKER` / `CLOSEN_CANDIDATE_WORKER` 打开即可，不需要新 Deployment。

#### 7.6.2 compose

`deploy/compose/docker-compose.prod.yml`：

- `py-api`：`CLOSEN_AI_TASK_EMITTER`、`CLOSEN_AI_PIPELINE_ROLLOUT`、`CLOSEN_AI_PIPELINE_ALLOWLIST`、`CLOSEN_MOOD_ANALYSIS_DEPTHS`、配额。
- `py-ai-worker`：`CLOSEN_MQ_WORKER=${CLOSEN_MQ_WORKER:-0}`。
- `go-api`：`CLOSEN_OUTBOX_WORKER`、`CLOSEN_CANDIDATE_WORKER`。
- `.env.example` 只写键名与默认值，不写真实值。

#### 7.6.3 开量顺序

1. 部署代码，所有开关 0；确认无行为变化。
2. 开 `CLOSEN_OUTBOX_WORKER=1`、`CLOSEN_CANDIDATE_WORKER=1`、`CLOSEN_MQ_WORKER=1`；无 emitter，队列为空。
3. 开 `CLOSEN_AI_TASK_EMITTER=1`，`ALLOWLIST=内部 2-3 个账号`；观察 24h。
4. `ROLLOUT=5` → 20 → 50 → 100；每档至少 48h。
5. 7 天后出报告，才允许写「生产开量」。

#### 7.6.4 回滚

- 立即：`CLOSEN_AI_PIPELINE_ROLLOUT=0`、`CLOSEN_AI_TASK_EMITTER=0`；不再产生新任务。
- 队列积压：保持 worker 开启排空；不要先关 worker。
- 需要停消费：`CLOSEN_MQ_WORKER=0`，消息留在队列，恢复后继续。
- 已发布洞察：按现有状态机下线（`Insight.status`），不要物理删除。
- 不删 `outbox_messages` / `inbox_messages`；保留取证。

### 7.7 7 天证据与报告

新增 `scripts/ai_pipeline_report.py`（只读）：

- `outbox_messages`：按 `routing_key` / `status` 计数，pending 最老年龄。
- `inbox_messages`：processed / duplicate / rejected，按 reason。
- `evidence` / `patterns` / `insights`：新增数、按状态。
- `ai_generation_runs`：按 purpose/status、p50/p95 latency、tokens。
- 输出 JSON + 一行摘要；不含正文、不含 user_id。
- `--since` / `--until`；无数据时明确 `no_data`。

验收报告必须包含：

- 7 天每天的任务数、成功率、失败 reason 分布；
- 每条链从 observation 到 insight 的通过率；
- 失败率最高的三个 reason；
- 回滚演练记录（时间、命令、观察结果）。

### 7.8 测试清单

| 层 | 测试 | 断言 |
|---|---|---|
| Python | `test_ai_task_outbox.py` | payload 键集/字节、message_id 确定、savepoint 失败不阻断业务 |
| Python | `test_ai_pipeline_rollout.py` | allowlist/百分比/关 |
| Python | `test_pattern_handler.py` | 证据归属、≥2 独立、禁止类型、引用逐字、无产出 ACK |
| Python | `test_insight_handler.py` | claim type、≥2 证据、confidence、hedge、安全 |
| Python | `test_ai_mq_contract.py` | 任务来源表按 stage、候选来源表不变、Go/Python 同值 |
| Python | `test_go_task_command_body_accepted.py` | `followUpAfter` 新边、registry 三 handler、body 五键、events.md 文案 |
| Go | `task_chain_test.go` | DAG、evidence→pattern、pattern→insight、ID 派生 |
| Go | `candidate_decode_test.go` | evidence 返回 ID/occurredAt |
| Go | `inbound_test.go` | follow-up 的 sourceRef 形状、失败回滚 |
| 契约 | `test_contract_freeze.py` | 119 行（KG-26/28/30 全部落地后） |
| 部署 | `kubectl kustomize` / compose config | 新 Deployment 语法、env 齐全 |

**变异自证：**

- 把 `followUpAfter` 的 evidence 边删掉 → `task_chain_test` 红；
- 把 `CallResult.ID` 改成空 → follow-up body 的 `sourceRef` 缺失，测试红；
- 把 `TASK_SOURCE_REF_TABLES` 的 pattern 改成 `memories` → 契约测试红；
- 把 emitter 的 `sort_keys` 去掉 → body 字节夹具红；
- 把 `is_enabled` 的 allowlist 判断删掉 → rollout 测试红。

### 7.9 D-10 边界：为什么不能顺手开 reflections/memories 切流

- Ingress 的 path 匹配不看 HTTP 方法（`deploy/k8s/50-ingress.yaml` 注释与
  `go-core/internal/cutover/path_method_matrix_test.go`）。
- `/api/v1/reflections` 同时挂 `GET`（Go 已实现）与 `POST`（Python 写、LLM、配额）；
- `/api/v1/memories` 同时挂 `GET`（Go 读）与 `POST`（Python 写）；
- `/api/v1/memories/{id}` 同时挂 `GET` 与 `DELETE`。
- 把整条 path 切给 Go，会把 Python 的写也切走；不切，Go 的读就永远接不到流量。

**结论：** KG-31 只做 AI 候选链。Go 读切流要么做 Go BFF 代理（新组件），要么改 Ingress 支持方法匹配（平台能力），另立任务。
本方案**不**把 `CLOSEN_CUTOVER_REFLECTIONS` / `CLOSEN_CUTOVER_MEMORIES` 写进 KG-31 的验收。

---

## 8. 站点任务（KG-10～KG-17）的技术实现

### 8.1 唯一内容源

新建 `site-content/pages.json`（或 `docs/content/pages.json`，二选一，**只能有一个**）：

```json
{
  "pages": [
    {
      "slug": "ai-journal",
      "lang": "en",
      "title": "...",
      "description": "...",
      "h1": "...",
      "body_md": "docs/content/drafts/ai-journal.en.md",
      "cta": "waitlist",
      "related": ["journal-app", "mood-tracker"],
      "sources": [{"url": "...", "date": "2026-11-01"}]
    }
  ]
}
```

`scripts/build_seo_pages.py`：

- 读 JSON + Markdown；
- 生成 `web/<slug>/index.html` 与 `web/en/<slug>/index.html`；
- 自动写 `<link rel="canonical">`、`hreflang`、`x-default`；
- 更新 `web/sitemap.xml`；
- 校验：title/description 唯一、H1 恰好一个、每页 ≥2 内链、无断链、无禁用词；
- `--check` 只校验不写文件，CI 用。

**不要**在 `site/` 与 `web/` 各维护一份。

### 8.2 页面与意图

| 页面 | 意图 | 必须回答 | 差异化段落 |
|---|---|---|---|
| `/en/ai-journal` | 什么是 AI journal | 和普通日记差在哪；「记得你」≠「记得一切」 | 记录→情绪→反思→行动结果 |
| `/en/journal-app` | 选一个 journal app | 为什么不是聊天机器人 | 证据回链 |
| `/en/mood-tracker` | 记录情绪 | 一键签到、7/30 天、热力图 | 非诊断声明 |
| `/en/self-reflection` | 怎么反思 | 从记情绪到找证据 | 每周模板 |
| `/en/journal-prompts` | 提示库 | 30 条原创提示 | 复用 KG-26 JSON |
| `/en/self-discovery` | 长期自我理解 | 画像随记录变化 | 不是一次性测验 |
| `/en/personality-test` | 人格框架 | MBTI/Enneagram 是框架 | 保守版 `16 types` |
| `/en/self-improvement` | 行动结果 | Goal→Action→Outcome | 预期 vs 实际 |
| `/en/goal-tracker` | 目标追踪 | 到期回访 | 不是通用打卡 |
| `/en/overthinking` | 反刍 vs 反思 | 3 步练习 | 危机资源 |
| `/en/compare/rosebud-alternative` | 对比 | 只写可核验差异 | 来源+日期 |
| `/en/compare/day-one-alternative` | 对比 | 同上 | 来源+日期 |

### 8.3 发布闸

- KG-02 法务签字；
- T7-05 唯一官网/运营主体确认；
- KG-18 内容质量清单逐项打勾；
- KG-17 爬虫 0 断链/0 孤儿；
- KG-16 额外依赖 KG-31：核心循环未生产化前，不写「已在生产可用」的对比结论。

### 8.4 回滚

- Cloudflare Pages 回退到上一个部署；
- 单页下线：删 `web/<slug>/` 与 sitemap 行，保留 `docs/content/drafts/` 原稿；
- 404 页面已有 `web/404.html`。

---

## 9. 接口与数据契约汇总

### 9.1 新增端点

| 方法 | 路径 | 归属 | 请求 | 响应 | 错误 |
|---|---|---|---|---|---|
| GET | `/api/v1/reflections/prompts` | Python | `cluster?` | 30 条提示 | 40001 |
| POST | `/api/v1/mood/analysis` | Python | `depth/checkin_id?/focus?/request_id?` | 分析结果 | 40001/40301/40303/40402 |
| POST | `/api/v1/share/quote-card` | Python | `source_type/source_id` | PDF | 40001/40402/40403 |

### 9.2 扩展的响应字段

| 端点 | 字段 | 类型 | 说明 |
|---|---|---|---|
| `GET /growth/dashboard` | `features.mood_analysis_depths` | bool | KG-28 开关 |
| `GET /growth/dashboard` | `features.insight_pipeline_enabled` | bool | KG-25 下一步卡是否包含 insight |
| `GET /growth/dashboard` | `next_step` | object/null | KG-25 |
| `POST /reflections` | `prompt_id` / `prompt_version` / `source` | 请求 | KG-26 |
| `GET /reflections` | `prompt_id` / `source` | 响应 | KG-26 |

这些都不新增端点行；`growth/dashboard` 当前没有 `response_model`，只改 Python schema 与 FE 类型。

### 9.3 不新增的东西

- 不新增 `GET /app-config`。
- 不新增 `mood_analyses` 表。
- 不新增 `reflection_prompts` 表。
- 不新增 `share_quotes` 表。
- 不新增 Alembic 迁移。
- 不新增分析事件属性键。

---

## 10. 数据、导出、清理、血缘

| 面 | 现状 | KG-26/28/30/31 的影响 | 要做的事 |
|---|---|---|---|
| 用户导出 | `backend/services/export_service.py:56` 的 `EXPORT_EXCLUDE_REASONS` | `outbox_messages` 未排除，payload 会进导出 | **确认**：outbox payload 属个人数据面，导出可保留；若要排除，加进 `EXPORT_EXCLUDE_REASONS` 并写理由 |
| 用户删除 | `backend/services/user_service.py:135` 已含 `OutboxMessage` | 新增行会被清理 | 不需要改 |
| 数据血缘 | `docs/data-lineage.md` / `backend/tests/test_data_lineage_ledger.py` | 新增 outbox 生产者 | 加一条 `Python memory/reflection → outbox_messages → Go outbox → MQ` 边 |
| PG schema 快照 | `go-core/db/schema/*.sql` 是只读派生 | 无新表/列 | 不需要改 |
| 指标 | `CORE_LOOP_STEPS` 7 格 | KG-19 另起表 | 不改 `metrics_export.py` |
| 提示库 | 新 JSON | 不入库 | 备份/版本靠 git；`version` 字段 |
| 分析结果 | 不落库 | 保存为反思才落 `growth_records` | 用户删除账号时随 `growth_records` 清理 |
| 原话卡 | 不落卡片文件 | 无 | 源删除即失效 |

**导出策略建议：** `outbox_messages.payload` 里有 `sourceRef` 与时间，不含正文，但仍是个人数据面。
如果法务要求导出包含「系统内部投递记录」，保留；如果要求最小化，加入
`EXPORT_EXCLUDE_REASONS`，理由写「内部投递意图，不是用户内容」。二选一，KG-02 拍板。

---

## 11. 开关、灰度、回滚总表

| 开关 | 默认 | 作用 | 回滚 |
|---|---|---|---|
| `CLOSEN_MOOD_ANALYSIS_DEPTHS` | 0 | KG-28 入口/端点 | 0 |
| `CLOSEN_FREE_DAILY_MOOD_ANALYSES` | 1 | KG-28 配额 | 调回 |
| `CLOSEN_PRO_DAILY_MOOD_ANALYSES` | 20 | KG-28 配额 | 调回 |
| `CLOSEN_PRO_PLUS_DAILY_MOOD_ANALYSES` | 50 | KG-28 配额 | 调回 |
| `CLOSEN_AI_TASK_EMITTER` | 0 | KG-31 第一段发射 | 0 |
| `CLOSEN_AI_PIPELINE_ROLLOUT` | 0 | KG-31 比例 | 0 |
| `CLOSEN_AI_PIPELINE_ALLOWLIST` | 空 | KG-31 点名 | 清空 |
| `CLOSEN_MQ_WORKER` | 0 | Python 消费 | 0，但先排空 |
| `CLOSEN_OUTBOX_WORKER` | 0 | Go 发布 outbox | 0 |
| `CLOSEN_CANDIDATE_WORKER` | 0 | Go 消费候选 | 0 |
| `CLOSEN_CUTOVER_*` | 0 | Go 读切流 | 不在 KG-31 范围 |

**红线：** 任何开关默认 0；没有 7 天数据不许写「生产开量」；没有回滚演练不许结案。

---

## 12. 测试与证据清单

### 12.1 每个代码任务的 DoD

| 任务 | 必跑 | 证据 |
|---|---|---|
| KG-23 | `npm test -- i18nLocales claimsCopy` + `python scripts/check_claims.py --self-test` | 0 命中输出、self-test 红、15 语言键齐 |
| KG-24 | `npm test -- paywallCopy` + 权益对照表 | 文案 diff、15 语言键齐 |
| KG-25 | `pytest test_dashboard_next_step.py` + `npm test -- funnel nextStepCard` | 6 状态、5 事件、调用点 |
| KG-26 | `pytest test_prompt_library test_reflection_prompt_save` + jest | 30 条、版本、metadata |
| KG-27 | `npm test -- moodStats` | 7/30、空态/有数据态截图 |
| KG-28 | `pytest test_mood_analysis` + jest | 证据子串、降级、危机、配额、开关 |
| KG-30 | `pytest test_quote_card` + jest | 归属、AI 记忆拒绝、删除 |
| KG-31 | pytest + Go test + 契约 + 部署 config + 7 天报告 | 链通过率、失败 reason、回滚演练 |

### 12.2 契约计数

| 时点 | router | app | 总行 |
|---|---:|---:|---:|
| 现在 | 115 | 1 | 116 |
| KG-26 落地 | 116 | 1 | 117 |
| + KG-28 | 117 | 1 | 118 |
| + KG-30 | 118 | 1 | 119 |

每次改 `test_contract_freeze.py:105-108` 与 `contracts/endpoints.md` / `contracts/openapi.json`（跑 `python scripts/export_contract.py`）。

### 12.3 不能假装跑过的

- 真 PG 迁移/血缘：本机没有 PG 时写「未跑」，不写绿。
- 原生 IAP/深链：Expo Go 跑不了，写「未跑」。
- ASC/Play 审核：外部账号，写「待审」。
- Cloudflare 发布：T7-05 未决，写「未发布」。

---

## 13. 30/60/90 执行顺序

### 0–30 天

| 周 | 代码 | 文档/外部 |
|---|---|---|
| W1 | KG-23、KG-24、KG-27 | KG-01、KG-03、KG-04 |
| W2 | KG-26、KG-30 | KG-09、KG-18、KG-19 |
| W3 | KG-25（不依赖 insight 的版本） | KG-02 法务启动、KG-20 |
| W4 | KG-28 后端 + 前端 | KG-10/11 草稿、KG-08 |

### 31–60 天

| 周 | 代码 | 文档/外部 |
|---|---|---|
| W5 | KG-28 实验上线 | KG-12/13/14 草稿 |
| W6 | KG-31 阶段 A/B（emitter + handlers） | KG-05、KG-17 |
| W7 | KG-31 阶段 C（Go 链） | KG-21 账号 |
| W8 | KG-31 canary + 7 天观察 | KG-22 首份复盘 |

### 61–90 天

| 周 | 代码 | 文档/外部 |
|---|---|---|
| W9 | KG-31 放量/回滚演练 | KG-15/16 法务 |
| W10 | KG-25 insight 步骤开启 | KG-10～16 发布（若 T7-05 解除） |
| W11 | 数据复盘、修失败 reason | KG-22 月度 |
| W12 | 结案证据归档 | 台账更新 |

**并行原则：** 文档/法务/商店不阻塞代码；代码批次之间只共享 §3 的 D0–D12，不共享临时分支。

---

## 14. 台账修订建议（本文不改台账）

| 任务 | 建议修改 | 原因 |
|---|---|---|
| KG-25 | 依赖加 `KG-31（验证闸）` | 「首条有证据洞察」在 KG-31 前不可达；不加会做出死胡同 |
| KG-31 | 拆成 KG-31a「AI 候选链生产开量」与 KG-31b「Go 读切流」 | 当前描述把管线和 D-10 切流混在一起；reflections/memories 切流受 Ingress 方法匹配阻塞 |
| KG-31 | 把「切流键开量记录」改成「emitter/worker 开关 + 7 天流量 + 回滚演练」 | 管线开量不靠 `CLOSEN_CUTOVER_*` |
| KG-31 | 明确链是 `evidence→pattern→insight`，不是 `fact→pattern` | 当前代码 observation 产 evidence；台账没写这条 |
| KG-24 | 范围从「四条」扩到「四条 + 副标题 + Pro+ 三项 + compareSample」 | 实际虚假表述不止四条 |
| KG-23 | 注明现网 `web/` 无英文禁用词、`site/` 未发布；实际生产改动是 onboarding + 不部署 site + `web/index.html:527` | 避免把「改 site」当成「改现网」 |
| KG-19 | 要求另起一张 `| 关键词码 |` 表，不改核心七格 | `test_metrics_export.py:67-82` 只读第一张 `| 机器码 |` 表 |
| KG-10～16 | 增加 D0 前置：英文页放 `web/en/`，不部署 `site/` | 当前没有 URL/语言决策，直接写页面会变成两套源 |
| KG-28 | 注明开关走 dashboard `features`，不新增 `/app-config` | 契约最小化 |
| KG-30 | 注明 memory 必须 `generation_run_id IS NULL` | 否则 AI 抽取的记忆会被当成「用户原话」 |
| KG-26 | 注明提示正文只做 en/zh，其他语言回退 en 并显式返回 `language_used` | 避免 30×15 翻译爆炸和静默错语言 |
| KG-20 | 注明 App Store campaign link 用 `pt/ct/mt`，不是 UTM；不承诺确定性归因 | UTM 到安装不是确定性链路 |
| KG-19/22 | 注明指标只从业务表数，不数事件 | 与 T7-06 口径一致 |

---

## 15. 风险清单

| 风险 | 影响 | 缓解 |
|---|---|---|
| KG-31 链改跨 Python/Go | 一条消息两边形状不一致 → 永久拒绝 | 跨语言 body 夹具 + 两侧按 stage 的表 + 7 天 canary |
| D-10 切流误解 | 把管线开量写成「Go 已接管」 | 本文 §7.9 + 台账修订 |
| KG-28 LLM 编造引用 | 用户看到没有依据的结论 | 逐字子串校验 + 降级 + 危机分流 |
| KG-28 重复扣配额 | 用户抱怨 | 前端禁用 + request_id + 配额上限 |
| KG-24 删键 | 14 个语言漏改 → i18n 红 | 15 语言同批改 + 测试 |
| KG-23 翻译质量 | 语义仍绝对化 | 人工复核 13 语言 + claims 闸 |
| 站点两套源 | SEO 蚕食/重复内容 | D0 唯一源 + 生成脚本 |
| 法务未签字 | 对比页/人格页不能发 | KG-02 前置 + 草稿状态 |
| 7 天数据不足 | 不能结案 | 台账写「未验证」，不伪造 |

---

## 16. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-01 | 初版：31 任务矩阵、D0–D12、KG-26/28/30/31 详细方案、台账修订建议 |
| 2026-11-01 | KG-26 结案回写：§6.1.2 路径改判为 `GET /api/v1/reflection-prompts`（N8 Prefix 根因）、§6.1.3 读侧字段撤销（Go 字节对齐闸，溯源只进写侧）；实测计数与 8 组变异见 `docs/growth/prompt-library-2026-11.md`，台账 KG-26 置 `REVIEW`。本批未动 KG-28/30/31，方案其余部分仍是待执行状态 |

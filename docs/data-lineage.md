# 数据血缘：一张表在「导出／删除／审计」三条链路上的位置

登记口径（T5-01 起，2026-10-18 由 T1-02 升级为全量双向对账）：**任何带 `user_id` 的表**，
以及任何在导出/排除清单里出现的表，都必须在下面的 §二 占一行，并且同时满足：

- 要么进 `backend/services/user_service.py` 的 `PURGE_TARGETS`（注销删净），
- 要么进 `PURGE_EXEMPT_TABLES` 并带理由（目前只有重试台账配得上，理由见那张表的 docstring）。

对账闸门（三条，缺一条这份文档就会开始漂）：

1. `backend/tests/test_purge_covers_every_user_scoped_table`（`test_account_deletion.py`）：
   `Base.metadata` 每张带 `user_id` 的表 ∈ `PURGE_TARGETS ∪ PURGE_EXEMPT_TABLES`，豁免逐条带理由；
2. `backend/tests/test_data_lineage_ledger.py`：**双向**核 §二 —— 文档点名的表必须还在库里
   （文档不许漂在代码前面），代码里的删除清单／导出靶／豁免表必须都在文档里（新表不许悄悄不进台账），
   而且每行的「来源／注销删除／导出」三列必须与 `PURGE_TARGETS`、`export_targets()`、
   `CANONICAL_TABLES` 的取值逐格相等；
3. 同文件里的正向证人：`chat_messages` 必须在册、文档与 `PURGE_TARGETS` 不许零交集——
   反向闸最经典的失效方式是把表格清空，让"全都不缺"变成恒真。

> 为什么第 2 条是这批改动的重点：T1-01 建 `data_export_jobs` 时没进这张表，而旧闸只核
> 「文档点名的表还在不在代码里」，所以漏登记一声不响。更糟的是导出侧：文档从 T5-01 起就写着
> `user_consents`／`safety_events`／`push_tokens`「不进导出」，而导出靶是从 `PURGE_TARGETS`
> 派生的、`EXPORT_EXCLUDE_REASONS` 里根本没有这三条——**用户的下载包里一直装着同意台账、
> 危机命中摘录和设备推送凭据**。这个矛盾是本次把闸做成双向时当场抓出来的，已在
> `export_service.py` 补上排除与理由（带日期的勘误记在 §五）。

## 一、三条链路的定义

| 链路 | 入口 | 语义 | 判据 |
|---|---|---|---|
| 个人内容导出 | `POST /user/export`（T1-01 异步 job，§14.2） | 用户把自己**创作/经历**的内容带走 | 内容是不是用户的 |
| 注销物理删除 | `DELETE /user/account` → QStash → `/cron/purge-user` → `purge_user` | 账号注销后一行都不留 | 有没有 `user_id` |
| 审计留存 | 无对外接口，只有日志（`purge 举证 …`、`POSTHOG-ERASURE-STUCK …`） | 证明「删过、什么时候删、删了几行」 | — |

列的取值：来源 `go-core` ＝ N2/N7 canonical 表，Go 是唯一写入口（D-5，Python 只在注销时读写）；
`py-api` ＝ Python 侧服务写入。注销删除 `✅` 进 `PURGE_TARGETS`、`豁免` 进
`PURGE_EXEMPT_TABLES`、`❌` 两者都不进。导出 `✅` 在 `export_targets()` 里、`❌` 在
`EXPORT_EXCLUDE_REASONS` 里。

## 二、逐表登记（全量：43 张，按上面三条闸逐格对账）

| 表 | 来源 | 注销删除 | 导出 | 理由 |
|---|---|---|---|---|
| `chat_messages` | py-api | ✅ | ✅ | 对话本体：用户创作与经历的第一手内容 |
| `memories` | py-api | ✅ | ✅ | 记忆文本行是用户画像本体；列级排除 `embedding`（派生向量，见 §五-2） |
| `mood_checkins` | py-api | ✅ | ✅ | 用户自己填的情绪签到 |
| `growth_records` | py-api | ✅ | ✅ | 成长记录正文属用户经历 |
| `user_milestones` | py-api | ✅ | ✅ | 里程碑徽章是用户获得的记录 |
| `personality_profiles` | py-api | ✅ | ✅ | 性格画像本体（AI 写、但写的是用户自己） |
| `personality_assessments` | py-api | ✅ | ✅ | 测评答题与结果 |
| `journey_events` | py-api | ✅ | ✅ | 第一周剧本事件的推进记录 |
| `onboarding_profiles` | py-api | ✅ | ✅ | 初读卡档案：用户当场填的答案 |
| `share_exports` | py-api | ✅ | ✅ | 分享导出行是用户生成的内容指针 |
| `ai_generation_runs` | py-api | ✅ | ✅ | O4 生成留痕：带 prompt 与产出，属个人数据面 |
| `outbox_messages` | go-core | ✅ | ✅ | payload 存事件正文 ⇒ 用户可读内容，注销必须删净（裁决七-6 N7-13） |
| `personal_models` | go-core | ✅ | ✅ | N2 canonical：模型本体 |
| `personal_model_versions` | go-core | ✅ | ✅ | N2 canonical：版本快照属用户画像历史 |
| `personal_model_events` | go-core | ✅ | ✅ | N2 canonical：模型变更事件 |
| `source_records` | go-core | ✅ | ✅ | N2 canonical：来源记录（对话/签到原文派生） |
| `evidence` | go-core | ✅ | ✅ | N2 canonical：证据正文 |
| `personal_facts` | go-core | ✅ | ✅ | N2 canonical：关于用户的事实 |
| `fact_evidence` | go-core | ✅ | ✅ | N2 canonical：纯关联表，无外键 ⇒ 必须自己出现在清单里 |
| `personal_goals` | go-core | ✅ | ✅ | N2 canonical：用户目标 |
| `personal_commitments` | go-core | ✅ | ✅ | N2 canonical：用户承诺 |
| `patterns` | go-core | ✅ | ✅ | N2 canonical：模式 |
| `pattern_evidence` | go-core | ✅ | ✅ | N2 canonical：纯关联表 |
| `insights` | go-core | ✅ | ✅ | N2 canonical：AI 对用户说过的话，属可读内容 |
| `insight_evidence` | go-core | ✅ | ✅ | N2 canonical：纯关联表 |
| `insight_patterns` | go-core | ✅ | ✅ | N2 canonical：纯关联表 |
| `proactives` | go-core | ✅ | ✅ | N2 canonical：主动消息本体 |
| `proactive_insights` | go-core | ✅ | ✅ | N2 canonical：纯关联表 |
| `proactive_patterns` | go-core | ✅ | ✅ | N2 canonical：纯关联表 |
| `outcomes` | go-core | ✅ | ✅ | N2 canonical：干预后的结果 |
| `outcome_links` | go-core | ✅ | ✅ | N2 canonical：纯关联表 |
| `user_feedback` | go-core | ✅ | ✅ | N2 canonical：用户对 AI 说法的纠正 |
| `inbox_messages` | go-core | ❌ | ❌ | 只有投递元数据、无 `user_id`（N2 口径），所以两条链路都不涉及 |
| `users` | py-api | ✅ | ✅ | 账户本体：注销的举证终点，导出里的 profile 行 |
| `subscriptions` | py-api | ✅ | ✅ | 用户自己的订阅状态属交易数据，可带走；删除受财税留存约束由注销链路统一处理 |
| `user_entitlements` | py-api | ✅ | ✅ | 买断/权益记录同上：是用户获得的凭证，不是平台凭据 |
| `receipts` | py-api | ✅ | ❌ | 支付收据**原文**含交易凭证：打进可转发的 zip 对泄露有增益，付费方另有账 |
| `refresh_tokens` | py-api | ✅ | ❌ | 登录凭据本体（§14.1 禁止导出物含凭据） |
| `push_tokens` | py-api | ✅ | ❌ | APNs/FCM 设备凭据：导出无意义、留着是风险；注销侧另有 `clear_push_tokens` 先清 |
| `user_consents` | py-api | ✅ | ❌ | 同意记录属**审计**（谁在哪个版本表过态），不是用户创作的内容；塞进导出包等于替平台做一份「你同意过什么」的二次分发。注销仍要删净：留在库里就成了「谁注销了」的旁证表（口径同 J4 墓碑表裁决） |
| `safety_events` | py-api | ✅ | ❌ | 危机命中审计（§5.16 ④）：摘录只在 24h 人工抽检链路内可见，不进导出 |
| `data_export_jobs` | py-api | ✅ | ✅ | 申请时刻、范围、产物路径都是用户自己的数据；注销时先按 `file_path` 删盘上产物再删行（T1-01） |
| `posthog_erasure_retries` | py-api | 豁免 | ❌ | T1-02 的外部平台重试台账：它唯一的存在意义就是「`users` 行已删、PostHog 还没擦干净」。进 `PURGE_TARGETS` 会让 `purge_user` 刚写的行被自己的删除循环抹掉，重试线索当场断。自我销毁：擦成功即删行，超 `posthog_erasure_retry_max_days` 打 ERROR 告警后也删行，终态只留日志——所以它不是 J4 否掉的墓碑表 |

## 三、兼容期布尔位的出处

`users.ai_consent` / `users.analytics_consent` / `users.push_enabled` 是**开关**，
`user_consents` 是**台账**（Baseline §14.2 要的是后者）。三条写路径都必须落台账：
`POST /user/consents`、旧端点 `POST /user/ai-consent`·`/user/analytics-consent`、
`PATCH /user/settings` 的 `push_enabled`。闸门见
`backend/tests/test_consent_purposes.py`（双写不脱钩一节）。

## 四、PostHog 侧残留（库外的那一半删除）

`erase_person_data` 走 PostHog Personal API 删人与事件。它在 `purge_user` 里**刻意不抛**
（分析平台抖动不能把注销卡在半途），失败时写 `posthog_erasure_retries` 一行，由
`/cron/purge-deleted-users`（每小时）与进程内兜底循环（每 10 分钟）重试；超过
`settings.posthog_erasure_retry_max_days` 打 `POSTHOG-ERASURE-STUCK` ERROR 后删行。
判据见 `backend/tests/test_posthog_erasure_retry.py` 与实库伴生闸
`test_posthog_erasure_retry_pg.py`。

## 五、勘误与列级排除

1. **2026-10-18（T1-02）导出列与代码不符**：`user_consents`／`safety_events`／`push_tokens`
   在本文件登记为「不进导出」，而导出靶派生自 `PURGE_TARGETS`、这三条从没进过
   `EXPORT_EXCLUDE_REASONS` ⇒ 用户的下载包里实际装着它们。根因是旧对账闸只核
   「文档点名的表还在不在代码里」这一个方向。已补排除并在
   `export_service.py:EXPORT_EXCLUDE_REASONS` 逐条写理由；闸改成双向由这条漂移本身作证。
2. 列级排除：`memories.embedding`（512 维派生向量，不可阅读且让 zip 体积翻数倍，可由记忆重建）。
   列级与表级同一诚实要求：每格必须有理由，闸逐条验（见 `test_data_export.py` 的形状护栏一节）。
3. 交易面（`subscriptions`／`user_entitlements`）此前只在文档里标「不导出」而没有可执行判据，
   现已与代码同值：导出 ✅、删除 ✅；只有 `receipts` 因含交易凭证原文而排除。

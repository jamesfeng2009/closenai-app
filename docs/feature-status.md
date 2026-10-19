# 功能状态台账（T0-05）

> 本文件由 `scripts/feature_status.py` 从 `docs/feature-status.json` 生成，**禁止手改**。
> 六态口径来自 Baseline §15，覆盖 §16 十行验收矩阵；每条声明的证据由脚本对账
> （`contracts/endpoints.md`／`frontend/src`／`deploy/schedule/crontab`／`go-core/internal/config`）。
> 生成时对账结论：全部判据通过。

## 状态一览

| 态 | 功能数 | 功能 |
|---|---|---|
| `SPECIFIED` | 0 | — |
| `IMPLEMENTED` | 1 | consent.ledger |
| `WIRED` | 6 | export.data、growth.history、insight.pattern、memory.manage、reflect.record、subscription.iap |
| `E2E_VERIFIED` | 5 | account.delete、journey.week1、life_archive.pdf、privacy.terms、proactive.message |
| `PRODUCTION_ENABLED` | 0 | — |
| `VALUE_MEASURED` | 0 | — |
| 合计 | **12** | 覆盖 §16 十行矩阵（一行可拆多条时按 `matrix_row` 归位） |

## 逐条对账

| 功能 | §16 矩阵行 | 态 | 前端入口 | 端点 | 调度（crontab 实测） | 切流键（默认值） | 下一步缺口 |
|---|---|---|---|---|---|---|---|
| `reflect.record` 反思记录（草稿生成 → 保存 → 列表回读） | 反思记录 | **WIRED** | frontend/src/screens/ReflectScreen.tsx:记录入口 | 3 条 | — | CLOSEN_CUTOVER_REFLECTIONS | §16 必测异常四条（网络失败、重复提交、空内容、越权读取）没有一条端到端闸；N8 灰度键 CLOSEN_CUTOVER_REFLECTIONS 默认 0 |
| ↳ 证据 |  |  | 调用点：generateReflectionDraft✓、saveReflection✓、listReflections✓ | 测试：frontend/__tests__/reflectionRepository.test.ts、backend/tests/test_n8_batch5_contract.py | 端到端：— | 实测：— |  |
| `growth.history` 历史记录（列表 / 时间线 / 详情） | 历史记录 | **WIRED** | frontend/src/screens/GrowthRecordsScreen.tsx:历史列表 | 4 条 | — | — | §16 必测异常「空列表 / 分页边界 / 跨用户读取」未立端到端闸；历史查看率与回访率指标未采 |
| ↳ 证据 |  |  | 调用点：getGrowthRecords✓、getTimeline✓、getTimelineDay✓、getRelationshipTimeline✓ | 测试：frontend/__tests__/growthRecordsWiring.test.ts、backend/tests/test_growth_ordering.py、backend/tests/test_growth_service.py | 端到端：— | 实测：— |  |
| `memory.manage` 记忆管理（新建 / 详情 / 演化 / 删除） | 记忆管理 | **WIRED** | frontend/src/screens/MemoriesScreen.tsx:记忆页 | 6 条 | — | CLOSEN_CUTOVER_MEMORIES | N8 批 6 的 memories 读路径灰度键 CLOSEN_CUTOVER_MEMORIES 默认 0（管道通了、流量仍是零）；重复候选、矛盾检测失败、空演化三条必测异常未立端到端闸 |
| ↳ 证据 |  |  | 调用点：listMemories✓、createMemory✓、getMemory✓、versionMemory✓、deleteMemory✓ | 测试：frontend/__tests__/memoriesWiring.test.ts、backend/tests/test_memory_service.py、backend/tests/test_memory_ordering.py、backend/tests/test_n8_batch6_contract.py | 端到端：— | 实测：— |  |
| `insight.pattern` 模式/洞察（候选生成 → 证据验证 → 展示） | 模式/洞察 | **WIRED** | frontend/src/screens/InsightsScreen.tsx:洞察与依据区 | 3 条 | /api/v1/cron/daily-insight（已排程）、/api/v1/cron/weekly-insight（已排程） | CLOSEN_CUTOVER_DISCOVERY | 证据段（记忆→命令→候选→Go 入站→权威表）2026-10-19 起跨进程真跑通一次（test_v3_evidence_e2e.py，本机真库＋真 broker），但**产品流量仍是零**：候选生产面（R6）没有发射点，「生成 Pattern → 展示」这后半段仍未被任何端到端闸摸过，所以状态不升到 E2E_VERIFIED；证据跨用户/模型超时/用户否认三条必测异常归 W3（T3-01…T3-05） |
| ↳ 证据 |  |  | 调用点：getWeeklyInsight✓、getPatternDiscovery✓ | 测试：frontend/__tests__/insightEvidence.test.ts、frontend/__tests__/insightTier.test.ts、backend/tests/test_evidence_policy.py、backend/tests/test_evidence_excerpt_locatable.py | 端到端：backend/tests/test_v3_evidence_e2e.py | 实测：— |  |
| `proactive.message` 主动消息（设置 → 调度 → 生成 → 投递 → 关闭） | 主动消息 | **E2E_VERIFIED** | frontend/src/screens/ProfileScreen.tsx:推送时间/频率/总开关/免打扰四行 | 3 条 | /api/v1/cron/daily-message（已排程） | — | PRODUCTION_ENABLED 还差宿主 VPS 上 `crontab deploy/schedule/crontab` 的安装取证（文件在库里有，装没装只能上机看） |
| ↳ 证据 |  |  | 调用点：updateUserSettings✓、getUserProfile✓ | 测试：backend/tests/test_quiet_hours.py、backend/tests/test_push_frequency_setting.py、backend/tests/test_push_enabled_setting.py、frontend/__tests__/quietHoursWiring.test.ts、frontend/__tests__/pushFrequencyWiring.test.ts、frontend/__tests__/pushEnabledWiring.test.ts | 端到端：backend/tests/test_delivery_off_e2e.py、backend/tests/test_delivery_off_pg.py、backend/tests/test_cron_service.py | 实测：pytest test_delivery_off_e2e.py 5 passed＋test_delivery_off_pg.py 2 passed（真 PG 一次性空库，CLOSEN_REQUIRE_PG=1）＋test_cron_service/test_quiet_hours 等合计 50 passed；jest quietHoursWiring 29 passed＋i18nLocales 107 passed；变异 13 红 2 绿（scripts/mutate_w5_gates.py） |  |
| `journey.week1` 第一周成长剧本（cron → 到期事件 → 投递 → 确认） | 第一周成长剧本 | **E2E_VERIFIED** | frontend/src/screens/Week1ReportScreen.tsx:旅程页 | 3 条 | /api/v1/cron/journey（已排程） | CLOSEN_CUTOVER_JOURNEY | D1/D7 留存与剧本完成率是产品指标，本机无真实用户数据 ⇒ VALUE_MEASURED 不可宣称；CLOSEN_CUTOVER_JOURNEY 默认 0 |
| ↳ 证据 |  |  | 调用点：getJourneyEvents✓、acknowledgeJourneyEvent✓ | 测试：frontend/__tests__/journey.test.ts、backend/tests/test_journey.py、backend/tests/test_idempotency_indexes.py | 端到端：backend/tests/test_journey.py | 实测：pytest test_journey.py 28 passed（含 deliver/dispatch 链）＋test_idempotency_indexes.py 16 passed（journey 日序唯一索引）＋test_cron_schedule_coverage.py 排程对账；jest journey.test.ts 在同批 1325 passed 内 |  |
| `life_archive.pdf` Life Archive PDF（权益验证 → 生成 → App 下载） | Life Archive PDF | **E2E_VERIFIED** | frontend/src/screens/PaywallScreen.tsx:已购档位与档案领取口 | 3 条 | /api/v1/cron/delivery-audit（已排程） | — | 原生（iOS/Android）下载与打开 PDF 的真机取证未做。设备侧的「下载失败」自 2026-10-18（T1-04）起不再是空白：PaywallScreen 上报 archive_share_result{result,reason} 且 PdfUnavailableError 有独立机器码，但那条事件的真机实测（StoreKit 沙盒买一单，看三步事件是否齐）仍未做，且 DELIVERY-ALERT 在 Loki／Alertmanager 的落点没取过证 |
| ↳ 证据 |  |  | 调用点：getLifeArchivePdf✓、getEntitlements✓ | 测试：frontend/__tests__/pdfExportWiring.test.ts、frontend/__tests__/lifeArchiveConfig.test.ts、backend/tests/test_life_archive.py、backend/tests/test_delivery_audit.py、backend/tests/test_delivery_audit_pg.py、backend/tests/test_delivery_observability.py、frontend/__tests__/archiveShareTracking.test.ts | 端到端：backend/tests/test_life_archive.py | 实测：pytest test_life_archive.py 17 passed（权益门禁＋生成失败＋重复请求形状）；jest pdfExportWiring＋lifeArchiveConfig 在全量 1325 passed 内；T1-04 追加 pytest test_delivery_audit.py 17 passed＋test_delivery_observability.py 19 passed＋真库 test_delivery_audit_pg.py 2 passed＋jest archiveShareTracking.test.ts 17 passed |  |
| `subscription.iap` 订阅状态（IAP 凭证校验 → 服务端 entitlement → 状态同步） | 订阅状态 | **WIRED** | frontend/src/screens/PaywallScreen.tsx:购买与恢复购买 | 4 条 | /api/v1/cron/trial-reminder（已排程）、/api/v1/cron/delivery-audit（已排程） | — | 沙箱回执、恢复购买、退款/撤销三条必测异常都要真 StoreKit/Play Billing 事件，本机没有；通知端点 POST /iap/notifications 前端零调用是设计使然（Apple 服务端直连）。2026-10-18（T1-04）补上的是**事件面与每日对账**：iap_verify_started/succeeded/failed 三格都有埋点（含超时与 Apple 非零 status 两个失败出口，reason 只放机器码），`/cron/delivery-audit` 数「付了钱、过了宽限期、库里没有 life_archive 成功行」的用户数并按阈值打 DELIVERY-ALERT；真机沙盒那一跑与 Loki 告警落点仍未取证，所以停在 WIRED |
| ↳ 证据 |  |  | 调用点：verifyReceipt✓、getSubscriptionStatus✓、getEntitlements✓ | 测试：backend/tests/test_iap_service.py、backend/tests/test_iap_jws.py、frontend/__tests__/introOffer.test.ts、backend/tests/test_delivery_observability.py、backend/tests/test_delivery_audit.py、frontend/__tests__/archiveShareTracking.test.ts | 端到端：— | 实测：— |  |
| `account.delete` 账户注销（软删除 → 异步物理删除 → 孤儿防护） | 数据导出/删除 | **E2E_VERIFIED** | frontend/src/screens/PrivacySecurityScreen.tsx:注销入口（AuthContext.deleteAccount） | 3 条 | /api/v1/cron/purge-deleted-users（已排程） | — | 工作单 T1-02 第②件（登出清 push token）未做——与既有产品裁决冲突：`AuthContext.tsx:106-121` 写明「登出不注销，后端 APNs 410 自清」，`orphanEndpoints.test.ts` 把那句钉成闸；要翻这个决定归 B-04（产品/安全）。另：「删除完成率」指标未采；QStash 回调 POST /cron/purge-user 的公网验签只能在有 QStash 凭据的环境证，本机未跑；PostHog 擦除走的是真值配置还是 stub、以及 7 天 POSTHOG-ERASURE-STUCK 告警在日志面（Loki/Alertmanager）的落点均未取证 |
| ↳ 证据 |  |  | 调用点：deleteAccount✓ | 测试：frontend/__tests__/accountLifecycleCopy.test.ts、backend/tests/test_purge_loop.py、backend/tests/test_posthog_erasure_retry.py、backend/tests/test_data_lineage_ledger.py | 端到端：backend/tests/test_account_deletion.py、backend/tests/test_posthog_erasure_retry_pg.py | 实测：pytest test_account_deletion.py 30 passed（软删→物理删→孤儿防护＋「加表不加 purge 即红」的覆盖闸＋豁免的行为证人）＋test_posthog_erasure_retry.py 11 passed＋真库伴生闸 test_posthog_erasure_retry_pg.py 4 passed（一次性空库 alembic upgrade head 到 e5a7b9d1f3a5：跨 session 认领只有一次成功、豁免在迁移出来的库上成立、唯一约束拒绝第二行）＋test_data_lineage_ledger.py 5 passed（43 行台账与 PURGE_TARGETS／export_targets() 双向逐格对账）＋test_purge_loop.py 5 passed＋test_canonical_schema.py 真 PG 23 passed（八步逐级往返）；变异自证 scripts/mutate_t102_lifecycle_gates.py 打红 25/25、保持绿 3/3；jest accountLifecycleCopy.test.ts 23 passed（本批未改前端其余面） |  |
| `export.data` 通用数据导出（异步 job → 包 → 下载） | 数据导出/删除 | **WIRED** | frontend/src/screens/PrivacySecurityScreen.tsx 的「数据导出」块：申请 → 轮询五态 → 取字节 → 系统分享/浏览器下载 | 4 条 | /api/v1/cron/export-jobs（已排程） | — | 范围口径 2026-10-18（T1-02）修过一次，方向是**收窄**：`user_consents`／`safety_events`／`push_tokens` 此前跟着 `PURGE_TARGETS` 派生进了用户的 zip，而 `docs/data-lineage.md` 从 T5-01 起就写着它们不进导出——台账升级为双向对账后当场抓住，已补进 `EXPORT_EXCLUDE_REASONS`（变异证人 D3 就是这条漂移）。真库面已补（backend/tests/test_data_export_pg.py 4 passed：pgvector 排除列根本没进 SELECT、CAS 认领真并发只交出去一次、两 worker 同轮只落一个包、注销后库零行盘零文件）。仍欠两条才到 PRODUCTION_ENABLED：① 原生落盘只做过代码面闸（archiveExport.ts 的 Sharing/FileSystem 分支、web 的 Blob 分支），本机跑不了 expo 原生模块，真机「存到文件」没实测；② deploy/schedule/crontab 第 32 行这条 */5 只是声明，现役宿主还没装 |
| ↳ 证据 |  |  | 调用点：postUserExport✓、listUserExports✓、getUserExport✓、getUserExportDownload✓ | 测试：backend/tests/test_data_export.py、backend/tests/test_data_export_pg.py、backend/tests/test_data_lineage_ledger.py、backend/tests/test_canonical_schema.py、frontend/__tests__/dataExportWiring.test.ts | 端到端：— | 实测：— |  |
| `consent.ledger` 同意台账（六用途 × 文案版本 × 授予/撤回） | 隐私/条款 | **IMPLEMENTED** | （未立项：客户端授权管理屏不存在，旧 App 继续走两个布尔端点，两端已双写本表） | 2 条 | — | — | Baseline §15 末句口径：有后端 API 但前端零调用方只能标 IMPLEMENTED，不能标 WIRED。台账管理屏（六用途逐项开关＋版本原文）另立任务；旧布尔位的双写撤回联动已结案（T5-01，pytest 21 passed） |
| ↳ 证据 |  |  | 调用点：— | 测试：backend/tests/test_consent_purposes.py、backend/tests/test_data_lineage_ledger.py | 端到端：backend/tests/test_consent_purposes.py | 实测：— |  |
| `privacy.terms` 隐私政策与条款（App 内入口＋官网页脚静态页） | 隐私/条款 | **E2E_VERIFIED** | frontend/src/screens/PrivacySecurityScreen.tsx:隐私与安全页 | 静态发布（6 份资产） | — | — | 页面可达率与提审阻断数是线上指标；官网侧没有测试运行器，闸按 test_k8s_manifests.py 先例放本仓 |
| ↳ 证据 |  |  | 调用点：—｜资产：site/src/pages/privacy.astro、site/src/pages/terms.astro、site/src/pages/zh/privacy.astro、site/src/pages/zh/terms.astro、site/src/legal.ts、site/src/components/Legal.astro | 测试：backend/tests/test_legal_pages.py | 端到端：backend/tests/test_legal_pages.py | 实测：pytest test_legal_pages.py 29 passed（两端断链闸：URL 非占位、语言覆盖、内容与实际处理一致）；15 语言文案由 jest i18nLocales 107 passed 守 |  |

## 内部能力登记（无用户入口，状态封顶 IMPLEMENTED）

- **`ai.grpc.client`** Go→Python AI 面 gRPC 客户端（embedding / safety 二次校验 / 流式对话）（**IMPLEMENTED**，处置日期 2027-01-17）：Baseline §10.4：双侧实现齐全但生产零调用方——实测（2026-10-19）引用 internal/aiclient 的非测试 .go 文件只有 tools/aiclientprobe 一个值班工具，cmd/ 与 internal/ 的其余生产包零命中；§21 要的「谁是生产调用方」今天答不出来，所以只能标 IMPLEMENTED。
  - 代码面：go-core/internal/aiclient/aiclient.go、go-core/internal/aiclient/grpc.go、go-core/internal/aiclient/sse.go、go-core/internal/aiclient/breaker.go、go-core/internal/aiclient/degrade.go、backend/ai/rpc/server.py、deploy/k8s/50-ai-rpc.yaml
  - 测试面：go-core/internal/aiclient/grpc_test.go、go-core/internal/aiclient/aiclient_test.go、go-core/internal/aiclient/stream_test.go、go-core/internal/aiclient/trace_test.go
  - 非生产调用方：go-core/tools/aiclientprobe/main.go
  - 到期动作：到期（2027-01-17）仍无生产调用方 ⇒ 删除 go-core/internal/aiclient 与 deploy/k8s/50-ai-rpc.yaml（T6-01 选项 A 的 90 天条款）；若选 B 接线则改为「insight 发布前 ClassifySafety 二次校验，超时 300ms、失败降级沿用候选安全态并记指标」，并配降级测试＋失败指标才许升态。
  - 缺口：异步面（RabbitMQ）已经承载 observation／candidate 投递，同步 gRPC 面仍是零流量；本条登记由 go-core/internal/aiclient/production_caller_ledger_test.go 与 test_feature_status_truth.py 双侧看住——零调用方这件事一侧消失即红。

## 为什么这些态不能更高（口径备忘）

- **`account.delete`**（E2E_VERIFIED）：工作单 T1-02 第②件（登出清 push token）未做——与既有产品裁决冲突：`AuthContext.tsx:106-121` 写明「登出不注销，后端 APNs 410 自清」，`orphanEndpoints.test.ts` 把那句钉成闸；要翻这个决定归 B-04（产品/安全）。另：「删除完成率」指标未采；QStash 回调 POST /cron/purge-user 的公网验签只能在有 QStash 凭据的环境证，本机未跑；PostHog 擦除走的是真值配置还是 stub、以及 7 天 POSTHOG-ERASURE-STUCK 告警在日志面（Loki/Alertmanager）的落点均未取证
- **`consent.ledger`**（IMPLEMENTED）：Baseline §15 末句口径：有后端 API 但前端零调用方只能标 IMPLEMENTED，不能标 WIRED。台账管理屏（六用途逐项开关＋版本原文）另立任务；旧布尔位的双写撤回联动已结案（T5-01，pytest 21 passed）
- **`export.data`**（WIRED）：范围口径 2026-10-18（T1-02）修过一次，方向是**收窄**：`user_consents`／`safety_events`／`push_tokens` 此前跟着 `PURGE_TARGETS` 派生进了用户的 zip，而 `docs/data-lineage.md` 从 T5-01 起就写着它们不进导出——台账升级为双向对账后当场抓住，已补进 `EXPORT_EXCLUDE_REASONS`（变异证人 D3 就是这条漂移）。真库面已补（backend/tests/test_data_export_pg.py 4 passed：pgvector 排除列根本没进 SELECT、CAS 认领真并发只交出去一次、两 worker 同轮只落一个包、注销后库零行盘零文件）。仍欠两条才到 PRODUCTION_ENABLED：① 原生落盘只做过代码面闸（archiveExport.ts 的 Sharing/FileSystem 分支、web 的 Blob 分支），本机跑不了 expo 原生模块，真机「存到文件」没实测；② deploy/schedule/crontab 第 32 行这条 */5 只是声明，现役宿主还没装
- **`growth.history`**（WIRED）：§16 必测异常「空列表 / 分页边界 / 跨用户读取」未立端到端闸；历史查看率与回访率指标未采
- **`insight.pattern`**（WIRED）：证据段（记忆→命令→候选→Go 入站→权威表）2026-10-19 起跨进程真跑通一次（test_v3_evidence_e2e.py，本机真库＋真 broker），但**产品流量仍是零**：候选生产面（R6）没有发射点，「生成 Pattern → 展示」这后半段仍未被任何端到端闸摸过，所以状态不升到 E2E_VERIFIED；证据跨用户/模型超时/用户否认三条必测异常归 W3（T3-01…T3-05）
- **`journey.week1`**（E2E_VERIFIED）：D1/D7 留存与剧本完成率是产品指标，本机无真实用户数据 ⇒ VALUE_MEASURED 不可宣称；CLOSEN_CUTOVER_JOURNEY 默认 0
- **`life_archive.pdf`**（E2E_VERIFIED）：原生（iOS/Android）下载与打开 PDF 的真机取证未做。设备侧的「下载失败」自 2026-10-18（T1-04）起不再是空白：PaywallScreen 上报 archive_share_result{result,reason} 且 PdfUnavailableError 有独立机器码，但那条事件的真机实测（StoreKit 沙盒买一单，看三步事件是否齐）仍未做，且 DELIVERY-ALERT 在 Loki／Alertmanager 的落点没取过证
- **`memory.manage`**（WIRED）：N8 批 6 的 memories 读路径灰度键 CLOSEN_CUTOVER_MEMORIES 默认 0（管道通了、流量仍是零）；重复候选、矛盾检测失败、空演化三条必测异常未立端到端闸
- **`privacy.terms`**（E2E_VERIFIED）：页面可达率与提审阻断数是线上指标；官网侧没有测试运行器，闸按 test_k8s_manifests.py 先例放本仓
- **`proactive.message`**（E2E_VERIFIED）：PRODUCTION_ENABLED 还差宿主 VPS 上 `crontab deploy/schedule/crontab` 的安装取证（文件在库里有，装没装只能上机看）
- **`reflect.record`**（WIRED）：§16 必测异常四条（网络失败、重复提交、空内容、越权读取）没有一条端到端闸；N8 灰度键 CLOSEN_CUTOVER_REFLECTIONS 默认 0
- **`subscription.iap`**（WIRED）：沙箱回执、恢复购买、退款/撤销三条必测异常都要真 StoreKit/Play Billing 事件，本机没有；通知端点 POST /iap/notifications 前端零调用是设计使然（Apple 服务端直连）。2026-10-18（T1-04）补上的是**事件面与每日对账**：iap_verify_started/succeeded/failed 三格都有埋点（含超时与 Apple 非零 status 两个失败出口，reason 只放机器码），`/cron/delivery-audit` 数「付了钱、过了宽限期、库里没有 life_archive 成功行」的用户数并按阈值打 DELIVERY-ALERT；真机沙盒那一跑与 Loki 告警落点仍未取证，所以停在 WIRED

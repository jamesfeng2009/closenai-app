# 核心循环指标字典（T7-06）

本文件与 `backend/services/metrics_export.py` 的常量**同源**：
`backend/tests/test_metrics_export.py` 会逐行比对两边，改一边不改另一边就红。

## 口径三条

1. **数业务表，不数事件**。注册、反思、有证据洞察、行动、结果、第二次循环、付费交付
   全部从各自的业务表数出来。分析事件（PostHog、回执台账）只回答「有没有送到」，
   不参与产品漏斗——用户撤回分析同意时，产品事实仍然成立，漏斗不该塌一块。
2. **不造数**。窗口内没有真实用户时每个格子都是 0，并带 `data_status="no_users"`。
   没有批次日志时 `dropped` 是 `null`（未知），不拿 0 冒充。
3. **测试账号不进正式指标**。约定邮箱后缀 `@closenai.test`（`TEST_USER_SUFFIXES`），
   装载层按它排除，并在 `test_users_excluded` 报出排除条数。

## 核心循环七格

| 机器码 | 中文 | owner | UI 触发点 | API 函数 | 服务端入口 | 权威表 | 隐私边界 |
|---|---|---|---|---|---|---|---|
| `registered` | 注册（分群锚点） | BE | `AuthScreen.tsx::register` | `register` | `POST /api/v1/auth/register` | `users.created_at` | 只取注册时间、语言、状态；邮箱仅用于排除测试账号 |
| `first_reflection` | 首次反思 | FE+BE | `ReflectScreen.tsx::saveReflection` | `saveReflection` | `POST /api/v1/reflections` | `growth_records.record_type='reflection'` | 只数条数/最早时间，不读正文 |
| `first_insight_with_evidence` | 首条有证据洞察 | GO+FE | `InsightsScreen.tsx::evidence_count` | `listInsights` | `GET /api/v1/insights` | `insights` + `insight_evidence`（`status='published'`） | 只认「有证据边且已发布」，不读 statement |
| `first_action` | 首次行动 | FE+GO | `InsightsScreen.tsx::submitAction` | `createAction` | `POST /api/v1/goals/{goal_id}/actions` | `personal_commitments.created_at` | 只数条数/时间，不读标题与描述 |
| `first_outcome` | 到期结果 | FE+GO | `GrowthRecordsScreen.tsx::postActionOutcome` | `postActionOutcome` | `POST /api/v1/actions/{action_id}/outcomes` | `outcomes`（`origin='user_statement'`） | 只计数与 `result` 词表分布，不读 note |
| `second_cycle` | 第二次核心循环 | PLAT | 派生（无单独 UI 事件） | `—` | `—（本模块派生）` | `growth_records + insights + personal_commitments + outcomes` | 只有时间戳比较 |
| `paid_delivery` | 付费交付 | BE | `iap.ts::purchaseUpdatedListener` | `verifyReceipt` | `POST /api/v1/iap/verify` | `user_entitlements` + `share_exports` | 只数交付是否成立，不读导出 payload |


### 关键定义

- **一次核心循环成立**：同一用户存在 `反思 ≤ 洞察 ≤ 行动 < 结果` 四段齐全的时间序列，
  首次成立取最早那个满足条件的结果时间（`_first_loop_outcome`）。
- **第二次核心循环**：第一次结果之后，反思、洞察、行动**三件都重新出现过**，
  且之后又有新结果。只多一条结果不算——补记旧行动的结果也会让结果数 +1，
  但循环没有重新开始（`_second_cycle`）。
- **失败出口**：`outcomes.result = 'worse'`（词表见 `frontend/src/types/index.ts:358`）。
  它不进漏斗，但在 `quality.failure_outcomes` 里与漏斗一起出现。
- **洞察纠正**：`quality.rejected_insights` 与 `quality.corrections`（用户反馈条数）。

## 投递质量六格

| 键 | 来源 | 说明 |
|---|---|---|
| `queued` | 回执台账 `status='pending'` | 已认领、尚未确认送达 |
| `accepted` | 回执台账 `status='accepted'` | 分析平台已收下 |
| `relayed` | 同 `accepted` | 与既有响应体 `{relayed}` 同义，两个名字并存以便对账 |
| `duplicate` | 回执台账 `status='accepted'` 的重复提交 | 同 `(user_id, event_id)` 再次到达 |
| `dropped` | **批次响应** `dropped`（需 `--batch-stats` 传入） | 台账故意不落 dropped 行（`backend/models/analytics.py` 模块头），拿不到就 `null` |
| `latency_ms_p50/p95` | 台账 `created_at → updated_at`（仅 accepted） | 端到端中继延迟 |

恒等式：`accepted + duplicate + dropped + queued == total`（`identity_holds`），
`--strict` 下不成立即非零退出。

**抽样对账**：`reconcile_sampling(device, server, tolerance)` 按事件名逐行比设备侧与服务端
计数，任一行超出容差即 `clean=false`。两边都不许"合并成一条总量"了事。

## 导出

```bash
# 真库（需要 DATABASE_URL；本机无 PG 时按未跑记）
python scripts/export_metrics.py --since 2026-10-19 --until 2026-10-26 \
  --batch-stats batch.json --json metrics.json --html metrics.html --strict

# 离线（种子用户实验 T7-07 用同一条判据）
python scripts/export_metrics.py --since 2026-11-02 --until 2026-11-09 \
  --input journeys.json --html metrics.html
```

离线输入格式（时间一律 RFC3339）：

```json
{"journeys": [{
  "user_id": "u1",
  "registered_at": "2026-11-02T09:00:00Z",
  "language": "zh", "platform": "apns",
  "reflections": ["2026-11-02T10:00:00Z"],
  "insights":    ["2026-11-03T10:00:00Z"],
  "actions":     ["2026-11-03T12:00:00Z"],
  "outcomes":    ["2026-11-05T12:00:00Z"],
  "paid_deliveries": [],
  "rejected_insights": 0, "corrections": 1, "failure_outcomes": 0
}]}
```

导出只含聚合与计数，**不含 user_id、正文、邮箱**。

## 未覆盖（不算绿）

- 真库导出（`load_journeys` / `load_delivery`）需要 `DATABASE_URL`；本机没有 PG，
  这两条在单测里按未跑记，不假装通过。
- 平台切片来自 `push_tokens.platform`（`apns`/`fcm`），没有推送令牌的用户记 `unknown`。
- 看板是静态 HTML，没有部署与定时刷新；线上托管属部署面任务。

## 关键词渠道七格（KG-19）

> 这张表**不在** `backend/services/metrics_export.py` 的同源比对里：
> `backend/tests/test_metrics_export.py:67-82` 从第一个 `| 机器码 |` 读到第一行非表格就停，
> 所以改这里不会把那张闸带红。它的闸是 `backend/tests/test_keyword_funnel_doc.py`。
>
> 口径同上三条：数业务表不数事件、不造数、测试账号不进正式指标。
> 前三格（曝光、商店页、安装）的权威源在 App Store Connect / Play Console / 搜索控制台，
> 仓库里拿不到，只能人工导出；后四格全部指回上面核心表已有的服务端表与列。

| 关键词码 | 中文 | 定义 | 来源 | 窗口 | 分群 | 负责人 | 查询入口 | 数据质量检查 |
|---|---|---|---|---|---|---|---|---|
| `search_reach` | 曝光/搜索点击 | 落地页在搜索引擎结果里被展示的次数与被点击的次数，两个数分开记，不合成一个「排名」 | Google Search Console（主口径）、Bing Webmaster Tools、百度搜索资源平台；三者都是控制台读数，仓库内无接入 | 周（周一到周日；GSC 数据有 2 天延迟，取数日要退到 T+2） | 语言（en/zh）× 国家或地区 × 页面 slug × 查询簇（对应台账 §4 的簇名） | GROWTH | 各控制台的效果/查询报告按 `site-content/pages.json` 的 slug 过滤后导出 CSV，存 `docs/seo/search-console-baseline-2026-11/`（该目录尚未建立，接入动作属 KG-21，仍 `BLOCKED`） | 同一页面三个工具的点击数彼此相差超过 20% 时记「来源冲突」，不取平均、不合并；页面未进 sitemap 或未收录就写「未收录」，不写 0 |
| `store_page_view` | 商店页浏览 | 商店产品页被浏览的次数，只算产品页本身，不含搜索结果里的图标曝光 | App Store Connect 分析（App 商店视图）与 Play Console 商店分析（商店展示次数） | 周 | 商店（App Store / Google Play）× 国家或地区 × 来源类型（搜索 / 浏览 / 推广） | GROWTH | ASC 的 App 分析页选「App 商店页视图」并按来源类型拆分导出；Play Console 的商店分析页取「商店展示次数」与「商店访问次数」两行 | 两个商店的「曝光」定义不同（ASC 含搜索结果出现，Play 含浏览展示），只并排记、不合并成一条总量；出现产品页浏览数大于展示数即为口径撞车，当周该格作废 |
| `install` | 安装 | 完成安装并首次启动的设备数；渠道拆分依赖 KG-20 的 campaign link | App Store Connect 分析（App 下载 / 首次启动）与 Play Console（安装次数）；带 `pt/ct/mt` 的 campaign link 用于区分投放 | 周（并对安装后 24h 做一次回访） | 商店 × 国家或地区 × campaign（`pt`/`ct`/`mt`，见 `docs/growth/attribution-2026-11.md`）；新装与重装只有商店侧能拆，App 内不区分 | PLAT+GROWTH | ASC 按 campaign 维度导出下载 CSV，Play Console 取安装次数；同一行必须能指回一条 `store_page_view`，否则该渠道的转化率无法计算 | 商店对样本量过小的分群会隐藏（隐私阈值），隐藏记 `suppressed` 而不是 0；安装数除以商店页浏览数大于 1 时判为分母取错，整条转化率不采信 |
| `first_value` | 首次价值动作 | 安装后第一次「用户自己留下了一条记录」：首次情绪签到或首次保存反思，两者取更早的那个 | 服务端业务表（不经分析事件） | 安装后 24h 与 7d 两个窗口都算，报告里并列 | 语言 × 平台（`push_tokens.platform`，无令牌记 `unknown`）；渠道分群待 KG-20 落库后补，当前服务端没有安装来源列 | FE+BE | 签到取 `mood_checkins.created_at`（`backend/models/mood.py:15`），反思取 `growth_records.record_type='reflection'` 的最早时间（`backend/models/growth.py:16`，与核心表第 2 格同一判据）；聚合经 `python scripts/export_metrics.py --since --until --json` 的 `journeys` | 两条路各自单独数过再取更早，任一列缺失即判红；发过一条聊天消息不算价值动作（消息不是用户保存下来的记录，见 `docs/growth/first-value-path-2026-11.md`）；`@closenai.test` 后缀排除并在 `test_users_excluded` 报出条数 |
| `first_evidence_insight` | 首条有证据洞察 | 服务端为该用户产出的第一条「带证据边且已发布」的洞察 | 服务端表 `insights` 联 `insight_evidence`，与核心表第 3 格同一判据（`status='published'`） | 首次价值动作后 7d | 语言 × 渠道（待落库）× 生成路径（canonical 新链与旧口径分开记，两边产出率不同） | GO+FE | 直接复用 `backend/services/metrics_export.py:69-78` 的 `first_insight_with_evidence` 定义（表列与隐私边界同源），入口 `GET /api/v1/insights`；看板同上 `export_metrics.py` | 只有 statement、没有证据边的记录不计入本格；每条计入的洞察必须能回链到用户自己的记录（决策 D4），回链断掉即脏数据单列；生产流量为零时按 `no_users` 报，不拿 0 当「转化差」的结论 |
| `first_action` | 首次行动 | 用户从洞察或反思里落下的第一条行动承诺（不是待办清单里的通用条目） | 服务端表 `personal_commitments.created_at` | 首条有证据洞察后 7d；无洞察但自行写下行动的单独分群记，不并进主路径 | 语言 × 入口页面（洞察页 / 目标页 / 首页下一步卡）× 渠道（待落库） | FE+GO | 表在 `backend/models/canonical.py:255`；服务端定义取 `backend/services/metrics_export.py:79-88` 的 `first_action` 行（`POST /api/v1/goals/{goal_id}/actions`）；事件侧对照 `first_action`（`frontend/src/services/funnel.ts:29-39`） | 事件计数与服务端表计数逐行对账，用 `reconcile_sampling(device, server, tolerance)`，任一行超容差即漂移；只数条数与时间，不读 `title` / `description` |
| `due_outcome` | 到期结果回访 | 到期的行动被用户回填了结果，即「预期 vs 实际」这一格有了用户的原话 | 服务端表 `outcomes`，取 `origin='user_statement'` | 承诺到期日 ±7d（到期前回填的也算，但记为提前） | 结果词表（`better` / `same` / `worse`）× 语言 × 渠道（待落库） | FE+GO | 表在 `backend/models/canonical.py:500`；`result` 的取值词表在 `frontend/src/types/index.ts:420`（`ActionResultChoice`）；服务端定义取 `metrics_export.py:89-98` 的 `first_outcome` 行，`worse` 同时进 `quality.failure_outcomes` | 到期未回填记「无回执」，不计为失败；补记旧行动的结果会让结果数加一但不推进第二次循环（判据 `_second_cycle`），两格必须分开；`result` 落在词表之外即脏数据，单列并回报 |

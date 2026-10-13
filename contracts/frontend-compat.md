# 前端逐字兼容清单（N0 · 已打包 App 硬约束面）

> 用途：Go Core 接管对外 HTTP 时，本文件是**响应字段级**的唯一基线。
> 为什么需要单独一份：`contracts/openapi.json` 里 98 个端点的 200 响应全是空 schema
> （handler 返回 `success(dict)`，`grep -c "response_model=" backend/routers/*.py` = 0），
> 所以 openapi 只能冻结请求侧 + 信封；`data` 里的键名只有前端消费点能证明。
> 端点级对账（哪些路径有前端调用点、哪些封装未接线）在 `contracts/endpoints.md` 第九节，由脚本生成；
> 本文件是人工登记的字段清单，改动须同步 `backend/tests/test_contract_freeze.py`。
> 证据均实测于 2026-10-13。

## 一、出口与基础约定

| 约定 | 值 | 出处 |
| --- | --- | --- |
| Base | `EXPO_PUBLIC_API_BASE`，缺省回落 `http://localhost:8000/api/v1`（Android `10.0.2.2`） | `frontend/src/services/api.ts:38-43` |
| 发请求的地方 | 全前端只有 3 处：`api.ts:180`（refresh，无 Authorization）、`api.ts:201`（统一 `request`）、`api.ts:296`（SSE 走 XHR）。其余 service 全部转调 api.ts | 实测 grep `fetch(`/`XMLHttpRequest` |
| 请求头 | `Content-Type: application/json` + `X-Language: <en\|zh… 取前 2 位>` + `Authorization: Bearer <access_token>` | `api.ts:145-154` |
| 响应头 | 只消费 `X-Trace-Id` 之外的 body；trace 由后端中间件写回（`backend/core/middleware.py:38`） | — |

## 二、信封与错误分支（Go 必须逐值复现）

- **解包判据**：`typeof body.code === 'number' && 'data' in body` 才认信封（`api.ts:162-170`）；
  ⇒ `code` 一旦从 number 改字符串，前端把整个响应当非信封处理。
- **成功判据硬编码 `code === 200`**（`api.ts:187`、`api.ts:255`），不看 HTTP 2xx。
- `message` 直接进错误文案（中英双语 ` / ` 分隔口径见 `endpoints.md` 第一节）。
- **`trace_id` / `user_id` 零消费**：全 `frontend/src` 只在 `api.ts:5` 注释里出现过一次 ⇒ Go 侧可换实现，
  但键名仍须保留（判据之外还有 `endpoints.md` 的信封 5 键断言）。
- HTTP 状态码分支只有三类：`!res.ok`（`api.ts:185`、`api.ts:245`）、`401` → 单飞刷新后重试一次
  （`api.ts:214-227`，SSE 重开流一次 `api.ts:338-351`）、`2xx` 区间判断（`api.ts:353`）。
  **实测无 429/退避逻辑**（`frontend/src` 全量 grep `429` 零命中）⇒ 限流仍以业务码下发。
- 业务码分支（`ApiError.code`）：

| code | 前端行为 | 出处 |
| --- | --- | --- |
| `40401` USER_NOT_FOUND | 已登录态 → 清登录态回登录页 | `api.ts:239` |
| `40301` QUOTA_EXCEEDED | 配额弹窗 | `screens/HomeScreen.tsx:245`、`screens/ReflectScreen.tsx:303` |
| `40302` PRO_REQUIRED | 跳 Paywall | `screens/ShareCardScreen.tsx:47`、`screens/PersonalityScreen.tsx:427` |
| `40303` AI_CONSENT_REQUIRED | 弹数据共享同意 | `screens/ReflectScreen.tsx:296` |
| `40402` RESOURCE_NOT_FOUND | 初读卡空态兜底 | `screens/FirstReadCardScreen.tsx:45` |
| `40103` | 前端自造（无 token 时的本地码，后端不发） | `api.ts:225`、`api.ts:347` |

## 三、SSE 流式契约（唯一一条，改名即断对话）

`POST /api/v1/chat/stream`（`backend/routers/chat.py:53`），前端用 **XHR + onprogress 增量读 `responseText`**
（`api.ts:294-333`），因此：

1. 每帧必须是**单行** `data: {...}\n\n`，非 `data: ` 前缀的行直接忽略（`api.ts:302`）。
2. `type` 只认 4 个值，字段名逐字：

| type | 读取字段 | 前端行为 |
| --- | --- | --- |
| `token` | `content` | 追加渲染（`api.ts:312-314`） |
| `memory_context` | `memories` | 传 `onMemoryContext`，**当前唯一调用方 `ReflectScreen.tsx:286-316` 未传该回调 ⇒ 后端在发、UI 无人接** | 
| `done` | — | 收尾（`api.ts:318`） |
| `error` | `message`、`code` | 错误兜底（`api.ts:321`） |

3. **失败有两条路径**：进入流之前仍是 HTTP 非 200 + JSON 信封（403 `40303`、429 配额），
   流内失败才是 200 + `error` 帧（`backend/routers/chat.py:136`）。Go 侧两条都得保留。
4. 收不到 `done` 时前端用 `onDone` 兜底（`api.ts:369`）⇒ 不能靠「不发 done」来结束流。

## 四、二进制（PDF）端点

`Content-Type: application/pdf` + `Content-Disposition: attachment; filename="…"`，
文件名清单见 `endpoints.md` 第七节（含两个按参数拼接的 `closenai_{period}_report.pdf` /
`closenai_life_archive_{scope}.pdf`）。前端按字节流落盘/分享，**文件名与媒体类型属逐字兼容项**。

## 五、本地 SQLite 同步面（字段改名 = 静默丢数据，最危险的一类）

拉取式全量覆写，**没有 cursor/增量口径**，只有 `limit` 参数。以下映射一旦服务端改名，
本地不报错、只丢数据：

| 来源端点 | 读取字段 → 本地列 | 出处 | 改名后果 |
| --- | --- | --- | --- |
| `GET /mood/history` | `checkins[].{id, created_at→date, mood_emoji→emoji, ai_interpretation, created_at}` | `screens/HomeScreen.tsx:41-58` | 热力图/日历静默变空（逐行 `catch(() => {})`） |
| `GET /chat/history` | `[].{id, role, content, created_at, memory_refs, message_type}` | `screens/ReflectScreen.tsx:129-160` | `message_type` 改名 ⇒ 消息中心永久漏主动消息；`memory_refs` 改名 ⇒ 依据回链消失 |
| `GET /growth/dashboard` | 整个对象按 JSON 原样缓存到 `app_settings@dashboard_cache` | `screens/HomeScreen.tsx:102` | `GrowthDashboard` 任一键改名 ⇒ 离线首屏渲染异常且无报错 |
| `GET /onboarding/first-read` | `assertions`（`Array.isArray` 作有效性判据） | `services/firstRead.ts:67` | 改名 ⇒ 初读卡入口直接消失 |
| `GET /personality/profile` | `{id, mbti_type→mbti, enneagram_type→enneagram, created_at}` | `screens/PersonalityScreen.tsx:76-86` | 画像快照列错位 |

## 六、响应载荷里被逐字读取的业务字段（按端点）

前端类型定义 `frontend/src/types/index.ts` 文件头声明「与 `backend/models/schemas.py` 同步」，
它是请求/响应字段名的**前端侧单一事实源**；下表是屏级实际读取点（不含仅类型定义的字段）。

| 端点 | 逐字读取的字段 | 消费点 |
| --- | --- | --- |
| `POST /auth/{login,register,apple,login-code,refresh}` | `access_token`、`refresh_token`、`user.{id,email,username,name,tier,email_verified}` | `api.ts:90-92`、`types/index.ts:537-544` |
| `POST /mood/checkin` | `id/mood_emoji/mood_label/ai_interpretation/streak_updated/created_at/deep_interpretation` | `HomeScreen.tsx:216-241`、`ReflectScreen.tsx:325-345` |
| `GET /growth/dashboard` | 全对象，含 `is_pro`、`streak_freeze_count`、`milestones[].{milestone_type,achieved}` | `HomeScreen.tsx:176,206,276-335,421-493` |
| `GET /user/profile` | `ai_consent`、`tier`、`daily_quota_used/total`、`push_time`、`conversation_style`、`primary_framework`、`username`、`analytics_consent` | `ReflectScreen.tsx:120`、`InsightsScreen.tsx:106`、`ProfileScreen.tsx:262-341,403` |
| `GET /chat/history` | `memory_refs[].{memory_id,excerpt,created_at}`（后端 `chat.py:173-191` 构造） | `ReflectScreen.tsx:495-501` |
| `GET /journey/events` | `events[].{event_kind,status,day_no,acknowledged}`（`status` 枚举里前端只认 `sent`/`seen` 为已送达）<br>`payload.{title,body,quotes[{memory_id,text,date}],stats.{chats,checkin_days}}`<br>`unseen_count` | `config/journey.ts:25,29-43`、`Week1ReportScreen.tsx:86-87`、`HomeScreen.tsx:164` |
| `GET/POST /personality/assessment*`、`/enneagram*` | `status`（枚举 `not_started`…）、`current_index`、`total`、`question.{index,total,text,options.{a,b,neutral}}`、`result.{enneagram_type,type_label,combo_name,combo_mbti}`、`completed`、`next_question`、`profile`、`message` | `PersonalityScreen.tsx:149-211,236-404,489-545` |
| `GET /personality/profile` | `stage`（枚举 `icebreaking`…）、`cold_start_stage`、`cold_start_progress`、`dimension_signals.{EI,SN,TF,JP}`、`mbti_type`、`enneagram_type`、`observations`、`unique_traits`、`signals_count`、`estimated_completion_days` | `PersonalityScreen.tsx:39-41,89-114,632-637` |
| `POST /onboarding/first-read`、`GET 同路径` | `assertions`、`summary`、`closer`、`share_url` | `FirstReadCardScreen.tsx:66-68,120-126` |
| `GET /share/personality-card` | `layout`（`compact\|combo`）、`user_name`、`mbti_type/mbti_label`、`tagline`、`observations`、`unique_traits`、`dimensions.{mbti,enneagram}`、`combo_insight`、`streak_count`、`relationship_level`、`share_text` | `ShareCardScreen.tsx:117-169` |
| `GET /share/report/{period}` | `period_label`、`range`、`insight_level`、`mood_trend`、`chat_count`、`content`、`share_url` | `InsightsScreen.tsx:169-323` |
| `GET /subscription|/iap/status` | `tier`、`is_active`、`expires_at`、`product_id`、`is_trial` | `PaywallScreen.tsx:180-200,329`、`Week1ReportScreen.tsx:39` |
| `POST /iap/verify` | `valid`、`kind`（`subscription\|entitlement`） | `services/iap.ts:249-251` |
| `GET /iap/entitlements` | `owned_scopes`、`entitlements[]` | `PaywallScreen.tsx:135-162` |
| `POST /analytics/batch` | `relayed`（`dropped` 只出现在返回类型声明里，实测无人读） | `services/analytics.ts:106`、`services/api.ts:677` |
| `GET /memories`、`DELETE /memories/{id}` | `id`、`content`、`category`（枚举 `values\|preference\|relationship\|goal\|factual`）、`created_at` | `screens/MemoriesScreen.tsx:111,115`、`types/index.ts:78`；`services/evidence.ts:21-27` 用 `id/content/created_at` 现构 `memory_ref{memory_id,created_at,excerpt}` |

## 七、逐字兼容最小集（一句话版）

**路径**＝`endpoints.md` 第九节里「无（封装未接线）标记」的 48 条端点（含 `/api/v1` 前缀与
`limit/days/day/language/variant/period/scope/year/regenerate` 查询键名）；
**信封**＝`code`(number，成功值 200) / `message` / `data` 三键；
**头**＝`Authorization: Bearer` + `X-Language`；
**SSE**＝`data: ` 单行帧 + `type ∈ {token(content), memory_context(memories), done, error(message,code)}`；
**字段**＝第五节 5 组同步映射 + 第六节各端点列表；
**枚举值**＝`message_type='proactive'`、`kind='subscription'|'entitlement'`、`layout='compact'|'combo'`、
`status='not_started'…`、`stage='icebreaking'…`、`milestone_type='streak_7'…` —— 改枚举字面量与改键名同罪。

## 八、登记缺口（带日期，按「发现缺陷不顺手修」只记不补）

- ↳ 2026-10-13 **本地 LWW 冲突解决未接线**：`frontend/src/services/db/syncService.ts:8`
  导出 `resolveConflict()`（按 `updated_at ?? created_at` 取新），但全 `frontend/src` 无任何 import
  （实测 grep 只命中定义行本身）⇒ 所谓「远端为真相源」实际靠第五节各屏的**全量覆写**，
  不是靠该函数。归属：前端本地层，与 N/O 主线无关；N8 切流期间若出现双写，本条是首个嫌疑点。
- ↳ 2026-10-13 **SSE `memory_context` 事件后端在发（`backend/routers/chat.py:103`）、UI 无人接**
  （`ReflectScreen.tsx:286-316` 未传 `onMemoryContext`）。Go 侧若删该事件前端不会报错，
  但这是 P0-F02 依据可视化的欠账，属 C 节产品面，不在 N0 处理。
- ↳ 2026-10-13 **响应字段无类型基线**（第一节说明的 openapi 空 schema）。补齐方式已在
  `endpoints.md` 第八节归到 N2（建 canonical schema 时加 `response_model`），
  不在 N0 逐端点补注解——那会与 N8 按域切流重复劳动。

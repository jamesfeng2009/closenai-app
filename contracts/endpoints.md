# ClosenAI HTTP 契约基线（N0 契约冻结）

> 本文件由 `.venv/bin/python scripts/export_contract.py` 生成，**不要手改**。
> 校验：`backend/tests/test_contract_freeze.py` 断言
> ① 本表条数 == router 装饰器计数 + app 级路由计数；
> ② 表内 (方法, 路径) 集合与 `app.openapi()` 完全相等（无未登记、无多余）；
> ③ `contracts/openapi.json` 与当前 `app.openapi()` 逐字节一致。
>
> 迁移口径（D-1 改判，2026-10-12）：**Go Core 接管对外 HTTP 与权威态**，Python 只留 agent/AI。
> 已打包/已提审的 App 只认本文件登记的契约，因此**本表是 Go 侧的实现清单，不是建议**。
> P1 结论：端点与响应结构不变 ⇒ 客户端零改动 ⇒ 迁移与提审可并行。

## 一、统一响应信封（所有 JSON 端点）

`backend/core/response.py:17` `success()` 固定产出以下 5 个键，**键名逐字不可改**：

| 键 | 类型 | 说明 |
| --- | --- | --- |
| `code` | int | 成功固定 200；失败为业务错误码（见第四节） |
| `message` | str | 人类可读，中英双语以 ` / ` 分隔 |
| `data` | object\|null | 业务载荷 |
| `trace_id` | str\|null | 请求追踪 ID（`X-Trace-Id` 透传，`backend/core/middleware.py:38`） |
| `user_id` | str | 未登录为 `""` |

失败响应由全局异常处理器统一构造（`backend/core/middleware.py:52` 起三个 handler），
业务代码只抛 `AppException`，**不手动构造失败体**。

## 二、HTTP 状态码口径

- 装饰器上不写 `status_code=`（实测 `grep -c "status_code=" backend/routers/*.py` = 0）⇒ 成功一律 **200**，无 201/204 语义。
- 业务码是 5 位整数，不能当 HTTP 状态；`backend/core/errors.py:107` `_derive_http_status()` 按段位归一：
  `4xxxx → code//100`（如 40301 → 403），`5xxxx` 同理，其余原值。
- 校验失败（缺字段/类型错/越界）走 FastAPI `RequestValidationError` → **HTTP 400** + `code=40001`
  （注意：不是默认 422；`backend/core/middleware.py:73`）。
- 未处理异常 → **HTTP 500** + `code=500`。

## 三、鉴权与语言参数

- 私有端点靠 `Depends(get_current_user)` 解析 Bearer JWT；公开端点在 `backend/core/auth.py` 白名单内。
- `language` 由 `Depends(get_request_language)` 从查询参/`Accept-Language` 推导，**不属于响应契约**。
- 这两个是 FastAPI 依赖，不体现在 openapi 的 `security` 字段里（`grep -c security contracts/openapi.json` = 0），
  Go 侧需按本表「鉴权」列实现，不能只读 openapi。

## 四、业务错误码全集（Go 侧必须逐值复现）

由 `backend/core/errors.py` 的 `ErrorCode` 生成，共 43 个：

| code | 名称 | HTTP |
| --- | --- | --- |
| 200 | `OK` | 200 |
| 400 | `BAD_REQUEST` | 400 |
| 401 | `UNAUTHORIZED` | 401 |
| 403 | `FORBIDDEN` | 403 |
| 404 | `NOT_FOUND` | 404 |
| 409 | `CONFLICT` | 409 |
| 429 | `RATE_LIMITED` | 429 |
| 40001 | `INVALID_PARAMS` | 400 |
| 40002 | `INVALID_EMOJI` | 400 |
| 40003 | `INVALID_LANGUAGE` | 400 |
| 40004 | `INVALID_TIMEZONE` | 400 |
| 40005 | `VERIFICATION_CODE_INVALID` | 400 |
| 40006 | `VERIFICATION_CODE_EXPIRED` | 400 |
| 40007 | `PASSWORD_TOO_WEAK` | 400 |
| 40101 | `TOKEN_EXPIRED` | 401 |
| 40102 | `TOKEN_INVALID` | 401 |
| 40103 | `MISSING_TOKEN` | 401 |
| 40104 | `INVALID_CREDENTIALS` | 401 |
| 40105 | `REFRESH_TOKEN_INVALID` | 401 |
| 40106 | `APPLE_AUTH_FAILED` | 401 |
| 40301 | `QUOTA_EXCEEDED` | 403 |
| 40302 | `PRO_REQUIRED` | 403 |
| 40303 | `AI_CONSENT_REQUIRED` | 403 |
| 40304 | `ENTITLEMENT_REQUIRED` | 403 |
| 40401 | `USER_NOT_FOUND` | 404 |
| 40402 | `RESOURCE_NOT_FOUND` | 404 |
| 40403 | `MEMORY_NOT_FOUND` | 404 |
| 40404 | `EMAIL_NOT_REGISTERED` | 404 |
| 40901 | `ALREADY_CHECKED_IN` | 409 |
| 40902 | `SUBSCRIPTION_ALREADY_EXISTS` | 409 |
| 40903 | `EMAIL_ALREADY_USED` | 409 |
| 40904 | `USERNAME_ALREADY_USED` | 409 |
| 42901 | `LLM_RATE_LIMITED` | 429 |
| 42902 | `EMAIL_SEND_RATE_LIMITED` | 429 |
| 42903 | `SHARE_RESOLVE_RATE_LIMITED` | 429 |
| 500 | `INTERNAL_ERROR` | 500 |
| 503 | `SERVICE_UNAVAILABLE` | 503 |
| 50001 | `LLM_ERROR` | 500 |
| 50002 | `LLM_TIMEOUT` | 500 |
| 50003 | `DATABASE_ERROR` | 500 |
| 50004 | `PUSH_FAILED` | 500 |
| 50005 | `IAP_VERIFY_FAILED` | 500 |
| 50006 | `IAP_VERIFY_TIMEOUT` | 500 |

## 五、端点登记表

> 422 列 = openapi 中该操作声明的响应码集合（装饰器无 status_code，故只可能是 `200`/`200+422`）。
> 鉴权列：`auth` = handler 依赖 `get_current_user`；`anon` = 无该依赖（含 cron 走 `X-Cron-Secret` 与公开端点）。
> 前端列 = 已打包 App 的实际调用点（自动对账 `frontend/src`），**有出处 ⇒ 该端点属 P1 零改动范围，切流必须先保它**。

| # | 方法 | 路径 | 响应形态 | 鉴权 | 前端调用 | openapi 响应码 | 请求体模型(字段·必填) | 其余参数 | handler |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | POST | `/api/v1/analytics/batch` | JSON 信封 | auth | frontend/src/services/api.ts:677 | 200+422 | `AnalyticsBatchRequest`: events* | - | `backend/routers/analytics.py:24` `analytics_batch` |
| 2 | POST | `/api/v1/auth/apple` | JSON 信封 | anon | frontend/src/services/api.ts:807 | 200+422 | `AppleLoginRequest`: auth_code, identity_token*, language, ref | - | `backend/routers/auth.py:96` `apple_login` |
| 3 | POST | `/api/v1/auth/change-password` | JSON 信封 | auth | frontend/src/services/api.ts:850 | 200+422 | `ChangePasswordRequest`: new_password*, old_password* | - | `backend/routers/auth.py:161` `change_password` |
| 4 | POST | `/api/v1/auth/forgot-password` | JSON 信封 | anon | frontend/src/services/api.ts:830 | 200+422 | `ForgotPasswordRequest`: email*, language | - | `backend/routers/auth.py:128` `forgot_password` |
| 5 | POST | `/api/v1/auth/login` | JSON 信封 | anon | frontend/src/services/api.ts:767 | 200+422 | `LoginRequest`: identifier*, password* | - | `backend/routers/auth.py:64` `login` |
| 6 | POST | `/api/v1/auth/login-code` | JSON 信封 | anon | frontend/src/services/api.ts:793 | 200+422 | `LoginCodeRequest`: code*, email*, language, ref | - | `backend/routers/auth.py:84` `login_code` |
| 7 | POST | `/api/v1/auth/logout` | JSON 信封 | anon | frontend/src/services/api.ts:822 | 200+422 | `RefreshRequest`: refresh_token* | - | `backend/routers/auth.py:118` `logout` |
| 8 | GET | `/api/v1/auth/me` | JSON 信封 | auth | frontend/src/services/api.ts:866 | 200 | - | - | `backend/routers/auth.py:151` `me` |
| 9 | POST | `/api/v1/auth/refresh` | JSON 信封 | anon | — | 200+422 | `RefreshRequest`: refresh_token* | - | `backend/routers/auth.py:108` `refresh` |
| 10 | POST | `/api/v1/auth/register` | JSON 信封 | anon | frontend/src/services/api.ts:750 | 200+422 | `RegisterRequest`: email*, language, password*, ref, username* | - | `backend/routers/auth.py:50` `register` |
| 11 | POST | `/api/v1/auth/reset-password` | JSON 信封 | anon | frontend/src/services/api.ts:842 | 200+422 | `ResetPasswordRequest`: code*, email*, new_password* | - | `backend/routers/auth.py:138` `reset_password` |
| 12 | POST | `/api/v1/auth/send-code` | JSON 信封 | anon | frontend/src/services/api.ts:781 | 200+422 | `SendCodeRequest`: email*, language, purpose* | - | `backend/routers/auth.py:74` `send_code` |
| 13 | PATCH | `/api/v1/auth/username` | JSON 信封 | auth | frontend/src/services/api.ts:858 | 200+422 | `UpdateUsernameRequest`: username* | - | `backend/routers/auth.py:174` `update_username` |
| 14 | POST | `/api/v1/bedtime/step` | JSON 信封 | auth | — | 200+422 | `BedtimeStepRequest`: step*, text | - | `backend/routers/bedtime.py:42` `bedtime_step` |
| 15 | GET | `/api/v1/bedtime/today` | JSON 信封 | auth | — | 200 | - | - | `backend/routers/bedtime.py:31` `bedtime_today` |
| 16 | GET | `/api/v1/chat/history` | JSON 信封 | auth | frontend/src/services/api.ts:386 | 200+422 | - | limit(query) | `backend/routers/chat.py:149` `chat_history` |
| 17 | POST | `/api/v1/chat/stream` | SSE 流式 | auth | — | 200+422 | `ChatStreamRequest`: content*, message_type | - | `backend/routers/chat.py:54` `stream_chat` |
| 18 | GET | `/api/v1/cron/bedtime` | JSON 信封 | anon | — | 200 | - | - | `backend/routers/cron.py:158` `bedtime_reminder` |
| 19 | GET | `/api/v1/cron/care-push` | JSON 信封 | anon | — | 200 | - | - | `backend/routers/cron.py:205` `care_push` |
| 20 | GET | `/api/v1/cron/cleanup-tokens` | JSON 信封 | anon | — | 200 | - | - | `backend/routers/cron.py:320` `cleanup_tokens` |
| 21 | GET | `/api/v1/cron/curriculum` | JSON 信封 | anon | — | 200 | - | - | `backend/routers/cron.py:174` `curriculum_generation` |
| 22 | GET | `/api/v1/cron/daily-insight` | JSON 信封 | anon | — | 200 | - | - | `backend/routers/cron.py:222` `daily_insight` |
| 23 | GET | `/api/v1/cron/daily-message` | JSON 信封 | anon | — | 200 | （无声明模型，按 handler 读取） | - | `backend/routers/cron.py:84` `daily_message` |
| 24 | POST | `/api/v1/cron/follow-up` | JSON 信封 | anon | — | 200 | - | - | `backend/routers/cron.py:262` `follow_up` |
| 25 | GET | `/api/v1/cron/health` | JSON 信封 | anon | — | 200 | - | - | `backend/routers/cron.py:335` `health` |
| 26 | GET | `/api/v1/cron/journey` | JSON 信封 | anon | — | 200 | - | - | `backend/routers/cron.py:103` `journey_dispatch` |
| 27 | GET | `/api/v1/cron/mood-reminder` | JSON 信封 | anon | — | 200 | - | - | `backend/routers/cron.py:142` `mood_reminder` |
| 28 | POST | `/api/v1/cron/process-user` | JSON 信封 | anon | — | 200 | - | - | `backend/routers/cron.py:119` `process_user` |
| 29 | GET | `/api/v1/cron/purge-deleted-users` | JSON 信封 | anon | — | 200 | - | - | `backend/routers/cron.py:304` `purge_deleted_users` |
| 30 | POST | `/api/v1/cron/purge-user` | JSON 信封 | anon | — | 200 | - | - | `backend/routers/cron.py:280` `purge_user` |
| 31 | GET | `/api/v1/cron/trial-reminder` | JSON 信封 | anon | — | 200 | - | - | `backend/routers/cron.py:190` `trial_reminder` |
| 32 | GET | `/api/v1/cron/weekly-drift` | JSON 信封 | anon | — | 200 | - | - | `backend/routers/cron.py:246` `weekly_drift` |
| 33 | GET | `/api/v1/cron/weekly-insight` | JSON 信封 | anon | — | 200 | - | - | `backend/routers/cron.py:234` `weekly_insight` |
| 34 | GET | `/api/v1/curriculum/week` | JSON 信封 | auth | — | 200 | - | - | `backend/routers/curriculum.py:28` `curriculum_week` |
| 35 | POST | `/api/v1/curriculum/{event_id}/read` | JSON 信封 | auth | — | 200+422 | - | event_id(path) | `backend/routers/curriculum.py:40` `curriculum_read` |
| 36 | GET | `/api/v1/discovery/daily` | JSON 信封 | auth | frontend/src/services/api.ts:589（封装未接线） | 200 | - | - | `backend/routers/discovery.py:24` `daily` |
| 37 | GET | `/api/v1/growth/badges` | JSON 信封 | auth | frontend/src/services/api.ts:430 | 200 | - | - | `backend/routers/growth.py:109` `badges` |
| 38 | GET | `/api/v1/growth/dashboard` | JSON 信封 | auth | frontend/src/services/api.ts:410 | 200 | - | - | `backend/routers/growth.py:36` `dashboard` |
| 39 | GET | `/api/v1/growth/insights` | JSON 信封 | auth | frontend/src/services/api.ts:414 | 200 | - | - | `backend/routers/growth.py:52` `insights` |
| 40 | GET | `/api/v1/growth/level` | JSON 信封 | auth | frontend/src/services/api.ts:425 | 200 | - | - | `backend/routers/growth.py:99` `level_detail` |
| 41 | GET | `/api/v1/growth/memory-card` | JSON 信封 | auth | — | 200+422 | - | days_ago(query) | `backend/routers/growth.py:171` `memory_card` |
| 42 | GET | `/api/v1/growth/patterns` | JSON 信封 | auth | frontend/src/services/api.ts:660 | 200 | - | - | `backend/routers/growth.py:132` `patterns` |
| 43 | GET | `/api/v1/growth/records` | JSON 信封 | auth | — | 200+422 | - | tag(query), days(query), limit(query) | `backend/routers/growth.py:144` `records` |
| 44 | GET | `/api/v1/growth/relationship-timeline` | JSON 信封 | auth | frontend/src/services/api.ts:435 | 200+422 | - | limit(query) | `backend/routers/growth.py:87` `relationship_timeline` |
| 45 | GET | `/api/v1/growth/reports/{period}` | JSON 信封 | auth | — | 200+422 | - | period(path) | `backend/routers/growth.py:120` `report` |
| 46 | GET | `/api/v1/growth/timeline` | JSON 信封 | auth | frontend/src/services/api.ts:418 | 200+422 | - | days(query) | `backend/routers/growth.py:63` `timeline` |
| 47 | GET | `/api/v1/growth/timeline/day` | JSON 信封 | auth | frontend/src/services/api.ts:440 | 200+422 | - | day(query) | `backend/routers/growth.py:75` `timeline_day` |
| 48 | GET | `/api/v1/growth/topics` | JSON 信封 | auth | — | 200+422 | - | days(query) | `backend/routers/growth.py:158` `topics` |
| 49 | GET | `/api/v1/iap/entitlements` | JSON 信封 | auth | frontend/src/services/api.ts:707 | 200 | - | - | `backend/routers/subscription.py:55` `life_archive_entitlements` |
| 50 | POST | `/api/v1/iap/notifications` | JSON 信封 | anon | — | 200 | （无声明模型，按 handler 读取） | - | `backend/routers/subscription.py:76` `app_store_notifications` |
| 51 | GET | `/api/v1/iap/status` | JSON 信封 | auth | frontend/src/services/api.ts:686 | 200 | - | - | `backend/routers/subscription.py:65` `subscription_status` |
| 52 | POST | `/api/v1/iap/verify` | JSON 信封 | auth | frontend/src/services/api.ts:696 | 200+422 | `ReceiptVerifyRequest`: is_sandbox, product_id*, receipt_data* | - | `backend/routers/subscription.py:31` `verify_receipt` |
| 53 | GET | `/api/v1/journey/events` | JSON 信封 | auth | frontend/src/services/api.ts:645 | 200 | - | - | `backend/routers/journey.py:24` `list_journey_events` |
| 54 | POST | `/api/v1/journey/events/acknowledge` | JSON 信封 | auth | frontend/src/services/api.ts:650 | 200+422 | `JourneyAckRequest`: event_id* | - | `backend/routers/journey.py:33` `acknowledge_journey_event` |
| 55 | GET | `/api/v1/memories` | JSON 信封 | auth | frontend/src/services/api.ts:579 | 200+422 | - | limit(query), category(query) | `backend/routers/memories.py:33` `list_memories` |
| 56 | POST | `/api/v1/memories` | JSON 信封 | auth | — | 200+422 | `MemoryCreate`: category, content*, conviction, metadata, source | - | `backend/routers/memories.py:59` `create_memory` |
| 57 | POST | `/api/v1/memories/search` | JSON 信封 | auth | frontend/src/services/api.ts:583 | 200+422 | `MemorySearchRequest`: include_superseded, limit, query* | - | `backend/routers/memories.py:80` `search_memories` |
| 58 | POST | `/api/v1/memories/version` | JSON 信封 | auth | — | 200+422 | `MemoryVersionRequest`: emotion_context, memory_id*, new_content, reason, transition_type* | - | `backend/routers/memories.py:100` `version_memory` |
| 59 | DELETE | `/api/v1/memories/{memory_id}` | JSON 信封 | auth | — | 200+422 | - | memory_id(path) | `backend/routers/memories.py:122` `delete_memory` |
| 60 | GET | `/api/v1/memories/{memory_id}` | JSON 信封 | auth | — | 200+422 | - | memory_id(path) | `backend/routers/memories.py:45` `get_memory` |
| 61 | POST | `/api/v1/mood/checkin` | JSON 信封 | auth | frontend/src/services/api.ts:392 | 200+422 | `MoodCheckinRequest`: emoji*, note | - | `backend/routers/mood.py:27` `checkin` |
| 62 | GET | `/api/v1/mood/history` | JSON 信封 | auth | frontend/src/services/api.ts:404 | 200 | - | - | `backend/routers/mood.py:46` `history` |
| 63 | GET | `/api/v1/mood/trend` | JSON 信封 | auth | frontend/src/services/api.ts:399（封装未接线） | 200+422 | - | days(query) | `backend/routers/mood.py:56` `trend` |
| 64 | GET | `/api/v1/onboarding/first-read` | JSON 信封 | auth | frontend/src/services/api.ts:626<br>frontend/src/services/api.ts:638 | 200 | - | - | `backend/routers/onboarding.py:38` `get_first_read` |
| 65 | POST | `/api/v1/onboarding/first-read` | JSON 信封 | auth | — | 200+422 | `FirstReadRequest`: answers, language, persona_type | - | `backend/routers/onboarding.py:24` `create_first_read` |
| 66 | GET | `/api/v1/personality/assessment` | JSON 信封 | auth | frontend/src/services/api.ts:478 | 200 | - | - | `backend/routers/personality.py:70` `get_assessment` |
| 67 | POST | `/api/v1/personality/assessment/answer` | JSON 信封 | auth | frontend/src/services/api.ts:490 | 200+422 | `AssessmentAnswerRequest`: answer, option | - | `backend/routers/personality.py:90` `answer_assessment` |
| 68 | POST | `/api/v1/personality/assessment/start` | JSON 信封 | auth | frontend/src/services/api.ts:483 | 200 | - | - | `backend/routers/personality.py:80` `start_assessment` |
| 69 | POST | `/api/v1/personality/combo-report` | JSON 信封 | auth | frontend/src/services/api.ts:498 | 200 | - | - | `backend/routers/personality.py:101` `combo_report` |
| 70 | GET | `/api/v1/personality/enneagram` | JSON 信封 | auth | frontend/src/services/api.ts:505 | 200 | - | - | `backend/routers/personality.py:115` `get_enneagram` |
| 71 | POST | `/api/v1/personality/enneagram/answer` | JSON 信封 | auth | frontend/src/services/api.ts:517 | 200+422 | `AssessmentAnswerRequest`: answer, option | - | `backend/routers/personality.py:135` `answer_enneagram` |
| 72 | POST | `/api/v1/personality/enneagram/start` | JSON 信封 | auth | frontend/src/services/api.ts:510 | 200 | - | - | `backend/routers/personality.py:125` `start_enneagram` |
| 73 | GET | `/api/v1/personality/profile` | JSON 信封 | auth | frontend/src/services/api.ts:469 | 200 | - | - | `backend/routers/personality.py:38` `get_profile` |
| 74 | POST | `/api/v1/personality/refresh` | JSON 信封 | auth | frontend/src/services/api.ts:473 | 200 | - | - | `backend/routers/personality.py:53` `refresh` |
| 75 | GET | `/api/v1/reflections` | JSON 信封 | auth | frontend/src/services/api.ts:456<br>frontend/src/services/api.ts:463 | 200+422 | - | limit(query) | `backend/routers/reflection.py:62` `list_reflections` |
| 76 | POST | `/api/v1/reflections` | JSON 信封 | auth | — | 200+422 | `ReflectionCreate`: content*, related_memory_ids | - | `backend/routers/reflection.py:49` `save_reflection` |
| 77 | POST | `/api/v1/reflections/generate` | JSON 信封 | auth | frontend/src/services/api.ts:446 | 200+422 | `ReflectionGenerateRequest`: context | - | `backend/routers/reflection.py:31` `generate_draft` |
| 78 | POST | `/api/v1/share/first-read-card` | PDF 二进制 | auth | — | 200 | - | - | `backend/routers/share.py:73` `first_read_card_pdf` |
| 79 | POST | `/api/v1/share/life-archive` | JSON 信封 | auth | frontend/src/services/api.ts:721（封装未接线） | 200+422 | `LifeArchiveExportRequest`: language, regenerate, scope | - | `backend/routers/share.py:214` `life_archive` |
| 80 | GET | `/api/v1/share/life-archive/pdf` | PDF 二进制 | auth | — | 200+422 | - | scope(query), language(query), regenerate(query) | `backend/routers/share.py:234` `life_archive_pdf` |
| 81 | GET | `/api/v1/share/link` | JSON 信封 | auth | frontend/src/services/api.ts:616（封装未接线） | 200 | - | - | `backend/routers/share.py:159` `share_link` |
| 82 | POST | `/api/v1/share/memory-card` | PDF 二进制 | auth | — | 200+422 | `MemoryCardRequest`: memory_id* | - | `backend/routers/share.py:91` `memory_card_pdf` |
| 83 | GET | `/api/v1/share/on-this-day` | JSON 信封 | auth | — | 200 | - | - | `backend/routers/share.py:110` `on_this_day` |
| 84 | GET | `/api/v1/share/personality-card` | JSON 信封 | auth | frontend/src/services/api.ts:601 | 200+422 | - | language(query), variant(query) | `backend/routers/share.py:61` `personality_card` |
| 85 | GET | `/api/v1/share/report/{period}` | JSON 信封 | auth | frontend/src/services/api.ts:606 | 200+422 | - | period(path), language(query) | `backend/routers/share.py:184` `report_json` |
| 86 | GET | `/api/v1/share/report/{period}/pdf` | PDF 二进制 | auth | — | 200+422 | - | period(path), language(query) | `backend/routers/share.py:196` `report_pdf` |
| 87 | GET | `/api/v1/share/resolve/{code}` | JSON 信封 | anon | — | 200+422 | - | code(path) | `backend/routers/share.py:169` `resolve_share_code` |
| 88 | GET | `/api/v1/share/year-in-review` | JSON 信封 | auth | — | 200+422 | - | year(query), language(query) | `backend/routers/share.py:124` `year_in_review` |
| 89 | GET | `/api/v1/share/year-in-review/pdf` | PDF 二进制 | auth | — | 200+422 | - | year(query), language(query) | `backend/routers/share.py:139` `year_in_review_pdf` |
| 90 | DELETE | `/api/v1/user/account` | JSON 信封 | auth | frontend/src/services/api.ts:550 | 200 | - | - | `backend/routers/user.py:117` `delete_account` |
| 91 | GET | `/api/v1/user/ai-consent` | JSON 信封 | auth | frontend/src/services/api.ts:555 | 200 | - | - | `backend/routers/user.py:56` `get_ai_consent` |
| 92 | POST | `/api/v1/user/ai-consent` | JSON 信封 | auth | — | 200+422 | `AIConsentUpdate`: consent* | - | `backend/routers/user.py:67` `set_ai_consent` |
| 93 | POST | `/api/v1/user/analytics-consent` | JSON 信封 | auth | — | 200+422 | `AIConsentUpdate`: consent* | - | `backend/routers/user.py:79` `set_analytics_consent` |
| 94 | GET | `/api/v1/user/profile` | JSON 信封 | auth | frontend/src/services/api.ts:526 | 200 | - | - | `backend/routers/user.py:35` `get_profile` |
| 95 | DELETE | `/api/v1/user/push-token` | JSON 信封 | auth | frontend/src/services/api.ts:537<br>frontend/src/services/api.ts:545（封装未接线） | 200 | - | - | `backend/routers/user.py:106` `clear_push_token` |
| 96 | POST | `/api/v1/user/push-token` | JSON 信封 | auth | — | 200+422 | `PushTokenRequest`: platform*, token* | - | `backend/routers/user.py:94` `register_push_token` |
| 97 | PATCH | `/api/v1/user/settings` | JSON 信封 | auth | frontend/src/services/api.ts:530 | 200+422 | `UserSettingsUpdate`: chat_style, conversation_style, enneagram_type, language, mbti_type, name, push_enabled, push_frequency, push_time, timezone | - | `backend/routers/user.py:45` `update_settings` |
| 98 | GET | `/health` | JSON 信封 | anon | — | 200 | - | - | `backend/main.py:170` `health_check` |

## 六、SSE 契约（唯一一条流式端点）

`POST /api/v1/chat/stream`（`backend/routers/chat.py:53`）——前端逐事件解析，**事件名与字段名逐字不可改**。

- 响应头（`backend/routers/chat.py:44` `_stream_headers()`）：
  `Cache-Control: no-cache`、`Connection: keep-alive`、`X-Accel-Buffering: no`
- 帧格式（`backend/routers/chat.py:39` `_sse_event()`）：每帧一行 `data: {"type": <事件名>, ...}\n\n`
- 事件类型全集：

| 事件 | 载荷字段 | 出处 |
| --- | --- | --- |
| `memory_context` | `memories: [str]`（截前 3 条） | `backend/routers/chat.py:102` |
| `token` | `content: str` | `backend/routers/chat.py:114` |
| `done` | 无 | `backend/routers/chat.py:130` |
| `error` | `code: int`、`message: str` | `backend/routers/chat.py:136` |

⚠️ 进入流之前仍走信封错误（403 `40303` AI_CONSENT_REQUIRED、429 `40301/42901` 配额），
即前端要同时处理「HTTP 非 200 + JSON 信封」和「200 + SSE `error` 帧」两条失败路径。

## 七、二进制（PDF）端点

以下端点返回 `StreamingResponse(iter([pdf]))`，**不是** JSON 信封，前端按字节流落盘：
`Content-Type: application/pdf` + `Content-Disposition: attachment; filename="<登记值>"`。
Go 侧接管后必须保持文件名逐字一致（分享/归档链路按文件名展示）。


| 端点 | Content-Disposition filename | 出处 |
| --- | --- | --- |
| POST `/api/v1/share/first-read-card` | `closenai_first_read.pdf` | `backend/routers/share.py:73` |
| GET `/api/v1/share/life-archive/pdf` | `closenai_life_archive_{scope}.pdf（按路径/查询参数拼接）` | `backend/routers/share.py:234` |
| POST `/api/v1/share/memory-card` | `closenai_memory_card.pdf` | `backend/routers/share.py:91` |
| GET `/api/v1/share/report/{period}/pdf` | `closenai_{period}_report.pdf（按路径/查询参数拼接）` | `backend/routers/share.py:196` |
| GET `/api/v1/share/year-in-review/pdf` | `closenai_year_in_review.pdf` | `backend/routers/share.py:139` |


## 八、登记缺口（带日期，按「发现缺陷不顺手修」只记不补）

- ↳ 2026-10-13 **响应载荷无类型**：全部 98 个端点的 200 响应在 openapi 里都是空 schema
  （`grep -c "response_model=" backend/routers/*.py` = 0，因 handler 返回 `success(dict)`）。
  ⇒ `contracts/openapi.json` 只能冻结**请求侧 + 信封**，`data` 内字段基线只能来自
  `contracts/frontend-compat.md`（前端实际消费面）。归属 N2 建 canonical schema 时用
  `response_model` 补齐，不在 N0 逐端点加注解（会与 N8 按域切流重复劳动）。
- ↳ 2026-10-13 **业务错误码不在 openapi**：错误只由全局异常处理器产生，规范里查不到 4xx 码。
  本文件第四节是替代基线，Go 侧需逐值复现。
- ↳ 2026-10-13 **`GET /health`（app 级）与 `GET /api/v1/cron/health` 同名 operationId 前缀**，
  前者 98 条里唯一不挂 router，故与「97 个 router 端点」计数相差 1 的原因即此（不是漏登记）。

## 九、前端调用面对账（自动扫描 `frontend/src`）

- 扫法：`request<T>('/path')` 与 `${API_BASE}/path` 两类调用点（前端只有 api.ts 一处出口发请求，
  `services/api.ts:201` 统一 `fetch`、`:296` SSE 走 XHR，其余 service 均转调 api.ts）。
- 结果：**98 个端点里 53 个有前端调用点，其中 48 个的封装确实被屏/hook 接线**。
  「前端调用」列带 `（封装未接线）` 的 = api.ts 里有函数但全仓无人调用
  ⇒ 改这些端点的响应不会立刻炸 App，Go 切流可排在后；无标记的才是 P1 硬约束面。
- 悬空检查（前端调了但登记表里没有的路径）：

-（无）

字段级（`data` 里的键名）逐字兼容清单不在本文件——openapi 的 200 响应是空 schema（见第八节），
基线来自 `contracts/frontend-compat.md`。

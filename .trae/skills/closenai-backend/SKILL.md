---
name: "closenai-backend"
description: "ClosenAI backend development with FastAPI, QStash, Redis, SSE streaming, host crontab jobs, APNs/FCM push. Invoke when working on backend API routes, async tasks, push notifications, real-time SSE, or VPS deployment/ops (deploy/compose)."
---

# ClosenAI Backend Development

ClosenAI 后端开发规范：FastAPI (async) + VPS 常驻容器（docker compose）+ Supabase PostgreSQL + QStash + Redis + SSE + APNs/FCM

> 2026-10-18（T0-02）：Vercel 全线下马——`vercel.json` 已删除、`pyproject.toml` 的 `[tool.vercel]` 段已清；现役唯一调度源＝`deploy/schedule/crontab`（宿主 crontab），现役部署形态＝VPS 单机 `deploy/compose/docker-compose.prod.yml`。本文件不得再把 Vercel 写成现役平台。

## Tech Stack

| Component | Technology | Notes |
|-----------|-----------|-------|
| API Framework | FastAPI (async) | Existing, reuse |
| Deployment | VPS 单机 docker compose（`deploy/compose/docker-compose.prod.yml`＋Caddy） | 常驻 uvicorn 进程，无 Serverless 60s 限制 |
| Database | Supabase PostgreSQL | asyncpg + SQLAlchemy 2.0 |
| Task Queue | QStash (Upstash) | Batch LLM dispatch (50 users/chunk) |
| Cron Jobs | 宿主 crontab（`deploy/schedule/crontab`，GET＋`Authorization: Bearer $CRON_SECRET`）+ QStash dispatch | 单一事实源，改排程只改那一个文件 |
| Cache | Upstash Redis | Mood check-in cache, etc. |
| Push | Self-built APNs/FCM provider | push/service.py |
| Monitoring | PostHog（行为分析 + Error Tracking）+ 容器日志（json-file，50m×3） | `posthog` Python SDK；Sentry 仅用于 App 端崩溃（见 closenai-frontend SKILL）；已移除 Axiom |

## 🔴 文件位置约束（必须遵守）

**所有后端代码必须放在 `backend/` 目录下，严禁放在项目根目录或其他位置。**

```
项目根目录/
├── backend/             ← 所有后端代码 ONLY HERE
│   ├── main.py
│   ├── config.py
│   ├── routers/
│   ├── services/
│   ├── models/
│   └── repositories/
├── frontend/            ← 前端代码 ONLY HERE
├── .trae/               ← 技能配置
├── .trellis/            ← 框架配置
└── closenai-prd/        ← 产品文档
```

**禁止行为：**
- ❌ 在项目根目录创建 `.py` 文件
- ❌ 在 `frontend/` 里写后端代码
- ❌ 在 `closenai-prd/` 里写代码

## Project Structure（实际目录）

```
backend/
├── main.py                    # FastAPI app entry
├── config.py                  # Environment configuration
├── requirements.txt           # Python dependencies
├── core/                      # 基础设施层（统一响应/错误码/日志/中间件/LLM）
│   ├── errors.py              # 错误码 + 业务异常
│   ├── response.py            # 统一响应 success()/fail()
│   ├── logging.py             # 日志系统（trace_id/user_id 透传）
│   ├── middleware.py          # TraceIdMiddleware + 全局异常处理
│   ├── auth.py                # get_current_user 依赖
│   └── llm.py                 # LLM 客户端封装
├── routers/                   # API route handlers
│   ├── __init__.py
│   ├── chat.py                # Chat & SSE streaming endpoints
│   └── memories.py            # Memory management endpoints
├── services/                  # Business logic layer
│   ├── __init__.py
│   ├── memory_service.py      # Memory retrieval/search
│   └── message_generator.py   # AI message generation
├── models/                    # Pydantic data models
│   ├── __init__.py
│   └── schemas.py             # Request/Response schemas
└── repositories/              # Database access layer
    └── __init__.py
```

### 新增文件时的位置规则

| 文件类型 | 必须放在 |
|---------|---------|
| API 路由（`@router.get/post`） | `backend/routers/` |
| 业务逻辑（Service 类） | `backend/services/` |
| 数据模型（Pydantic/SQLAlchemy） | `backend/models/` |
| 数据库操作（CRUD） | `backend/repositories/` |
| 测试文件 | `backend/tests/` |
| 推送服务 | `backend/services/push/` |
| 定时任务 | `backend/routers/`（作为路由端点） |

## 🔴 依赖双文件约束（必须遵守）

**依赖必须同时写入两个文件，两边不许漂（见 pyproject 内 R3/R5 注释）：**

| 文件 | 用途 | 谁读取 |
|------|------|--------|
| `backend/requirements.txt` | **镜像装包唯一清单**（`deploy/Dockerfile.py-api` pip install） | docker compose / 镜像构建 |
| `pyproject.toml` `[project.dependencies]` | 本地开发 / IDE / 工具链 | 本地 |

新增任何 Python 依赖（如 `sqlalchemy`、`asyncpg`、`alembic`、`pgvector`）时，**两处都要加**。

## 数据库层（Alembic + SQLAlchemy）

- 连接：`backend/database.py` 创建 async engine + `Base` + `get_db`
- 版本管理：建表/改表走 **Alembic 迁移**（`alembic upgrade head`），禁止手动 SQL Editor
- 连接串：`DATABASE_URL` 从 `.env` 读取，生产用 Supabase **Transaction pooler**（端口 6543）
- 详见表结构约定 → `closenai-supabase` SKILL

```
backend/
├── database.py          # async engine + Base + get_db
├── alembic.ini
├── alembic/             # env.py + versions/
└── models/              # SQLAlchemy ORM（每表一文件）
```

## Key Architecture Patterns

### 1. SSE Streaming Response (Real-time AI Chat)

```python
from fastapi.responses import StreamingResponse
import asyncio

@router.post("/chat/messages/stream")
async def stream_chat(request: ChatRequest):
    async def event_generator():
        # Preprocessing pipeline (~170ms)
        embedding = await get_embedding(request.content)
        drift_check = await sql_drift_check(request.user_id, embedding)
        preprocess = await flash_lite_preprocess(request.content, drift_check)
        
        # Stream LLM response
        async for token in gemini_stream(request.content, preprocess):
            yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )
```

### 2. Cron Job + QStash Dispatch (Active Messages)

```
宿主 crontab (hourly GET /api/v1/cron/*) → FastAPI → Query users in timezone batch
  → QStash dispatch (50 users/chunk)
  → LLM generate message → Store in DB → Send push notification

Timezone batches:
  UTC+8 (Beijing/Singapore) → Cron 01:00 UTC
  UTC+1 (Berlin/Paris)      → Cron 08:00 UTC
  UTC+0 (London)            → Cron 09:00 UTC
  UTC-5 (New York)          → Cron 14:00 UTC
  UTC-8 (Los Angeles)       → Cron 17:00 UTC
```

### 3. 常驻进程的成本档位（原「冷启动缓解」改判）

- 常驻进程无冷启动；`deploy/schedule/crontab` 的 `*/5` 那条打 `/api/v1/cron/health` 兜底探活
- 非实时任务用 lite 档模型（`settings.*_lite_model`，便宜且快；这条与平台无关，保留）
- Redis cache for common mood check-in responses
- `max_tokens=60-100` for short responses (mood check-ins)

### 4. Push Notification Architecture

```
Backend generates message → Stores in Supabase chat_messages → Sends push
  ↓
App foreground: SSE receives message, push notification suppressed (shouldShowAlert: false)
App background/killed: APNs/FCM push notification → User taps → App opens → Fetches messages
```

**Push token management:**
- On logout: Clear push_token in DB (`UPDATE users SET push_token = NULL`)
- On account deletion: Clear token + set `is_active = false`
- Cron Job checks: `push_token IS NOT NULL AND is_active = true` before sending
- On re-login: Re-register push token

### 5. LLM Fallback Strategy

```
Active message LLM call
  ├── Success → Store message → Send push
  └── Failure → Fallback to Rule Engine template message
       Day 1: "嗨！我是你的AI朋友，很高兴认识你！"
       Day 2+: "早上好！今天感觉怎么样？"

Real-time chat LLM call
  ├── Success → Stream tokens via SSE
  └── Failure → "稍等，我想想..." + retry
```

## 统一响应格式 / 错误码 / 日志系统（必须遵守）

所有 API 返回统一 JSON 结构，错误码集中维护，日志带 trace_id / user_id 透传。
基础设施位于 `backend/core/`：

```
backend/core/
├── errors.py      # 错误码体系（ErrorCode 枚举）+ 业务异常 AppException
├── response.py    # 统一响应构造 success() / fail()
├── logging.py     # 日志系统（contextvars 透传 trace_id / user_id）
├── middleware.py  # TraceIdMiddleware + 全局异常处理器
├── auth.py        # get_current_user 依赖 + resolve_user_id_from_request
└── llm.py         # LLM 客户端封装（多 Provider + 多语言）
```

### 1. 统一响应格式

所有接口（成功与失败）返回：

```json
{
  "code": 200,          // 整数状态码（非字符串），成功 200，错误为业务错误码
  "message": "success", // 人类可读消息
  "data": { ... },      // 业务数据（可为 null）
  "trace_id": "xxx",    // 请求追踪 ID（透传 X-Trace-Id 或生成 UUID）
  "user_id": "xxx"      // 用户 ID（未登录为 ""）
}
```

**路由规范：**
- 成功：`return success(data)`（`from backend.core.response import success`）
- 失败：抛 `raise AppException(ErrorCode.XXX, "消息")`，全局处理器自动转标准响应
- **禁止**在路由里手动构造失败 dict / 直接返回裸 dict / 返回 Pydantic 模型
- **不要**为路由设置 `response_model=`（返回的是统一包装，非业务模型）
- SSE 流式接口（`/chat/stream`）保持 SSE 事件格式，不套统一包装

### 2. 错误码体系（backend/core/errors.py）

错误码为**整数状态码**，遵循 HTTP 语义分段：

| 段 | 含义 | 例子 |
|----|------|------|
| 2xx | 成功 | 200 OK |
| 4xx | 客户端错误 | 400 参数、401 认证、403 权限、404 资源、409 冲突、429 限流 |
| 4xxxx | 业务细分 | 40001 参数无效、40101 token 过期、40403 记忆不存在 |
| 5xx | 服务端错误 | 500 内部、503 不可用 |
| 5xxxx | 业务细分 | 50001 LLM 错误、50003 数据库错误 |

**新增错误码必须**在 `ErrorCode` 枚举中集中定义，禁止在业务代码硬编码数字。

### 3. 日志系统（backend/core/logging.py）

- 使用 `contextvars` 透传 `trace_id` / `user_id`，每条日志自动附加
- 中间件在请求入口设置 `trace_id`（透传 `X-Trace-Id` 或生成 UUID）与 `user_id`
- 业务代码只需 `logger = logging.getLogger(__name__)`，无需手动拼 trace_id
- 日志格式：
  ```
  2026-08-04 00:30:31 | INFO | closenai | trace_id=xxx | user_id=xxx | message
  ```

### 4. 认证与 user_id 透传（backend/core/auth.py）

user_id 来源（按优先级）：
1. `Authorization: Bearer <JWT>` → 解码 `sub`（需配 `JWT_SECRET`）
2. `X-User-Id` 头 → 透传（网关/开发）
3. 未解析 → `anonymous`

路由通过 `user_id: str = Depends(get_current_user)` 获取当前用户，并自动写入 contextvar。
**注意**：`BaseHTTPMiddleware` 在子任务中执行 `call_next`，依赖内设置的 contextvar 不会回传外层；
因此 `user_id` 由中间件在 `call_next` **之前**用 `resolve_user_id_from_request` 写入外层 context，
保证成功与异常路径的日志都能透传。

### 5. 多语言透传（backend/core/language.py）

语言解析遵循 **Header 优先 + 库兜底** 双轨机制（与 `X-User-Id` 透传模式一致）：

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1 | `X-Language` header | 前端每次请求携带，实时、无状态 |
| 2 | `users.language` | 数据库偏好，跨设备/重装兜底 |
| 3 | 默认 `en` | 兜底 |

- 依赖：`language: str = Depends(get_request_language)`（`from backend.core.language import get_request_language`）
- `X-Language` 归一化：`zh-CN → zh`（取 `-` 前段小写），仅接受 `supported_languages` 内的码
- 该 `language` 必须传给 LLM 调用（`llm.stream(..., language=language)`），保证 AI 用用户语言回复
- 前端所有请求带 `X-Language` header（取当前 i18n 语言），见 `frontend/src/services/api.ts` 的 `getHeaders()`

## 监控与分析（PostHog）

后端统一使用 **PostHog**（Cloud·EU 区域）做行为分析与错误追踪；**Sentry 仅用于 App 端崩溃**（见 closenai-frontend SKILL）；短期原始日志用 **容器日志**（`deploy/compose` json-file，50m×3 轮转；当天排障）。**已移除 Axiom**。

### 依赖与配置

- 依赖：`posthog`（Python SDK），同时写入 `pyproject.toml` + `backend/requirements.txt`
- 配置（config.py）：生产才启用，配置为空则跳过初始化
  - `POSTHOG_API_KEY`
  - `POSTHOG_HOST=https://eu.i.posthog.com`

### 服务端行为事件

- 关键业务事件由后端上报（订阅、配额、AI 用量等），**仅行为元数据，禁止携带任何敏感内容**（反思文本 / 记忆 / 对话原文 / 情绪具体值）
- 统一经 `backend/services/analytics_service.py` 封装，业务代码不直接依赖 PostHog SDK

### `/analytics/batch` 端点（接收前端本地事件队列）

前端把本地 `analytics_events` 队列批量 POST 到 `/api/v1/analytics/batch`。后端处理顺序：

1. 校验用户 `analytics_consent`（未同意则丢弃）
2. **匿名化**：仅保留行为属性，剔除任何内容字段
3. 用事件携带的 **`occurred_at`（真实发生时间）** 上报 PostHog —— 保证离线延迟事件的口径准确
4. 成功后返回，前端将事件标记 `synced=1` 并清理

> 前端**不装 PostHog SDK、不直连第三方**，PostHog token 不进客户端；交接方只依赖自家后端契约，分析后台保持私有。

### 错误追踪

- 后端异常/错误上报 **PostHog Error Tracking**（免费 100K 异常/月），与行为分析同账号统一管理

### 红线

- **绝不用本地缓存的 `subscription_status` 做权益 / 配额 gate 判断**，必须实时回服务端验证
- 分析数据不写入业务数据库（Postgres），避免污染与删除流程冲突；`analytics_consent` 除外（存 `users` 表，见 closenai-supabase SKILL）

## Performance Targets

| Metric | Target | Feature |
|--------|--------|---------|
| AI reply first token | ~2.5-3.2s (incl. 170ms preprocessing) | P0-F01 real-time chat |
| Active message generation | Async, no real-time requirement | P0-F01 active messages |
| Mood check-in reply | < 2s | P0-F03 |
| Personality discovery | < 3s (incl. LLM) | P0-F05 |
| Push delivery rate | 85-95% | P0-F01 push |

## Important Constraints

- **无常驻超时后仍不许裸跑批**：批量 LLM 调用一律走 QStash 分块（50 users/chunk，口径沿用）
- **调度只排 GET**（宿主 crontab + Bearer 验签）；QStash 回调保持 POST（豁免登记见 `backend/tests/test_cron_schedule_coverage.py`）
- **No persistent connections**: No WebSocket. Use SSE for streaming, API polling for sync
- **冷启动**：VPS 常驻后不再是约束；原 60s/冷启动条款随 Vercel 下线一并作废
- **QStash cost**: 10K DAU = ~400 chunks/day. Pro plan ($20/month, 100K calls/day) needed
- **Auth migration**: Supabase Auth → self-signed JWT (HS256). Keep auth logic in one service file

## 时间与时区（出海强制，细则见 .trellis/spec/api-conventions.md §6）

- **存储一律 UTC + `timestamptz`**：models 必须 `DateTime(timezone=True)`，禁止 naive DateTime
- **API 传输一律 ISO 8601 显式 UTC**（`...T01:00:00Z`），禁止无时区后缀字符串
- **"今日"语义（配额重置/streak/主动消息/每日洞察）按用户本地时区**：统一走 `core/time_utils.py` 的 `local_today(tz)`/`local_now(tz)`，禁止无参 `date.today()`/`datetime.now()`
- **与 timestamptz 列比较必须 tz-aware**：用 `local_day_start_utc(tz)`（本地 00:00 → UTC）做范围起点，当前时刻用 `utc_now()`；禁止 naive `datetime.combine(day, min.time())`（会被当 UTC，边界偏 ±14h）
- **按"日"分组/星期统计**：先转用户本地时区再取 date/weekday（见 `mood_repository.count_by_day(tz=...)`，Python 侧转换兼容 SQLite/PG，禁用 PG 专有 `func.timezone`）
- **`users.timezone` 只存 IANA 名**（`Asia/Shanghai`），禁止 `"UTC+8"` 偏移格式（zoneinfo 静默回退 UTC 的坑）
- 前端启动时上报设备时区到 `PATCH /user/settings`（App.tsx 已内置）

---
name: "closenai-backend"
description: "ClosenAI backend development with FastAPI, Vercel Serverless, QStash, Redis, SSE streaming, Cron Jobs, APNs/FCM push. Invoke when working on backend API routes, async tasks, push notifications, real-time SSE, or Vercel deployment."
---

# ClosenAI Backend Development

ClosenAI 后端开发规范：FastAPI (async) + Vercel Serverless + Supabase PostgreSQL + QStash + Redis + SSE + APNs/FCM

## Tech Stack

| Component | Technology | Notes |
|-----------|-----------|-------|
| API Framework | FastAPI (async) | Existing, reuse |
| Deployment | Vercel Serverless | Pro plan, 60s timeout |
| Database | Supabase PostgreSQL | asyncpg + SQLAlchemy 2.0 |
| Task Queue | QStash (Upstash) | Batch LLM dispatch (50 users/chunk) |
| Cron Jobs | Vercel Cron (hourly GET) + QStash dispatch | Reuse daily_fortune_cron pattern |
| Cache | Upstash Redis | Mood check-in cache, etc. |
| Push | Self-built APNs/FCM provider | push/service.py |
| Monitoring | Axiom + Sentry | Existing, reuse |

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
Vercel Cron (hourly GET) → FastAPI → Query users in timezone batch
  → QStash dispatch (50 users/chunk)
  → LLM generate message → Store in DB → Send push notification

Timezone batches:
  UTC+8 (Beijing/Singapore) → Cron 01:00 UTC
  UTC+1 (Berlin/Paris)      → Cron 08:00 UTC
  UTC+0 (London)            → Cron 09:00 UTC
  UTC-5 (New York)          → Cron 14:00 UTC
  UTC-8 (Los Angeles)       → Cron 17:00 UTC
```

### 3. Cold Start Mitigation

- Vercel Cron calls `/health` every 5 minutes to keep functions warm
- Use Gemini 2.5 Flash-Lite for non-real-time tasks (cheaper + faster cold start)
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

## Performance Targets

| Metric | Target | Feature |
|--------|--------|---------|
| AI reply first token | ~2.5-3.2s (incl. 170ms preprocessing) | P0-F01 real-time chat |
| Active message generation | Async, no real-time requirement | P0-F01 active messages |
| Mood check-in reply | < 2s | P0-F03 |
| Personality discovery | < 3s (incl. LLM) | P0-F05 |
| Push delivery rate | 85-95% | P0-F01 push |

## Important Constraints

- **Vercel Serverless timeout**: Pro plan 60s. Batch LLM calls MUST use QStash chunking
- **Vercel Cron**: GET requests only. QStash callback uses POST
- **No persistent connections**: No WebSocket. Use SSE for streaming, API polling for sync
- **Cold start**: 1-3s. Mitigate with /health warming + mini models + Redis cache
- **QStash cost**: 10K DAU = ~400 chunks/day. Pro plan ($20/month, 100K calls/day) needed
- **Auth migration**: Supabase Auth → self-signed JWT (HS256). Keep auth logic in one service file
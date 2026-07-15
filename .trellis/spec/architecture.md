# 技术架构规范

> 来源：ClosenAI 技术方案设计 v1.0 (2026-07-07) + PRD v2.0
> 状态：P0 阶段执行标准

---

## 1. 技术栈选型

| 组件 | 技术 | 说明 |
|------|------|------|
| API 框架 | FastAPI (Python 3.12, async) | 已有，复用。原生支持 SSE StreamingResponse |
| 部署 | Vercel Serverless (Pro 计划) | 已有，Pro 计划 60s 超时 |
| 数据库 | Supabase PostgreSQL (asyncpg + SQLAlchemy 2.0) | 已有，复用 |
| 实时消息 | Supabase Realtime (Postgres Changes) | App 前台时订阅 `chat_messages` 表变更 |
| 异步任务队列 | QStash (Upstash) | 已有，复用——批量 LLM 调用分块分发（每块 50 用户） |
| 定时任务 | Vercel Cron (每小时 GET) + QStash 分发 | 复用 `daily_fortune_cron` 架构 |
| 缓存 | Upstash Redis | 已有，用于情绪签到缓存等 |
| LLM | OpenAI GPT-4o / GPT-4o-mini + 豆包(Doubao) | 主动消息用 mini/豆包，实时对话用 GPT-4o |
| 推送 | 自建 APNs/FCM provider (FastAPI) | 已有 `push/service.py`，替代 Expo Push |
| 向量搜索 | pgvector (Supabase Pro) | P0-F02 长期记忆需要 |
| 监控 | Axiom + Sentry | 已有，复用 |

### 选型约束

- **不引入 Expo Push**：现有后端已有自建 APNs/FCM provider (`push/service.py`)，P0 直接复用，减少技术栈变更
- **不用 Pinecone**：MVP 阶段可选 Supabase Free + Pinecone Free (2GB)，公测阶段升级 Supabase Pro 统一在 pgvector 内处理
- **Supabase Free 500MB 不足**：1 万 DAU 每日摘要策略下 ~8 天即满，必须升级 Pro ($25/月)

---

## 2. 部署架构

### 2.1 当前架构（P0 Serverless）

```
┌──────────────┐     ┌──────────────────────────────────────────────┐
│  React Native │     │              Vercel Serverless                │
│   (Expo) App  │────▶│                                              │
│               │     │  ┌─────────────┐  ┌──────────────────────┐  │
│  - 前台: WS   │     │  │ Vercel Cron  │  │  FastAPI (Python 3.12, async)     │  │
│    (Realtime) │     │  │ (每小时 GET) │─▶│  /api/v1/*           │  │
│  - 后台: 推送 │     │  └──────┬──────┘  │                      │  │
│    (APNs/FCM) │     │         │         │  - SSE StreamingResp  │  │
│               │     │         ▼         │  - JWT Auth           │  │
│               │◀────│  ┌─────────────┐  │  - QStash 回调        │  │
│               │     │  │   QStash    │  │                      │  │
│               │     │  │ (分块 50人) │  └──────────┬───────────┘  │
└──────────────┘     │  └─────────────┘             │              │
                     └──────────────────────────────┼──────────────┘
                              │                      │
                              ▼                      ▼
                     ┌──────────────┐      ┌──────────────────┐
                     │ Supabase PG  │      │  Upstash Redis   │
                     │ + pgvector   │      │  (缓存/验证码)    │
                     │ + Realtime   │      └──────────────────┘
                     └──────────────┘
                              │
                              ▼
                     ┌──────────────────┐
                     │  OpenAI API      │
                     │  GPT-4o / mini   │
                     └──────────────────┘
```

### 2.2 Vercel Serverless 关键约束

| 约束 | 值 | 应对方案 |
|------|-----|---------|
| 函数超时 | Pro 计划 60s (Hobby 10s 不够) | 批量 LLM 调用必须用 QStash 分块（每块 50 用户） |
| 冷启动 | 1-3s | Vercel Cron 每 5 分钟调 `/health` 保持函数温热 |
| Cron 限制 | 仅支持 GET 请求 | QStash callback 可用 POST |
| 持久连接 | 不支持 WebSocket | IM 实时通信依赖 Supabase Realtime（迁移 VPS 后启用已有 WebSocket 代码） |

### 2.3 保热机制

```json
// vercel.json — 每 5 分钟健康检查保热
{
  "crons": [
    { "path": "/api/v1/health", "schedule": "*/5 * * * *" },
    { "path": "/api/v1/cron/active-message", "schedule": "0 * * * *" },
    { "path": "/api/v1/cron/growth-insight", "schedule": "0 23 * * *" }
  ]
}
```

---

## 3. Vercel SSE 流式支持

### 3.1 实时对话（用户发消息后）

用户发消息后 AI 流式回复，首 token < 3 秒，使用 SSE (Server-Sent Events) 流式输出降低感知延迟。

```python
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from typing import AsyncGenerator
import json

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

@router.post("/messages/stream")
async def stream_chat_message(
    request: ChatMessageRequest,
    user: dict = Depends(get_current_user),
) -> StreamingResponse:
    """
    SSE 流式对话接口。
    用户发消息 → 后端注入记忆 → LLM 流式生成 → 逐 token SSE 推送。
    延迟要求：< 3 秒首 token。
    """
    async def event_stream() -> AsyncGenerator[str, None]:
        # 1. 存储用户消息
        await chat_service.save_user_message(user["sub"], request.content)

        # 2. 记忆注入（延迟 < 500ms）
        memories = await memory_service.retrieve_relevant(
            user_id=user["sub"],
            query=request.content,
            limit=5,
        )
        insights = await memory_service.get_recent_insights(user["sub"], days=7)

        # 3. 构建 LLM 上下文
        system_prompt = prompt_builder.build_chat_prompt(
            personality=await personality_service.get(user["sub"]),
            memories=memories,
            insights=insights,
        )

        # 4. LLM 流式生成（GPT-4o-mini，实时对话模型）
        async for chunk in llm_service.stream_chat(
            model="gpt-4o-mini",
            system=system_prompt,
            user_msg=request.content,
            max_tokens=500,
        ):
            yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"

        # 5. 流结束，保存完整 AI 消息
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        },
    )
```

### 3.2 主动消息 vs 回复消息（两种生成模式）

| 维度 | AI 主动消息（每日 1 条） | AI 回复消息（用户发后） |
|------|------------------------|------------------------|
| 触发方 | 后端 Cron Job（按时区分组） | 用户发送消息（实时） |
| 用户在等？ | 否（用户可能没开 App） | 是（用户在对话界面等待） |
| 延迟要求 | 无严格要求（10-30 秒可接受） | < 3 秒 |
| 生成方式 | LLM 异步生成 → 存入 DB → 发推送 | LLM 实时流式生成（SSE） |
| 成本优化 | GPT-4o-mini 批量生成 | GPT-4o-mini 流式生成 |
| 失败处理 | LLM 失败 → fallback 模板消息 | LLM 失败 → "稍等，我想想..." + 重试 |

---

## 4. Cron Job 方案

### 4.1 时区分组触发

9:00 指用户设备本地时区的 9:00。后端 Cron Job 按时区分组触发（每小时轮询一个时区批次）。

```
时区分组示例（UTC 偏移）：
  UTC+8 (Beijing/Singapore)  → Cron 01:00 UTC 触发
  UTC+1 (Berlin/Paris)       → Cron 08:00 UTC 触发
  UTC+0 (London)             → Cron 09:00 UTC 触发
  UTC-5 (New York)           → Cron 14:00 UTC 触发
  UTC-8 (Los Angeles)        → Cron 17:00 UTC 触发
```

App 端通过 `Intl.DateTimeFormat().resolvedOptions().timeZone` 获取设备时区标识符（如 `Asia/Shanghai`、`America/New_York`）并上传后端。用户跨时区旅行时，App 启动时检测时区变化并更新，次日按新时区触发。

### 4.2 主动消息生成流程

```python
# 1. Vercel Cron (每小时 GET) → FastAPI → 查时区命中 9:00±15min 的用户
#    筛选条件：timezone = 当前批次时区 AND 偏好时间命中 9:00±15min

# 2. 对每个用户，构建 LLM Prompt：
prompt = {
    "system": "你是用户的 AI 朋友。基于以下信息生成一条日常消息...",
    "context": {
        "personality_type": "INTP",
        "recent_memories": [],
        "yesterday_conversation": "用户说跟同事吵架了",
        "pending_personality_question": True,
        "astrology_data": None,
        "day_number": 3
    }
}

# 3. 调用 LLM（GPT-4o-mini，异步，无实时压力）→ 生成 50-150 字消息
# 4. 存入 DB（chat_messages 表）+ 发送自建推送
# 5. 用户打开 App / 点击推送 → 拉取消息 → 显示在对话流

# Fallback：LLM 调用失败 → 使用模板消息
#   Day 1: "嗨！我是你的 AI 朋友，很高兴认识你！你周末更喜欢一个人待着，还是跟朋友出去？"
#   Day 2+: "早上好！今天感觉怎么样？有什么想聊的吗？"
```

### 4.3 QStash 分块策略

```python
# 批量 LLM 调用必须分块，每块 50 用户
BATCH_SIZE = 50

async def dispatch_active_messages(users: list[User]):
    """将用户列表分块，通过 QStash 异步分发"""
    for i in range(0, len(users), BATCH_SIZE):
        batch = users[i:i + BATCH_SIZE]
        await qstash_client.publish_json(
            url=f"{API_BASE}/api/v1/cron/active-message/batch",
            body={"user_ids": [u.id for u in batch]},
            method="POST",  # QStash callback 可用 POST
        )
```

### 4.4 三种 App 状态的消息送达

| App 状态 | Realtime 连接 | 送达方式 | 用户体验 |
|----------|--------------|---------|---------|
| 前台运行 | Supabase Realtime 活跃 | Realtime 推送新消息事件 | 消息实时出现在对话流，无需推送通知 |
| 后台挂起 | iOS 挂起 App 后连接断开 | APNs/FCM 推送通知 | 收到推送 → 点击 → 打开 App → 拉取消息 |
| 被 Kill | 完全断开 | APNs/FCM 推送通知 | 推送是唯一送达通道 |

**方案 A（推荐）：统一发推送，App 前台时静默处理。**

```javascript
// App 端：前台收到推送 → 静默处理（不弹通知），Realtime 自动更新对话流
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: false,    // 前台不弹通知
    shouldPlaySound: false,
    shouldSetBadge: false,
  }),
});
```

---

## 5. 迁移到 VPS 的触发条件

### 5.1 架构决策

**P0 阶段保持 Vercel Serverless + Supabase Pro，不迁移 VPS。**

理由：
- 500MB 瓶颈通过升级套餐解决，零迁移成本（点按钮即可）
- P0 时间应花在功能开发上，而非 3-5 周基础设施迁移
- 冷启动 + 60s 超时已有解决方案（`/health` 保热 + QStash 分块，均已实现）
- 月成本 $45-65 vs VPS $14-46，差额不值得迁移

### 5.2 迁移 VPS 触发条件（达到任一时评估）

| 触发条件 | 阈值 | 说明 |
|----------|------|------|
| DAU 增长 | > 5,000 | QStash 调用量大，成本上升 |
| 月基础设施成本 | > $100 | Serverless 成本优势消失 |
| 冷启动频繁 | 影响用户体验且 `/health` 保热不够用 | 用户体验受损 |
| 运维资源 | 有专职运维人员 | 具备自托管能力 |

### 5.3 VPS 迁移后的组件替换

| 原 SaaS | VPS 替代 | 成本 |
|---------|---------|------|
| Supabase Pro (PG+Auth+Storage) | Hetzner CPX41+CCX23 双机 + FastAPI JWT + Cloudflare R2 | $42+$2 |
| QStash Pro | Celery + Redis Beat | $0 |
| Upstash Redis | 本地 Redis | $0 |
| Axiom 监控 | Sentry Free + 文件日志 | $0 |
| Supabase 备份 | pg_dump cron → R2 | $0.50 |

### 5.4 迁移关键点

- **Auth 迁移**：Supabase Auth → 自建 JWT（约 8 天工作量），核心是 `user_id` UUID 保持不变，业务表零改动
- **Realtime 迁移**：已有 `app/core/websocket.py` 的 `ConnectionManager` 实现，取消注释 + 替换 Supabase Realtime 调用（约 2-3 天）
- **最大隐性成本**：DevOps 时间（初期搭建 20-40 小时，日常维护 2-5 小时/月）

---

## 6. 性能要求基线

| 指标 | 要求 | 对应功能 |
|------|------|---------|
| AI 回复消息（用户发后） | < 3 秒（含 LLM 调用，流式生成） | P0-F01, P0-F02 |
| AI 主动消息生成（后端异步） | 无实时要求（LLM 失败 fallback 模板） | P0-F01 |
| 情绪签到 AI 回复 | < 2 秒 | P0-F03 |
| 性格发现对话响应 | < 3 秒（含 LLM 调用） | P0-F05 |
| 记忆注入延迟 | 不增加对话响应 > 500ms | P0-F02 |
| 推送到达率（设备端） | 85-95% | P0-F01 |
| 星盘计算超时 | < 8000ms | 占星后端 |
| 每日运势 API 超时 | < 6000ms | 占星后端 |

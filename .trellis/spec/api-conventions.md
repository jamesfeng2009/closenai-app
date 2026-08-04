# API 规范

> 来源：ClosenAI 技术方案设计 v1.0 + PRD v2.0
> 状态：P0 阶段执行标准

---

## 1. RESTful 命名规范

### 1.1 URL 结构

```
基础路径: /api/v1

资源命名: 小写复数名词，kebab-case
  /api/v1/chat/messages          — 对话消息
  /api/v1/memories               — 记忆
  /api/v1/mood-checkins          — 情绪签到
  /api/v1/personality            — 性格画像
  /api/v1/growth/insights        — 成长洞察
  /api/v1/growth/timeline        — 成长时间线
  /api/v1/growth/metrics         — 成长指标
  /api/v1/push/send              — 推送发送
  /api/v1/auth/apple             — Apple 登录
  /api/v1/auth/google            — Google 登录
  /api/v1/auth/email/send-code   — 发送验证码
  /api/v1/auth/email/verify      — 验证码验证
```

### 1.2 HTTP 方法语义

| 方法 | 语义 | 示例 |
|------|------|------|
| GET | 获取资源（幂等） | `GET /api/v1/chat/messages?limit=20&before={msg_id}` |
| POST | 创建资源 / 触发动作 | `POST /api/v1/chat/messages/stream`（流式对话） |
| PUT | 全量更新资源（幂等） | `PUT /api/v1/profile` |
| PATCH | 部分更新资源 | `PATCH /api/v1/memories/{id}`（修改记忆状态） |
| DELETE | 删除资源（软删除） | `DELETE /api/v1/memories/{id}` |

### 1.3 查询参数规范

```
分页: ?limit=20&offset=0  或  ?limit=20&before={cursor_id}
排序: ?sort=-created_at    （- 前缀降序）
过滤: ?status=current&category=values
语言: ?lang=zh              （zh/en/id，默认跟随用户偏好）
```

---

## 2. SSE 流式对话接口设计

### 2.1 接口定义

```
POST /api/v1/chat/messages/stream
Content-Type: application/json
Authorization: Bearer {jwt_token}
```

**请求体：**

```json
{
  "content": "最近工作压力好大",
  "chat_id": "uuid-xxx",
  "context": {
    "mood_emoji": "😰",
    "is_mood_checkin": false
  }
}
```

**响应：SSE 流（Content-Type: text/event-stream）**

```
data: {"type": "user_saved", "message_id": "msg-001"}

data: {"type": "token", "content": "看到你选了焦虑。"}

data: {"type": "token", "content": "作为习惯用逻辑分析的人，"}

data: {"type": "token", "content": "允许自己感受就好。"}

data: {"type": "token", "content": "想聊聊是什么让你焦虑吗？"}

data: {"type": "ai_saved", "message_id": "msg-002", "full_content": "看到你选了焦虑。作为习惯用逻辑分析的人，允许自己感受就好。想聊聊是什么让你焦虑吗？"}

data: {"type": "done"}
```

### 2.2 SSE 事件类型

| type | 说明 | 携带数据 |
|------|------|---------|
| `user_saved` | 用户消息已存入 DB | `message_id` |
| `token` | LLM 流式输出的单个 token | `content` |
| `ai_saved` | AI 完整消息已存入 DB | `message_id`, `full_content` |
| `error` | 流式生成出错 | `error_code`, `message` |
| `done` | 流式结束 | 无 |

### 2.3 客户端处理

```typescript
// 前端 SSE 消费示例
const eventSource = new EventSource(
  `${API_BASE}/api/v1/chat/messages/stream`,
  { method: 'POST', body: JSON.stringify({ content, chat_id }) }
);

eventSource.addEventListener('token', (event) => {
  const data = JSON.parse(event.data);
  appendToCurrentMessage(data.content);  // 逐 token 追加显示
});

eventSource.addEventListener('done', () => {
  eventSource.close();
  markMessageComplete();
});

eventSource.addEventListener('error', (event) => {
  // LLM 失败 → 显示 "稍等，我想想..." 并重试
  showFallbackMessage();
  retryAfter(2000);
});
```

---

## 3. 错误处理格式

### 3.1 统一错误响应

所有 API 错误返回统一的 JSON 格式：

```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "今日 AI 消息配额已用完，请明天再来",
    "details": {
      "quota_used": 5,
      "quota_limit": 5,
      "reset_at": "2026-07-16T00:00:00Z"
    },
    "request_id": "req-uuid-xxx"
  }
}
```

### 3.2 错误码定义

| HTTP 状态码 | error.code | 说明 | 处理方式 |
|------------|-----------|------|---------|
| 400 | `VALIDATION_ERROR` | 请求参数校验失败 | 前端修正后重试 |
| 401 | `TOKEN_EXPIRED` | JWT 过期 | 用 refresh_token 刷新 |
| 401 | `TOKEN_INVALID` | JWT 无效 | 重新登录 |
| 403 | `QUOTA_EXCEEDED` | 免费配额用完 | 提示"明天再来"或升级 Pro |
| 403 | `AI_CONSENT_REQUIRED` | 未同意 AI 数据共享 | 弹出 5.1.2(i) 弹窗 |
| 404 | `RESOURCE_NOT_FOUND` | 资源不存在 | 前端处理空状态 |
| 409 | `CONFLICT` | 资源状态冲突（如重复签到） | 前端忽略或提示 |
| 422 | `LLM_GENERATION_FAILED` | LLM 生成失败（Gemini API 超时/限流/内容过滤） | 主动消息 fallback 模板；对话返回"稍等"重试 |
| 429 | `RATE_LIMIT_EXCEEDED` | 请求频率超限 | 指数退避重试 |
| 500 | `INTERNAL_ERROR` | 服务器内部错误 | 记录 request_id 报告 |

### 3.3 LLM 失败处理

```python
# 主动消息失败 → fallback 模板
async def generate_active_message(user: User) -> str:
    try:
        return await llm_service.chat(
            model="gemini-2.5-flash-lite",
            messages=build_prompt(user),
            timeout=30,
        )
    except (LLMTimeoutError, LLMError):
        # Fallback: Rule Engine 模板消息
        if user.day_number == 1:
            return "嗨！我是你的 AI 朋友，很高兴认识你！你周末更喜欢一个人待着，还是跟朋友出去？"
        return "早上好！今天感觉怎么样？有什么想聊的吗？"

# 实时对话失败 → "稍等" + 重试
async def stream_chat_message(user: User, content: str):
    try:
        async for chunk in llm_service.stream_chat(...):
            yield chunk
    except LLMError:
        yield "稍等，我想想..."
        # 指数退避重试 1 次
        await asyncio.sleep(1)
        async for chunk in llm_service.stream_chat(...):
            yield chunk
```

---

## 4. JWT Auth 方案

### 4.1 Token 结构

使用 HS256 对称加密，JWT 内嵌时区信息（替代 Supabase DB trigger）。

```python
from jose import jwt
import time

JWT_SECRET = os.environ["JWT_SECRET"]  # HS256 对称密钥

def create_access_token(user_id: str, timezone: str) -> str:
    """签发 Access Token，7 天过期"""
    payload = {
        "sub": user_id,
        "timezone": timezone,       # 内嵌时区，替代 Supabase DB trigger
        "iat": int(time.time()),
        "exp": int(time.time()) + 86400 * 7,  # 7 天过期
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def create_refresh_token(user_id: str) -> str:
    """签发 Refresh Token，30 天过期"""
    payload = {"sub": user_id, "exp": int(time.time()) + 86400 * 30}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def verify_token(token: str) -> dict:
    """验证 JWT，返回 payload"""
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
```

### 4.2 三种登录方式

| 登录方式 | 端点 | 流程 | 工作量 |
|----------|------|------|--------|
| Apple Sign In | `POST /api/v1/auth/apple` | 前端获取 Apple ID Token → FastAPI 验证（Apple 公钥 JWKS）→ 签发自家 JWT | ~3 天 |
| Google Sign In | `POST /api/v1/auth/google` | 前端获取 Google ID Token → FastAPI 验证（Google 公钥 JWKS）→ 签发自家 JWT | ~2 天 |
| Email 验证码 | `POST /api/v1/auth/email/send-code` + `POST /api/v1/auth/email/verify` | Resend/SES 发邮件 + Redis 存验证码（5 分钟过期）→ 验证成功 → 签发 JWT | ~2 天 |

### 4.3 认证中间件

```python
from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> dict:
    """JWT 认证依赖，所有需登录的接口使用"""
    try:
        payload = verify_token(credentials.credentials)
        return payload  # {"sub": user_id, "timezone": "...", "iat": ..., "exp": ...}
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "TOKEN_EXPIRED", "message": "JWT 已过期"}}
        )
    except jwt.JWTError:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "TOKEN_INVALID", "message": "JWT 无效"}}
        )
```

### 4.4 user_id 设计原则

- **使用 UUID，不使用自增 ID**——UUID 迁移后可直接沿用
- **user_id 是所有业务表的关联外键**——迁移 Auth 系统时业务表零改动
- **不在 auth 表中存业务数据**——业务数据只存 `public.profiles`，auth 表只用于认证

---

## 5. 请求/响应规范

### 5.1 通用请求头

```
Authorization: Bearer {jwt_token}
Content-Type: application/json
Accept-Language: zh|en|id
X-Client-Version: 1.0.0
X-Device-Timezone: Asia/Shanghai
```

### 5.2 通用响应结构

**单个资源：**

```json
{
  "data": {
    "id": "uuid-xxx",
    "type": "message",
    "content": "今天感觉怎么样？",
    "role": "assistant",
    "created_at": "2026-07-15T01:00:00Z",
    "is_ai_generated": true
  }
}
```

**列表资源：**

```json
{
  "data": [
    { "id": "uuid-1", "content": "...", "role": "user" },
    { "id": "uuid-2", "content": "...", "role": "assistant" }
  ],
  "pagination": {
    "limit": 20,
    "has_more": true,
    "next_cursor": "uuid-1"
  }
}
```

### 5.3 AI 消息标识规范

所有 AI 生成的消息必须包含 `is_ai_generated: true` 字段，前端据此标注"AI 生成"标签（App Store 合规要求）。

```json
{
  "data": {
    "id": "msg-002",
    "type": "message",
    "content": "看到你选了焦虑。作为习惯用逻辑分析的人，允许自己感受就好。",
    "role": "assistant",
    "is_ai_generated": true,
    "created_at": "2026-07-15T01:00:05Z"
  }
}
```

### 5.4 推送接口

```
POST /api/v1/push/send
Authorization: Bearer {internal_service_token}
```

```json
{
  "user_id": "uuid-xxx",
  "title": "ClosenAI",
  "body": "ClosenAI 给你发了一条消息",
  "data": {
    "type": "ai_message",
    "chat_id": "uuid-xxx"
  }
}
```

**P0 推送文案固定**：`"ClosenAI 给你发了一条消息"`（P1 升级为 AI 生成个性化文案）。

### 5.5 Logout/注销推送处理

```python
# Logout 时清除 push_token
# UPDATE users SET push_token = NULL WHERE id = ?
# Cron Job 发送前检查 push_token IS NOT NULL，为空则跳过推送

# 注销账号时：
# 1. 清除 push_token
# 2. 标记 is_active = false
# 3. Cron Job 检查 is_active = true，为 false 则不生成消息也不发推送
# 4. 已生成消息保留在 DB，用户重新登录后可查看历史消息
```

---

## 6. 时间与时区规范（出海强制）

### 6.1 三条铁律

| 层 | 规则 | 实现 |
|----|------|------|
| **存储** | 一律 UTC，列类型必须 `timestamptz`（`DateTime(timezone=True)`），禁止 naive `DateTime` | SQLAlchemy models 全部 `timezone=True`，PostgreSQL 内部以 UTC 存储 |
| **传输** | API 序列化一律 ISO 8601 显式 UTC（`2026-07-15T01:00:00Z`），禁止无时区后缀的字符串 | Pydantic 默认序列化 tz-aware datetime 即带偏移 |
| **展示** | 前端渲染时按设备本地时区转换，后端不输出"格式化好的本地时间字符串" | RN `new Date(iso)` 天然按设备时区 |

> 注意："不带时区" ≠ "以 UTC 为准"。naive datetime 会被服务器/客户端本地时区静默污染，是出海应用最常见的数据事故源。正确做法是**显式 UTC**（带 `Z`/`+00:00`）。

### 6.2 业务日边界：唯一例外用"用户本地时区"

- "今日"语义（每日配额重置、streak 连续打卡、主动消息 9:00 触发、每日洞察）**必须按用户本地时区**计算，禁用服务器时区/UTC 日期
- 统一走 `backend/core/time_utils.py` 的 `local_today(tz)` / `local_now(tz)`，禁止业务代码直接 `date.today()` / `datetime.now()`（无参）
- `users.timezone` 存 **IANA 时区名**（`Asia/Shanghai`、`America/New_York`），禁止 `"UTC+8"` 偏移格式（`zoneinfo` 无法解析会静默回退 UTC）
- 前端启动时上报一次设备时区：`Intl.DateTimeFormat().resolvedOptions().timeZone` → `PATCH /user/settings {timezone}`；用户跨时区旅行时下次启动自动纠正

### 6.3 检查清单（新增时间字段/功能时）

- [ ] 新列是 `DateTime(timezone=True)` 而非 naive `DateTime`
- [ ] API 响应示例带 `Z` 后缀
- [ ] 涉及"今日/每日"逻辑走 `time_utils`，参数来自 `users.timezone`
- [ ] Cron 按用户时区分组触发，而非固定 UTC 时刻全量发

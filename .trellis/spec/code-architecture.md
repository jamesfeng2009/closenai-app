# 代码架构规范

> 来源：ClosenAI 技术方案设计 v1.0 + PRD v2.0
> 状态：P0 阶段执行标准

---

## 1. 前后端分离原则

### 1.1 架构边界

```
┌─────────────────────────┐     ┌─────────────────────────────────┐
│      Frontend            │     │           Backend                │
│  React Native (Expo)     │     │       FastAPI (async)            │
│                          │     │                                  │
│  - UI 渲染               │◀───▶│  - RESTful API (/api/v1/*)       │
│  - 状态管理              │ HTTP│  - SSE 流式响应                   │
│  - 本地缓存              │  SSE│  - JWT 认证                      │
│  - Supabase Realtime     │     │  - 业务逻辑                      │
│    订阅                   │     │  - LLM 调用编排                   │
│                          │     │  - Cron Job / QStash 任务         │
└─────────────────────────┘     └─────────────────────────────────┘
```

### 1.2 职责划分

| 层面 | 前端职责 | 后端职责 |
|------|---------|---------|
| 数据获取 | 调用 API 获取数据，Supabase Realtime 订阅 | 提供 RESTful API，管理数据库 |
| 业务逻辑 | 仅 UI 交互逻辑（过渡动画、空状态展示） | 所有业务逻辑（消息生成、记忆管理、成本分层） |
| LLM 调用 | 禁止前端直接调用 LLM API | 所有 LLM 调用在后端编排（成本控制、Prompt 安全） |
| 认证 | 存储 JWT，携带 Authorization 头 | JWT 签发与验证 |
| 推送 | 请求权限、获取 Token、处理推送点击 | 发送 APNs/FCM 推送 |
| 成本控制 | 无感知 | Rule Engine 70% / LLM 30% 分层（后端决策） |

### 1.3 API 契约原则

- 前端不依赖后端内部实现，仅依赖 API 契约（请求/响应格式）
- 后端 API 变更需保持向后兼容（新增字段不破坏旧版本）
- 使用 OpenAPI Schema 自动生成 API 文档（FastAPI 原生支持）

---

## 2. 后端分层架构

### 2.1 目录结构

```
app/
├── api/                          # 路由层（HTTP 入口）
│   ├── v1/
│   │   ├── __init__.py
│   │   ├── auth.py               # /api/v1/auth/*
│   │   ├── chat.py               # /api/v1/chat/*
│   │   ├── memories.py           # /api/v1/memories/*
│   │   ├── mood_checkins.py      # /api/v1/mood-checkins/*
│   │   ├── personality.py        # /api/v1/personality/*
│   │   ├── growth.py             # /api/v1/growth/*
│   │   ├── profile.py            # /api/v1/profile
│   │   └── push.py               # /api/v1/push/*
│   └── deps.py                   # 依赖注入（get_current_user 等）
│
├── services/                     # 服务层（业务逻辑）
│   ├── chat_service.py           # 对话消息管理
│   ├── memory_service.py         # 记忆检索与注入
│   ├── llm_service.py            # LLM 调用封装（流式/非流式）
│   ├── personality_service.py    # 性格画像管理
│   ├── mood_service.py           # 情绪签到
│   ├── growth_service.py         # 成长洞察生成
│   ├── active_message_service.py # 主动消息生成（Rule Engine + LLM 分层）
│   ├── push_service.py           # APNs/FCM 推送
│   ├── prompt_builder.py         # Prompt 构建器
│   └── rule_engine.py            # Rule Engine（模板消息生成）
│
├── repositories/                 # 数据访问层（数据库操作）
│   ├── base_repository.py        # 基类（CRUD 通用方法）
│   ├── chat_repository.py        # chat_messages 表操作
│   ├── memory_repository.py      # memories 表操作 + pgvector 查询
│   ├── mood_repository.py        # mood_checkins 表操作
│   ├── personality_repository.py # personality 表操作
│   ├── growth_repository.py      # growth_records 表操作
│   └── user_repository.py        # profiles / users 表操作
│
├── models/                       # 数据模型层（SQLAlchemy ORM）
│   ├── base.py                   # SQLAlchemy Base
│   ├── user.py                   # User / Profile
│   ├── chat.py                   # ChatMessage
│   ├── memory.py                 # Memory
│   ├── mood.py                   # MoodCheckin
│   ├── personality.py            # PersonalityProfile
│   └── growth.py                 # GrowthRecord / GrowthMetric
│
├── schemas/                      # Pydantic 请求/响应模型
│   ├── chat.py
│   ├── memory.py
│   ├── mood.py
│   ├── personality.py
│   ├── growth.py
│   └── common.py                 # 通用响应/错误模型
│
├── core/                         # 核心基础设施
│   ├── config.py                 # 环境配置
│   ├── security.py               # JWT 签发与验证
│   ├── database.py               # 数据库连接池
│   ├── redis.py                  # Redis 连接
│   ├── websocket.py              # ConnectionManager（VPS 迁移时启用）
│   └── logging.py                # 日志配置
│
└── main.py                       # FastAPI 应用入口
```

### 2.2 层间依赖规则

```
api/ (路由层)
  ↓ 依赖注入
services/ (服务层)
  ↓ 调用
repositories/ (数据访问层)
  ↓ 操作
models/ (ORM 模型) → Database

schemas/ (Pydantic 模型) — 被所有层引用，定义数据契约
core/ (基础设施) — 被所有层引用，提供配置/安全/连接
```

**禁止反向依赖**：repositories 不能调用 services，services 不能调用 api。

### 2.3 路由层示例

```python
# app/api/v1/chat.py
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from app.api.deps import get_current_user
from app.schemas.chat import ChatMessageRequest, ChatMessageResponse
from app.services.chat_service import ChatService

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

@router.post("/messages/stream")
async def stream_message(
    request: ChatMessageRequest,
    user: dict = Depends(get_current_user),
    chat_service: ChatService = Depends(),
):
    """路由层：仅做参数接收、认证、调用 service、返回响应"""
    return StreamingResponse(
        chat_service.stream_message(user["sub"], request),
        media_type="text/event-stream",
    )
```

### 2.4 服务层示例

```python
# app/services/chat_service.py
from typing import AsyncGenerator
from app.repositories.chat_repository import ChatRepository
from app.repositories.memory_repository import MemoryRepository
from app.services.llm_service import LLMService
from app.services.prompt_builder import PromptBuilder

class ChatService:
    def __init__(
        self,
        chat_repo: ChatRepository,
        memory_repo: MemoryRepository,
        llm_service: LLMService,
        prompt_builder: PromptBuilder,
    ):
        self.chat_repo = chat_repo
        self.memory_repo = memory_repo
        self.llm_service = llm_service
        self.prompt_builder = prompt_builder

    async def stream_message(
        self, user_id: str, request: ChatMessageRequest
    ) -> AsyncGenerator[str, None]:
        """服务层：编排业务逻辑（存消息→注入记忆→LLM流式→存AI消息）"""
        # 1. 存储用户消息
        await self.chat_repo.save_user_message(user_id, request.content)

        # 2. 记忆注入
        memories = await self.memory_repo.retrieve_relevant(
            user_id, request.content, limit=5
        )

        # 3. 构建 Prompt
        system_prompt = self.prompt_builder.build_chat_prompt(memories)

        # 4. LLM 流式生成
        full_response = ""
        async for chunk in self.llm_service.stream_chat(
            model="gemini-2.5-flash",
            system=system_prompt,
            user_msg=request.content,
        ):
            full_response += chunk
            yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"

        # 5. 保存完整 AI 消息
        await self.chat_repo.save_ai_message(user_id, full_response)
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
```

### 2.5 数据访问层示例

```python
# app/repositories/memory_repository.py
from app.repositories.base_repository import BaseRepository
from app.models.memory import Memory
from sqlalchemy.ext.asyncio import AsyncSession

class MemoryRepository(BaseRepository[Memory]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Memory)

    async def retrieve_relevant(
        self, user_id: str, query_text: str, limit: int = 5
    ) -> list[Memory]:
        """pgvector 语义搜索，范围包含 current + superseded"""
        query_embedding = await self._get_embedding(query_text)
        result = await self.session.execute(
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.status.in_(["current", "superseded"]),
                Memory.deleted == False,
            )
            .order_by(Memory.embedding.cosine_distance(query_embedding))
            .limit(limit)
        )
        return result.scalars().all()
```

---

## 3. 开闭原则（扩展开放，修改关闭）

### 3.1 Rule Engine 扩展点

消息生成采用 Rule Engine + LLM 分层架构（70% / 30%）。新增消息类型时，只需新增模板，不修改现有逻辑。

```python
# app/services/rule_engine.py
from abc import ABC, abstractmethod

class MessageStrategy(ABC):
    """消息生成策略接口——新增类型只需实现此接口"""
    @abstractmethod
    async def generate(self, user: User, context: dict) -> str:
        pass

class GreetingStrategy(MessageStrategy):
    """早安问候——模板 + 变量填充"""
    async def generate(self, user, context):
        template = random.choice(GREETING_TEMPLATES)
        return template.format(
            name=user.nickname,
            streak=context["streak_count"],
            personality=user.personality_type,
        )

class MoodCheckinStrategy(MessageStrategy):
    """情绪签到回复——情绪映射模板"""
    async def generate(self, user, context):
        mood = context["mood_emoji"]
        templates = MOOD_TEMPLATES[mood]  # 每种情绪 10+ 模板
        return random.choice(templates).format(personality=user.personality_type)

class LLMDeepStrategy(MessageStrategy):
    """深度消息——调用 LLM"""
    async def generate(self, user, context):
        return await self.llm_service.chat(
            model="gemini-2.5-flash-lite",
            messages=self.prompt_builder.build_active_message_prompt(user, context),
        )

class ActiveMessageService:
    """主动消息服务——通过策略模式编排，新增类型不改此类"""
    def __init__(self):
        self.strategies = {
            "greeting": GreetingStrategy(),
            "mood_checkin": MoodCheckinStrategy(),
            "llm_deep": LLMDeepStrategy(),
        }

    async def generate(self, user, context) -> str:
        strategy_type = self._decide_strategy(user, context)
        strategy = self.strategies[strategy_type]
        try:
            return await strategy.generate(user, context)
        except LLMError:
            # LLM 失败 → fallback 到 Rule Engine 模板
            return await self.strategies["greeting"].generate(user, context)
```

### 3.2 LLM 服务扩展点

```python
# app/services/llm_service.py
class LLMProvider(ABC):
    """LLM 提供商接口——新增提供商只需实现此接口"""
    @abstractmethod
    async def chat(self, model: str, messages: list, **kwargs) -> str:
        pass

    @abstractmethod
    async def stream_chat(self, model: str, messages: list, **kwargs) -> AsyncGenerator:
        pass

class GeminiProvider(LLMProvider):
    """Google Gemini 系列实现（通过 OpenAI 兼容端点）"""
    def __init__(self):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=os.environ["GEMINI_API_KEY"],
        )

    async def chat(self, model, messages, **kwargs):
        response = await self.client.chat.completions.create(
            model=model, messages=messages, **kwargs
        )
        return response.choices[0].message.content

    async def stream_chat(self, model, messages, **kwargs):
        stream = await self.client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

class DoubaoProvider(LLMProvider):
    """豆包模型实现——未来扩展，不修改现有代码"""
    async def chat(self, model, messages, **kwargs):
        # 豆包 API 调用实现
        pass
```

---

## 4. 职责单一原则

### 4.1 Service 职责划分

每个 Service 只负责一个业务领域的逻辑：

| Service | 唯一职责 | 不应做的事 |
|---------|---------|-----------|
| `ChatService` | 对话消息的生成、存储、流式推送 | 不直接操作 LLM API（通过 LLMService） |
| `MemoryService` | 记忆的提取、存储、检索、注入 | 不直接构建 Prompt（通过 PromptBuilder） |
| `LLMService` | LLM API 调用封装（流式/非流式/重试） | 不包含业务逻辑（不知道"记忆"是什么） |
| `PromptBuilder` | Prompt 模板构建（记忆+性格+洞察拼接） | 不调用 LLM，不操作数据库 |
| `PushService` | APNs/FCM 推送发送 | 不生成消息内容（仅发送已生成的内容） |
| `ActiveMessageService` | 主动消息的生成编排（Rule Engine vs LLM 决策） | 不直接生成模板（通过 RuleEngine） |
| `GrowthService` | 成长洞察生成、成长指标计算 | 不管理对话消息 |

### 4.2 Repository 职责

每个 Repository 只负责一个表的数据访问：

- 只做 CRUD 操作和查询，不包含业务逻辑
- pgvector 语义搜索封装在 `MemoryRepository` 中
- 分页、过滤、排序逻辑封装在 `BaseRepository` 中

---

## 5. 依赖注入模式

### 5.1 FastAPI 依赖注入

```python
# app/api/deps.py
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_session
from app.core.security import verify_token
from app.repositories.chat_repository import ChatRepository
from app.repositories.memory_repository import MemoryRepository
from app.services.chat_service import ChatService
from app.services.llm_service import LLMService
from app.services.prompt_builder import PromptBuilder

async def get_current_user(
    token: str = Depends(oauth2_scheme),
) -> dict:
    payload = verify_token(token)
    return payload

# Repository 依赖
async def get_chat_repository(
    session: AsyncSession = Depends(get_session),
) -> ChatRepository:
    return ChatRepository(session)

async def get_memory_repository(
    session: AsyncSession = Depends(get_session),
) -> MemoryRepository:
    return MemoryRepository(session)

# Service 依赖（注入 Repository 和其他 Service）
async def get_chat_service(
    chat_repo: ChatRepository = Depends(get_chat_repository),
    memory_repo: MemoryRepository = Depends(get_memory_repository),
    llm_service: LLMService = Depends(),
    prompt_builder: PromptBuilder = Depends(),
) -> ChatService:
    return ChatService(
        chat_repo=chat_repo,
        memory_repo=memory_repo,
        llm_service=llm_service,
        prompt_builder=prompt_builder,
    )
```

### 5.2 路由中使用依赖注入

```python
# app/api/v1/chat.py
@router.post("/messages/stream")
async def stream_message(
    request: ChatMessageRequest,
    user: dict = Depends(get_current_user),
    chat_service: ChatService = Depends(get_chat_service),  # 注入完整 Service
):
    return StreamingResponse(
        chat_service.stream_message(user["sub"], request),
        media_type="text/event-stream",
    )
```

### 5.3 依赖注入的优势

1. **可测试性**：单元测试时可注入 Mock Repository/Service
2. **可替换性**：LLMService 从 Gemini 切换到豆包时，只需替换 Provider 实现
3. **松耦合**：路由层不直接实例化 Service，Service 不直接实例化 Repository
4. **生命周期管理**：FastAPI 自动管理依赖的生命周期（如数据库 Session 的创建和关闭）

```python
# 单元测试示例
async def test_chat_service_with_mock():
    mock_repo = MockChatRepository()
    mock_llm = MockLLMService()
    mock_llm.stream_chat.return_value = async_iter(["Hello", " world"])

    service = ChatService(
        chat_repo=mock_repo,
        memory_repo=MockMemoryRepository(),
        llm_service=mock_llm,
        prompt_builder=MockPromptBuilder(),
    )

    chunks = []
    async for chunk in service.stream_message("user-1", request):
        chunks.append(chunk)

    assert "Hello" in "".join(chunks)
```

---

## 6. Auth 接口抽象（降低迁移成本）

### 6.1 集中 Auth 逻辑

后端 auth 相关逻辑集中在一个 service 文件中，迁移时只改这一个文件。

```python
# app/services/auth_service.py
class AuthService:
    """Auth 服务——迁移 VPS 时只修改此类"""

    async def verify_apple_token(self, id_token: str) -> dict:
        """验证 Apple ID Token（Apple 公钥 JWKS）"""
        # 当前：调 Supabase Auth
        # 迁移后：直接验证 Apple JWKS
        pass

    async def verify_google_token(self, id_token: str) -> dict:
        """验证 Google ID Token（Google 公钥 JWKS）"""
        pass

    async def send_email_code(self, email: str) -> None:
        """发送验证码邮件 + Redis 存验证码（5 分钟过期）"""
        pass

    async def verify_email_code(self, email: str, code: str) -> bool:
        """验证邮箱验证码"""
        pass

    def create_jwt(self, user_id: str, timezone: str) -> str:
        """签发自家 JWT"""
        return create_access_token(user_id, timezone)
```

### 6.2 迁移最佳实践

- **不要在 auth 表中存业务数据**——业务数据只存 `public.profiles`
- **user_id 用 UUID**——迁移后可直接沿用
- **不要深度依赖 Supabase Auth 的特殊功能**——如 DB trigger 同步时区
- **做好 Auth 接口抽象**——迁移时只改 `auth_service.py` 一个文件

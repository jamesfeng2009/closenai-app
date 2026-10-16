"""
对话业务编排 (P0-F01)。

承载 POST /chat/stream 的阶段一事务编排（配额校验 + 扣减 + 用户消息落库），
使 router 层只做传输（SSE 流），不持有事务边界（四层架构：routers → services → repositories）。

R3 起还多一件事：`rpc_chat_stream` / `validate_stream_input` 是 gRPC `StreamChat`
的服务端底座。`backend/ai/rpc/server.py` 的边界闸不许它自己碰数据库
（见 `backend/tests/test_ai_boundary.py`），所以「自带 session 的只生成链」放在这一层。
"""
import logging
from typing import AsyncGenerator, Tuple

from backend.core.errors import AppException, ErrorCode
from backend.database import SessionLocal
from backend.models.schemas import ChatStreamRequest
from backend.repositories import chat_repository, user_repository
from backend.services.message_generator import message_generator
from backend.services.quota_service import quota_service

logger = logging.getLogger("closenai.chat")


class ChatService:
    async def prepare_chat_turn(
        self, user_id: str, content: str, message_type: str
    ) -> None:
        """阶段一（独立短事务，持有用户行锁）：配额检查 + 扣减 + 写入用户消息 → commit 释放锁。

        B6 说明：拆事务是为避免 FOR UPDATE 行锁横跨 LLM 流式生成（3-10s）阻塞同用户
        并发；用独立 session 而非在请求级 db 上中途 commit，保住 get_db()「请求结束统一
        commit/rollback」的事务契约。配额校验+扣减（写用户消息+累加 chat_count）在同一
        事务内原子完成。若阶段二 LLM 失败，用户消息保留、配额仍计数（用户已发起请求，
        消耗一次对话轮数是合理语义）。

        Raises:
            AppException(QUOTA_EXCEEDED)：配额超限，由 router 转换为 SSE error 事件
                （保持客户端 SSE 错误处理契约）。
        """
        async with SessionLocal() as session:
            try:
                user = await user_repository.require_user(
                    session, user_id, for_update=True
                )
                # 配额检查（B2）：对话入口只受对话轮数约束，统一池耗尽不阻断对话
                if await quota_service.is_chat_quota_exceeded(session, user):
                    raise AppException(
                        ErrorCode.QUOTA_EXCEEDED,
                        "今天的次数已经用完，请明天再来 / Daily chat limit reached, please come back tomorrow",
                    )
                # 保存用户消息（并累计聊天次数，首页"聊天次数"展示）
                await chat_repository.create(
                    session, user_id, "user", content, message_type=message_type
                )
                await user_repository.increment_chat_count(session, user_id)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    def validate_stream_input(self, content: str, message_type: str) -> ChatStreamRequest:
        """R3 gRPC `StreamChat` 的入参校验：复用 HTTP 的同一个 Pydantic 模型。

        4000 上限与 message_type 取值面都不许在 RPC handler 里重抄一遍
        （`closenai_ai_rpc.proto:207-208` 逐字「重抄＝两处会漂」）。
        空 message_type 用模型的 Field 默认（reply），与 HTTP 缺省同形。

        Raises:
            pydantic.ValidationError：由 handler 翻成 `INVALID_ARGUMENT`。
        """
        return ChatStreamRequest(content=content, message_type=message_type or "reply")

    async def rpc_chat_stream(
        self, user_id: str, content: str, message_type: str, language: str
    ) -> AsyncGenerator[Tuple[str, object], None]:
        """R3 gRPC `StreamChat` 的生成链：自带 session，只产内容事件。

        产出 `("memory", [dict])`（有记忆时一次，条数与 HTTP 的 `memories[:3]` 同形）
        与 `("token", str)`（每个生成块），收尾由 handler 发 `DONE` 块。

        与 HTTP 面（`routers/chat.py:99-130`）的**刻意差异**，三条都归 R3b：
        不做 consent 前置（`:73`）、不做配额与用户消息落库（`:78-80`）、
        流后不落 AI 回复／不抽取记忆／不记承诺（`:121-130`）——那些是权威态写入，
        按 O3 的裁决归 Go。本函数保证的是「同一个生成器、同一份 prompt 组装」，
        于是两条路径的文案不会分叉。

        末尾的 commit 只为一件事：`generate_chat_stream` 的危机分支会写一行
        `safety_events` 审计（`message_generator.py:88-102`，规范 §二 2.3）。
        不 commit 就等于让危机信号静默消失——那是比多写一行审计表更坏的取舍。
        """
        async with SessionLocal() as session:
            try:
                memories = await message_generator.get_memory_context(session, user_id, content)
                if memories:
                    yield "memory", memories[:3]
                async for text in message_generator.generate_chat_stream(
                    db=session,
                    user_id=user_id,
                    user_input=content,
                    message_type=message_type,
                    language=language,
                ):
                    yield "token", text
                await session.commit()
            except Exception:
                await session.rollback()
                raise


chat_service = ChatService()

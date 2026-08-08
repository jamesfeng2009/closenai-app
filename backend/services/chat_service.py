"""
对话业务编排 (P0-F01)。

承载 POST /chat/stream 的阶段一事务编排（配额校验 + 扣减 + 用户消息落库），
使 router 层只做传输（SSE 流），不持有事务边界（四层架构：routers → services → repositories）。
"""
import logging

from backend.core.errors import AppException, ErrorCode
from backend.database import SessionLocal
from backend.repositories import chat_repository, user_repository
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


chat_service = ChatService()

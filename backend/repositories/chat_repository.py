"""
对话消息数据访问层（P0-F01）。

职责：只与 chat_messages 表交互，不含业务逻辑。
- 对话历史查询 / 主动消息写入
"""
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.time_utils import local_day_start_utc
from backend.models.chat import ChatMessage


class ChatRepository:
    """chat_messages 表数据访问。"""

    async def create(
        self,
        db: AsyncSession,
        user_id: str,
        role: str,
        content: str,
        message_type: str = "reply",
        ai_generated: bool = False,
        memory_ids: Optional[List[str]] = None,
    ) -> ChatMessage:
        """写入一条消息。"""
        message = ChatMessage(
            id=str(uuid4()),
            user_id=user_id,
            message_type=message_type,
            role=role,
            content=content,
            ai_generated=ai_generated,
            memory_ids=",".join(memory_ids) if memory_ids else None,
        )
        db.add(message)
        await db.flush()
        return message

    async def get_history(
        self, db: AsyncSession, user_id: str, limit: int = 20
    ) -> List[ChatMessage]:
        """查询最近对话历史（按时间倒序）。"""
        result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.user_id == user_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_today(
        self,
        db: AsyncSession,
        user_id: str,
        role: Optional[str] = None,
        message_type: Optional[str] = None,
        start: Optional[datetime] = None,
    ) -> int:
        """统计用户今日消息条数（用于配额判断）。

        `start` 应为用户本地今日 00:00 的 tz-aware UTC 时间
        （调用方用 time_utils.local_day_start_utc 计算）；缺省回退 UTC 当日起点。
        role/message_type 可选过滤。
        """
        start = start or local_day_start_utc(None)
        stmt = select(func.count(ChatMessage.id)).where(
            ChatMessage.user_id == user_id, ChatMessage.created_at >= start
        )
        if role:
            stmt = stmt.where(ChatMessage.role == role)
        if message_type:
            stmt = stmt.where(ChatMessage.message_type == message_type)
        result = await db.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_total(self, db: AsyncSession, user_id: str) -> int:
        """统计用户累计消息条数。"""
        result = await db.execute(
            select(func.count(ChatMessage.id)).where(ChatMessage.user_id == user_id)
        )
        return int(result.scalar_one() or 0)

    async def get_last_user_message(
        self, db: AsyncSession, user_id: str
    ) -> Optional[ChatMessage]:
        """查询用户最近一条主动发送的消息（用于连续未回复天数计算）。"""
        result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.user_id == user_id, ChatMessage.role == "user")
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


chat_repository = ChatRepository()
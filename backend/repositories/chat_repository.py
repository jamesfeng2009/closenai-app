"""
对话消息数据访问层（P0-F01）。

职责：只与 chat_messages 表交互，不含业务逻辑。
- 对话历史查询 / 主动消息写入
"""
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
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
        idempotency_key: Optional[str] = None,
    ) -> ChatMessage:
        """写入一条消息（低层写入，撞唯一约束会抛）。

        主动消息的幂等写入请用 `create_idempotent`；这里保持"抛"的语义，
        因为对话回复等路径本就该让错误浮出来。
        """
        message = ChatMessage(
            id=str(uuid4()),
            user_id=user_id,
            message_type=message_type,
            role=role,
            content=content,
            ai_generated=ai_generated,
            memory_ids=",".join(memory_ids) if memory_ids else None,
            idempotency_key=idempotency_key,
        )
        db.add(message)
        await db.flush()
        return message

    async def create_idempotent(
        self,
        db: AsyncSession,
        user_id: str,
        role: str,
        content: str,
        message_type: str,
        idempotency_key: str,
        ai_generated: bool = False,
        memory_ids: Optional[List[str]] = None,
    ) -> tuple[ChatMessage, bool]:
        """带幂等键写入；返回 (行, 是否本次新建)。

        撞 uq_chat_messages_proactive_key（并发双写 / QStash 重试 / 多实例）时回滚到
        savepoint 并返回既有行、created=False——调用方据此判定「已发送」，
        不能抛 500（Baseline 不变量 9：重复触达是幂等成功，不是故障）。
        """
        try:
            async with db.begin_nested():
                message = await self.create(
                    db,
                    user_id,
                    role,
                    content,
                    message_type=message_type,
                    ai_generated=ai_generated,
                    memory_ids=memory_ids,
                    idempotency_key=idempotency_key,
                )
            return message, True
        except IntegrityError:
            existing = await self.get_by_idempotency_key(db, idempotency_key)
            if existing is None:
                raise
            return existing, False

    async def get_by_idempotency_key(
        self, db: AsyncSession, idempotency_key: str
    ) -> Optional[ChatMessage]:
        result = await db.execute(
            select(ChatMessage).where(ChatMessage.idempotency_key == idempotency_key)
        )
        return result.scalars().first()

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

    async def count_since(
        self, db: AsyncSession, user_id: str, start: datetime, role: Optional[str] = None
    ) -> int:
        """统计 start 之后的消息条数（role 可选过滤；用于画像刷新新数据检测）。"""
        stmt = select(func.count(ChatMessage.id)).where(
            ChatMessage.user_id == user_id, ChatMessage.created_at >= start
        )
        if role:
            stmt = stmt.where(ChatMessage.role == role)
        result = await db.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_between(
        self, db: AsyncSession, user_id: str, start: datetime, end: datetime
    ) -> int:
        """统计时间窗口 [start, end) 内的消息条数（单日摘要卡片对话数）。"""
        result = await db.execute(
            select(func.count(ChatMessage.id)).where(
                ChatMessage.user_id == user_id,
                ChatMessage.created_at >= start,
                ChatMessage.created_at < end,
            )
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

    async def list_recent_activity(
        self, db: AsyncSession, user_id: str, start: datetime, limit: int = 200
    ) -> List[datetime]:
        """查询 start 之后用户消息的发送时间（role=user，用于活跃时段统计）。"""
        result = await db.execute(
            select(ChatMessage.created_at)
            .where(
                ChatMessage.user_id == user_id,
                ChatMessage.role == "user",
                ChatMessage.created_at >= start,
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_user_contents(
        self,
        db: AsyncSession,
        user_id: str,
        start: datetime,
        end: datetime,
        limit: int = 50,
    ) -> List[str]:
        """查询时间窗口 [start, end) 内用户消息内容（role=user，按时间倒序截断）。

        用于渐变漂移周检测的消息窗口向量化。
        """
        result = await db.execute(
            select(ChatMessage.content)
            .where(
                ChatMessage.user_id == user_id,
                ChatMessage.role == "user",
                ChatMessage.created_at >= start,
                ChatMessage.created_at < end,
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
        return [c for c in result.scalars().all() if c]


chat_repository = ChatRepository()
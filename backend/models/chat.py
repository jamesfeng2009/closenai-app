"""
对话消息表模型。

对应 P0-F01 主动消息 + 实时对话。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base

# 部分唯一索引的谓词（T5-03）：迁移件 c3e5a7b9d1f2 里写的是同一句，改动要两边同步。
_PROACTIVE_KEY_PREDICATE = text("message_type = 'proactive' AND idempotency_key IS NOT NULL")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    # 消息类型: proactive | reply | mood_checkin | discovery | follow_up
    message_type: Mapped[str] = mapped_column(String(20), default="reply")
    role: Mapped[str] = mapped_column(String(10))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    # 关联记忆上下文（用于可追溯）
    memory_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 幂等键（T5-03）：主动消息为 `proactive:<user_id>:<本地日>:<slot>`，
    # 其余写入一律 NULL（不参与唯一冲突）。键由调用方给，仓库层只负责撞约束时视为已发送。
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # 部分唯一索引：只管 proactive 且已带键的行——对话回复一天几十条，全表唯一等于把功能打死。
    # 迁移件 c3e5a7b9d1f2 建的是同一条索引，两边名字必须一致（test_idempotency_indexes.py 对账）。
    # 条件按方言各写一遍：SQLAlchemy 的 Index 没有通用 .where()，只有 dialect 前缀参数。
    __table_args__ = (
        Index(
            "uq_chat_messages_proactive_key",
            "idempotency_key",
            unique=True,
            postgresql_where=_PROACTIVE_KEY_PREDICATE,
            sqlite_where=_PROACTIVE_KEY_PREDICATE,
        ),
    )
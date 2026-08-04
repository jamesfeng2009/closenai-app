"""
记忆表模型（长期记忆，含 pgvector）。

对应 P0-F02 长期记忆：信念演变追踪、渐变漂移、回归识别。
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import HALFVEC

from backend.database import Base


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    category: Mapped[str] = mapped_column(
        String(20), default="factual"
    )  # values | relationship | goal | preference | factual
    content: Mapped[str] = mapped_column(Text)
    # 记忆状态: current | superseded | reverted
    status: Mapped[str] = mapped_column(String(20), default="current")
    # 置信度 0.0 - 1.0
    conviction_score: Mapped[float] = mapped_column(Float, default=0.5)
    # 来源: self_reflection | ai_inference | quoted_other
    source: Mapped[str] = mapped_column(String(20), default="self_reflection")
    # 向量嵌入（512 维 halfvec，DashScope text-embedding-v4；halfvec 存储/索引空间减半，Supabase 免费版性价比最优）
    embedding: Mapped[list[float] | None] = mapped_column(HALFVEC(512), nullable=True)
    # 渐变漂移分数
    drift_score: Mapped[float] = mapped_column(Float, default=0.0)
    # 情绪上下文
    emotion_context: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 版本化字段
    superseded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reverted_from: Mapped[str | None] = mapped_column(String(36), nullable=True)
    transition_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
"""
成长记录表模型。

对应 P0-F07 成长记录：每日洞察、周报、模式发现。
"""
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class GrowthRecord(Base):
    __tablename__ = "growth_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    # 记录类型: daily_insight | weekly_summary | pattern_discovery
    record_type: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    # 关联记忆 ID（JSON 数组字符串）
    related_memory_ids: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
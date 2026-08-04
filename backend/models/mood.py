"""
情绪签到表模型。

对应 P0-F03 情绪签到。
"""
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class MoodCheckin(Base):
    __tablename__ = "mood_checkins"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    mood_emoji: Mapped[str] = mapped_column(String(10))
    mood_label: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ai_interpretation: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
"""
情绪签到表模型。

对应 P0-F03 情绪签到。
"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class MoodCheckin(Base):
    __tablename__ = "mood_checkins"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    mood_emoji: Mapped[str] = mapped_column(String(10))
    mood_label: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ai_interpretation: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 用户本地日历日（写入时由 service 按 users.timezone 计算）。
    # 与 user_id 组成唯一约束：同一用户同一本地日仅一条签到，
    # 是 create_once_today 并发双写 IntegrityError 兜底的真实防线。
    # 可空：历史行由迁移按 created_at 的 UTC 日期近似回填；NULL 不参与唯一冲突。
    checkin_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "checkin_date", name="uq_mood_checkins_user_day"),
    )

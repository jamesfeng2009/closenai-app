"""
成长记录表模型。

对应 P0-F07 成长记录：每日洞察、周报、模式发现。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, false, func
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
    # 记录标签（逗号分隔，如 "情绪,工作"，规则打标，P0-F07 §6）
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 结构化摘要（JSON 字符串：周报 mood_distribution/top_topics 等，P1-F03 §1）
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 事件后跟进消息是否已发送（P0-F07：3-7 天主动跟进防重复标记）
    follow_up_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UserMilestone(Base):
    """用户里程碑徽章（PRD P0-F05）：streak 连续 7/30/100/365 天达成。"""

    __tablename__ = "user_milestones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    # 徽章类型: streak_7 | streak_30 | streak_100 | streak_365
    milestone_type: Mapped[str] = mapped_column(String(30))

    achieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
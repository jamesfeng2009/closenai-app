"""
性格画像表模型。

对应 P0-F05 对话式性格发现。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class PersonalityProfile(Base):
    __tablename__ = "personality_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    mbti_type: Mapped[str | None] = mapped_column(String(4), nullable=True)
    # 观察记录（JSON 字符串）
    observations: Mapped[str | None] = mapped_column(Text, nullable=True)
    unique_traits: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PersonalityAssessment(Base):
    """对话式性格评估（PRD P0-F06）：8 题问答状态与答案。"""

    __tablename__ = "personality_assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    # 答案（JSON 数组字符串：[{"q": 0, "option": "a"|"b"|"neutral", "pole": "I"|...|null, "text": ...}]）
    answers: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 状态: in_progress | completed
    status: Mapped[str] = mapped_column(String(20), default="in_progress")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
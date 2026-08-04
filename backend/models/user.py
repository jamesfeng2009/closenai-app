"""
用户表模型。

对应 P0-F04 Streak + 关系等级、配额、订阅档位。
"""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # 用户资料
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 扩展字段：Streak / 关系等级 / 配额
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    # 多语言：用户偏好语言（13 种之一，控制 LLM 回复语言）
    language: Mapped[str] = mapped_column(String(10), default="en")
    # 主动消息偏好时间（HH:MM，用户本地时区）
    push_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    # 推送频率控制（P1-F01）：daily | important | off，默认 daily
    push_frequency: Mapped[str] = mapped_column(String(10), default="daily")
    # 推送总开关（P1-F05 引导默认关闭）
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # 对话风格（free 不可用，Pro 可切换）
    chat_style: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # 引导流程中用户已知的 MBTI（P1-F05），用于性格画像种子
    mbti_type: Mapped[str | None] = mapped_column(String(4), nullable=True)
    # AI 数据共享同意（合规 5.1.2(i)）：未同意时 AI 对话接口返回 403 AI_CONSENT_REQUIRED
    ai_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    streak_count: Mapped[int] = mapped_column(Integer, default=0)
    last_active_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    relationship_level: Mapped[str] = mapped_column(String(20), default="L1")
    chat_count: Mapped[int] = mapped_column(Integer, default=0)
    daily_quota_used: Mapped[int] = mapped_column(Integer, default=0)
    tier: Mapped[str] = mapped_column(String(20), default="free")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
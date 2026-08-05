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
    # 特殊事件推送月度计数（P1-F01 §6：里程碑/关心类推送每月 ≤4 次，按用户本地月跨月重置）
    special_push_count: Mapped[int] = mapped_column(Integer, default=0)
    special_push_month: Mapped[str | None] = mapped_column(String(7), nullable=True)
    # 对话风格（free 不可用，Pro 可切换）
    chat_style: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # 引导流程中用户已知的 MBTI（P1-F05），用于性格画像种子
    mbti_type: Mapped[str | None] = mapped_column(String(4), nullable=True)
    # AI 数据共享同意（合规 5.1.2(i)）：未同意时 AI 对话接口返回 403 AI_CONSENT_REQUIRED
    ai_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    streak_count: Mapped[int] = mapped_column(Integer, default=0)
    # Streak 冻结券剩余数（PRD P0-F05：免费每月 1 张 / Pro 每月 3 张，断签日自动消耗）
    streak_freeze_count: Mapped[int] = mapped_column(Integer, default=0)
    # 冻结券月度发放标记（用户本地月份 YYYY-MM，新月首次活跃时重置配额）
    streak_freeze_grant_month: Mapped[str | None] = mapped_column(String(7), nullable=True)
    # 生日（PRD P0-F06：推导太阳星座，用户主动输入）
    birthday: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 九型人格编号（PRD P0-F06：用户主动输入 1-9；P1 接入 36 题测试）
    enneagram_type: Mapped[str | None] = mapped_column(String(2), nullable=True)
    last_active_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 累计活跃天数（每日首次活跃 +1，与 streak 中断解耦；PRD P0-F05 等级晋升依据）
    active_days: Mapped[int] = mapped_column(Integer, default=0)
    relationship_level: Mapped[str] = mapped_column(String(20), default="L1")
    chat_count: Mapped[int] = mapped_column(Integer, default=0)
    daily_quota_used: Mapped[int] = mapped_column(Integer, default=0)
    tier: Mapped[str] = mapped_column(String(20), default="free")

    # ── 认证（三种登录方式：Apple / 邮箱验证码 / 用户名密码；字段集互补，均 nullable）──
    # 邮箱（密码登录与验证码登录共用；唯一，partial index 仅约束非 NULL 行）
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 用户名（密码登录标识符之一；唯一，partial index）
    username: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # argon2 密码哈希（仅密码登录用户有值；明文永不存储）
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Apple 账号 sub（Sign in with Apple；唯一，partial index）
    apple_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 邮箱验证时间（验证码登录/注册即视为已验证；None 表示未验证）
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── 账号生命周期（软删除：注销即时失效，QStash 异步物理删除）──
    # status='active' 正常；'deleted' 已注销（get_user 过滤掉，业务路径抛 USER_NOT_FOUND）
    # partial unique index 加 deleted_at IS NULL 条件，软删除记录不占唯一约束位 → 同 email/apple_id 可重新注册
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
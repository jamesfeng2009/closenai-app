"""
订阅与收据表模型。

对应 IAP 订阅：pro / pro_plus 自动续期订阅。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    product_id: Mapped[str] = mapped_column(String(50))
    # 档位: free | pro | pro_plus
    tier: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    original_transaction_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    # 是否免费试用中（P1-F02 §4，从收据 is_trial_period 解析）
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False)
    # 试用到期 24h 提醒是否已发送（防 cron 重复提醒）
    trial_reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # B3：original_transaction_id 唯一约束（partial unique index）。
    # 防止并发请求双写同一收据到不同账号（多账号白嫖）；
    # 仅在非 NULL 时生效，多 NULL 不冲突（未走收据校验的旧数据兼容）。
    __table_args__ = (
        Index(
            "ix_subscriptions_original_tx_unique",
            "original_transaction_id",
            unique=True,
            postgresql_where=text("original_transaction_id IS NOT NULL"),
        ),
    )


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    product_id: Mapped[str] = mapped_column(String(50))
    # 收据原始数据（Base64）
    receipt_data: Mapped[str] = mapped_column(String(8192))
    status: Mapped[str] = mapped_column(String(20))  # valid | invalid | expired | refunded
    transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
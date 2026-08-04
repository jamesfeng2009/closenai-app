"""
推送 Token 表模型。

对应 P0-F01 主动消息推送（APNs/FCM）。
"""
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class PushToken(Base):
    __tablename__ = "push_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    # 平台: apns | fcm
    platform: Mapped[str] = mapped_column(String(10))
    device_token: Mapped[str] = mapped_column(String(512))
    # 时区（用于分时区推送）
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
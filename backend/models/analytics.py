"""分析事件的逐事件接收台账（T7-02）。

为什么需要这张表：`relayed/dropped` 只是一个**批次级**结果，前端拿到 200 后无法知道
哪一条被分析平台收下了、哪一条是重复提交、哪一条值得重试。没有逐事件台账时，
「同一条事件因网络重试被送了两次」在后端没有任何可对账的痕迹。

幂等键是 `(user_id, event_id)`：`event_id` 由客户端在入队时生成，重试沿用同一个值。
状态只写两格——`pending`（本次请求正在中继）与 `accepted`（已送到分析平台）。
可重试失败会删掉 pending 行，让下一次重试重新认领；永久拒绝不落行（它不需要幂等，
也不会被重试）。因此这张表只保留「已经成立」的接收事实，不会变成事件镜像。

注销时它必须一起删：表里带 `user_id`，按 `PURGE_TARGETS` 的通用循环清理。
"""
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class AnalyticsEventReceipt(Base):
    __tablename__ = "analytics_event_receipts"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # pending：已认领、正在中继；accepted：分析平台已收下（重复提交据此判 duplicate）。
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    reason: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

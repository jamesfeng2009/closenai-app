"""source.deleted 的本地投递台账（P2 #20）。

存在的理由：事件原来只在提交之后投一次，投失败就只剩一行日志。broker 抖动、拓扑晚建、
进程重启都会让「用户删掉的那行」在 Go 的 source_records 里永远挂着——用户看到的是
「删了，但洞察还引用它」。把投递意图和删除写进**同一笔事务**，失败留下 pending 行，
下一次 cron 按退避重投，这条链才不再依赖「第一次就成功」。

口径：
- `message_id` 作主键：由指针确定性派生（`contract.source_deleted_message_id`），
  同一行的两次删除意图天然收敛成一条，不会被重试叠出多行。
- 只存指针（谁、哪张表、哪一行）与 trace，不存消息体：体由 `encode_source_deleted`
  按契约重新生成，避免两处编码漂移。
- `status` 只写 pending / published / failed；`failed` 是达到重试上限后的死信态，
  行保留供运维按 `last_error` 排查。
- 无外键（D-5）：带 user_id ⇒ 必须进 `PURGE_TARGETS`，注销时一并删掉。
"""
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base

# 取值面封闭：多一个状态＝多一个没人认领的桶
SOURCE_EVENT_STATUSES = ("pending", "published", "failed")


class SourceEventOutbox(Base):
    __tablename__ = "source_event_outbox"

    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    source_table: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # ''＝产生这一行时没有 trace（与 outbox_messages.trace_id 同口径）
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 下一次允许重投的时刻：退避的记账位，也是 cron 的扫描谓词
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_source_event_outbox_claim", "status", "available_at"),
    )

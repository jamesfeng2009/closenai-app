"""PostHog 侧擦除失败的重试台账（T1-02，Baseline §14.1「注销即删干净」的外部平台面）。

为什么要一张表，而不是 `users.purge_error` 一个布尔位：`purge_user` 在**同一段事务里**把
`users` 行删掉了。把「外部平台还没删干净」写在一张会被自己删掉的行上，等于把状态记在
马上要销毁的便签里——cron 下一轮来读，行已经不在了，残留连一次重试机会都没有。

为什么这不是 J4 裁决否掉的那种墓碑表：J4 否掉的是「只有孤立 user_id、没有任何消费方的
留存表」。这里每一行都有唯一消费方——`/cron/purge-deleted-users` 的重试路径，而且行是
**自我销毁**的：擦除成功即删、超过 `settings.posthog_erasure_retry_max_days` 打一条
ERROR 告警后也删。所以「谁注销了」这份旁证最多存活到擦除成功或告警那一刻，不会永久留存；
举证口径仍与 J4 一致地走结构化日志（`purge 举证 … posthog=pending:…` 与
`erasure 重试 …`），表里不留终态历史。

列的口径沿用本仓既有裁决：不建外键（D-5，注销靠逐表 rowcount）、`user_id` 唯一（QStash
重试与多 worker 不能把同一用户叠成多行，也天然成为幂等键）、时间列全部 `DATETIME(tz)`。
`last_reason` 存机器码（`not_configured`／`http_403`／`request_failed`，出处
`analytics_service.erase_person_data` 的返回值），不是给用户看的文案。
"""
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class PostHogErasureRetry(Base):
    __tablename__ = "posthog_erasure_retries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # 同时是 PostHog 的 distinct_id：注销链路里两者同值（见 analytics_service 模块 docstring）。
    user_id: Mapped[str] = mapped_column(String(36))
    last_reason: Mapped[str] = mapped_column(String(64), default="")
    # 已经试过几次（写第一行时算 1 次：那次就是 purge_user 里失败的调用）
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    first_failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # 下一次允许重试的时刻：既是退避的记账位，也是 CAS 认领的谓词（见 erasure_repository.claim）。
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # QStash 重试与多 worker 不能把同一用户叠成多行，唯一约束同时是 upsert 的幂等键。
        UniqueConstraint("user_id", name="uq_posthog_erasure_retries_user_id"),
        # cron 只按「到点了没有」扫这张表，一条索引够用；表按设计不会大（成功即删、7 天封顶）。
        Index("ix_posthog_erasure_retries_next_attempt", "next_attempt_at"),
    )

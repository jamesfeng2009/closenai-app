"""analytics_event_receipts 数据访问层（T7-02）。

幂等键是 `(user_id, event_id)`。`claim` 用 `INSERT … ON CONFLICT DO NOTHING` 的
rowcount 决定「这条是不是本次认领的」——两个并发请求同时送同一个 event_id 时，
只有一个能拿到 rowcount=1，另一条读到 pending 后按可重试回报，分析平台不会收到两份。

为什么不用「先查后插」：那在并发下会把两条都放行，正是本票要消灭的双份事件。
SQLAlchemy 的 sqlite/postgresql 方言各有一个 `on_conflict_do_nothing`，语义一致。
"""
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.time_utils import utc_now
from backend.models.analytics import AnalyticsEventReceipt


class AnalyticsReceiptRepository:
    """逐事件接收台账。"""

    async def claim(
        self, db: AsyncSession, user_id: str, event_id: str
    ) -> tuple[bool, str | None]:
        """认领一条事件。返回 (是否认领成功, 认领失败时的已有状态)。"""
        dialect = db.get_bind().dialect.name
        insert = sqlite_insert if dialect == "sqlite" else pg_insert
        result = await db.execute(
            insert(AnalyticsEventReceipt)
            .values(user_id=user_id, event_id=event_id, status="pending")
            .on_conflict_do_nothing(index_elements=["user_id", "event_id"])
        )
        if result.rowcount == 1:
            return True, None
        status = await db.execute(
            select(AnalyticsEventReceipt.status).where(
                AnalyticsEventReceipt.user_id == user_id,
                AnalyticsEventReceipt.event_id == event_id,
            )
        )
        return False, status.scalar_one_or_none()

    async def mark_accepted(self, db: AsyncSession, user_id: str, event_id: str) -> None:
        await db.execute(
            update(AnalyticsEventReceipt)
            .where(
                AnalyticsEventReceipt.user_id == user_id,
                AnalyticsEventReceipt.event_id == event_id,
            )
            .values(status="accepted", reason=None, updated_at=utc_now())
        )

    async def release(self, db: AsyncSession, user_id: str, event_id: str) -> None:
        """中继失败时删掉 pending 行：下一次重试要能重新认领，而不是永远撞 in_flight。"""
        await db.execute(
            delete(AnalyticsEventReceipt).where(
                AnalyticsEventReceipt.user_id == user_id,
                AnalyticsEventReceipt.event_id == event_id,
            )
        )


analytics_receipt_repository = AnalyticsReceiptRepository()

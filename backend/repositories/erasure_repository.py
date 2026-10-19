"""posthog_erasure_retries 数据访问层（T1-02）。

职责边界与其余 repository 一致：只与这张表交互。业务判定（要不要登记、退避多久、
超过 7 天要不要告警后删行）全在 `services/user_service.py` 的重试路径里。

三条刻意取舍：
1. **认领用 CAS 而不是 `FOR UPDATE SKIP LOCKED`**：`UPDATE … WHERE id=? AND
   next_attempt_at<=:now` 的 rowcount 就是「谁抢到了这一行」。SQLite（本机测试库）不渲染
   FOR UPDATE，而本仓的既有口径是让两种库语义一致（同 T1-01 的 `export_repository.claim`）。
   这一条不是纸面担心：本轮在一次性空库上实测过两条 session 同时认领同一行。
2. **只按 `user_id` 唯一，不建外键**（D-5）：这张表活着的期间 `users` 行已经不存在了，
   任何指向 `users.id` 的外键都会让注销本身失败——「外部平台还没删干净」绝不能
   反过来阻止本地删除。
3. **扫到点行时回 ORM 对象而不是只回 id**：这张表按设计不会大（成功即删、超龄即删），
   而重试判定的四个字段（user_id / attempts / first_failed_at / next_attempt_at）全都要用，
   只回 id 就得每行再查一次——把「一轮 cron 两次查询乘 N」换成一次查询，读起来也更直白。
"""
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.erasure import PostHogErasureRetry


class ErasureRepository:
    """posthog_erasure_retries 表数据访问。"""

    async def get(self, db: AsyncSession, user_id: str) -> PostHogErasureRetry | None:
        result = await db.execute(
            select(PostHogErasureRetry).where(PostHogErasureRetry.user_id == user_id)
        )
        return result.scalars().first()

    async def record_failure(
        self,
        db: AsyncSession,
        *,
        row_id: str,
        user_id: str,
        reason: str,
        now: datetime,
        next_attempt_at: datetime,
    ) -> PostHogErasureRetry:
        """登记「PostHog 还没擦干净」。同一 user 重复失败要累加次数而不是叠出第二行。

        `first_failed_at` 只在插入时赋值，之后不动——它是 7 天告警时钟的起点，重置它就等于
        让一个一直失败的用户永远走不到告警那一格。
        upsert 用「先查后写」而不是 `ON CONFLICT`：SQLite 与 PG 的冲突子句在 SQLAlchemy 里
        是两套方言写法，而这里的去重靠 `user_id` 唯一约束兜底，不需要原子 upsert。
        """
        row = await self.get(db, user_id)
        if row is None:
            row = PostHogErasureRetry(
                id=row_id,
                user_id=user_id,
                last_reason=reason,
                attempts=1,
                first_failed_at=now,
                next_attempt_at=next_attempt_at,
                last_attempt_at=now,
            )
            db.add(row)
        else:
            row.last_reason = reason
            row.attempts = (row.attempts or 0) + 1
            row.next_attempt_at = next_attempt_at
            row.last_attempt_at = now
        await db.flush()
        return row

    async def due_rows(self, db: AsyncSession, now: datetime, *, limit: int) -> list[PostHogErasureRetry]:
        """到点的待重试行（按 next_attempt_at 升序，最多 limit 行）。"""
        result = await db.execute(
            select(PostHogErasureRetry)
            .where(PostHogErasureRetry.next_attempt_at <= now)
            .order_by(PostHogErasureRetry.next_attempt_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def claim(
        self, db: AsyncSession, row_id: str, *, now: datetime, next_attempt_at: datetime
    ) -> bool:
        """CAS 认领：拿到的同时就把 `next_attempt_at` 推到未来。

        推时刻必须写在同一条 UPDATE 的 SET 里，不是「认领之后再更新」：谓词
        `next_attempt_at <= :now` 只有被自己改过才可能对第二个 worker 变成假，否则两个
        worker 会各自 rowcount=1、把同一个人擦两遍。SQLite 不渲染 FOR UPDATE，
        这条 CAS 是两种库都成立的唯一形状（同 T1-01 的 `export_repository.claim`）。
        """
        result = await db.execute(
            update(PostHogErasureRetry)
            .where(
                PostHogErasureRetry.id == row_id,
                PostHogErasureRetry.next_attempt_at <= now,
            )
            .values(last_attempt_at=now, next_attempt_at=next_attempt_at)
            # 不让 SQLAlchemy 把这次批量更新同步回内存对象：它的 `evaluate` 同步会拿
            # 已加载行里的 naive datetime（SQLite 的 DateTime(timezone=True) 读出来就是这个形状）
            # 去和带 tz 的 `now` 比，当场 TypeError。这里判的是 rowcount，同步与否不影响判定，
            # 而同步失败的代价是整条 CAS 变成异常。
            .execution_options(synchronize_session=False)
        )
        return (result.rowcount or 0) == 1

    async def reschedule(
        self, db: AsyncSession, row_id: str, *, reason: str, attempts: int
    ) -> None:
        """这一轮又没擦掉：记失败原因与次数。

        时刻不在这里推——`claim()` 已经把 `next_attempt_at` 推到下一班车了。
        """
        await db.execute(
            update(PostHogErasureRetry)
            .where(PostHogErasureRetry.id == row_id)
            .values(last_reason=reason, attempts=attempts)
        )

    async def delete(self, db: AsyncSession, row_id: str) -> int:
        """擦成功了（或告警发过了）就删行：这张表的存在意义只有「还有活要干」。"""
        result = await db.execute(
            delete(PostHogErasureRetry).where(PostHogErasureRetry.id == row_id)
        )
        return result.rowcount or 0

    async def count(self, db: AsyncSession) -> int:
        result = await db.execute(select(PostHogErasureRetry.id))
        return len(result.scalars().all())

    async def pending_user_ids(self, db: AsyncSession) -> list[str]:
        """当前还欠着外部擦除的用户（观测与测试用，不参与调度判定）。"""
        result = await db.execute(select(PostHogErasureRetry.user_id))
        return list(result.scalars().all())


erasure_repository = ErasureRepository()

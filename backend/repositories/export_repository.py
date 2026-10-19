"""data_export_jobs 数据访问层（T1-01）。

职责边界与其余 repository 一致：只与这张表交互，不含业务判定（限频窗口、重试预算、
产物落盘都在 services/export_service.py）。

两条刻意取舍：
1. **认领用 CAS 而不是 `FOR UPDATE SKIP LOCKED`**：`UPDATE … WHERE id=? AND status='queued'`
   的 rowcount 就是「谁抢到了」。SQLite（本机测试库）不渲染 FOR UPDATE，PG 专用写法会让
   两边形状不同；CAS 在两种库上语义一致，单实例部署够用，多实例并发认领也只会各领各的。
2. **删除只按 user_id**：注销链路（D-5）不靠外键级联，本表的清理必须能被
   `PURGE_TARGETS` 的通用循环直接执行。
"""
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.export import DataExportJob

# 「已经交付过 or 正在交付」的状态：限频判据按这三条算，failed/expired 不占额度——
# 失败不该让用户再等 24 小时，过期则是设计上的重新申请点。
BLOCKING_STATUSES = ("queued", "running", "ready")


class ExportRepository:
    """data_export_jobs 表数据访问。"""

    async def create(
        self, db: AsyncSession, *, job_id: str, user_id: str, scope: str, now: datetime
    ) -> DataExportJob:
        job = DataExportJob(
            id=job_id, user_id=user_id, scope=scope, status="queued", created_at=now
        )
        db.add(job)
        await db.flush()
        return job

    async def get(self, db: AsyncSession, job_id: str) -> DataExportJob | None:
        result = await db.execute(
            select(DataExportJob).where(DataExportJob.id == job_id)
        )
        return result.scalars().first()

    async def latest_blocking(
        self, db: AsyncSession, user_id: str, since: datetime
    ) -> DataExportJob | None:
        """`since` 之后该用户最近一个未失败/未过期的 job（限频的证据行）。"""
        result = await db.execute(
            select(DataExportJob)
            .where(
                DataExportJob.user_id == user_id,
                DataExportJob.created_at >= since,
                DataExportJob.status.in_(BLOCKING_STATUSES),
            )
            .order_by(DataExportJob.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def list_for_user(
        self, db: AsyncSession, user_id: str, *, limit: int = 20
    ) -> list[DataExportJob]:
        result = await db.execute(
            select(DataExportJob)
            .where(DataExportJob.user_id == user_id)
            .order_by(DataExportJob.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def ids_by_status(
        self, db: AsyncSession, statuses: tuple[str, ...]
    ) -> list[str]:
        """按状态取 job id（创建顺序），用于 cron 的回收/清理/认领候选集。

        只回 id 不回 ORM 行：一次调度要扫的状态可能有几千行，把整行拉进 py-api 的
        1.5Gi 上限没有意义，逐个 `get()` 才吃得到内存里的一个 job。
        """
        result = await db.execute(
            select(DataExportJob.id)
            .where(DataExportJob.status.in_(statuses))
            .order_by(DataExportJob.created_at.asc())
        )
        return list(result.scalars().all())

    async def claim(self, db: AsyncSession, job_id: str, now: datetime) -> bool:
        """CAS 认领：queued → running。rowcount=1 才算拿到，认领失败什么都不写。"""
        result = await db.execute(
            update(DataExportJob)
            .where(DataExportJob.id == job_id, DataExportJob.status == "queued")
            .values(status="running", started_at=now, error_code="")
        )
        return (result.rowcount or 0) == 1

    async def finish(
        self,
        db: AsyncSession,
        job_id: str,
        *,
        status: str,
        now: datetime,
        file_path: str = "",
        byte_size: int = 0,
        error_code: str = "",
        attempts: int | None = None,
        expires_at: datetime | None = None,
        deleted_at: datetime | None = None,
    ) -> None:
        values: dict = {"status": status, "finished_at": now}
        values["file_path"] = file_path
        values["byte_size"] = byte_size
        values["error_code"] = error_code
        if attempts is not None:
            values["attempts"] = attempts
        if expires_at is not None:
            values["expires_at"] = expires_at
        if deleted_at is not None:
            values["deleted_at"] = deleted_at
        await db.execute(update(DataExportJob).where(DataExportJob.id == job_id).values(**values))

    async def file_paths_for_user(
        self, db: AsyncSession, user_id: str
    ) -> list[tuple[str, str]]:
        """该用户已落盘但未登记的产物路径（注销时先取路径，再删行）。

        必须在 DELETE 之前调用：行删掉以后 file_path 就再也读不到了，文件会变成
        库上无记录的孤儿——那正是「注销即删干净」这句话最容易被破的地方。
        """
        result = await db.execute(
            select(DataExportJob.id, DataExportJob.file_path).where(
                DataExportJob.user_id == user_id, DataExportJob.file_path != ""
            )
        )
        return [(row[0], row[1]) for row in result.all()]


export_repository = ExportRepository()

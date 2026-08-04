"""
成长记录数据访问层（P0-F07）。

职责：只与 growth_records 表交互，不含业务逻辑。
- 每日洞察 / 周报 / 模式发现 的写入与查询
"""
from datetime import timedelta
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.time_utils import utc_now
from backend.models.growth import GrowthRecord


class GrowthRepository:
    """growth_records 表数据访问。"""

    async def create(
        self,
        db: AsyncSession,
        user_id: str,
        record_type: str,
        content: str,
        related_memory_ids: Optional[List[str]] = None,
    ) -> GrowthRecord:
        """写入一条成长记录。"""
        record = GrowthRecord(
            id=str(uuid4()),
            user_id=user_id,
            record_type=record_type,
            content=content,
            related_memory_ids=",".join(related_memory_ids) if related_memory_ids else None,
        )
        db.add(record)
        await db.flush()
        return record

    async def get_latest(
        self, db: AsyncSession, user_id: str, record_type: str
    ) -> Optional[GrowthRecord]:
        """获取某类型最新一条记录。"""
        result = await db.execute(
            select(GrowthRecord)
            .where(GrowthRecord.user_id == user_id, GrowthRecord.record_type == record_type)
            .order_by(GrowthRecord.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_recent(
        self,
        db: AsyncSession,
        user_id: str,
        record_type: Optional[str] = None,
        days: int = 7,
        limit: int = 10,
    ) -> List[GrowthRecord]:
        """查询最近 N 天内的成长记录（按时间倒序）。"""
        start = utc_now() - timedelta(days=days)
        stmt = select(GrowthRecord).where(
            GrowthRecord.user_id == user_id, GrowthRecord.created_at >= start
        )
        if record_type:
            stmt = stmt.where(GrowthRecord.record_type == record_type)
        stmt = stmt.order_by(GrowthRecord.created_at.desc()).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def list_by_type(
        self, db: AsyncSession, user_id: str, record_type: str, limit: int = 10
    ) -> List[GrowthRecord]:
        """查询指定类型的记录（按时间倒序）。"""
        result = await db.execute(
            select(GrowthRecord)
            .where(GrowthRecord.user_id == user_id, GrowthRecord.record_type == record_type)
            .order_by(GrowthRecord.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


growth_repository = GrowthRepository()
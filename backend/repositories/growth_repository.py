"""
成长记录数据访问层（P0-F07）。

职责：只与 growth_records 表交互，不含业务逻辑。
- 每日洞察 / 周报 / 模式发现 的写入与查询
- 事件后跟进（follow_up_sent 标记）的扫描与更新
"""
from datetime import datetime, timedelta
from typing import List, Optional, Sequence
from uuid import uuid4

from sqlalchemy import func, select, update
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
        tags: Optional[List[str]] = None,
        metadata_json: Optional[str] = None,
    ) -> GrowthRecord:
        """写入一条成长记录。"""
        record = GrowthRecord(
            id=str(uuid4()),
            user_id=user_id,
            record_type=record_type,
            content=content,
            related_memory_ids=",".join(related_memory_ids) if related_memory_ids else None,
            tags=",".join(tags) if tags else None,
            metadata_json=metadata_json,
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

    async def list_between(
        self,
        db: AsyncSession,
        user_id: str,
        start: datetime,
        end: datetime,
        limit: int = 20,
    ) -> List[GrowthRecord]:
        """查询时间窗口 [start, end) 内的成长记录（按时间倒序，用于回忆卡片）。"""
        result = await db.execute(
            select(GrowthRecord)
            .where(
                GrowthRecord.user_id == user_id,
                GrowthRecord.created_at >= start,
                GrowthRecord.created_at < end,
            )
            .order_by(GrowthRecord.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_between(
        self,
        db: AsyncSession,
        user_id: str,
        start: datetime,
        end: datetime,
        record_type: Optional[str] = None,
    ) -> int:
        """统计 [start, end) 内的成长记录条数（record_type 为 None 时统计全部类型）。

        用 COUNT 而不是 list_between 再取长度：年报卡上的数字必须精确，
        而列表查询带 limit，超过 limit 就会静默少算。
        """
        stmt = select(func.count(GrowthRecord.id)).where(
            GrowthRecord.user_id == user_id,
            GrowthRecord.created_at >= start,
            GrowthRecord.created_at < end,
        )
        if record_type is not None:
            stmt = stmt.where(GrowthRecord.record_type == record_type)
        result = await db.execute(stmt)
        return int(result.scalar_one() or 0)

    async def list_by_type_between(
        self,
        db: AsyncSession,
        user_id: str,
        record_type: str,
        start: datetime,
        end: datetime,
        limit: int = 10,
    ) -> List[GrowthRecord]:
        """查询 [start, end) 窗口内指定类型的记录（按时间倒序；睡前复盘按本地日自查用）。"""
        result = await db.execute(
            select(GrowthRecord)
            .where(
                GrowthRecord.user_id == user_id,
                GrowthRecord.record_type == record_type,
                GrowthRecord.created_at >= start,
                GrowthRecord.created_at < end,
            )
            .order_by(GrowthRecord.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_pending_follow_ups(
        self,
        db: AsyncSession,
        record_types: Sequence[str],
        start: datetime,
        end: datetime,
        limit: int = 200,
    ) -> List[GrowthRecord]:
        """查询窗口 [start, end) 内未发送跟进消息的记录（按时间正序，跨用户）。"""
        result = await db.execute(
            select(GrowthRecord)
            .where(
                GrowthRecord.record_type.in_(record_types),
                GrowthRecord.follow_up_sent.is_(False),
                GrowthRecord.created_at >= start,
                GrowthRecord.created_at < end,
            )
            .order_by(GrowthRecord.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_follow_up_sent(self, db: AsyncSession, record_id: str) -> None:
        """标记记录已发送跟进消息（幂等：重复标记结果一致）。"""
        await db.execute(
            update(GrowthRecord)
            .where(GrowthRecord.id == record_id)
            .values(follow_up_sent=True)
        )


growth_repository = GrowthRepository()
"""
分享导出缓存数据访问层（P1-F04）。

职责：只与 share_exports 表交互，不含业务逻辑。
- 按 用户+period+period_key 查询缓存
- 写入缓存（唯一约束兜底并发双写）
"""
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.share import ShareExport


class ShareRepository:
    """share_exports 表数据访问。"""

    async def get_cached(
        self, db: AsyncSession, user_id: str, period: str, period_key: str
    ) -> Optional[ShareExport]:
        """查询同周期的导出缓存。"""
        result = await db.execute(
            select(ShareExport).where(
                ShareExport.user_id == user_id,
                ShareExport.period == period,
                ShareExport.period_key == period_key,
            )
        )
        return result.scalar_one_or_none()

    async def save(
        self, db: AsyncSession, user_id: str, period: str, period_key: str, payload: str
    ) -> ShareExport:
        """写入导出缓存。

        并发双写命中唯一约束（IntegrityError）时回滚到 savepoint 并重查，
        返回已存在的缓存行（与 user_repository 并发创建处理同款模式）。
        """
        row = ShareExport(
            id=str(uuid4()),
            user_id=user_id,
            period=period,
            period_key=period_key,
            payload=payload,
        )
        db.add(row)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            existing = await self.get_cached(db, user_id, period, period_key)
            if existing is not None:
                return existing
            raise
        return row


share_repository = ShareRepository()

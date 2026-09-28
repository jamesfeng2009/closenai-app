"""
safety_events 数据访问层（§5.16 ④ / 措辞规范 §二）。

职责：危机转介命中审计的写入与抽检查询；"当日停推"判定也由本表回答
（不新增 users 列，命中记录即事实源）。
"""
import json
from datetime import datetime, timedelta
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.time_utils import utc_now
from backend.models.journey import SafetyEvent


class SafetyRepository:
    """safety_events 表数据访问。"""

    async def record(
        self,
        db: AsyncSession,
        user_id: str,
        kind: str,
        source: str = "user_message",
        matched_terms: Optional[List[str]] = None,
        hotline: Optional[str] = None,
        language: Optional[str] = None,
        excerpt: Optional[str] = None,
        advice_blocked: bool = False,
    ) -> SafetyEvent:
        row = SafetyEvent(
            id=str(uuid4()),
            user_id=user_id,
            kind=kind,
            source=source,
            matched_terms=json.dumps(matched_terms or [], ensure_ascii=False),
            hotline=hotline,
            language=language,
            # 原文仅留摘录供人工抽检（隐私最小化：不整段留存会话）
            excerpt=(excerpt or "")[:280] or None,
            advice_blocked=advice_blocked,
        )
        db.add(row)
        await db.flush()
        return row

    async def has_hit_since(self, db: AsyncSession, user_id: str, since: datetime) -> bool:
        """窗口内是否命中过危机触发词（营销/升级类推送挂起判定依据）。"""
        result = await db.execute(
            select(SafetyEvent.id)
            .where(SafetyEvent.user_id == user_id, SafetyEvent.created_at >= since)
            .limit(1)
        )
        return result.first() is not None

    async def latest_hit(
        self, db: AsyncSession, user_id: str, within_hours: int = 24
    ) -> Optional[SafetyEvent]:
        result = await db.execute(
            select(SafetyEvent)
            .where(
                SafetyEvent.user_id == user_id,
                SafetyEvent.created_at >= utc_now() - timedelta(hours=within_hours),
            )
            .order_by(SafetyEvent.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_for_review(
        self, db: AsyncSession, review_status: str = "pending", limit: int = 100
    ) -> List[SafetyEvent]:
        """待人工抽检队列（24h 抽检口径）。"""
        result = await db.execute(
            select(SafetyEvent)
            .where(SafetyEvent.review_status == review_status)
            .order_by(SafetyEvent.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def set_review(
        self, db: AsyncSession, event_id: str, review_status: str
    ) -> None:
        row = await db.get(SafetyEvent, event_id)
        if row is not None:
            row.review_status = review_status
            await db.flush()


safety_repository = SafetyRepository()

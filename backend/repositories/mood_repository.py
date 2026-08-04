"""
情绪签到数据访问层（P0-F03）。

职责：只与 mood_checkins 表交互，不含业务逻辑。
"""
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.time_utils import local_day_start_utc, safe_zoneinfo, utc_now
from backend.models.mood import MoodCheckin


class MoodRepository:
    """mood_checkins 表数据访问。"""

    async def create(
        self,
        db: AsyncSession,
        user_id: str,
        mood_emoji: str,
        mood_label: str,
        ai_interpretation: str,
    ) -> MoodCheckin:
        """写入一条签到记录。"""
        record = MoodCheckin(
            id=str(uuid4()),
            user_id=user_id,
            mood_emoji=mood_emoji,
            mood_label=mood_label,
            ai_interpretation=ai_interpretation,
        )
        db.add(record)
        await db.flush()
        return record

    async def get_today(
        self, db: AsyncSession, user_id: str, start: Optional[datetime] = None
    ) -> Optional[MoodCheckin]:
        """查询用户今日第一条签到（用于去重 / 检查今日是否已签到）。

        `start` 应为用户本地今日 00:00 的 tz-aware UTC 时间
        （调用方用 time_utils.local_day_start_utc 计算），缺省回退 UTC 当日起点。
        """
        start = start or local_day_start_utc(None)
        result = await db.execute(
            select(MoodCheckin)
            .where(MoodCheckin.user_id == user_id, MoodCheckin.created_at >= start)
            .order_by(MoodCheckin.created_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_since(self, db: AsyncSession, user_id: str, start: datetime) -> int:
        """统计 start 之后的签到条数（用于配额计算）。"""
        result = await db.execute(
            select(func.count(MoodCheckin.id)).where(
                MoodCheckin.user_id == user_id, MoodCheckin.created_at >= start
            )
        )
        return int(result.scalar_one() or 0)

    async def get_history(
        self, db: AsyncSession, user_id: str, days: int = 30
    ) -> List[MoodCheckin]:
        """查询最近 N 天签到历史（按时间倒序）。"""
        start = utc_now() - timedelta(days=days)
        result = await db.execute(
            select(MoodCheckin)
            .where(MoodCheckin.user_id == user_id, MoodCheckin.created_at >= start)
            .order_by(MoodCheckin.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_recent(
        self, db: AsyncSession, user_id: str, limit: int = 7
    ) -> List[MoodCheckin]:
        """查询最近 N 条签到（按时间倒序）。"""
        result = await db.execute(
            select(MoodCheckin)
            .where(MoodCheckin.user_id == user_id)
            .order_by(MoodCheckin.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_by_day(
        self, db: AsyncSession, user_id: str, days: int = 30, tz: Optional[str] = None
    ) -> List[tuple[datetime, str]]:
        """按天聚合签到，返回 (签到日期, 当日首个情绪表情)。

        按用户本地时区的日历日分组（Python 侧转换，兼容 SQLite/PG），缺省按 UTC 日期。
        """
        start = utc_now() - timedelta(days=days)
        rows = await db.execute(
            select(MoodCheckin.created_at, MoodCheckin.mood_emoji)
            .where(MoodCheckin.user_id == user_id, MoodCheckin.created_at >= start)
            .order_by(MoodCheckin.created_at.asc())
        )
        zone = safe_zoneinfo(tz)
        result: List[tuple[datetime, str]] = []
        for created_at, emoji in rows.all():
            aware = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
            result.append((aware.astimezone(zone).date(), emoji))
        return result


mood_repository = MoodRepository()
"""
情绪签到数据访问层（P0-F03）。

职责：只与 mood_checkins 表交互，不含业务逻辑。
"""
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
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
        checkin_date: Optional[date] = None,
    ) -> MoodCheckin:
        """写入一条签到记录（低层写入，不锚定本地日；业务入口请用 create_once_today）。"""
        record = MoodCheckin(
            id=str(uuid4()),
            user_id=user_id,
            mood_emoji=mood_emoji,
            mood_label=mood_label,
            ai_interpretation=ai_interpretation,
            checkin_date=checkin_date,
        )
        db.add(record)
        await db.flush()
        return record

    async def create_once_today(
        self,
        db: AsyncSession,
        user_id: str,
        mood_emoji: str,
        mood_label: str,
        ai_interpretation: str,
        day_start: datetime,
        checkin_date: Optional[date] = None,
    ) -> tuple[MoodCheckin, bool]:
        """写入今日签到；若今日已存在则返回已有记录（created=False）。

        先查后写；并发下若撞上 (user_id, checkin_date) 唯一约束（IntegrityError），
        回滚到 savepoint 后重查并返回已存在记录，保证同一用户同一本地日不会双记。
        调用方需配合用户行锁（for_update）先行串行化，唯一约束作为 DB 层兜底防线
        （SQLite 无行锁语义，约束由兜底分支真正兜底）。
        """
        checkin_date = checkin_date or utc_now().date()
        existing = await self.get_today(db, user_id, day_start)
        if existing:
            return existing, False
        record = MoodCheckin(
            id=str(uuid4()),
            user_id=user_id,
            mood_emoji=mood_emoji,
            mood_label=mood_label,
            ai_interpretation=ai_interpretation,
            checkin_date=checkin_date,
        )
        db.add(record)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            # 并发双写兜底：回滚到 savepoint，重查返回已存在记录
            # （先按时间窗查，再按本地日查——时区变更边缘下 day_start 窗口可能查不到）
            existing = await self.get_today(db, user_id, day_start)
            if existing is None:
                existing = await self.get_by_date(db, user_id, checkin_date)
            if existing is None:
                raise
            return existing, False
        return record, True

    async def update(
        self,
        db: AsyncSession,
        record: MoodCheckin,
        mood_emoji: str,
        mood_label: str,
        ai_interpretation: str,
    ) -> MoodCheckin:
        """更新已有签到记录（同一本地日重复签到走更新，不新增行）。"""
        record.mood_emoji = mood_emoji
        record.mood_label = mood_label
        record.ai_interpretation = ai_interpretation
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

    async def get_by_date(
        self, db: AsyncSession, user_id: str, checkin_date: date
    ) -> Optional[MoodCheckin]:
        """按用户本地日历日查询签到（唯一约束兜底分支的重查路径）。"""
        result = await db.execute(
            select(MoodCheckin)
            .where(
                MoodCheckin.user_id == user_id,
                MoodCheckin.checkin_date == checkin_date,
            )
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
        """查询最近 N 条签到（按时间倒序）。

        `id` 次级键不是装饰：`created_at` 只有 `server_default=func.now()`，PG 的 `now()`
        是**事务级**时间戳，同事务写的多行逐位相同；`LIMIT N` 遇上同刻并列时，
        谁进结果集、以什么顺序进都由查询计划决定。Python/Go 两侧读同一批行做逐字节
        契约比较（`GET /mood/history`），这里不确定＝切流前后出现只有重录才能发现的漂移。
        次级键是 uuid4 ⇒ 给的是**字典序不是写入序**，它保证可复现，不恢复历史同刻行的真实先后。
        """
        result = await db.execute(
            select(MoodCheckin)
            .where(MoodCheckin.user_id == user_id)
            .order_by(MoodCheckin.created_at.desc(), MoodCheckin.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_between(
        self, db: AsyncSession, user_id: str, start: datetime, end: datetime
    ) -> List[MoodCheckin]:
        """查询 [start, end) 区间内的全部签到（按时间升序）。

        与 get_history(days) 的区别是刻意的：自然年/季度这类日历窗口不能用「距今 N 天」
        的滚动窗口近似——差几个小时就会把去年 12 月 31 日的签到算进今年。
        不加 limit：一年的签到上限是 366 条（同本地日唯一约束），截断会让年报数字失真。
        """
        result = await db.execute(
            select(MoodCheckin)
            .where(
                MoodCheckin.user_id == user_id,
                MoodCheckin.created_at >= start,
                MoodCheckin.created_at < end,
            )
            .order_by(MoodCheckin.created_at.asc())
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
            # 升序 + id 升序：调用方（growth_service / mood_service）按「升序首行」取当日
            # 首个 emoji，同刻并列时取谁完全取决于数据库返回序。次级键方向必须与
            # Go 侧 selectMoodTrendRows 一致，否则 /mood/trend 的当日标签两侧不同。
            .order_by(MoodCheckin.created_at.asc(), MoodCheckin.id.asc())
        )
        zone = safe_zoneinfo(tz)
        result: List[tuple[datetime, str]] = []
        for created_at, emoji in rows.all():
            aware = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
            result.append((aware.astimezone(zone).date(), emoji))
        return result


mood_repository = MoodRepository()
"""
用户数据访问层（P0-F04）。

职责：只与 users 表交互，不含业务逻辑。
- get_or_create_user：按需创建用户（开发态 anonymous，无鉴权时使用）
- Streak / 关系等级 / 配额 / 语言 / 推送 Token 的读写
"""
from datetime import date, datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.user import User

# list_all 分页大小（offset 迭代直至取完，避免硬上限漏用户）
LIST_ALL_PAGE_SIZE = 500


class UserRepository:
    """users 表数据访问。"""

    async def get_or_create_user(
        self, db: AsyncSession, user_id: str, *, for_update: bool = False
    ) -> User:
        """按 user_id 获取用户，不存在则创建（返回新行）。

        for_update=True 时对用户行加 SELECT ... FOR UPDATE 行锁（同事务内
        串行化配额校验+写入，防并发超配额/双写；SQLite 方言忽略该子句）。
        并发创建同 user_id 时捕获 PK 冲突（IntegrityError），回滚到 savepoint
        后重查并返回已存在的行。
        """
        user = await self.get_user(db, user_id, for_update=for_update)
        if user:
            return user
        user = User(
            id=user_id,
            timezone="UTC",
            language="en",
            streak_count=0,
            relationship_level="L1",
            chat_count=0,
            daily_quota_used=0,
            tier="free",
        )
        db.add(user)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            # 并发请求已抢先创建：重查已存在的行（新用户行锁由获胜事务持有，
            # 此处 FOR UPDATE 会等待其提交后拿到锁，天然完成串行化）
            user = await self.get_user(db, user_id, for_update=for_update)
            if user is None:
                raise
        return user

    async def get_user(
        self, db: AsyncSession, user_id: str, *, for_update: bool = False
    ) -> Optional[User]:
        """按 user_id 查询用户（for_update=True 加行锁）。"""
        stmt = select(User).where(User.id == user_id)
        if for_update:
            stmt = stmt.with_for_update()
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(
        self, db: AsyncSession, page_size: int = LIST_ALL_PAGE_SIZE
    ) -> list[User]:
        """列出全部用户（用于 Cron 批量处理）。

        分页拉取（每批 page_size，offset 迭代直至取完），避免硬上限漏处理。
        """
        users: list[User] = []
        offset = 0
        while True:
            result = await db.execute(
                select(User)
                .order_by(User.created_at.asc())
                .limit(page_size)
                .offset(offset)
            )
            batch = list(result.scalars().all())
            users.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        return users

    async def update_settings(
        self,
        db: AsyncSession,
        user_id: str,
        *,
        name: Optional[str] = None,
        language: Optional[str] = None,
        timezone: Optional[str] = None,
        chat_style: Optional[str] = None,
        push_time: Optional[str] = None,
        push_frequency: Optional[str] = None,
        push_enabled: Optional[bool] = None,
        mbti_type: Optional[str] = None,
        birthday: Optional[date] = None,
        enneagram_type: Optional[str] = None,
    ) -> Optional[User]:
        """更新用户设置（仅更新传入的非空字段）。"""
        values: dict = {}
        if name is not None:
            values["name"] = name
        if language is not None:
            values["language"] = language
        if timezone is not None:
            values["timezone"] = timezone
        if chat_style is not None:
            values["chat_style"] = chat_style
        if push_time is not None:
            values["push_time"] = push_time
        if push_frequency is not None:
            values["push_frequency"] = push_frequency
        if push_enabled is not None:
            values["push_enabled"] = push_enabled
        if mbti_type is not None:
            values["mbti_type"] = mbti_type
        if birthday is not None:
            values["birthday"] = birthday
        if enneagram_type is not None:
            values["enneagram_type"] = enneagram_type
        if not values:
            return await self.get_user(db, user_id)
        await db.execute(
            update(User).where(User.id == user_id).values(**values)
        )
        await db.flush()
        return await self.get_user(db, user_id)

    async def update_streak(
        self,
        db: AsyncSession,
        user_id: str,
        streak_count: int,
        last_active_date: date,
        active_days: int,
        streak_freeze_count: Optional[int] = None,
        streak_freeze_grant_month: Optional[str] = None,
    ) -> None:
        """更新连续签到天数、最近活跃日期、累计活跃天数与冻结券状态。"""
        values: dict = {
            "streak_count": streak_count,
            "last_active_date": last_active_date,
            "active_days": active_days,
        }
        if streak_freeze_count is not None:
            values["streak_freeze_count"] = streak_freeze_count
        if streak_freeze_grant_month is not None:
            values["streak_freeze_grant_month"] = streak_freeze_grant_month
        await db.execute(
            update(User)
            .where(User.id == user_id)
            .values(**values)
        )
        await db.flush()

    async def update_streak_freeze(
        self, db: AsyncSession, user_id: str, count: int, grant_month: str
    ) -> None:
        """月度发放冻结券（重置为该档位月配额并记录发放月份）。"""
        await db.execute(
            update(User)
            .where(User.id == user_id)
            .values(streak_freeze_count=count, streak_freeze_grant_month=grant_month)
        )
        await db.flush()

    async def increment_chat_count(self, db: AsyncSession, user_id: str) -> None:
        """对话轮次 +1（累计聊天次数）。"""
        await db.execute(
            update(User).where(User.id == user_id).values(chat_count=User.chat_count + 1)
        )
        await db.flush()

    async def update_relationship_level(
        self, db: AsyncSession, user_id: str, level: str
    ) -> None:
        """更新关系等级。"""
        await db.execute(
            update(User).where(User.id == user_id).values(relationship_level=level)
        )
        await db.flush()

    async def update_tier(self, db: AsyncSession, user_id: str, tier: str) -> None:
        """更新订阅档位（free/pro/pro_plus）。"""
        await db.execute(
            update(User).where(User.id == user_id).values(tier=tier)
        )
        await db.flush()

    async def set_ai_consent(self, db: AsyncSession, user_id: str, consent: bool) -> None:
        """更新 AI 数据共享同意状态（合规 5.1.2(i)）。"""
        await db.execute(
            update(User).where(User.id == user_id).values(ai_consent=consent)
        )
        await db.flush()

    async def increment_special_push(self, db: AsyncSession, user_id: str, month: str) -> None:
        """特殊事件推送月度计数 +1（month 为用户本地 "YYYY-MM"，跨月重置为 1）。"""
        user = await self.get_user(db, user_id)
        count = (
            (user.special_push_count or 0) + 1
            if user is not None and user.special_push_month == month
            else 1
        )
        await db.execute(
            update(User)
            .where(User.id == user_id)
            .values(special_push_count=count, special_push_month=month)
        )
        await db.flush()


user_repository = UserRepository()
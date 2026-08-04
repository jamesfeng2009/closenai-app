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
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.user import User


class UserRepository:
    """users 表数据访问。"""

    async def get_or_create_user(self, db: AsyncSession, user_id: str) -> User:
        """按 user_id 获取用户，不存在则创建（返回新行）。"""
        user = await self.get_user(db, user_id)
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
        await db.flush()
        return user

    async def get_user(self, db: AsyncSession, user_id: str) -> Optional[User]:
        """按 user_id 查询用户。"""
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def list_all(self, db: AsyncSession, limit: int = 1000) -> list[User]:
        """列出全部用户（用于 Cron 批量处理）。"""
        result = await db.execute(select(User).order_by(User.created_at.asc()).limit(limit))
        return list(result.scalars().all())

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
    ) -> None:
        """更新连续签到天数与最近活跃日期。"""
        await db.execute(
            update(User)
            .where(User.id == user_id)
            .values(streak_count=streak_count, last_active_date=last_active_date)
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


user_repository = UserRepository()
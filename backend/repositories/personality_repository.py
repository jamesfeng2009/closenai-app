"""
性格画像数据访问层（P0-F06）。

职责：只与 personality_profiles 表交互，不含业务逻辑。
"""
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.personality import PersonalityProfile


class PersonalityRepository:
    """personality_profiles 表数据访问。"""

    async def get_latest(self, db: AsyncSession, user_id: str) -> Optional[PersonalityProfile]:
        """获取用户最新一版性格画像。"""
        result = await db.execute(
            select(PersonalityProfile)
            .where(PersonalityProfile.user_id == user_id)
            .order_by(PersonalityProfile.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list(self, db: AsyncSession, user_id: str) -> List[PersonalityProfile]:
        """获取用户全部画像（按版本升序）。"""
        result = await db.execute(
            select(PersonalityProfile)
            .where(PersonalityProfile.user_id == user_id)
            .order_by(PersonalityProfile.version.asc())
        )
        return list(result.scalars().all())

    async def create(
        self,
        db: AsyncSession,
        user_id: str,
        mbti_type: Optional[str],
        observations: Optional[str],
        unique_traits: Optional[str],
        version: int,
    ) -> PersonalityProfile:
        """写入一版性格画像。"""
        profile = PersonalityProfile(
            id=str(uuid4()),
            user_id=user_id,
            mbti_type=mbti_type,
            observations=observations,
            unique_traits=unique_traits,
            version=version,
        )
        db.add(profile)
        await db.flush()
        return profile


personality_repository = PersonalityRepository()
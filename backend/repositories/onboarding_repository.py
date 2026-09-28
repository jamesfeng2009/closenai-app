"""
onboarding_profiles 数据访问层（架构方案 §二 M1）。

职责：只与 onboarding_profiles 表交互；初读卡的断言规则在 first_read_service。
"""
import json
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.journey import OnboardingProfile


class OnboardingRepository:
    """onboarding_profiles 表数据访问。"""

    async def get(self, db: AsyncSession, user_id: str) -> Optional[OnboardingProfile]:
        result = await db.execute(
            select(OnboardingProfile).where(OnboardingProfile.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        db: AsyncSession,
        user_id: str,
        persona_type: Optional[str],
        answers: list,
        first_read: dict,
        language: str,
    ) -> OnboardingProfile:
        """写入/覆盖初读卡结果（每用户一行，重复提交以最后一次为准）。"""
        profile = await self.get(db, user_id)
        if profile is None:
            profile = OnboardingProfile(
                id=str(uuid4()),
                user_id=user_id,
                persona_type=persona_type,
                answers=json.dumps(answers, ensure_ascii=False),
                first_read=json.dumps(first_read, ensure_ascii=False),
                language=language,
            )
            db.add(profile)
        else:
            profile.persona_type = persona_type
            profile.answers = json.dumps(answers, ensure_ascii=False)
            profile.first_read = json.dumps(first_read, ensure_ascii=False)
            profile.language = language
        await db.flush()
        return profile


onboarding_repository = OnboardingRepository()

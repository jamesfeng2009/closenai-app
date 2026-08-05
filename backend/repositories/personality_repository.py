"""
性格画像数据访问层（P0-F06）。

职责：只与 personality_profiles / personality_assessments 表交互，不含业务逻辑。
"""
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.time_utils import utc_now
from backend.models.personality import PersonalityAssessment, PersonalityProfile


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

    # ── 对话式评估（P0-F06：8 题问答状态）──

    async def get_in_progress_assessment(
        self, db: AsyncSession, user_id: str
    ) -> Optional[PersonalityAssessment]:
        """获取用户进行中的评估（最新一条 in_progress）。"""
        result = await db.execute(
            select(PersonalityAssessment)
            .where(
                PersonalityAssessment.user_id == user_id,
                PersonalityAssessment.status == "in_progress",
            )
            .order_by(PersonalityAssessment.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_completed_assessments(
        self, db: AsyncSession, user_id: str
    ) -> List[PersonalityAssessment]:
        """全部已完成评估（每次完成生成一版画像；刷新配额需剔除这部分"免费"版本）。"""
        result = await db.execute(
            select(PersonalityAssessment).where(
                PersonalityAssessment.user_id == user_id,
                PersonalityAssessment.status == "completed",
            )
        )
        return list(result.scalars().all())

    async def create_assessment(
        self, db: AsyncSession, user_id: str
    ) -> PersonalityAssessment:
        """开始一轮新评估。"""
        assessment = PersonalityAssessment(
            id=str(uuid4()), user_id=user_id, answers="[]", status="in_progress"
        )
        db.add(assessment)
        await db.flush()
        return assessment

    async def save_assessment(
        self,
        db: AsyncSession,
        assessment: PersonalityAssessment,
        answers: str,
        status: str,
    ) -> PersonalityAssessment:
        """保存评估答案与状态（显式刷新 updated_at，兼容不触发 onupdate 的方言）。"""
        assessment.answers = answers
        assessment.status = status
        assessment.updated_at = utc_now()
        await db.flush()
        return assessment


personality_repository = PersonalityRepository()
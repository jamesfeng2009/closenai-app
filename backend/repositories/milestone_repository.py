"""
里程碑徽章数据访问层（P0-F05）。

职责：只与 user_milestones 表交互，不含业务逻辑。
- 徽章列表查询 / 达成写入（(user_id, milestone_type) 唯一约束去重）
"""
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.growth import UserMilestone


class MilestoneRepository:
    """user_milestones 表数据访问。"""

    async def list_for_user(self, db: AsyncSession, user_id: str) -> List[UserMilestone]:
        """获取用户已达成的全部徽章（按达成时间升序）。"""
        result = await db.execute(
            select(UserMilestone)
            .where(UserMilestone.user_id == user_id)
            .order_by(UserMilestone.achieved_at.asc())
        )
        return list(result.scalars().all())

    async def create(
        self, db: AsyncSession, user_id: str, milestone_type: str
    ) -> Optional[UserMilestone]:
        """写入一枚徽章；已存在（唯一约束冲突）时返回 None（幂等，不重复达成）。"""
        record = UserMilestone(
            id=str(uuid4()), user_id=user_id, milestone_type=milestone_type
        )
        db.add(record)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            # 并发/重复达成兜底：回滚到 savepoint，视为已存在
            return None
        return record


milestone_repository = MilestoneRepository()

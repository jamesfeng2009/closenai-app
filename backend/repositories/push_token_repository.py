"""
推送 Token 数据访问层（P1-F01）。

职责：只与 push_tokens 表交互，不含业务逻辑。
"""
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.push import PushToken


class PushTokenRepository:
    """push_tokens 表数据访问。"""

    async def list_for_user(self, db: AsyncSession, user_id: str) -> List[PushToken]:
        """查询用户全部推送 Token（apns / fcm）。"""
        result = await db.execute(
            select(PushToken).where(PushToken.user_id == user_id)
        )
        return list(result.scalars().all())

    async def list_by_platform(self, db: AsyncSession, platform: str) -> List[PushToken]:
        """查询指定平台的全部 Token（用于批量推送）。"""
        result = await db.execute(
            select(PushToken).where(PushToken.platform == platform)
        )
        return list(result.scalars().all())

    async def get(
        self, db: AsyncSession, user_id: str, platform: str
    ) -> Optional[PushToken]:
        """查询某用户某平台的 Token。"""
        result = await db.execute(
            select(PushToken).where(
                PushToken.user_id == user_id, PushToken.platform == platform
            )
        )
        return result.scalar_one_or_none()

    async def delete(self, db: AsyncSession, token_id: str) -> None:
        """删除失效 Token。"""
        await db.execute(
            delete(PushToken).where(PushToken.id == token_id)
        )
        await db.flush()


push_token_repository = PushTokenRepository()
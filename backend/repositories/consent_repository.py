"""user_consents 数据访问层（Baseline §14.2）。

职责：只与 user_consents 表交互，不含联动业务（双写布尔位、PostHog 擦除在 service 层）。
幂等约定：同一 (user_id, purpose, version) 只有一行，重复表态刷新时刻而不是插新行。
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.consent import CONSENT_TEXT_VERSION, UserConsent


class ConsentRepository:
    """user_consents 表数据访问。"""

    async def get(
        self,
        db: AsyncSession,
        user_id: str,
        purpose: str,
        version: str = CONSENT_TEXT_VERSION,
    ) -> Optional[UserConsent]:
        result = await db.execute(
            select(UserConsent).where(
                UserConsent.user_id == user_id,
                UserConsent.purpose == purpose,
                UserConsent.version == version,
            )
        )
        return result.scalars().first()

    async def list_for_user(
        self, db: AsyncSession, user_id: str
    ) -> list[UserConsent]:
        """某用户全部同意台账（用途＋版本正序，API 直接按这个顺序回）。"""
        result = await db.execute(
            select(UserConsent)
            .where(UserConsent.user_id == user_id)
            .order_by(UserConsent.purpose.asc(), UserConsent.version.asc())
        )
        return list(result.scalars().all())

    async def record(
        self,
        db: AsyncSession,
        user_id: str,
        purpose: str,
        granted: bool,
        at: datetime,
        version: str = CONSENT_TEXT_VERSION,
    ) -> UserConsent:
        """写一条表态（授予⇒刷新 granted_at、清 revoked_at；撤回⇒写 revoked_at）。

        先查后写；并发撞上 uq_user_consents_user_purpose_version 时由调用方
        （consent_service 的用户行锁 FOR UPDATE）串行化，表约束只是兜底防线。
        """
        existing = await self.get(db, user_id, purpose, version)
        if existing is not None:
            if granted:
                existing.granted_at = at
                existing.revoked_at = None
            else:
                existing.revoked_at = at
            await db.flush()
            return existing
        row = UserConsent(
            id=str(uuid4()),
            user_id=user_id,
            purpose=purpose,
            version=version,
            granted_at=at if granted else None,
            revoked_at=None if granted else at,
        )
        db.add(row)
        await db.flush()
        return row

consent_repository = ConsentRepository()

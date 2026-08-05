"""
订阅与收据数据访问层（P1-F02）。

职责：只与 subscriptions / receipts 表交互，不含业务逻辑。
- 订阅的 upsert / 查询 / 过期 / 退款
- 收据的写入与查询
- 试用到期提醒的扫描与标记
"""
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.subscription import Subscription, Receipt


class SubscriptionRepository:
    """subscriptions / receipts 表数据访问。"""

    async def get_active(self, db: AsyncSession, user_id: str) -> Optional[Subscription]:
        """查询用户当前激活的订阅。"""
        result = await db.execute(
            select(Subscription)
            .where(Subscription.user_id == user_id, Subscription.is_active.is_(True))
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_by_original_transaction(
        self, db: AsyncSession, original_transaction_id: str
    ) -> Optional[Subscription]:
        """按原始交易 ID 查询订阅（用于续订/退款通知回查）。"""
        result = await db.execute(
            select(Subscription).where(
                Subscription.original_transaction_id == original_transaction_id
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        db: AsyncSession,
        user_id: str,
        *,
        product_id: str,
        tier: str,
        expires_at: Optional[datetime],
        original_transaction_id: Optional[str],
        is_active: bool = True,
        is_trial: bool = False,
    ) -> Subscription:
        """写入/更新订阅。同一用户同一原始交易只保留一条，重复则更新。"""
        existing = None
        if original_transaction_id:
            existing = await self.get_by_original_transaction(db, original_transaction_id)
        if existing is None:
            existing = await self.get_active(db, user_id)

        if existing:
            existing.product_id = product_id
            existing.tier = tier
            # 订阅周期变化（新购/续期）时重置提醒状态，允许对新周期再提醒一次
            if existing.expires_at != expires_at:
                existing.trial_reminder_sent = False
            existing.expires_at = expires_at
            existing.original_transaction_id = original_transaction_id or existing.original_transaction_id
            existing.is_active = is_active
            existing.is_trial = is_trial
            await db.flush()
            return existing

        sub = Subscription(
            id=str(uuid4()),
            user_id=user_id,
            product_id=product_id,
            tier=tier,
            expires_at=expires_at,
            original_transaction_id=original_transaction_id,
            is_active=is_active,
            is_trial=is_trial,
        )
        db.add(sub)
        await db.flush()
        return sub

    async def list_expiring_trials(
        self, db: AsyncSession, now: datetime, until: datetime
    ) -> List[Subscription]:
        """扫描 (now, until] 窗口内到期且未提醒的试用订阅。"""
        result = await db.execute(
            select(Subscription).where(
                Subscription.is_active.is_(True),
                Subscription.is_trial.is_(True),
                Subscription.trial_reminder_sent.is_(False),
                Subscription.expires_at.isnot(None),
                Subscription.expires_at > now,
                Subscription.expires_at <= until,
            )
        )
        return list(result.scalars().all())

    async def mark_trial_reminded(self, db: AsyncSession, subscription_id: str) -> None:
        """标记试用到期提醒已发送。"""
        await db.execute(
            update(Subscription)
            .where(Subscription.id == subscription_id)
            .values(trial_reminder_sent=True)
        )
        await db.flush()

    async def set_active(self, db: AsyncSession, subscription_id: str, is_active: bool) -> None:
        """更新订阅激活状态。"""
        await db.execute(
            update(Subscription).where(Subscription.id == subscription_id).values(is_active=is_active)
        )
        await db.flush()

    async def create_receipt(
        self,
        db: AsyncSession,
        user_id: str,
        *,
        product_id: str,
        receipt_data: str,
        status: str,
        transaction_id: Optional[str] = None,
    ) -> Receipt:
        """写入一条收据审计记录。"""
        receipt = Receipt(
            id=str(uuid4()),
            user_id=user_id,
            product_id=product_id,
            receipt_data=receipt_data[:8192],
            status=status,
            transaction_id=transaction_id,
        )
        db.add(receipt)
        await db.flush()
        return receipt


subscription_repository = SubscriptionRepository()
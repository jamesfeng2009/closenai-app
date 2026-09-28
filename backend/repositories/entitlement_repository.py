"""
user_entitlements 数据访问层（架构方案 §二 M6）。

一次性买断权益：与 subscriptions / users.tier 完全解耦，
判定只看 revoked_at IS NULL 的既有行（退款后置空权益）。
"""
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.journey import UserEntitlement

# 档位高低（导出范围包含关系：legacy ⊃ all ⊃ 1y），取用户最高档判定权限
SCOPE_RANK = {"1y": 1, "all": 2, "legacy": 3}


class EntitlementRepository:
    """user_entitlements 表数据访问。"""

    async def grant(
        self,
        db: AsyncSession,
        user_id: str,
        product_id: str,
        scope: str,
        transaction_id: str,
        purchase_date: datetime,
        original_transaction_id: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> UserEntitlement:
        """写入一笔买断权益（幂等：同 transaction_id 命中即复用，重装/重试不重复发放）。

        跨账号复用同一 transaction 由调用方（iap_service）先行判定并拒单——
        仓库层不改绑既有归属，避免收据所有权在此静默漂移。
        已退款（revoked_at 非空）的收据重新授权时恢复为有效——对应 Apple
        REFUND 后用户在商店内恢复购买的可预期行为。
        """
        existing = await self.get_by_transaction(db, transaction_id)
        if existing is not None:
            changed = False
            if existing.revoked_at is not None:
                existing.revoked_at = None
                changed = True
            if existing.scope != scope:
                existing.scope = scope
                changed = True
            if changed:
                await db.flush()
            return existing
        row = UserEntitlement(
            id=str(uuid4()),
            user_id=user_id,
            product_id=product_id,
            scope=scope,
            transaction_id=transaction_id,
            original_transaction_id=original_transaction_id,
            purchase_date=purchase_date,
            environment=environment,
        )
        db.add(row)
        await db.flush()
        return row

    async def get_by_transaction(
        self, db: AsyncSession, transaction_id: str
    ) -> Optional[UserEntitlement]:
        result = await db.execute(
            select(UserEntitlement).where(UserEntitlement.transaction_id == transaction_id)
        )
        return result.scalar_one_or_none()

    async def list_active(self, db: AsyncSession, user_id: str) -> List[UserEntitlement]:
        result = await db.execute(
            select(UserEntitlement)
            .where(
                UserEntitlement.user_id == user_id,
                UserEntitlement.revoked_at.is_(None),
            )
            .order_by(UserEntitlement.purchase_date.asc())
        )
        return list(result.scalars().all())

    async def best_scope(self, db: AsyncSession, user_id: str) -> Optional[str]:
        """用户当前最高权益档（无有效权益返回 None）。"""
        rows = await self.list_active(db, user_id)
        scopes = [r.scope for r in rows if r.scope in SCOPE_RANK]
        return max(scopes, key=SCOPE_RANK.__getitem__) if scopes else None

    async def has(self, db: AsyncSession, user_id: str, scope: str) -> bool:
        """是否持有 ≥ 指定档位的权益（高档向下兼容低档导出范围）。"""
        best = await self.best_scope(db, user_id)
        if best is None or scope not in SCOPE_RANK:
            return False
        return SCOPE_RANK[best] >= SCOPE_RANK[scope]

    async def revoke_by_transaction(
        self, db: AsyncSession, transaction_id: str, at: datetime
    ) -> Optional[UserEntitlement]:
        row = await self.get_by_transaction(db, transaction_id)
        if row is None:
            return None
        row.revoked_at = at
        await db.flush()
        return row

    async def revoke_by_original_transaction(
        self, db: AsyncSession, original_transaction_id: str, at: datetime
    ) -> int:
        """按原始交易撤销全部权益（Apple REFUND/REVOKE 通知口径）。返回撤销行数。"""
        result = await db.execute(
            select(UserEntitlement).where(
                UserEntitlement.original_transaction_id == original_transaction_id,
                UserEntitlement.revoked_at.is_(None),
            )
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.revoked_at = at
        await db.flush()
        return len(rows)


entitlement_repository = EntitlementRepository()

"""
refresh_tokens 表数据访问层。

职责：只与 refresh_tokens 表交互，不含业务逻辑。
- 创建 / 按 hash 查询 / 吊销（单条 + 全量）refresh token
- 轮转替换链
"""
from typing import Optional
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.time_utils import utc_now
from backend.models.auth import RefreshToken


class AuthRepository:
    """refresh_tokens 表数据访问。"""

    async def create_refresh_token(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        token_hash: str,
        expires_at,
        token_id: Optional[str] = None,
    ) -> str:
        """创建 refresh token 记录，返回 token id（即 JWT jti）。"""
        tid = token_id or str(uuid4())
        db.add(
            RefreshToken(
                id=tid,
                user_id=user_id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
        )
        await db.flush()
        return tid

    async def get_active_by_hash(
        self, db: AsyncSession, token_hash: str
    ) -> Optional[RefreshToken]:
        """按 hash 取未吊销的 refresh token 记录。"""
        stmt = select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_hash_any(
        self, db: AsyncSession, token_hash: str, *, for_update: bool = False
    ) -> Optional[RefreshToken]:
        """按 hash 取 refresh token 记录（含已吊销，供轮转重放检测）。

        for_update=True 加行锁串行化并发刷新（SQLite 方言忽略该子句）。
        """
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        if for_update:
            stmt = stmt.with_for_update()
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_expired(self, db: AsyncSession) -> int:
        """物理删除已过期的 refresh token（cron 定期清理，防表无限增长）。"""
        result = await db.execute(
            delete(RefreshToken).where(RefreshToken.expires_at < utc_now())
        )
        await db.flush()
        return result.rowcount or 0

    async def revoke_token(
        self,
        db: AsyncSession,
        token_id: str,
        *,
        replaced_by: Optional[str] = None,
    ) -> None:
        """吊销单条 refresh token（可记录替换者）。"""
        values: dict = {"revoked_at": utc_now()}
        if replaced_by is not None:
            values["replaced_by"] = replaced_by
        await db.execute(update(RefreshToken).where(RefreshToken.id == token_id).values(**values))
        await db.flush()

    async def revoke_all_for_user(self, db: AsyncSession, user_id: str) -> None:
        """吊销该用户全部未吊销 refresh token（改密 / 重置 / 强制下线时调用）。"""
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utc_now())
        )
        await db.flush()


auth_repository = AuthRepository()

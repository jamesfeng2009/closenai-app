"""
认证模型。

RefreshToken：refresh token 轮转与吊销（哈希存储，明文永不落库）。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class RefreshToken(Base):
    """refresh token 记录（轮转 + 吊销链，防重放）。

    - token_hash: sha256(refresh_token 明文)，用于按 hash 查询校验
    - revoked_at: 非 None 表示已吊销（登出 / 改密 / 重置 / 轮转替换）
    - replaced_by: 轮转时指向新 token id，形成替换链
    """

    __tablename__ = "refresh_tokens"

    # 即 JWT 的 jti（uuid4）
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    # sha256 hex（64 字符）
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    __table_args__ = (
        Index("ix_refresh_tokens_user", "user_id"),
        Index("ix_refresh_tokens_hash", "token_hash"),
    )

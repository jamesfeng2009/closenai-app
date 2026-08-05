"""add auth fields and refresh_tokens table

Revision ID: d1e3f5a7b9c2
Revises: 69b435ebef56
Create Date: 2026-08-05 00:00:00.000000

- users: 新增 email / username / password_hash / apple_id / email_verified_at（认证字段）
- 3 个 partial unique index（email / username / apple_id，仅约束非 NULL 行，允许多个 NULL 共存）
- 新建 refresh_tokens 表（refresh token 轮转与吊销，哈希存储）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1e3f5a7b9c2'
down_revision: Union[str, None] = '69b435ebef56'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users 认证字段 ──
    op.add_column('users', sa.Column('email', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('username', sa.String(length=50), nullable=True))
    op.add_column('users', sa.Column('password_hash', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('apple_id', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True))

    # ── partial unique index（仅对非 NULL 行生效，允许多个 NULL 共存）──
    # SQLite / PostgreSQL 均支持 partial index
    op.execute(
        "CREATE UNIQUE INDEX ix_users_email_unique ON users (email) WHERE email IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_users_username_unique ON users (username) WHERE username IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_users_apple_id_unique ON users (apple_id) WHERE apple_id IS NOT NULL"
    )

    # ── refresh_tokens 表 ──
    op.create_table(
        'refresh_tokens',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('replaced_by', sa.String(length=36), nullable=True),
    )
    op.create_index('ix_refresh_tokens_user', 'refresh_tokens', ['user_id'])
    op.create_index('ix_refresh_tokens_hash', 'refresh_tokens', ['token_hash'])


def downgrade() -> None:
    op.drop_index('ix_refresh_tokens_hash', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_user', table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
    op.execute("DROP INDEX IF EXISTS ix_users_apple_id_unique")
    op.execute("DROP INDEX IF EXISTS ix_users_username_unique")
    op.execute("DROP INDEX IF EXISTS ix_users_email_unique")
    op.drop_column('users', 'email_verified_at')
    op.drop_column('users', 'apple_id')
    op.drop_column('users', 'password_hash')
    op.drop_column('users', 'username')
    op.drop_column('users', 'email')

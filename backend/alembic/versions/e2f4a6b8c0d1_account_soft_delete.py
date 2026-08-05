"""account soft delete: status + deleted_at

Revision ID: e2f4a6b8c0d1
Revises: d1e3f5a7b9c2
Create Date: 2026-08-05 12:00:00.000000

- users: 新增 status(default 'active') / deleted_at 字段
- 重建 3 个 partial unique index（email / username / apple_id），条件加 deleted_at IS NULL
  → 软删除记录不占唯一约束位，同 email/apple_id/username 注销后可重新注册
- 新增 ix_users_deleted 索引（仅 deleted 行，供 cron 兜底扫描）

注销策略：DELETE /user/account 改为软删除（status='deleted' + deleted_at=now）即时失效，
QStash 异步投递物理删除任务到 /cron/purge-user。30 分钟 access_token 窗口期内，
get_user 过滤 deleted 用户 → 业务路径抛 USER_NOT_FOUND → 前端登出，不建 orphan。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2f4a6b8c0d1'
down_revision: Union[str, None] = 'd1e3f5a7b9c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users 账号生命周期字段 ──
    op.add_column(
        'users',
        sa.Column('status', sa.String(length=20), server_default='active', nullable=False),
    )
    op.add_column(
        'users',
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )

    # ── 重建 3 个 partial unique index（加 deleted_at IS NULL 条件）──
    # 软删除记录不参与唯一约束 → 同 email/username/apple_id 注销后可重新注册（新 user_id）
    # SQLite / PostgreSQL 均支持 partial index
    op.execute("DROP INDEX IF EXISTS ix_users_email_unique")
    op.execute(
        "CREATE UNIQUE INDEX ix_users_email_unique ON users (email) "
        "WHERE email IS NOT NULL AND deleted_at IS NULL"
    )
    op.execute("DROP INDEX IF EXISTS ix_users_username_unique")
    op.execute(
        "CREATE UNIQUE INDEX ix_users_username_unique ON users (username) "
        "WHERE username IS NOT NULL AND deleted_at IS NULL"
    )
    op.execute("DROP INDEX IF EXISTS ix_users_apple_id_unique")
    op.execute(
        "CREATE UNIQUE INDEX ix_users_apple_id_unique ON users (apple_id) "
        "WHERE apple_id IS NOT NULL AND deleted_at IS NULL"
    )

    # ── cron 兜底扫描索引（只索引 deleted 行，省空间）──
    # 供 /cron/purge-deleted-users 扫描 deleted_at 超 1 小时未物理删除的记录（P1 待办）
    op.execute(
        "CREATE INDEX ix_users_deleted ON users (deleted_at) WHERE deleted_at IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_deleted")
    op.execute("DROP INDEX IF EXISTS ix_users_apple_id_unique")
    op.execute(
        "CREATE UNIQUE INDEX ix_users_apple_id_unique ON users (apple_id) "
        "WHERE apple_id IS NOT NULL"
    )
    op.execute("DROP INDEX IF EXISTS ix_users_username_unique")
    op.execute(
        "CREATE UNIQUE INDEX ix_users_username_unique ON users (username) "
        "WHERE username IS NOT NULL"
    )
    op.execute("DROP INDEX IF EXISTS ix_users_email_unique")
    op.execute(
        "CREATE UNIQUE INDEX ix_users_email_unique ON users (email) WHERE email IS NOT NULL"
    )
    op.drop_column('users', 'deleted_at')
    op.drop_column('users', 'status')

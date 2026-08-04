"""add push frequency fields to users

Revision ID: 4b6c8d0e2f1a
Revises: 3a5b7c9d1e2f
Create Date: 2026-08-04 01:40:00.000000

- users: 新增 push_frequency（daily|important|off）与 push_enabled（推送总开关）
- 对应 P1-F01 推送频率控制 / P1-F05 引导默认关闭推送
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4b6c8d0e2f1a'
down_revision: Union[str, None] = '3a5b7c9d1e2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('push_frequency', sa.String(length=10), nullable=False, server_default='daily'),
    )
    op.add_column(
        'users',
        sa.Column('push_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('users', 'push_enabled')
    op.drop_column('users', 'push_frequency')
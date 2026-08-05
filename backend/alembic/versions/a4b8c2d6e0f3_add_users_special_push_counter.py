"""users 表新增特殊事件推送月度计数（P1-F01 §6）

Revision ID: a4b8c2d6e0f3
Revises: 9e1b3d5f7a2c
Create Date: 2026-08-04 20:00:00.000000

- users.special_push_count：当月特殊事件（里程碑/连续负面关心类）推送已发次数，每月 ≤4
- users.special_push_month：计数所属月份（用户本地 "YYYY-MM"），跨月由写入方重置为 1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4b8c2d6e0f3'
down_revision: Union[str, None] = '9e1b3d5f7a2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('special_push_count', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'users',
        sa.Column('special_push_month', sa.String(length=7), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('users', 'special_push_month')
    op.drop_column('users', 'special_push_count')

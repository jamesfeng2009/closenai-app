"""P2 #24 主用性格框架：users.primary_framework（mbti | enneagram）

Revision ID: a3c5e7f9b1d2
Revises: f2a4c6e8a0b2
Create Date: 2026-11-09

为什么加这一列：前端「选择性格框架」原先只调 PATCH 再改本地 state，后端
`UserSettingsUpdate` 里根本没有这个字段 ⇒ 写不进去；个人页重进就退回默认框架。
列可空、无默认值：存量用户一律等于「未选过」，不替他们猜一个框架。

无反向迁移原则照旧：downgrade 仅供本地回退，生产只正向执行。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c5e7f9b1d2'
down_revision: Union[str, Sequence[str], None] = 'f2a4c6e8a0b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('primary_framework', sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('users', 'primary_framework')

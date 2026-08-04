"""add users.language column

Revision ID: 2f9c1a3b8d4e
Revises: 41cb43e22901
Create Date: 2026-08-04 00:40:00.000000

多语言支持：为 users 表新增 language 字段（默认 en），控制 LLM 回复语言。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2f9c1a3b8d4e'
down_revision: Union[str, None] = '41cb43e22901'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('language', sa.String(length=10), nullable=True, server_default='en'),
    )
    # 既有行默认 en，随后收紧为 NOT NULL
    op.alter_column('users', 'language', nullable=False, server_default='en')


def downgrade() -> None:
    op.drop_column('users', 'language')
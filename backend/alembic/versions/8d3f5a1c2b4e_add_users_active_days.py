"""users 表新增 active_days（累计活跃天数）

Revision ID: 8d3f5a1c2b4e
Revises: 7c2e4a9b1f03
Create Date: 2026-08-04 15:30:00.000000

背景：
- PRD P0-F05 要求关系等级按"累计对话天数"晋升（L1 0-6 / L2 7-29 / L3 30-89 / L4 90+），
  原实现错误地按 chat_count（消息数）计算且该字段无增长链路，等级恒为 L1。
- 新增 active_days 字段，在每日首次活跃（update_streak）时累计 +1，与 streak 中断解耦。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '8d3f5a1c2b4e'
down_revision: Union[str, None] = '7c2e4a9b1f03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS active_days INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS active_days")

"""growth_records 表新增 follow_up_sent（事件后 3-7 天主动跟进防重复标记）

Revision ID: c5d7e9f1a3b6
Revises: d7e9f1a3b5c7
Create Date: 2026-08-08 10:00:00.000000

背景：
- PRD P0-F07 事件后主动跟进：每日扫描 3-7 天前的 reflection/milestone 记录，
  生成「上次你提到...现在怎么样了？」式跟进消息写入对话流。
- follow_up_sent 标记该记录是否已发送跟进消息，防 cron 重复触发导致重复发送；
  存量记录默认 false（窗口外的旧记录不会被扫描，无需回填）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5d7e9f1a3b6'
down_revision: Union[str, Sequence[str], None] = 'd7e9f1a3b5c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "growth_records",
        sa.Column("follow_up_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("growth_records", "follow_up_sent")

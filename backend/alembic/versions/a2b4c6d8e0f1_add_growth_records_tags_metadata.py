"""growth_records 表新增 tags / metadata_json

Revision ID: a2b4c6d8e0f1
Revises: 9e1b3d5f7a2c
Create Date: 2026-08-04 20:00:00.000000

背景：
- PRD P0-F07 §6：洞察标签体系（#情绪 #关系 #工作 等），支持按标签筛选记录。
- PRD P1-F03 §1：周报生成时同步保存结构化摘要（mood_distribution/top_topics 等 JSON）。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a2b4c6d8e0f1'
down_revision: Union[str, Sequence[str], None] = '9e1b3d5f7a2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE growth_records ADD COLUMN IF NOT EXISTS tags TEXT")
    op.execute("ALTER TABLE growth_records ADD COLUMN IF NOT EXISTS metadata_json TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE growth_records DROP COLUMN IF EXISTS metadata_json")
    op.execute("ALTER TABLE growth_records DROP COLUMN IF EXISTS tags")

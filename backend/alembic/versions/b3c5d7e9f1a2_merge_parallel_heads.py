"""合并并行任务的四个迁移 head，恢复单头线性链

Revision ID: b3c5d7e9f1a2
Revises: a2b4c6d8e0f1, a2f4b6c8d0e2, a4b8c2d6e0f3, c4d6e8f0a2b4
Create Date: 2026-08-04 20:30:00.000000

背景：
- 四个并行任务各自基于 9e1b3d5f7a2c 新增了迁移（growth_records 标签/元数据、
  Streak 冻结券/里程碑/性格评估、特殊事件推送计数、分享导出/试用字段），
  形成 4 个 head；本迁移为纯 merge（无 schema 变更），恢复单头。
- 先例：9e1b3d5f7a2c 曾合并 6d8e0f2a4b3c 与 8d3f5a1c2b4e 两个 head。
"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = 'b3c5d7e9f1a2'
down_revision: Union[str, Sequence[str], None] = (
    'a2b4c6d8e0f1',
    'a2f4b6c8d0e2',
    'a4b8c2d6e0f3',
    'c4d6e8f0a2b4',
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

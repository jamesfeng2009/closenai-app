"""memories 表新增 deleted（软删除标记）

Revision ID: 9e1b3d5f7a2c
Revises: 6d8e0f2a4b3c, 8d3f5a1c2b4e
Create Date: 2026-08-04 18:00:00.000000

背景：
- PRD P0-F02/spec memory-layer §6：用户主动删除记忆走软删除（deleted=true），
  不物理删除；删除 current 记忆时对应的 superseded 记忆恢复为 current。
- 用户注销级联物理删除（user_service.delete_account）不受软删除影响。
- 本迁移同时合并现存两个 head（6d8e0f2a4b3c 与 8d3f5a1c2b4e），恢复单头线性链。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '9e1b3d5f7a2c'
down_revision: Union[str, Sequence[str], None] = ('6d8e0f2a4b3c', '8d3f5a1c2b4e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE memories ADD COLUMN IF NOT EXISTS deleted BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS deleted")

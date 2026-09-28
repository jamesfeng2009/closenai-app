"""personality_assessments 新增 kind 列（PRD P1-F03 九型人格框架接入）

Revision ID: d7e9f1a3b5c7
Revises: c6d8e0f2a4b6
Create Date: 2026-08-08

背景：
- P1-F03：九型人格 36 题渐进式评估与 P0-F06 的 8 题 16 型评估共用
  personality_assessments 表（同为"答案 JSON + 状态"结构），新增 kind 列区分：
  mbti（默认，存量行由 server_default 回填）| enneagram。
- 刷新配额口径（list_completed_assessments）只统计 kind='mbti' 的已完成评估，
  九型评估不影响 16 型画像的"首次免费"剔除逻辑。
"""
from alembic import op
import sqlalchemy as sa

revision = "d7e9f1a3b5c7"
down_revision = "c6d8e0f2a4b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "personality_assessments",
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
            server_default="mbti",
        ),
    )


def downgrade() -> None:
    op.drop_column("personality_assessments", "kind")

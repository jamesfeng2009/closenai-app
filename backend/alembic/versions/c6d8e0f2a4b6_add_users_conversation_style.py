"""add conversation_style to users (PRD P1-F04 三种 AI 对话风格)

Revision ID: c6d8e0f2a4b6
Revises: b8d0e2f4a6c8
Create Date: 2026-08-08

枚举值：warm 温暖知心 / rational 理性分析 / philosophical 哲学思辨，默认 warm。
存量用户由 server_default 回填 warm。
"""
from alembic import op
import sqlalchemy as sa

revision = "c6d8e0f2a4b6"
down_revision = "b8d0e2f4a6c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "conversation_style",
            sa.String(length=20),
            nullable=False,
            server_default="warm",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "conversation_style")

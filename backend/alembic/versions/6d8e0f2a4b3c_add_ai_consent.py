"""add ai_consent to users (App Store 合规 5.1.2(i) 数据共享同意)

Revision ID: 6d8e0f2a4b3c
Revises: 5c7d9e1f3a2b
Create Date: 2026-08-04
"""
from alembic import op
import sqlalchemy as sa

revision = "6d8e0f2a4b3c"
down_revision = "5c7d9e1f3a2b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("ai_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "ai_consent")

"""add analytics_consent to users (GDPR/5.1.2(i) 独立分析同意)

Revision ID: f8c9a1b3d5e7
Revises: e2f4a6b8c0d1
Create Date: 2026-08-07
"""
from alembic import op
import sqlalchemy as sa

revision = "f8c9a1b3d5e7"
down_revision = "e2f4a6b8c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("analytics_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "analytics_consent")

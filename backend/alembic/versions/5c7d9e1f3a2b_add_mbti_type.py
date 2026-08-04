"""add mbti_type to users (P1-F05 onboarding seed)

Revision ID: 5c7d9e1f3a2b
Revises: 4b6c8d0e2f1a
Create Date: 2026-08-04
"""
from alembic import op
import sqlalchemy as sa

revision = "5c7d9e1f3a2b"
down_revision = "4b6c8d0e2f1a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("mbti_type", sa.String(length=4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "mbti_type")
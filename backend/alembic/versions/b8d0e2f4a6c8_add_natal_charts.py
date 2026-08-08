"""新增 natal_charts 本命星盘表

Revision ID: b8d0e2f4a6c8
Revises: e4f6a8b0c2d4
Create Date: 2026-08-08

背景：
- PRD 本命星盘（探索 Tab 可选入口）：出生信息录入 → 三大星座（太阳/月亮/上升）
  + 行星位置（水/金/火/木/土所在星座，纯文本）+ AI 文本解读缓存。
- 时区由后端从出生城市推导（内置城市库），用户不填时区；城市库未命中回退
  users.timezone 并以 tz_fallback 标注。
- 出生时间未知（birth_time_unknown）按 12:00 计算，rising_sign 置 NULL。
- 每用户一条：uq_natal_charts_user 唯一约束。
- PRD 禁止项不落库：圆形星盘轮盘图数据、爱情运势、合盘/匹配。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b8d0e2f4a6c8"
down_revision: Union[str, None] = "e4f6a8b0c2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "natal_charts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=False),
        sa.Column("birth_time", sa.Time(), nullable=True),
        sa.Column("birth_time_unknown", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("birth_place", sa.String(100), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("tz_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sun_sign", sa.String(20), nullable=True),
        sa.Column("moon_sign", sa.String(20), nullable=True),
        sa.Column("rising_sign", sa.String(20), nullable=True),
        sa.Column("planets_json", sa.Text(), nullable=True),
        sa.Column("interpretation_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", name="uq_natal_charts_user"),
    )
    op.create_index("ix_natal_charts_user_id", "natal_charts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_natal_charts_user_id", table_name="natal_charts")
    op.drop_table("natal_charts")

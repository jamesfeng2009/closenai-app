"""T7-02 分析事件逐事件接收台账：analytics_event_receipts

Revision ID: b7c9d1e3f5a2
Revises: f6b8d0e2a4c6
Create Date: 2026-10-25

为什么需要这张表：`/analytics/batch` 原来只回 `{relayed, dropped}`，HTTP 200 被前端
当成「全部接收」。同一条事件在网络重试里会被再送一次，服务端没有任何幂等键可以拦住。
本表以 `(user_id, event_id)` 为主键：认领 → 中继 → 置 accepted。重复提交命中主键时
直接回报 duplicate，不再外发。可重试失败删行（下一次能重新认领），永久拒绝不落行。

口径：
- 无外键（D-5）：注销由 `PURGE_TARGETS` 逐表删行，本表在清单里。
- `status` 只写 pending / accepted；duplicate/permanent 是响应状态，不落到表里。
- 不做 canonical DDL 快照的输入：Go 侧按 raw SQL 读写（与 legacy_batch1.go 同法），
  本表不属于 `export_pg_schema.py` 的三份 canonical 快照。

无反向迁移原则照旧：downgrade 仅供本地回退，生产只正向执行。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7c9d1e3f5a2'
down_revision: Union[str, Sequence[str], None] = 'f6b8d0e2a4c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'analytics_event_receipts',
        sa.Column('user_id', sa.String(length=36), primary_key=True),
        sa.Column('event_id', sa.String(length=128), primary_key=True),
        sa.Column('status', sa.String(length=16), nullable=False,
                  server_default='pending'),
        sa.Column('reason', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('analytics_event_receipts')

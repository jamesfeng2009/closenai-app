"""source.deleted 本地投递台账：source_event_outbox（P2 #20）

Revision ID: f2a4c6e8a0b2
Revises: b7c9d1e3f5a2
Create Date: 2026-11-09

为什么需要这张表：`source.deleted` 原来只在提交之后投一次，失败只剩一行日志。删除
已经把 memories 行删掉了，事件丢了就再也补不上——Go 的 source_records 会一直指着
一条不存在的行。本表把投递意图写进删行的同一笔事务，投递失败留 pending 行，
`/cron/purge-deleted-users`（每小时）按退避补投。

口径：
- `message_id` 作主键：由 `(user_id, source_table, source_id)` 确定性派生，
  同一次删除的重试收敛成一行，不会被补投叠出重复行。
- 只存指针与 trace，不存消息体：体由契约层重新编码，避免两处 payload 漂移。
- 无外键（D-5）：带 user_id ⇒ 已登记进 `PURGE_TARGETS`，注销时一并删除。
- 不属于 canonical 快照面（与 analytics_event_receipts 同法），不参与
  `export_pg_schema.py` 的三份快照。

无反向迁移原则照旧：downgrade 仅供本地回退，生产只正向执行。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2a4c6e8a0b2'
down_revision: Union[str, Sequence[str], None] = 'b7c9d1e3f5a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'source_event_outbox',
        sa.Column('message_id', sa.String(length=128), primary_key=True),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('source_table', sa.String(length=64), nullable=False),
        sa.Column('source_id', sa.String(length=128), nullable=False),
        sa.Column('trace_id', sa.String(length=128), nullable=False,
                  server_default=''),
        sa.Column('status', sa.String(length=16), nullable=False,
                  server_default='pending'),
        sa.Column('attempt_count', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('available_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(length=512), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_source_event_outbox_user_id', 'source_event_outbox',
                    ['user_id'])
    op.create_index('ix_source_event_outbox_claim', 'source_event_outbox',
                    ['status', 'available_at'])


def downgrade() -> None:
    # 项目约定：仅正向执行，反向迁移供本地回退用，生产不执行
    op.drop_index('ix_source_event_outbox_claim', table_name='source_event_outbox')
    op.drop_index('ix_source_event_outbox_user_id', table_name='source_event_outbox')
    op.drop_table('source_event_outbox')

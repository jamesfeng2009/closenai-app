"""P2 #28 离线补投键：chat_messages.client_message_id（同一用户内唯一）

Revision ID: b4d6f8a0c2e4
Revises: a3c5e7f9b1d2
Create Date: 2026-11-09

为什么需要：离线时用户消息只落在本机 SQLite，联网后要补投到服务端；在线那条
已经由 SSE 在阶段一落库。两条路若各生成一个 uuid，同一条消息就会出现两次。
补投与在线发送都带同一个客户端 id，由 (user_id, client_message_id) 的**部分**
唯一索引兜底：服务端自己生成的消息（AI 回复/主动消息）该列为 NULL，不进索引。

无反向迁移原则照旧：downgrade 仅供本地回退，生产只正向执行。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4d6f8a0c2e4'
down_revision: Union[str, Sequence[str], None] = 'a3c5e7f9b1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'chat_messages',
        sa.Column('client_message_id', sa.String(length=64), nullable=True),
    )
    op.create_index(
        'uq_chat_messages_client_id',
        'chat_messages',
        ['user_id', 'client_message_id'],
        unique=True,
        postgresql_where=sa.text('client_message_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_chat_messages_client_id', table_name='chat_messages')
    op.drop_column('chat_messages', 'client_message_id')

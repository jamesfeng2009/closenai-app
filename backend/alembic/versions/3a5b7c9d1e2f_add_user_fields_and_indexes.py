"""add user fields and performance indexes

Revision ID: 3a5b7c9d1e2f
Revises: 2f9c1a3b8d4e
Create Date: 2026-08-04 00:50:00.000000

- users: 新增 name / push_time / chat_style 字段
- 为高频查询/写入路径补充组合索引（user_id + 时间/状态/类型）
- memories.embedding 增加 pgvector HNSW 索引（加速语义检索）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3a5b7c9d1e2f'
down_revision: Union[str, None] = '2f9c1a3b8d4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users 新增字段 ──
    op.add_column('users', sa.Column('name', sa.String(length=100), nullable=True))
    op.add_column('users', sa.Column('push_time', sa.String(length=5), nullable=True))
    op.add_column('users', sa.Column('chat_style', sa.String(length=30), nullable=True))

    # 覆盖导入：兼容旧版本 pgvector 无 sqlalchemy() 的情况
    from pgvector.sqlalchemy import Vector

    # ── 组合索引（查询/写入性能）──
    # mood_checkins：按用户 + 时间倒序拉取历史/趋势
    op.create_index('ix_mood_checkins_user_created', 'mood_checkins', ['user_id', 'created_at'], unique=False)
    # memories：按用户 + 状态 + 类别过滤；按用户 + 状态 + 时间倒序
    op.create_index('ix_memories_user_status_category', 'memories', ['user_id', 'status', 'category'], unique=False)
    op.create_index('ix_memories_user_status_created', 'memories', ['user_id', 'status', 'created_at'], unique=False)
    # growth_records：按用户 + 类型 + 时间倒序
    op.create_index('ix_growth_records_user_type_created', 'growth_records', ['user_id', 'record_type', 'created_at'], unique=False)
    # chat_messages：按用户 + 时间倒序
    op.create_index('ix_chat_messages_user_created', 'chat_messages', ['user_id', 'created_at'], unique=False)
    # personality_profiles：按用户 + 版本倒序
    op.create_index('ix_personality_profiles_user_version', 'personality_profiles', ['user_id', 'version'], unique=False)

    # ── pgvector HNSW 索引（语义检索加速）──
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_memories_embedding_hnsw
        ON memories USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memories_embedding_hnsw")
    op.drop_index('ix_personality_profiles_user_version', table_name='personality_profiles')
    op.drop_index('ix_chat_messages_user_created', table_name='chat_messages')
    op.drop_index('ix_growth_records_user_type_created', table_name='growth_records')
    op.drop_index('ix_memories_user_status_created', table_name='memories')
    op.drop_index('ix_memories_user_status_category', table_name='memories')
    op.drop_index('ix_mood_checkins_user_created', table_name='mood_checkins')
    op.drop_column('users', 'chat_style')
    op.drop_column('users', 'push_time')
    op.drop_column('users', 'name')
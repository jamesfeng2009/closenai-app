"""memories.embedding: vector(768) → halfvec(512)（text-embedding-v4）

Revision ID: 7c2e4a9b1f03
Revises: 3a5b7c9d1e2f
Create Date: 2026-08-04 12:00:00.000000

背景：
- Embedding 模型从 text-embedding-v3 升级为 v4（Qwen3-Embedding，100+ 语种含 vi/km），
  两个模型向量空间不兼容，存量向量必须作废重嵌入。
- 维度从 768 降为 512（v4 MRL 官方推荐截断点，召回损失 <1%）。
- 类型从 vector(float4) 改为 halfvec(float2)：存储与 HNSW 索引空间合计节省 ~66%，
  Supabase 免费版 500MB 数据库约束下可承载记忆量从 ~6 万条提升到 ~19 万条。

注意：
- halfvec 需要 pgvector >= 0.7.0（Supabase 已满足）。
- 本迁移会将存量 embedding 置 NULL（反正需要重嵌入）；重嵌入由业务侧按需触发
  （记忆下次被检索/更新时由 _embed_or_none 重新生成）。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '7c2e4a9b1f03'
down_revision: Union[str, None] = '3a5b7c9d1e2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 先删旧 HNSW 索引（vector_cosine_ops 不能用于 halfvec 列）
    op.execute("DROP INDEX IF EXISTS ix_memories_embedding_hnsw")

    # 变更列类型为 halfvec(512)，存量向量直接置 NULL（v3/v4 空间不兼容，必须重嵌入）
    op.execute(
        """
        ALTER TABLE memories
        ALTER COLUMN embedding TYPE halfvec(512) USING NULL
        """
    )

    # 重建 HNSW 索引（halfvec 余弦距离）
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_memories_embedding_hnsw
        ON memories USING hnsw (embedding halfvec_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memories_embedding_hnsw")
    op.execute(
        """
        ALTER TABLE memories
        ALTER COLUMN embedding TYPE vector(768) USING NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_memories_embedding_hnsw
        ON memories USING hnsw (embedding vector_cosine_ops)
        """
    )

"""
记忆数据访问层（P0-F02）。

职责：只与 memories 表交互，不含业务逻辑。
- 列表 / 详情 / 创建 / 语义搜索（pgvector）
- 三态版本化（current / superseded / reverted）写入
"""
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select, update, delete, text, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.memory import Memory


class MemoryRepository:
    """memories 表数据访问。"""

    async def create(
        self,
        db: AsyncSession,
        user_id: str,
        content: str,
        category: str,
        source: str,
        conviction: float,
        embedding: Optional[List[float]] = None,
        *,
        status: str = "current",
        emotion_context: Optional[str] = None,
    ) -> Memory:
        """新建一条记忆。"""
        memory = Memory(
            id=str(uuid4()),
            user_id=user_id,
            category=category,
            content=content,
            status=status,
            conviction_score=conviction,
            source=source,
            embedding=embedding,
            emotion_context=emotion_context,
        )
        db.add(memory)
        await db.flush()
        return memory

    async def get(self, db: AsyncSession, memory_id: str) -> Optional[Memory]:
        """按主键查询记忆。"""
        result = await db.execute(select(Memory).where(Memory.id == memory_id))
        return result.scalar_one_or_none()

    async def list(
        self,
        db: AsyncSession,
        user_id: str,
        limit: int = 20,
        category: Optional[str] = None,
        status: str = "current",
    ) -> List[Memory]:
        """查询记忆列表，默认只返回 current 状态。"""
        stmt = select(Memory).where(Memory.user_id == user_id, Memory.status == status)
        if category:
            stmt = stmt.where(Memory.category == category)
        stmt = stmt.order_by(Memory.created_at.desc()).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def semantic_search(
        self,
        db: AsyncSession,
        user_id: str,
        query_embedding: List[float],
        limit: int = 10,
        include_superseded: bool = False,
    ) -> List[Memory]:
        """
        pgvector 余弦相似度搜索。

        默认仅搜索 current 状态；include_superseded=True 时纳入 superseded/reverted
        （用于回归识别）。
        """
        status_filter = "status IN ('current')"
        if include_superseded:
            status_filter = "status IN ('current', 'superseded', 'reverted')"
        # 显式将查询向量 CAST 为 halfvec(512)，避免 "operator does not exist: halfvec <=> double precision[]" 错误
        sql = text(
            f"""
            SELECT *, 1 - (embedding <=> CAST(:q AS halfvec)) AS similarity
            FROM memories
            WHERE user_id = :uid AND {status_filter} AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:q AS halfvec)
            LIMIT :limit
            """
        )
        result = await db.execute(
            sql, {"uid": user_id, "q": query_embedding, "limit": limit}
        )
        return list(result.mappings().all())

    async def version_out(
        self,
        db: AsyncSession,
        memory_id: str,
        *,
        status: str,
        superseded_by: Optional[str] = None,
        superseded_at: Optional[datetime] = None,
        transition_reason: Optional[str] = None,
    ) -> None:
        """把旧记忆标记为 superseded / reverted。"""
        values: dict = {"status": status}
        if superseded_by is not None:
            values["superseded_by"] = superseded_by
        if superseded_at is not None:
            values["superseded_at"] = superseded_at
        if transition_reason is not None:
            values["transition_reason"] = transition_reason
        await db.execute(update(Memory).where(Memory.id == memory_id).values(**values))
        await db.flush()

    async def run_drift_score(
        self,
        db: AsyncSession,
        memory_id: str,
        drift_score: float,
    ) -> None:
        """更新渐变漂移分数。"""
        await db.execute(
            update(Memory).where(Memory.id == memory_id).values(drift_score=drift_score)
        )
        await db.flush()

    async def count_by_category(
        self, db: AsyncSession, user_id: str, status: str = "current"
    ) -> dict:
        """按类别统计 current 记忆数量（用于成长指标）。"""
        result = await db.execute(
            select(Memory.category, func.count(Memory.id))
            .where(Memory.user_id == user_id, Memory.status == status)
            .group_by(Memory.category)
        )
        return {category: count for category, count in result.all()}

    async def delete(self, db: AsyncSession, memory_id: str, user_id: str) -> bool:
        """删除一条记忆（强制归属校验，防越权删除）。返回是否删除成功。"""
        result = await db.execute(
            delete(Memory).where(Memory.id == memory_id, Memory.user_id == user_id)
        )
        await db.flush()
        return (result.rowcount or 0) > 0


memory_repository = MemoryRepository()
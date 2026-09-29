"""
记忆数据访问层（P0-F02）。

职责：只与 memories 表交互，不含业务逻辑。
- 列表 / 详情 / 创建 / 语义搜索（pgvector）
- 三态版本化（current / superseded / reverted）写入
"""
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select, update, text, func
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

    async def get(self, db: AsyncSession, memory_id: str, user_id: str) -> Optional[Memory]:
        """按主键查询记忆（强制归属校验，防 IDOR 越权读取；不含已软删除）。"""
        result = await db.execute(
            select(Memory).where(
                Memory.id == memory_id,
                Memory.user_id == user_id,
                Memory.deleted == False,  # noqa: E712
            )
        )
        return result.scalar_one_or_none()

    async def get_superseded_by(
        self, db: AsyncSession, memory_id: str, user_id: str
    ) -> Optional[Memory]:
        """查询被指定记忆取代的旧记忆（revert 回归时恢复用；不含已软删除）。"""
        result = await db.execute(
            select(Memory)
            .where(
                Memory.superseded_by == memory_id,
                Memory.user_id == user_id,
                Memory.status == "superseded",
                Memory.deleted == False,  # noqa: E712
            )
            .order_by(Memory.superseded_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def restore(self, db: AsyncSession, memory_id: str, reverted_from: str) -> None:
        """把被取代的旧记忆恢复为 current（清版本化字段，记录 reverted_from 溯源）。"""
        await db.execute(
            update(Memory)
            .where(Memory.id == memory_id)
            .values(
                status="current",
                superseded_by=None,
                superseded_at=None,
                reverted_from=reverted_from,
            )
        )
        await db.flush()

    async def list(
        self,
        db: AsyncSession,
        user_id: str,
        limit: int = 20,
        category: Optional[str] = None,
        status: str = "current",
    ) -> List[Memory]:
        """查询记忆列表，默认只返回 current 状态（不含已软删除）。"""
        stmt = select(Memory).where(
            Memory.user_id == user_id,
            Memory.status == status,
            Memory.deleted == False,  # noqa: E712
        )
        if category:
            stmt = stmt.where(Memory.category == category)
        stmt = stmt.order_by(Memory.created_at.desc()).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def list_by_ids(
        self, db: AsyncSession, user_id: str, memory_ids: List[str]
    ) -> List[Memory]:
        """按 ID 批量取记忆（强制归属校验：只返回该用户名下的行，用于对话引用的批量解析）。"""
        if not memory_ids:
            return []
        result = await db.execute(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.id.in_(memory_ids),
                Memory.deleted == False,  # noqa: E712
            )
        )
        return list(result.scalars().all())

    async def list_between(
        self,
        db: AsyncSession,
        user_id: str,
        start: datetime,
        end: datetime,
        limit: int = 10,
    ) -> List[Memory]:
        """查询创建时间落在 [start, end) 窗口内的记忆（按时间倒序，不含已软删除；用于回忆卡片）。"""
        result = await db.execute(
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.created_at >= start,
                Memory.created_at < end,
                Memory.deleted == False,  # noqa: E712
            )
            .order_by(Memory.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_since(self, db: AsyncSession, user_id: str, start: datetime) -> int:
        """统计 start 之后创建的记忆条数（不含软删除；用于画像刷新新数据检测）。"""
        result = await db.execute(
            select(func.count(Memory.id)).where(
                Memory.user_id == user_id,
                Memory.created_at >= start,
                Memory.deleted == False,  # noqa: E712
            )
        )
        return int(result.scalar_one() or 0)

    async def semantic_search(
        self,
        db: AsyncSession,
        user_id: str,
        query_embedding: List[float],
        limit: int = 10,
        include_superseded: bool = False,
        category: Optional[str] = None,
    ) -> List[Memory]:
        """
        pgvector 余弦相似度搜索（不含已软删除）。

        默认仅搜索 current 状态；include_superseded=True 时纳入 superseded
        （用于回归识别；reverted 按 spec §1.2 不再参与搜索，保证回归触发幂等）。
        category 可选：限定同类别检索（转变检测仅同 category 内进行）。
        """
        status_filter = "status IN ('current')"
        if include_superseded:
            status_filter = "status IN ('current', 'superseded')"
        category_filter = "AND category = :cat" if category else ""
        # 显式将查询向量 CAST 为 halfvec(512)，避免 "operator does not exist: halfvec <=> double precision[]" 错误
        sql = text(
            f"""
            SELECT *, 1 - (embedding <=> CAST(:q AS halfvec)) AS similarity
            FROM memories
            WHERE user_id = :uid AND {status_filter} AND deleted = FALSE
              AND embedding IS NOT NULL {category_filter}
            ORDER BY embedding <=> CAST(:q AS halfvec)
            LIMIT :limit
            """
        )
        params: dict = {"uid": user_id, "q": query_embedding, "limit": limit}
        if category:
            params["cat"] = category
        result = await db.execute(sql, params)
        return list(result.mappings().all())

    async def version_out(
        self,
        db: AsyncSession,
        memory_id: str,
        user_id: str,
        *,
        status: str,
        superseded_by: Optional[str] = None,
        superseded_at: Optional[datetime] = None,
        transition_reason: Optional[str] = None,
    ) -> None:
        """把旧记忆标记为 superseded / reverted（强制归属校验）。"""
        values: dict = {"status": status}
        if superseded_by is not None:
            values["superseded_by"] = superseded_by
        if superseded_at is not None:
            values["superseded_at"] = superseded_at
        if transition_reason is not None:
            values["transition_reason"] = transition_reason
        await db.execute(
            update(Memory)
            .where(Memory.id == memory_id, Memory.user_id == user_id)
            .values(**values)
        )
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
        """按类别统计 current 记忆数量（用于成长指标；不含已软删除）。"""
        result = await db.execute(
            select(Memory.category, func.count(Memory.id))
            .where(
                Memory.user_id == user_id,
                Memory.status == status,
                Memory.deleted == False,  # noqa: E712
            )
            .group_by(Memory.category)
        )
        return {category: count for category, count in result.all()}

    async def update_conviction(
        self, db: AsyncSession, memory_id: str, user_id: str, conviction: float
    ) -> None:
        """更新信念强度（重复表达加固，强制归属校验）。"""
        await db.execute(
            update(Memory)
            .where(Memory.id == memory_id, Memory.user_id == user_id)
            .values(conviction_score=conviction)
        )
        await db.flush()

    async def update_emotion_context(
        self, db: AsyncSession, memory_id: str, user_id: str, emotion_context: Optional[str]
    ) -> None:
        """更新情绪上下文标记（情绪过滤：pending_high_emotion；None = 解除标记，强制归属校验）。"""
        await db.execute(
            update(Memory)
            .where(Memory.id == memory_id, Memory.user_id == user_id)
            .values(emotion_context=emotion_context)
        )
        await db.flush()

    async def list_drift_candidates(
        self, db: AsyncSession, user_id: str, limit: int = 50
    ) -> List[Memory]:
        """渐变漂移检测候选：current 且 conviction≥0.4 且已向量化（不含已软删除）。"""
        result = await db.execute(
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.status == "current",
                Memory.conviction_score >= 0.4,
                Memory.deleted == False,  # noqa: E712
                Memory.embedding.isnot(None),
            )
            .order_by(Memory.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def delete(self, db: AsyncSession, memory_id: str, user_id: str) -> bool:
        """软删除一条记忆（deleted=true，强制归属校验，防越权删除）。

        删除的是 current 记忆时，把被它 supersede 的最新一条旧记忆恢复为 current
        （spec §6：记忆不物理删除，只改变状态）。返回是否删除成功。
        """
        memory = await self.get(db, memory_id, user_id)
        if not memory:
            return False
        was_current = memory.status == "current"
        await db.execute(
            update(Memory)
            .where(Memory.id == memory_id, Memory.user_id == user_id)
            .values(deleted=True)
        )
        await db.flush()
        if was_current:
            old = await self.get_superseded_by(db, memory_id, user_id)
            if old:
                await self.restore(db, old.id, reverted_from=memory_id)
        return True


memory_repository = MemoryRepository()
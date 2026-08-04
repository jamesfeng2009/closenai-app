"""
记忆服务 (P0-F02)。

长期记忆 + pgvector 语义检索 + 三态版本化（current/superseded/reverted）。

记忆注入链路：
    chat / mood 沉淀 → create_memory（embedding + 渐变漂移检测）→ 检索注入
"""
import logging
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AppException, ErrorCode
from backend.core.llm import llm
from backend.core.time_utils import utc_now
from backend.models.memory import Memory
from backend.models.schemas import MemoryResponse
from backend.repositories import memory_repository

logger = logging.getLogger("closenai.memory")

# 渐变漂移：相似度超过该阈值视为同一信念的演变，而非新记忆
DRIFT_SIMILARITY_THRESHOLD = 0.85


def _to_response(memory: Memory) -> MemoryResponse:
    """ORM → 响应模型。"""
    return MemoryResponse(
        id=memory.id,
        user_id=memory.user_id,
        content=memory.content,
        category=memory.category,
        status=memory.status,
        source=memory.source,
        conviction=memory.conviction_score,
        created_at=memory.created_at,
        superseded_at=memory.superseded_at,
        transition_reason=memory.transition_reason,
    )


def _to_response_from_row(row) -> MemoryResponse:
    """pgvector 检索行（含 similarity）→ 响应模型。"""
    return MemoryResponse(
        id=row["id"],
        user_id=row["user_id"],
        content=row["content"],
        category=row["category"],
        status=row["status"],
        source=row["source"],
        conviction=row["conviction_score"],
        created_at=row["created_at"],
        superseded_at=row["superseded_at"],
        transition_reason=row["transition_reason"],
    )


class MemoryService:
    """记忆服务。"""

    async def list_memories(
        self,
        db: AsyncSession,
        user_id: str,
        limit: int = 20,
        category: Optional[str] = None,
    ) -> List[MemoryResponse]:
        """查询记忆列表。"""
        memories = await memory_repository.list(db, user_id, limit=limit, category=category)
        return [_to_response(m) for m in memories]

    async def get_memory(self, db: AsyncSession, memory_id: str) -> Optional[MemoryResponse]:
        """获取单条记忆。"""
        memory = await memory_repository.get(db, memory_id)
        return _to_response(memory) if memory else None

    async def create_memory(
        self,
        db: AsyncSession,
        user_id: str,
        content: str,
        category: str = "factual",
        source: str = "self_reflection",
        conviction: float = 0.5,
        emotion_context: Optional[str] = None,
    ) -> MemoryResponse:
        """创建记忆（含向量化 + 渐变漂移检测）。"""
        embedding = await self._embed_or_none(content)

        # 渐变漂移检测：与已有 current 记忆高度相似时，视为同一信念演化
        if embedding:
            similar = await memory_repository.semantic_search(
                db, user_id, embedding, limit=1, include_superseded=False
            )
            if similar and similar[0].get("similarity", 0) >= DRIFT_SIMILARITY_THRESHOLD:
                existing = similar[0]
                # 提升置信度并更新内容（漂移累积）
                new_conviction = min(1.0, (existing["conviction_score"] or 0.5) + 0.05)
                memory = await memory_repository.create(
                    db,
                    user_id,
                    content=content,
                    category=category,
                    source=source,
                    conviction=new_conviction,
                    embedding=embedding,
                    emotion_context=emotion_context,
                )
                # 旧记忆标记 superseded（渐变漂移）
                await memory_repository.version_out(
                    db,
                    existing["id"],
                    status="superseded",
                    superseded_by=memory.id,
                    superseded_at=utc_now(),
                    transition_reason="gradual_drift",
                )
                return _to_response(memory)

        memory = await memory_repository.create(
            db,
            user_id,
            content=content,
            category=category,
            source=source,
            conviction=conviction,
            embedding=embedding,
            emotion_context=emotion_context,
        )
        return _to_response(memory)

    async def semantic_search(
        self,
        db: AsyncSession,
        user_id: str,
        query: str,
        limit: int = 10,
        include_superseded: bool = False,
    ) -> List[MemoryResponse]:
        """语义搜索记忆。"""
        embedding = await self._embed_or_none(query)
        if not embedding:
            return []
        rows = await memory_repository.semantic_search(
            db, user_id, embedding, limit=limit, include_superseded=include_superseded
        )
        return [_to_response_from_row(r) for r in rows]

    async def get_memory_context(
        self, db: AsyncSession, user_id: str, query: str, limit: int = 5
    ) -> List[MemoryResponse]:
        """获取记忆上下文（用于对话 / 主动消息注入）。"""
        return await self.semantic_search(db, user_id, query, limit=limit)

    async def record_version(
        self,
        db: AsyncSession,
        user_id: str,
        memory_id: str,
        transition_type: str,
        reason: Optional[str],
        emotion_context: Optional[dict] = None,
    ) -> None:
        """
        记录信念演变。

        - gradual: 渐变漂移（原记忆 superseded，置信度提升）
        - sudden: 突然转变（原记忆 superseded）
        - revert: 回归（原记忆标记 reverted，恢复被 superseded 的旧记忆）
        """
        memory = await memory_repository.get(db, memory_id)
        if not memory:
            raise AppException(ErrorCode.MEMORY_NOT_FOUND, "记忆不存在")

        if transition_type == "revert":
            # 回归：找到被该记忆取代的旧记忆，恢复为 current
            await memory_repository.version_out(
                db, memory_id, status="reverted", transition_reason=reason or "revert"
            )
            if memory.superseded_by:
                # 新记忆作为回归结果
                await memory_repository.create(
                    db,
                    user_id,
                    content=memory.content,
                    category=memory.category,
                    source=memory.source,
                    conviction=memory.conviction_score,
                    embedding=memory.embedding,
                    status="current",
                    emotion_context=memory.emotion_context,
                )
            return

        # gradual / sudden：旧记忆 superseded，写入新记忆
        new_conviction = min(1.0, memory.conviction_score + 0.1) if transition_type == "gradual" else memory.conviction_score
        new_memory = await memory_repository.create(
            db,
            user_id,
            content=memory.content,
            category=memory.category,
            source=memory.source,
            conviction=new_conviction,
            embedding=memory.embedding,
            emotion_context=memory.emotion_context,
        )
        await memory_repository.version_out(
            db,
            memory_id,
            status="superseded",
            superseded_by=new_memory.id,
            superseded_at=utc_now(),
            transition_reason=reason or transition_type,
        )

    async def _embed_or_none(self, text: str) -> Optional[List[float]]:
        """向量化文本，失败返回 None 不阻塞主流程。"""
        try:
            vectors = await llm.embed([text])
            return vectors[0] if vectors else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding 失败: %s", exc)
            return None


memory_service = MemoryService()
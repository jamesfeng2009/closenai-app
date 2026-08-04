"""
记忆路由 (P0-F02)。

端点：
    GET    /api/v1/memories                — 查询记忆列表
    GET    /api/v1/memories/{memory_id}    — 获取单条记忆
    POST   /api/v1/memories                — 创建记忆
    POST   /api/v1/memories/search         — 语义搜索
    POST   /api/v1/memories/version        — 信念演变记录
    DELETE /api/v1/memories/{memory_id}    — 删除记忆（合规：用户数据删除权）
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.errors import AppException, ErrorCode
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import (
    MemoryCreate, MemorySearchRequest, MemoryVersionRequest,
)
from backend.repositories import memory_repository
from backend.services.memory_service import MemoryService, memory_service

router = APIRouter()


def get_memory_service() -> MemoryService:
    return memory_service


@router.get("/memories")
async def list_memories(
    limit: int = 20,
    category: str = None,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: MemoryService = Depends(get_memory_service),
):
    """查询记忆列表。支持按 category 过滤。"""
    return success(await service.list_memories(db, user_id, limit=limit, category=category))


@router.get("/memories/{memory_id}")
async def get_memory(
    memory_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: MemoryService = Depends(get_memory_service),
):
    """获取单条记忆详情。"""
    memory = await service.get_memory(db, memory_id)
    if not memory:
        raise AppException(ErrorCode.MEMORY_NOT_FOUND, "记忆不存在")
    return success(memory)


@router.post("/memories")
async def create_memory(
    request: MemoryCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: MemoryService = Depends(get_memory_service),
):
    """创建记忆（含向量化 + 渐变漂移检测）。"""
    return success(
        await service.create_memory(
            db,
            user_id,
            content=request.content,
            category=request.category,
            source=request.source,
            conviction=request.conviction,
            emotion_context=None,
        )
    )


@router.post("/memories/search")
async def search_memories(
    request: MemorySearchRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: MemoryService = Depends(get_memory_service),
):
    """
    语义搜索记忆。

    使用 pgvector cosine similarity 搜索。
    默认排除 superseded 记忆，除非 include_superseded=true。
    """
    return success(
        await service.semantic_search(
            db, user_id, request.query, request.limit, request.include_superseded
        )
    )


@router.post("/memories/version")
async def version_memory(
    request: MemoryVersionRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: MemoryService = Depends(get_memory_service),
):
    """
    记录信念演变。

    转变类型：
    - gradual: 渐变漂移
    - sudden: 突然转变
    - revert: 回归到之前状态
    """
    await service.record_version(
        db, user_id, str(request.memory_id), request.transition_type, request.reason, request.emotion_context
    )
    return success({"memory_id": str(request.memory_id)})


@router.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """删除单条记忆（合规：用户可随时删除自己的数据）。"""
    deleted = await memory_repository.delete(db, memory_id, user_id)
    if not deleted:
        raise AppException(ErrorCode.MEMORY_NOT_FOUND, "记忆不存在")
    return success({"deleted": True})
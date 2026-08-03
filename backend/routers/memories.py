"""
记忆路由 (P0-F02)。

端点：
    GET  /api/v1/memories                — 查询记忆列表
    GET  /api/v1/memories/{memory_id}    — 获取单条记忆
    POST /api/v1/memories/search         — 语义搜索
    POST /api/v1/memories/version        — 信念演变记录
"""
from fastapi import APIRouter, Depends, HTTPException

from backend.models.schemas import (
    MemoryCreate, MemoryResponse, MemorySearchRequest, MemoryVersionRequest,
)
from backend.services.memory_service import MemoryService, memory_service

router = APIRouter()


def get_memory_service() -> MemoryService:
    return memory_service


@router.get("/memories", response_model=list[MemoryResponse])
async def list_memories(
    limit: int = 20,
    category: str = None,
    service: MemoryService = Depends(get_memory_service),
):
    """查询记忆列表。支持按 category 过滤。"""
    user_id = "demo-user"
    return await service.list_memories(user_id, limit=limit, category=category)


@router.get("/memories/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: str,
    service: MemoryService = Depends(get_memory_service),
):
    """获取单条记忆详情。"""
    memory = await service.get_memory(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return memory


@router.post("/memories/search", response_model=list[MemoryResponse])
async def search_memories(
    request: MemorySearchRequest,
    service: MemoryService = Depends(get_memory_service),
):
    """
    语义搜索记忆。

    使用 pgvector cosine similarity 搜索。
    默认排除 superseded 记忆，除非 include_superseded=true。
    """
    user_id = "demo-user"
    return await service.semantic_search(user_id, request.query, request.limit, request.include_superseded)


@router.post("/memories/version")
async def version_memory(
    request: MemoryVersionRequest,
    service: MemoryService = Depends(get_memory_service),
):
    """
    记录信念演变。

    转变类型：
    - gradual: 渐变漂移
    - sudden: 突然转变
    - revert: 回归到之前状态
    """
    user_id = "demo-user"
    result = await service.record_version(
        user_id, request.memory_id, request.transition_type, request.reason, request.emotion_context
    )
    return {"status": "ok", "memory_id": str(request.memory_id)}
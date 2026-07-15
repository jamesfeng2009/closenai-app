"""
记忆路由。

提供 Temporal Memory 的查询和管理接口。

端点：
    GET  /api/v1/memories          — 查询记忆列表
    GET  /api/v1/memories/{id}     — 获取单条记忆详情
"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/memories")
async def list_memories():
    """查询记忆列表。TODO: 接入 memory_service。"""
    return {"memories": [], "message": "TODO: 接入记忆服务"}


@router.get("/memories/{memory_id}")
async def get_memory(memory_id: str):
    """获取单条记忆详情。TODO: 接入 memory_service。"""
    return {"id": memory_id, "message": "TODO: 接入记忆服务"}

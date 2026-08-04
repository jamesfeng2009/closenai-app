"""
Reflection Card 路由 (P0-F04)。

端点：
    POST /api/v1/reflections/generate  — 生成今日 Reflection 草稿
    POST /api/v1/reflections           — 保存 Reflection
    GET  /api/v1/reflections           — 列出最近 Reflection
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.language import get_request_language
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import (
    ReflectionCreate,
    ReflectionGenerateRequest,
)
from backend.services.reflection_service import ReflectionService, reflection_service

router = APIRouter()


def get_reflection_service() -> ReflectionService:
    return reflection_service


@router.post("/reflections/generate")
async def generate_draft(
    request: ReflectionGenerateRequest,
    db: AsyncSession = Depends(get_db),
    language: str = Depends(get_request_language),
    user_id: str = Depends(get_current_user),
    service: ReflectionService = Depends(get_reflection_service),
):
    """生成今日 Reflection 草稿（基于情绪 + 记忆）。"""
    return success(await service.generate_draft(db, user_id, language, request.context))


@router.post("/reflections")
async def save_reflection(
    request: ReflectionCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ReflectionService = Depends(get_reflection_service),
):
    """保存 Reflection 为成长记录。"""
    return success(await service.save(db, user_id, request.content))


@router.get("/reflections")
async def list_reflections(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ReflectionService = Depends(get_reflection_service),
):
    """列出最近的 Reflection 记录。"""
    return success(await service.list(db, user_id, limit))
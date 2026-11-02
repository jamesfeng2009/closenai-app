"""
Reflection Card 路由 (P0-F04)。

端点：
    POST /api/v1/reflections/generate  — 生成今日 Reflection 草稿
    POST /api/v1/reflections           — 保存 Reflection（可带提示库溯源）
    GET  /api/v1/reflections           — 列出最近 Reflection

提示库本体在 `backend/routers/reflection_prompts.py`（`GET /api/v1/reflection-prompts`）：
它不吃 DB、也不该跟着 `/api/v1/reflections` 这段前缀被切给 Go，所以不放本文件。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.db_route import CommitBeforeResponseRoute
from backend.core.language import get_request_language
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import (
    ReflectionCreate,
    ReflectionGenerateRequest,
)
from backend.services.reflection_service import ReflectionService, reflection_service
from backend.services.user_service import user_service

router = APIRouter(route_class=CommitBeforeResponseRoute)


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
    """生成今日 Reflection 草稿（今日反思 + AI 观察，基于情绪 + 记忆）。

    需 AI 数据共享同意（5.1.2(i)）；草稿生成消耗 1 次 LLM 调用，
    计入统一免费配额，超额返回 QUOTA_EXCEEDED（40301）。
    新用户（无记忆无签到历史）返回对话式引导文案而非模板草稿。
    """
    await user_service.require_ai_consent(db, user_id)
    return success(await service.generate_draft(db, user_id, language, request.context))


@router.post("/reflections")
async def save_reflection(
    request: ReflectionCreate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ReflectionService = Depends(get_reflection_service),
):
    """保存 Reflection 为成长记录。

    可携带草稿关联的 `related_memory_ids`，以及提示库溯源
    （`prompt_id` + `prompt_version` + `source="prompt"`，KG-26）；
    溯源字段非法（提示不存在／库版本过期／成对缺失）返回 40001。
    """
    return success(
        await service.save(
            db,
            user_id,
            request.content,
            request.related_memory_ids,
            prompt_id=request.prompt_id,
            prompt_version=request.prompt_version,
            source=request.source,
        )
    )


@router.get("/reflections")
async def list_reflections(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ReflectionService = Depends(get_reflection_service),
):
    """列出最近的 Reflection 记录。"""
    return success(await service.list(db, user_id, limit))
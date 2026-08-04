"""
性格画像路由 (P0-F06)。

端点：
    GET  /api/v1/personality/profile  — 查看性格画像
    POST /api/v1/personality/refresh  — 刷新画像
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.response import success
from backend.database import get_db
from backend.services.personality_service import PersonalityService, personality_service

router = APIRouter()


def get_personality_service() -> PersonalityService:
    return personality_service


@router.get("/personality/profile")
async def get_profile(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: PersonalityService = Depends(get_personality_service),
):
    """
    查看性格画像。

    冷启动阶段（< 7 天）：返回进度条和阶段信息。
    成熟阶段（≥ 7 天）：返回 MBTI 类型 + 观察 + 独特之处。
    """
    return success(await service.get_profile(db, user_id))


@router.post("/personality/refresh")
async def refresh(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: PersonalityService = Depends(get_personality_service),
):
    """
    刷新性格画像。

    免费用户：总计 3 次刷新配额。
    Pro 用户：每月 5 次刷新配额。
    Pro+ 用户：不限次数。
    """
    return success(await service.refresh(db, user_id))
"""
性格画像路由 (P0-F05)。

端点：
    GET  /api/v1/personality/profile  — 查看性格画像
    POST /api/v1/personality/refresh  — 刷新画像
"""
from fastapi import APIRouter, Depends, HTTPException

from backend.models.schemas import (
    PersonalityProfileResponse, PersonalityRefreshResponse,
)
from backend.services.personality_service import PersonalityService, personality_service

router = APIRouter()


def get_personality_service() -> PersonalityService:
    return personality_service


@router.get("/personality/profile", response_model=PersonalityProfileResponse)
async def get_profile(
    service: PersonalityService = Depends(get_personality_service),
):
    """
    查看性格画像。

    冷启动阶段（< 7 天）：返回进度条和阶段信息。
    成熟阶段（≥ 7 天）：返回 MBTI 类型 + 观察 + 独特之处。
    """
    user_id = "demo-user"
    profile = await service.get_profile(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="尚未生成性格画像")
    return profile


@router.post("/personality/refresh", response_model=PersonalityRefreshResponse)
async def refresh(
    service: PersonalityService = Depends(get_personality_service),
):
    """
    刷新性格画像。

    免费用户：总计 3 次刷新配额。
    Pro 用户：每月 5 次刷新配额。
    Pro+ 用户：不限次数。
    """
    user_id = "demo-user"
    result = await service.refresh(user_id)
    return result
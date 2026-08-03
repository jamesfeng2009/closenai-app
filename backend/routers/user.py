"""
用户设置路由 (P0-F04)。

端点：
    GET   /api/v1/user/profile    — 用户信息
    PATCH /api/v1/user/settings   — 更新设置
    POST  /api/v1/user/push-token — 注册推送 Token
"""
from fastapi import APIRouter, Depends, HTTPException

from backend.models.schemas import (
    UserProfileResponse, UserSettingsUpdate, PushTokenRequest,
)
from backend.services.user_service import UserService, user_service

router = APIRouter()


def get_user_service() -> UserService:
    return user_service


@router.get("/user/profile", response_model=UserProfileResponse)
async def get_profile(
    service: UserService = Depends(get_user_service),
):
    """获取用户信息（Streak、等级、配额等）。"""
    user_id = "demo-user"
    profile = await service.get_profile(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="用户不存在")
    return profile


@router.patch("/user/settings", response_model=UserProfileResponse)
async def update_settings(
    settings: UserSettingsUpdate,
    service: UserService = Depends(get_user_service),
):
    """更新用户设置（昵称、对话风格、推送时间、时区）。"""
    user_id = "demo-user"
    return await service.update_settings(user_id, settings)


@router.post("/user/push-token")
async def register_push_token(
    request: PushTokenRequest,
    service: UserService = Depends(get_user_service),
):
    """注册设备推送 Token（登录时调用）。"""
    user_id = "demo-user"
    await service.register_push_token(user_id, request.token, request.platform)
    return {"status": "ok"}
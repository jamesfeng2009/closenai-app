"""
用户设置路由 (P0-F04)。

端点：
    GET    /api/v1/user/profile    — 用户信息
    PATCH  /api/v1/user/settings   — 更新设置
    GET    /api/v1/user/ai-consent — 查询 AI 数据共享同意状态
    POST   /api/v1/user/ai-consent — 设置 AI 数据共享同意（5.1.2(i)）
    POST   /api/v1/user/push-token — 注册推送 Token
    DELETE /api/v1/user/push-token — 清除推送 Token（登出时调用）
    DELETE /api/v1/user/account    — 注销账号（级联删除全部数据）
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import AIConsentUpdate, UserSettingsUpdate, PushTokenRequest
from backend.services.user_service import UserService, user_service

router = APIRouter()


def get_user_service() -> UserService:
    return user_service


@router.get("/user/profile")
async def get_profile(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    """获取用户信息（Streak、等级、配额等）。"""
    return success(await service.get_profile(db, user_id))


@router.patch("/user/settings")
async def update_settings(
    settings_update: UserSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    """更新用户设置（昵称、对话风格、推送时间、时区）。"""
    return success(await service.update_settings(db, user_id, settings_update))


@router.get("/user/ai-consent")
async def get_ai_consent(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    """查询 AI 数据共享同意状态。"""
    profile = await service.get_profile(db, user_id)
    return success({"ai_consent": profile.ai_consent})


@router.post("/user/ai-consent")
async def set_ai_consent(
    request: AIConsentUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    """设置 AI 数据共享同意（5.1.2(i)：同意后弹窗不再显示，撤回后 AI 功能停用）。"""
    consent = await service.set_ai_consent(db, user_id, request.consent)
    return success({"ai_consent": consent})


@router.post("/user/push-token")
async def register_push_token(
    request: PushTokenRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    """注册设备推送 Token（登录时调用）。"""
    await service.register_push_token(db, user_id, request.token, request.platform)
    return success({"status": "ok"})


@router.delete("/user/push-token")
async def clear_push_token(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    """清除用户全部推送 Token（登出时调用，登出后不再收到推送）。"""
    await service.clear_push_tokens(db, user_id)
    return success({"status": "ok"})


@router.delete("/user/account")
async def delete_account(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    """注销账号：级联删除全部用户数据（不可恢复）。"""
    counts = await service.delete_account(db, user_id)
    return success({"deleted": True, "counts": counts})
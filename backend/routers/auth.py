"""
认证路由 (Auth)。

端点：
    POST /auth/register         邮箱+用户名+密码注册
    POST /auth/login            用户名/邮箱+密码登录
    POST /auth/send-code        发送邮箱验证码（register|login|reset）
    POST /auth/login-code       邮箱验证码登录
    POST /auth/apple            Apple 登录
    POST /auth/refresh          刷新 token（轮转）
    POST /auth/logout           登出（吊销 refresh）
    POST /auth/forgot-password  忘记密码（发重置码）
    POST /auth/reset-password   重置密码
    # 需登录：
    GET   /auth/me              当前用户信息
    POST  /auth/change-password 改密码（旧+新）
    PATCH /auth/username        改用户名
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import (
    AppleLoginRequest,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginCodeRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SendCodeRequest,
    UpdateUsernameRequest,
)
from backend.services.auth_service import AuthService, auth_service

router = APIRouter()


def get_auth_service() -> AuthService:
    return auth_service


# ── 注册 / 登录（公开端点，无需鉴权）──


@router.post("/auth/register")
async def register(
    req: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
):
    """邮箱 + 用户名 + 密码注册。"""
    return success(
        await service.register_with_password(db, req.email, req.username, req.password, req.language)
    )


@router.post("/auth/login")
async def login(
    req: LoginRequest,
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
):
    """用户名 / 邮箱 + 密码登录。"""
    return success(await service.login_with_password(db, req.identifier, req.password))


@router.post("/auth/send-code")
async def send_code(
    req: SendCodeRequest,
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
):
    """发送邮箱验证码。purpose: register|login|reset。"""
    return success(await service.send_email_code(db, req.email, req.purpose, req.language))


@router.post("/auth/login-code")
async def login_code(
    req: LoginCodeRequest,
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
):
    """邮箱验证码登录（未注册自动建账号）。"""
    return success(await service.login_with_email_code(db, req.email, req.code, req.language))


@router.post("/auth/apple")
async def apple_login(
    req: AppleLoginRequest,
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
):
    """Apple 登录（Sign in with Apple）。"""
    return success(await service.login_with_apple(db, req.identity_token, req.language))


@router.post("/auth/refresh")
async def refresh(
    req: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
):
    """刷新 token（refresh token 轮转）。"""
    return success(await service.refresh_tokens(db, req.refresh_token))


@router.post("/auth/logout")
async def logout(
    req: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
):
    """登出：吊销当前 refresh token。"""
    return success(await service.logout(db, req.refresh_token))


@router.post("/auth/forgot-password")
async def forgot_password(
    req: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
):
    """忘记密码：发送重置验证码（防枚举，统一返回成功）。"""
    return success(await service.request_password_reset(db, req.email, req.language))


@router.post("/auth/reset-password")
async def reset_password(
    req: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    service: AuthService = Depends(get_auth_service),
):
    """重置密码：验证码 + 新密码。"""
    return success(await service.reset_password(db, req.email, req.code, req.new_password))


# ── 需登录端点 ──


@router.get("/auth/me")
async def me(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    """当前用户信息（校验 token 有效性）。"""
    return success(await service.get_me(db, user_id))


@router.post("/auth/change-password")
async def change_password(
    req: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    """修改密码（旧 + 新；改密后吊销全部 refresh，强制重新登录）。"""
    return success(
        await service.change_password(db, user_id, req.old_password, req.new_password)
    )


@router.patch("/auth/username")
async def update_username(
    req: UpdateUsernameRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    """修改用户名。"""
    return success(await service.update_username(db, user_id, req.username))

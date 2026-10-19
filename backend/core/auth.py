"""
认证依赖与工具。

职责：
- 从请求中解析当前用户 ID（user_id）并写入 contextvar
- JWT 签发（access / refresh）与严格解码
- 密码哈希（argon2）
- Apple identityToken 验签
- refresh token 哈希

user_id 来源（按优先级）：
1. `Authorization: Bearer <JWT>` → 解码 JWT 的 sub 字段（需设置 JWT_SECRET）
2. `X-User-Id` 头 → 透传（仅 development 环境的设备级身份，网关/开发使用）

环境分级（settings.environment）：
- development（默认）：X-User-Id 透传 + 匿名兜底，不阻塞开发
- staging：**与 production 同一档**（T6-04 决策点 D-T604-3）。这一档只多一道库面隔离，
  鉴权口径不许比生产松——否则「设成 staging」换来的是设备身份透传，那比它要防的双写更糟。
- production：强制 JWT Bearer。缺失 → 40103 MISSING_TOKEN；
  无效/过期 → 40102/40101；X-User-Id 一律忽略（防认证绕过）。
"""
import hashlib
import time
from datetime import timedelta

import httpx
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core import env_isolation
from backend.core.errors import AppException, ErrorCode
from backend.core.logging import get_logger, set_user_id
from backend.core.time_utils import utc_now
from backend.database import get_db

logger = get_logger("closenai.auth")

ANONYMOUS = "anonymous"

# ── 密码哈希（argon2）──
_pwd_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """argon2 哈希密码（明文永不存储/进日志）。"""
    return _pwd_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """校验密码；不匹配/哈希损坏均返回 False（不泄露原因）。"""
    try:
        return _pwd_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:  # noqa: BLE001  哈希格式损坏等
        return False


# ── JWT 签发 ──

def create_access_token(user_id: str) -> str:
    """签发 access token（短期，默认 30 分钟）。"""
    now = utc_now()
    payload = {
        "sub": user_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str, token_id: str) -> str:
    """签发 refresh token（长期，默认 30 天；jti=token_id 对应 refresh_tokens.id）。"""
    now = utc_now()
    payload = {
        "sub": user_id,
        "jti": token_id,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_refresh_expire_days),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """严格解码 JWT：失败即抛对应 401。"""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise AppException(
            ErrorCode.TOKEN_EXPIRED, "登录已过期，请重新登录 / Session expired"
        ) from exc
    except jwt.PyJWTError as exc:
        raise AppException(ErrorCode.TOKEN_INVALID, "无效的身份凭证 / Invalid token") from exc


def hash_refresh_token(token: str) -> str:
    """refresh token 哈希（sha256 hex，存库用；明文不落库）。"""
    return hashlib.sha256(token.encode()).hexdigest()


# ── Apple identityToken 验签 ──

APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"
# JWKS 缓存（进程级，1 小时刷新）
_apple_jwks_cache: dict = {"keys": None, "fetched_at": 0.0}
_APPLE_JWKS_TTL = 3600


async def _fetch_apple_jwks(force: bool = False) -> list[dict]:
    """拉取 Apple 公钥集（缓存 1 小时；force=True 绕过缓存强制刷新）。"""
    now = time.time()
    if (
        not force
        and _apple_jwks_cache["keys"]
        and (now - _apple_jwks_cache["fetched_at"]) < _APPLE_JWKS_TTL
    ):
        return _apple_jwks_cache["keys"]
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(APPLE_JWKS_URL)
        resp.raise_for_status()
        keys = resp.json().get("keys", [])
    _apple_jwks_cache["keys"] = keys
    _apple_jwks_cache["fetched_at"] = now
    return keys


async def verify_apple_identity_token(identity_token: str) -> tuple[str, str | None, bool]:
    """验签 Apple identityToken。

    Returns: (apple_sub, email, is_private_email)
    - apple_sub: Apple 账号唯一标识，作为本系统 apple_id
    - email: Apple payload 中的邮箱（可能为 None，仅首次授权含）
    - is_private_email: 是否 Apple 中继邮箱
    """
    if not settings.apple_client_id:
        raise AppException(
            ErrorCode.APPLE_AUTH_FAILED, "Apple 登录未配置 / Apple sign-in not configured"
        )
    try:
        unverified_header = jwt.get_unverified_header(identity_token)
        kid = unverified_header.get("kid")
        keys = await _fetch_apple_jwks()
        jwk = next((k for k in keys if k.get("kid") == kid), None)
        if not jwk:
            # kid 未命中：Apple 密钥可能已轮换而缓存未到期 → 强制刷新重试一次
            keys = await _fetch_apple_jwks(force=True)
            jwk = next((k for k in keys if k.get("kid") == kid), None)
        if not jwk:
            raise AppException(
                ErrorCode.APPLE_AUTH_FAILED, "Apple 公钥未找到 / Apple key not found"
            )
        key = jwt.algorithms.RSAAlgorithm.from_jwk(jwk)
        payload = jwt.decode(
            identity_token,
            key=key,
            algorithms=["RS256"],
            audience=settings.apple_client_id,
            issuer=APPLE_ISSUER,
        )
    except AppException:
        raise
    except Exception as exc:  # noqa: BLE001  验签/网络异常
        logger.warning("apple identity_token verify failed: %s", exc)
        raise AppException(
            ErrorCode.APPLE_AUTH_FAILED, "Apple 登录失败 / Apple sign-in failed"
        ) from exc
    sub = payload.get("sub")
    if not sub:
        raise AppException(ErrorCode.APPLE_AUTH_FAILED, "Apple token 缺少 sub / Missing Apple sub")
    email = payload.get("email")
    is_private = bool(payload.get("is_private_email", False))
    return str(sub), email, is_private


# ── 请求级鉴权依赖 ──

def _auth_enforced() -> bool:
    """受保护档强制鉴权（fail-closed）：production／prod／staging（D-T604-3）。

    判据集合在 `backend/core/env_isolation.py`，与 Go 侧 `config.ProtectedEnvironments`
    由跨语言闸对账。这里刻意不做 `.lower()`：词表是逐字小写的四格，
    设错大小写的那一档已经在 `_validate_startup_config` 拒绝启动，
    不该由鉴权面替它兜底（兜了就是把「以为在生产档其实没有」藏进请求路径）。
    """
    return env_isolation.is_protected(settings.environment)


def _decode_user_id_from_token(token: str) -> str | None:
    """尝试从 JWT 解码 user_id（sub）。失败返回 None（开发模式宽松路径）。

    refresh token 不得用于业务鉴权（type=refresh 直接拒绝）。
    """
    try:
        if not settings.jwt_secret:
            return None
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("type") == "refresh":
            return None
        sub = payload.get("sub") or payload.get("user_id")
        return str(sub) if sub else None
    except Exception:  # noqa: BLE001  token 无效/过期/未配置
        return None


def _decode_user_id_strict(token: str) -> str:
    """严格解码 JWT（生产路径）：失败即抛 401，不做任何降级。

    未配置 JWT_SECRET 时 jwt.decode 验签必然失败 → 40102，天然 fail-closed。
    """
    payload = decode_token(token)
    if payload.get("type") == "refresh":
        # refresh token 不得用于业务接口鉴权
        raise AppException(ErrorCode.TOKEN_INVALID, "无效的身份凭证 / Invalid token")
    sub = payload.get("sub") or payload.get("user_id")
    if not sub:
        raise AppException(ErrorCode.TOKEN_INVALID, "无效的身份凭证 / Invalid token")
    return str(sub)


def _bearer_token(request: Request) -> str | None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


def get_current_user(request: Request) -> str:
    """FastAPI 依赖：解析并透传当前用户 ID（生产环境强制鉴权）。"""
    token = _bearer_token(request)
    if _auth_enforced():
        if token is None:
            raise AppException(ErrorCode.MISSING_TOKEN, "缺少身份凭证 / Missing token")
        user_id = _decode_user_id_strict(token)
    else:
        user_id = resolve_user_id_from_request(request)
    set_user_id(user_id)
    return user_id


def resolve_user_id_from_request(request: Request) -> str:
    """解析 user_id（不抛异常；供中间件日志透传与开发模式依赖复用）。

    生产环境忽略 X-User-Id（仅记录日志上下文，鉴权由 get_current_user 强制）。
    """
    # 1. Bearer token
    token = _bearer_token(request)
    if token:
        user_id = _decode_user_id_from_token(token)
        if user_id:
            return user_id

    # 2. X-User-Id 透传（仅开发环境）
    if not _auth_enforced():
        x_user_id = request.headers.get("X-User-Id")
        if x_user_id:
            return x_user_id

    # 3. 匿名
    return ANONYMOUS

"""
认证依赖。

职责：
- 从请求中解析出当前用户 ID（user_id）
- 把 user_id 写入 contextvar，供日志/响应透传
- 返回 user_id 供路由使用

user_id 来源（按优先级）：
1. `Authorization: Bearer <JWT>` → 解码 JWT 的 sub 字段（需设置 JWT_SECRET）
2. `X-User-Id` 头 → 透传（仅 development 环境的设备级身份，网关/开发使用）

环境分级（settings.environment）：
- development（默认）：X-User-Id 透传 + 匿名兜底，不阻塞开发
- production：强制 JWT Bearer。缺失 → 40103 MISSING_TOKEN；
  无效/过期 → 40102/40101；X-User-Id 一律忽略（防认证绕过）。
"""
from fastapi import Request

from backend.config import settings
from backend.core.errors import AppException, ErrorCode
from backend.core.logging import set_user_id, get_logger

logger = get_logger("closenai.auth")

ANONYMOUS = "anonymous"


def _auth_enforced() -> bool:
    """生产环境强制鉴权（fail-closed）。"""
    return settings.environment.lower() in ("production", "prod")


def _decode_user_id_from_token(token: str) -> str | None:
    """尝试从 JWT 解码 user_id（sub）。失败返回 None（开发模式宽松路径）。"""
    try:
        import jwt

        if not settings.jwt_secret:
            return None
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        sub = payload.get("sub") or payload.get("user_id")
        return str(sub) if sub else None
    except Exception:  # noqa: BLE001  token 无效/过期/未配置
        return None


def _decode_user_id_strict(token: str) -> str:
    """严格解码 JWT（生产路径）：失败即抛 401，不做任何降级。

    未配置 JWT_SECRET 时 jwt.decode 验签必然失败 → 40102，天然 fail-closed。
    """
    import jwt

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise AppException(ErrorCode.TOKEN_EXPIRED, "登录已过期，请重新登录 / Session expired") from exc
    except jwt.PyJWTError as exc:  # 含 InvalidTokenError/InvalidKeyError（空密钥 fail-closed）
        raise AppException(ErrorCode.TOKEN_INVALID, "无效的身份凭证 / Invalid token") from exc
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

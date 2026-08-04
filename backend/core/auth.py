"""
认证依赖。

职责：
- 从请求中解析出当前用户 ID（user_id）
- 把 user_id 写入 contextvar，供日志/响应透传
- 返回 user_id 供路由使用

user_id 来源（按优先级）：
1. `Authorization: Bearer <JWT>` → 解码 JWT 的 sub 字段（需设置 JWT_SECRET）
2. `X-User-Id` 头 → 透传（网关/开发环境，配合信任网关时使用）

未解析到 user_id 时返回 "anonymous"（P0 阶段不阻塞开发；
正式启用鉴权后，应将此改为抛 MISSING_TOKEN 并强制登录）。
"""
from fastapi import Request

from backend.core.logging import set_user_id, get_logger

logger = get_logger("closenai.auth")

ANONYMOUS = "anonymous"


def _decode_user_id_from_token(token: str) -> str | None:
    """尝试从 JWT 解码 user_id（sub）。失败返回 None。"""
    try:
        import jwt

        from backend.config import settings

        if not settings.jwt_secret:
            return None
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        sub = payload.get("sub") or payload.get("user_id")
        return str(sub) if sub else None
    except Exception:  # noqa: BLE001  token 无效/过期/未配置
        return None


def get_current_user(request: Request) -> str:
    """FastAPI 依赖：解析并透传当前用户 ID。"""
    user_id = resolve_user_id_from_request(request)
    set_user_id(user_id)
    return user_id


def resolve_user_id_from_request(request: Request) -> str:
    """解析 user_id（供中间件与依赖复用，保证外层 context 已设置）。"""
    # 1. Bearer token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        user_id = _decode_user_id_from_token(auth_header[7:].strip())
        if user_id:
            return user_id

    # 2. X-User-Id 透传
    x_user_id = request.headers.get("X-User-Id")
    if x_user_id:
        return x_user_id

    # 3. 匿名（P0 开发阶段）
    logger.warning("未解析到 user_id，使用 anonymous")
    return ANONYMOUS
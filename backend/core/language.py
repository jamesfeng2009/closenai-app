"""
语言解析（多语言 header 透传）。

优先级（从高到低）：
1. `X-Language` header（客户端每次请求携带，实时、无状态）
2. `users.language`（数据库偏好，跨设备/重装保持，兜底）
3. 默认 `en`

与 `X-User-Id` 透传模式一致：header 优先，数据库兜底。
"""
from fastapi import Request, Depends

from backend.config import settings
from backend.core.auth import get_current_user
from backend.services.user_service import user_service


def resolve_request_language(request: Request) -> str | None:
    """
    从 `X-Language` header 解析语言码。

    仅返回 supported_languages 内的有效码；缺失或非法返回 None。
    `zh-CN` 这类带区域后缀的会归一化为 `zh`。
    """
    raw = request.headers.get("X-Language", "").strip()
    if not raw:
        return None
    code = raw.split("-")[0].lower()  # zh-CN → zh, en-US → en
    if code in settings.supported_languages:
        return code
    return None


async def get_request_language(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> str:
    """
    FastAPI 依赖：解析当前请求语言。

    优先级：X-Language header → users.language → en。
    """
    # 1. Header 优先
    header_lang = resolve_request_language(request)
    if header_lang:
        return header_lang

    # 2. 数据库偏好兜底
    try:
        db_lang = await user_service.get_language(user_id)
        if db_lang in settings.supported_languages:
            return db_lang
    except Exception:  # noqa: BLE001  读库失败不影响请求
        pass

    # 3. 默认
    return "en"
"""
统一响应格式。

所有接口返回统一 JSON 结构：
    {
        "code": 200,           # 整数状态码（成功 200，错误为业务错误码）
        "message": "success",  # 人类可读消息
        "data": {...},          # 业务数据（可为 null）
        "trace_id": "xxx",      # 请求追踪 ID
        "user_id": "xxx"        # 用户 ID（未登录为 "")
    }

路由/服务返回业务数据时，用 `success(data)` 包装。
错误统一由全局异常处理器生成 `fail(...)`，业务代码抛 `AppException` 即可，
不要手动构造失败响应。
"""
from typing import Any

from backend.core.logging import get_trace_id, get_user_id


def success(data: Any = None, message: str = "success", code: int = 200) -> dict:
    """构造成功响应。"""
    return {
        "code": code,
        "message": message,
        "data": data,
        "trace_id": get_trace_id(),
        "user_id": get_user_id(),
    }


def fail(
    code: int,
    message: str,
    data: Any = None,
    trace_id: str | None = None,
    user_id: str | None = None,
) -> dict:
    """构造失败响应（供全局异常处理器使用）。"""
    return {
        "code": code,
        "message": message,
        "data": data,
        "trace_id": trace_id or get_trace_id(),
        "user_id": user_id if user_id is not None else get_user_id(),
    }
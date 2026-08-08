"""
中间件与全局异常处理。

包含：
- TraceIdMiddleware：请求入口生成/透传 trace_id，写回响应头，并设置 contextvar
- 全局异常处理器：统一把异常转换为 {code, message, data, trace_id, user_id} 响应

中间件注册顺序：TraceIdMiddleware 必须在最外层（最先执行），保证后续日志都有 trace_id。
"""
import json
import uuid
from typing import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from backend.core.auth import resolve_user_id_from_request
from backend.core.errors import AppException, ErrorCode
from backend.core.logging import (
    get_logger,
    set_trace_id,
    set_user_id,
    log_app_exception,
)

logger = get_logger("closenai.middleware")

# 透传 header 名（客户端可携带 X-Request-Id 或 X-Trace-Id）
TRACE_HEADER = "X-Trace-Id"


class TraceIdMiddleware(BaseHTTPMiddleware):
    """为每个请求生成/透传 trace_id，并设置进 contextvar。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        trace_id = request.headers.get(TRACE_HEADER) or str(uuid.uuid4())
        set_trace_id(trace_id)
        # 在【外层 context】解析并设置 user_id，保证成功与异常路径日志都能透传
        set_user_id(resolve_user_id_from_request(request))
        response_body = await call_next(request)
        response_body.headers[TRACE_HEADER] = trace_id
        return response_body


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器，所有异常统一转换为标准响应。"""

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        log_app_exception(exc)
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "code": exc.code,
                "message": exc.message,
                "data": exc.data,
                "trace_id": set_trace_id_ctx(request),
                "user_id": get_user_id_ctx(),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 提取首个校验错误，给出可读信息
        # ctx 内含 ValueError 等异常对象，需兜底 str 序列化，否则响应渲染 500
        errors = json.loads(json.dumps(exc.errors(), default=str))
        first = errors[0] if errors else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        msg = first.get("msg", "参数校验失败")
        logger.warning("参数校验失败 loc=%s msg=%s", loc, msg)
        return JSONResponse(
            status_code=ErrorCode.BAD_REQUEST,
            content={
                "code": ErrorCode.INVALID_PARAMS,
                "message": (
                    f"参数错误: {loc} {msg} / Invalid parameters"
                    if loc else f"参数错误: {msg} / Invalid parameters"
                ),
                "data": errors,
                "trace_id": set_trace_id_ctx(request),
                "user_id": get_user_id_ctx(),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理异常: %s", exc)
        return JSONResponse(
            status_code=ErrorCode.INTERNAL_ERROR,
            content={
                "code": ErrorCode.INTERNAL_ERROR,
                "message": "服务器内部错误 / Internal server error",
                "data": None,
                "trace_id": set_trace_id_ctx(request),
                "user_id": get_user_id_ctx(),
            },
        )


def _set_trace_id_from_request(request: Request) -> str:
    """取请求 trace_id（若中间件已设置 contextvar 则用之）。"""
    from backend.core.logging import get_trace_id

    tid = get_trace_id()
    if not tid:
        tid = request.headers.get(TRACE_HEADER) or str(uuid.uuid4())
        set_trace_id(tid)
    return tid


def set_trace_id_ctx(request: Request) -> str:
    return _set_trace_id_from_request(request)


def get_user_id_ctx() -> str:
    from backend.core.logging import get_user_id

    return get_user_id()
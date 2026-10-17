"""
日志系统。

设计：
- 使用 contextvars 在请求上下文内透传 `trace_id` 与 `user_id`
- 每条日志自动附加 `trace_id` / `user_id`，便于全链路排查
- 中间件在请求入口设置 trace_id（透传 X-Trace-Id 或生成 UUID），认证后设置 user_id
- 业务代码只需 `logger = logging.getLogger(__name__)`，无需手动拼 trace_id

用法：
    from backend.core.logging import logger, set_trace_id, set_user_id

    logger.info("消息")          # 自动带 trace_id / user_id（若已设置）
    set_trace_id("xxx")          # 手动设置（一般由中间件完成）
    set_user_id("user-1")        # 认证后设置
"""
import contextvars
import logging
import re
import sys
from typing import Any

# T-8 trace 值消毒形状（工作单 2026-10-17）。**只有一份定义**，三个读点：
# HTTP dispatch（core/middleware.py）、gRPC 服务端（ai/rpc/server.py 的 _apply_trace，
# 批 T-E——R3 形状闸 test_r3_handler_shape 明令 rpc 层不许自己 `import re`），
# 以及对面 Go 的 httpapi.sanitizeTraceID（同一正则字面量，跨语言由测试钉住）。
# 必须 fullmatch：Python 的 `$` 会放过结尾换行，`abc\n` 得拒（一条日志变两条）。
TRACE_SHAPE = re.compile(r"[A-Za-z0-9._-]{1,128}")

# 请求上下文变量
_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
_user_id: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="")


def set_trace_id(trace_id: str) -> None:
    """设置当前请求的 trace_id。"""
    _trace_id.set(trace_id)


def set_user_id(user_id: str) -> None:
    """设置当前请求的 user_id（认证后调用）。"""
    _user_id.set(user_id)


def get_trace_id() -> str:
    """读取当前请求的 trace_id。"""
    return _trace_id.get()


def get_user_id() -> str:
    """读取当前请求的 user_id。"""
    return _user_id.get()


class ContextFilter(logging.Filter):
    """把 trace_id / user_id 附加到每条日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id.get() or "-"
        record.user_id = _user_id.get() or "-"
        return True


def setup_logging(level: str = "INFO") -> None:
    """配置根 logger。重复调用安全（幂等）。"""
    root = logging.getLogger()
    # 避免重复添加 handler
    root.handlers = []

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | trace_id=%(trace_id)s | "
            "user_id=%(user_id)s | %(message)s"
        )
    )
    handler.addFilter(ContextFilter())
    root.addHandler(handler)
    root.setLevel(level.upper())


# 便捷导入：业务代码统一用这个 logger
logger: logging.Logger = logging.getLogger("closenai")


def get_logger(name: str) -> logging.Logger:
    """获取带 context 的命名 logger。"""
    return logging.getLogger(name)


def log_app_exception(exc: Any, extra: dict | None = None) -> None:
    """记录业务异常（供全局异常处理器调用）。"""
    logger.error(
        "AppException code=%s message=%s",
        getattr(exc, "code", "?"),
        getattr(exc, "message", str(exc)),
        extra=extra or {},
    )
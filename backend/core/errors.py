"""
错误码体系（全局唯一维护）。

`code` 为整数状态码（非字符串），遵循 HTTP 语义分段：
- 2xx 成功
- 4xx 客户端错误（参数、认证、权限、资源不存在）
- 5xx 服务端错误（内部错误、服务不可用）

每个业务错误码在此集中定义，禁止在业务代码中硬编码数字。
给路由/服务抛错时，用 `AppException` 携带 code + message。

新增错误码 → 在此维护，并在 `errors.md` 或 SKILL 中登记。
"""
from enum import IntEnum


class ErrorCode(IntEnum):
    """业务错误码。

    4xxxx 段：4xx 客户端错误细分
    5xxxx 段：5xx 服务端错误细分
    """

    # ── 成功 ──
    OK = 200

    # ── 4xx 通用 ──
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    CONFLICT = 409
    RATE_LIMITED = 429

    # ── 4xxxx 业务细分 ──
    # 参数校验
    INVALID_PARAMS = 40001
    INVALID_EMOJI = 40002
    INVALID_LANGUAGE = 40003
    INVALID_TIMEZONE = 40004
    VERIFICATION_CODE_INVALID = 40005  # 邮箱验证码不正确
    VERIFICATION_CODE_EXPIRED = 40006  # 邮箱验证码已过期
    PASSWORD_TOO_WEAK = 40007  # 密码强度不足（≥8 位含字母+数字）
    # 认证
    TOKEN_EXPIRED = 40101
    TOKEN_INVALID = 40102
    MISSING_TOKEN = 40103
    INVALID_CREDENTIALS = 40104  # 用户名/邮箱或密码错误
    REFRESH_TOKEN_INVALID = 40105  # refresh token 无效/已吊销/已过期
    APPLE_AUTH_FAILED = 40106  # Apple identityToken 验签失败
    # 权限
    QUOTA_EXCEEDED = 40301
    PRO_REQUIRED = 40302
    AI_CONSENT_REQUIRED = 40303  # 未同意 AI 数据共享（5.1.2(i)），前端弹 consent 弹窗
    # 资源
    USER_NOT_FOUND = 40401
    RESOURCE_NOT_FOUND = 40402
    MEMORY_NOT_FOUND = 40403
    EMAIL_NOT_REGISTERED = 40404  # 邮箱未注册（reset 内部使用，对外不泄露）
    # 冲突
    ALREADY_CHECKED_IN = 40901
    SUBSCRIPTION_ALREADY_EXISTS = 40902
    EMAIL_ALREADY_USED = 40903  # 邮箱已被注册
    USERNAME_ALREADY_USED = 40904  # 用户名已被占用
    # 限流
    LLM_RATE_LIMITED = 42901
    EMAIL_SEND_RATE_LIMITED = 42902  # 验证码发送频率超限（60s 冷却 / 日上限）

    # ── 5xx 服务端 ──
    INTERNAL_ERROR = 500
    SERVICE_UNAVAILABLE = 503
    # 5xxxx 业务细分
    LLM_ERROR = 50001
    LLM_TIMEOUT = 50002
    DATABASE_ERROR = 50003
    PUSH_FAILED = 50004
    IAP_VERIFY_FAILED = 50005
    IAP_VERIFY_TIMEOUT = 50006  # B7：App Store 校验应用级超时（asyncio.wait_for 兜底）


class AppException(Exception):
    """业务异常。携带错误码、消息、可选的 data。

    Args:
        code: 错误码（ErrorCode 或 int）
        message: 人类可读的错误信息
        data: 可选，附加数据（如校验失败详情）
        http_status: 显式 HTTP 状态码（默认从 code 的段位推导）
    """

    def __init__(
        self,
        code: ErrorCode | int,
        message: str,
        data: object | None = None,
        http_status: int | None = None,
    ) -> None:
        self.code = int(code)
        self.message = message
        self.data = data
        # 默认 HTTP 状态码：显式指定优先；否则从业务码段位推导成合法 HTTP 状态
        self.http_status = http_status if http_status is not None else _derive_http_status(self.code)
        super().__init__(message)


def _derive_http_status(code: int) -> int:
    """把业务码段位映射为合法 HTTP 状态码。

    业务码（如 40001/40301/40401/50005）不能直接作为 HTTP 状态码，
    需按段位归一到标准 HTTP 状态。
    """
    if 40000 <= code < 50000:
        return code // 100  # 4xxxx → 4xx（如 40301 → 403）
    if 50000 <= code < 60000:
        return 500  # 5xxxx → 500
    return code
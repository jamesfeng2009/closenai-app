"""
邮件服务（Resend）。

发送邮箱验证码（注册 / 登录 / 重置密码）。文案走 core/copy.py 文案库
email namespace（15 语言，locale→en 回退）。
"""
import resend

from backend.config import settings
from backend.core import copy, env_isolation
from backend.core.errors import AppException, ErrorCode
from backend.core.logging import get_logger

logger = get_logger("closenai.email")

_PURPOSES = ("register", "login", "reset")


def _mask_email(email: str) -> str:
    """邮箱脱敏（日志用）：保留本地部分前 2 位与域名，其余打码。"""
    local, sep, domain = (email or "").partition("@")
    if not sep:
        return "***"
    return f"{local[:2]}***@{domain}"


def send_verification_code(email: str, code: str, purpose: str, language: str) -> None:
    """发送验证码邮件。purpose ∈ register|login|reset。

    - **受保护档**（`staging`／`production`／`prod`）未配置 resend_api_key：抛 503
      （禁止静默成功——验证码是登录凭证，发不出去却返回成功会把用户永久挡在登录流程外）
    - `development`：保留 DEV 告警降级，便于本地联调
    - 日志红线：验证码明文（5 分钟有效登录凭证）永不落日志，邮箱仅保留脱敏前缀

    档籍判据走 `env_isolation.is_protected`，**不许**在这里写字面量比较：
    `settings.environment == "production"` 既漏 `prod` 别名又漏 `staging`，
    于是 staging 上缺 key 时走 DEV 降级、接口返回成功而邮件根本不存在
    （2026-10-20 code review 抓到，与 D-T604-3「staging 与 production 同档」反着来）。
    """
    if not settings.resend_api_key:
        if env_isolation.is_protected(settings.environment):
            logger.error("受保护档（ENVIRONMENT=%s）缺失 resend_api_key，验证码邮件无法发送",
                         settings.environment)
            raise AppException(
                ErrorCode.SERVICE_UNAVAILABLE,
                "邮件服务暂不可用，请稍后重试 / Email service unavailable",
            )
        # 开发环境兜底：仅记脱敏告警并跳过发送（验证码明文不落日志）
        logger.warning(
            "[DEV] resend 未配置，验证码邮件未发送 email=%s purpose=%s",
            _mask_email(email),
            purpose,
        )
        return
    if purpose in _PURPOSES:
        subject = copy.t("email", f"subject.{purpose}", language)
    else:
        subject = "ClosenAI"
    line1 = copy.t("email", "body_line1", language).format(code=code)
    line2 = copy.t("email", "body_line2", language)
    html = f"<div style='font-family:sans-serif;color:#222'>{line1}<br/><br/>{line2}</div>"
    try:
        resend.api_key = settings.resend_api_key
        resend.Emails.send(
            {
                "from": settings.email_from,
                "to": [email],
                "subject": subject,
                "html": html,
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("send verification code to %s failed: %s", _mask_email(email), exc)
        raise AppException(
            ErrorCode.INTERNAL_ERROR, "验证码发送失败，请稍后重试 / Failed to send code"
        ) from exc

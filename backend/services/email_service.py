"""
邮件服务（Resend）。

发送邮箱验证码（注册 / 登录 / 重置密码）。按 language 双语文案
（zh 中文，其余英文，遵循项目「用户可见文案双表」规范）。
"""
import resend

from backend.config import settings
from backend.core.errors import AppException, ErrorCode
from backend.core.logging import get_logger

logger = get_logger("closenai.email")

_PURPOSE_SUBJECT_ZH = {
    "register": "您的 ClosenAI 注册验证码",
    "login": "您的 ClosenAI 登录验证码",
    "reset": "您的 ClosenAI 密码重置验证码",
}
_PURPOSE_SUBJECT_EN = {
    "register": "Your ClosenAI verification code",
    "login": "Your ClosenAI login code",
    "reset": "Your ClosenAI password reset code",
}


def send_verification_code(email: str, code: str, purpose: str, language: str) -> None:
    """发送验证码邮件。purpose ∈ register|login|reset。

    未配置 resend_api_key 时抛内部错误（开发环境可改用控制台日志输出验证码）。
    """
    if not settings.resend_api_key:
        # 开发环境兜底：打印验证码到日志，便于本地联调（生产必须配置）
        logger.warning("[DEV] resend 未配置，验证码 %s for %s (purpose=%s)", code, email, purpose)
        return
    zh = (language or "en").lower().startswith("zh")
    subject_map = _PURPOSE_SUBJECT_ZH if zh else _PURPOSE_SUBJECT_EN
    subject = subject_map.get(purpose, _PURPOSE_SUBJECT_EN.get(purpose, "ClosenAI"))
    if zh:
        line1 = f"您的验证码是：<strong style='font-size:24px;letter-spacing:4px'>{code}</strong>"
        line2 = "验证码 5 分钟内有效，请勿告知他人。"
    else:
        line1 = f"Your verification code is: <strong style='font-size:24px;letter-spacing:4px'>{code}</strong>"
        line2 = "This code expires in 5 minutes. Do not share it with anyone."
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
        logger.warning("send verification code to %s failed: %s", email, exc)
        raise AppException(
            ErrorCode.INTERNAL_ERROR, "验证码发送失败，请稍后重试 / Failed to send code"
        ) from exc

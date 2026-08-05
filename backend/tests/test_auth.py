"""core/auth.py 认证回归测试（B2：X-User-Id 无条件透传 = 认证绕过）。

覆盖：
- development：X-User-Id 透传、Bearer 优先、anonymous 兜底（保持现状）
- production：缺 token → 40103；无效 token → 40102；过期 → 40101；
  X-User-Id 一律忽略（即使无 Bearer）；Bearer 有效时 X-User-Id 不覆盖
"""
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from starlette.requests import Request

from backend.config import settings
from backend.core.auth import get_current_user, resolve_user_id_from_request
from backend.core.errors import AppException, ErrorCode

SECRET = "test-secret"


def _request(headers: dict) -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
    )


def _token(sub: str, exp: datetime | None = None) -> str:
    payload = {"sub": sub, "exp": exp or (datetime.now(timezone.utc) + timedelta(days=1))}
    return jwt.encode(payload, SECRET, algorithm="HS256")


@pytest.fixture
def prod_env(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret", SECRET)
    return settings


# ── development 模式（默认，保持开发体验）──


def test_dev_x_user_id_passthrough():
    assert settings.environment == "development"
    req = _request({"X-User-Id": "u-dev"})
    assert get_current_user(req) == "u-dev"


def test_dev_anonymous_fallback():
    assert get_current_user(_request({})) == "anonymous"


# ── production 模式（强制鉴权）──


def test_prod_rejects_x_user_id_only(prod_env):
    """B2 核心回归：仅 X-User-Id 头不再能通过认证。"""
    with pytest.raises(AppException) as exc_info:
        get_current_user(_request({"X-User-Id": "u-victim"}))
    assert exc_info.value.code == ErrorCode.MISSING_TOKEN


def test_prod_rejects_invalid_bearer(prod_env):
    with pytest.raises(AppException) as exc_info:
        get_current_user(_request({"Authorization": "Bearer forged.token.here"}))
    assert exc_info.value.code == ErrorCode.TOKEN_INVALID


def test_prod_rejects_expired_token(prod_env):
    expired = _token("u-1", exp=datetime.now(timezone.utc) - timedelta(hours=1))
    with pytest.raises(AppException) as exc_info:
        get_current_user(_request({"Authorization": f"Bearer {expired}"}))
    assert exc_info.value.code == ErrorCode.TOKEN_EXPIRED


def test_prod_accepts_valid_bearer(prod_env):
    req = _request({"Authorization": f"Bearer {_token('u-real')}"})
    assert get_current_user(req) == "u-real"


def test_prod_ignores_x_user_id_when_bearer_valid(prod_env):
    """伪造 X-User-Id 不能覆盖 Bearer 身份。"""
    req = _request(
        {"Authorization": f"Bearer {_token('u-real')}", "X-User-Id": "u-victim"}
    )
    assert get_current_user(req) == "u-real"


def test_prod_resolve_ignores_x_user_id_for_logging(prod_env):
    """中间件日志路径同样不得信任 X-User-Id。"""
    assert resolve_user_id_from_request(_request({"X-User-Id": "u-victim"})) == "anonymous"


def test_prod_fail_closed_without_jwt_secret(prod_env, monkeypatch):
    """未配置 JWT_SECRET 时验签必失败 → 40102（fail-closed）。"""
    monkeypatch.setattr(settings, "jwt_secret", "")
    with pytest.raises(AppException) as exc_info:
        get_current_user(_request({"Authorization": f"Bearer {_token('u-1')}"}))
    assert exc_info.value.code == ErrorCode.TOKEN_INVALID

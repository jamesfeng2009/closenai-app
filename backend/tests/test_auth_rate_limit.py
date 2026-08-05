"""认证限流回归测试（S4 验证码试错 / S5 登录限流 / S6 日限固定窗口 + 枚举速率前置）。

覆盖：
- _verify_code：错误码计数，达 CODE_MAX_ATTEMPTS 作废验证码（连正确码也拒绝）；
  验证成功清零试错计数
- login_with_password：连续失败 LOGIN_MAX_FAILURES 次后锁定（429）；
  锁定对命中账号按 user_id、未命中按 identifier；登录成功清零计数
- send_email_code：限流前置（业务校验前），register 枚举同样受冷却/日上限约束；
  日限 key 的 TTL 对齐固定窗口边界（下一个 UTC 0 点）

注：redis 未配置时走 backend.core.redis._mem_store 内存回退，每用例清空防串扰。
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.config import settings
from backend.core import redis as redis_client
from backend.core.auth import hash_password
from backend.core.errors import AppException, ErrorCode
from backend.repositories import user_repository
from backend.services.auth_service import (
    CODE_MAX_ATTEMPTS,
    CODE_TTL,
    DAILY_SEND_LIMIT,
    LOGIN_MAX_FAILURES,
    auth_service,
)


@pytest.fixture(autouse=True)
def clean_redis_mem():
    """内存 Redis 回退是模块级单例，用例间必须清空。"""
    redis_client._mem_store.clear()
    yield
    redis_client._mem_store.clear()


async def _make_password_user(db, email="u@x.com", username="user1", password="Password1"):
    user = await user_repository.create_user(
        db,
        user_id=f"u-{username}",
        email=email,
        username=username,
        password_hash=hash_password(password),
        email_verified=True,
    )
    await db.flush()
    return user


async def _store_code(email: str, code: str, purpose: str) -> None:
    await redis_client.setex(
        auth_service._code_key(email, purpose), CODE_TTL, auth_service._hash_code(code)
    )


# ── S4：验证码试错次数限制 ──


async def test_verify_code_wrong_code_raises_invalid():
    await _store_code("a@x.com", "123456", "login")
    with pytest.raises(AppException) as exc_info:
        await auth_service._verify_code("a@x.com", "000000", "login")
    assert exc_info.value.code == ErrorCode.VERIFICATION_CODE_INVALID


async def test_verify_code_invalidated_after_max_attempts():
    """连续试错达上限 → 验证码作废，即使之后输入正确码也视为过期。"""
    await _store_code("a@x.com", "123456", "login")
    for i in range(CODE_MAX_ATTEMPTS - 1):
        with pytest.raises(AppException) as exc_info:
            await auth_service._verify_code("a@x.com", "000000", "login")
        assert exc_info.value.code == ErrorCode.VERIFICATION_CODE_INVALID, f"第 {i + 1} 次应报不正确"
    # 第 CODE_MAX_ATTEMPTS 次错误 → 作废
    with pytest.raises(AppException) as exc_info:
        await auth_service._verify_code("a@x.com", "000000", "login")
    assert exc_info.value.code == ErrorCode.VERIFICATION_CODE_EXPIRED
    # 正确码也被拒绝（验证码已删）
    with pytest.raises(AppException) as exc_info:
        await auth_service._verify_code("a@x.com", "123456", "login")
    assert exc_info.value.code == ErrorCode.VERIFICATION_CODE_EXPIRED


async def test_verify_code_success_clears_attempts():
    """验证成功后试错计数清零（重新发码后从 0 计）。"""
    await _store_code("a@x.com", "123456", "login")
    with pytest.raises(AppException):
        await auth_service._verify_code("a@x.com", "000000", "login")
    await auth_service._verify_code("a@x.com", "123456", "login")  # 成功
    assert await redis_client.get(auth_service._attempts_key("a@x.com", "login")) is None
    assert await redis_client.get(auth_service._code_key("a@x.com", "login")) is None


# ── S5：密码登录限流 ──


async def test_login_locked_after_max_failures(db, monkeypatch):
    """连续失败 LOGIN_MAX_FAILURES 次 → 后续请求（即使密码正确）直接 429。"""
    monkeypatch.setattr(settings, "jwt_secret", "test-secret")
    await _make_password_user(db)
    for _ in range(LOGIN_MAX_FAILURES):
        with pytest.raises(AppException) as exc_info:
            await auth_service.login_with_password(db, "u@x.com", "WrongPass1")
        assert exc_info.value.code == ErrorCode.INVALID_CREDENTIALS
    # 已锁定：正确密码也 429
    with pytest.raises(AppException) as exc_info:
        await auth_service.login_with_password(db, "u@x.com", "Password1")
    assert exc_info.value.code == ErrorCode.RATE_LIMITED


async def test_login_success_clears_failures(db, monkeypatch):
    """失败若干次后登录成功 → 计数清零，后续失败重新计。"""
    monkeypatch.setattr(settings, "jwt_secret", "test-secret")
    await _make_password_user(db)
    for _ in range(LOGIN_MAX_FAILURES - 1):
        with pytest.raises(AppException):
            await auth_service.login_with_password(db, "u@x.com", "WrongPass1")
    resp = await auth_service.login_with_password(db, "u@x.com", "Password1")
    assert resp.access_token
    assert await redis_client.get(auth_service._login_fail_key("user:u-user1")) is None


async def test_login_rate_limit_applies_to_unknown_identifier(db):
    """未命中账号（枚举探测）按 identifier 计数限流。"""
    for _ in range(LOGIN_MAX_FAILURES):
        with pytest.raises(AppException) as exc_info:
            await auth_service.login_with_password(db, "ghost@x.com", "Whatever1")
        assert exc_info.value.code == ErrorCode.INVALID_CREDENTIALS
    with pytest.raises(AppException) as exc_info:
        await auth_service.login_with_password(db, "ghost@x.com", "Whatever1")
    assert exc_info.value.code == ErrorCode.RATE_LIMITED


async def test_login_lock_shared_across_email_and_username(db, monkeypatch):
    """同一账号用邮箱/用户名登录共享锁定（限流主体为 user_id）。"""
    monkeypatch.setattr(settings, "jwt_secret", "test-secret")
    await _make_password_user(db)
    for _ in range(LOGIN_MAX_FAILURES):
        with pytest.raises(AppException):
            await auth_service.login_with_password(db, "u@x.com", "WrongPass1")
    # 换用户名登录同样被锁
    with pytest.raises(AppException) as exc_info:
        await auth_service.login_with_password(db, "user1", "Password1")
    assert exc_info.value.code == ErrorCode.RATE_LIMITED


# ── S6：日限固定窗口 + 枚举速率前置 ──


async def test_send_code_rate_limit_precedes_business_check(db):
    """限流前置：register 用途对"已注册邮箱"同样计入日限，超限后报 429 而非 409。

    防枚举：攻击者无法绕过限流通过 409 差异探测已注册邮箱。
    """
    await _make_password_user(db, email="taken@x.com", username="taken")
    for i in range(DAILY_SEND_LIMIT):
        with pytest.raises(AppException) as exc_info:
            await auth_service.send_email_code(db, "taken@x.com", "register")
        # 限流前置：未超限时报业务码 409（且消耗日限额度）
        assert exc_info.value.code == ErrorCode.EMAIL_ALREADY_USED, f"第 {i + 1} 次应报 409"
    # 第 DAILY_SEND_LIMIT+1 次：限流先于业务校验 → 429
    with pytest.raises(AppException) as exc_info:
        await auth_service.send_email_code(db, "taken@x.com", "register")
    assert exc_info.value.code == ErrorCode.EMAIL_SEND_RATE_LIMITED


async def test_send_code_cooldown_blocks_immediate_resend(db):
    await auth_service.send_email_code(db, "new@x.com", "register")
    with pytest.raises(AppException) as exc_info:
        await auth_service.send_email_code(db, "new@x.com", "register")
    assert exc_info.value.code == ErrorCode.EMAIL_SEND_RATE_LIMITED


def test_daily_window_ttl_aligns_to_midnight(monkeypatch):
    """日限 TTL 对齐固定窗口边界：剩余秒数 = 到下一个 UTC 0 点。"""
    import backend.services.auth_service as mod

    fake_now = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(mod, "utc_now", lambda: fake_now)
    ttl = auth_service._daily_window_ttl()
    assert ttl == int(timedelta(hours=12).total_seconds())

    # 跨日边界：23:59:30 距 0 点仅 30 秒 → 触发 60s 下限保护（防退化 TTL）
    monkeypatch.setattr(
        mod, "utc_now", lambda: datetime(2026, 8, 6, 23, 59, 30, tzinfo=timezone.utc)
    )
    assert auth_service._daily_window_ttl() == 60

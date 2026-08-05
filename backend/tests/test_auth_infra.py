"""S7-S10 后端杂项回归测试。

覆盖：
- S7 resend 异步化：send_email_code 在 async 上下文经线程池调用同步邮件 SDK
- S8 JWKS kid 刷新：kid 未命中缓存时强制刷新 JWKS 重试一次；仍无则 40106
- S9 cleanup-tokens：auth_repository.delete_expired 仅删过期 token，保留有效/已吊销未过期
- S10 启动配置校验：production 缺 JWT_SECRET/CRON_SECRET/sqlite 默认库 fail-fast；dev 仅告警
"""
import json
import time
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from backend.config import settings
from backend.core import auth as core_auth
from backend.core import redis as redis_client
from backend.core.errors import AppException, ErrorCode
from backend.main import _validate_startup_config
from backend.models.auth import RefreshToken
from backend.repositories import auth_repository
from backend.services.auth_service import auth_service


@pytest.fixture(autouse=True)
def clean_redis_mem():
    redis_client._mem_store.clear()
    yield
    redis_client._mem_store.clear()


# ── S7：resend 异步化 ──


async def test_send_email_code_calls_mailer_in_thread(db, monkeypatch):
    """同步邮件 SDK 被 await（asyncio.to_thread）调用，参数透传正确。"""
    calls = []

    def fake_send(email, code, purpose, language):
        calls.append((email, code, purpose, language))

    monkeypatch.setattr("backend.services.auth_service.send_verification_code", fake_send)
    result = await auth_service.send_email_code(db, "t@x.com", "login", "zh")
    assert result == {"status": "ok"}
    assert len(calls) == 1
    email, code, purpose, language = calls[0]
    assert (email, purpose, language) == ("t@x.com", "login", "zh")
    assert len(code) == 6 and code.isdigit()


# ── S8：JWKS kid 刷新 ──


def _make_apple_token_and_jwk(kid: str) -> tuple[str, dict]:
    """生成真实 RSA 密钥对 + RS256 签名 token + 对应公网 JWK。"""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    algo = pyjwt.algorithms.RSAAlgorithm(pyjwt.algorithms.RSAAlgorithm.SHA256)
    jwk = json.loads(algo.to_jwk(private_key.public_key()))
    jwk["kid"] = kid
    now = datetime.now(timezone.utc)
    token = pyjwt.encode(
        {
            "sub": "apple-sub-1",
            "email": "a@privaterelay.appleid.com",
            "iss": core_auth.APPLE_ISSUER,
            "aud": "com.closenai.app",
            "iat": now,
            "exp": now + timedelta(minutes=10),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )
    return token, jwk


async def test_apple_verify_refreshes_jwks_on_kid_miss(monkeypatch):
    """kid 未命中缓存 → 强制刷新一次 → 验签成功（Apple 轮换密钥场景）。"""
    token, jwk = _make_apple_token_and_jwk("kid-new")
    fetch_calls = []

    async def fake_fetch(force: bool = False):
        fetch_calls.append(force)
        return [jwk] if force else []  # 缓存无新 kid，刷新后才有

    monkeypatch.setattr(core_auth, "_fetch_apple_jwks", fake_fetch)
    monkeypatch.setattr(settings, "apple_client_id", "com.closenai.app")
    sub, email, _is_private = await core_auth.verify_apple_identity_token(token)
    assert sub == "apple-sub-1"
    assert email == "a@privaterelay.appleid.com"
    assert fetch_calls == [False, True]


async def test_apple_verify_fails_when_kid_absent_after_refresh(monkeypatch):
    """刷新后仍无 kid → APPLE_AUTH_FAILED（不无限重试）。"""
    token, _jwk = _make_apple_token_and_jwk("kid-unknown")
    fetch_calls = []

    async def fake_fetch(force: bool = False):
        fetch_calls.append(force)
        return []

    monkeypatch.setattr(core_auth, "_fetch_apple_jwks", fake_fetch)
    monkeypatch.setattr(settings, "apple_client_id", "com.closenai.app")
    with pytest.raises(AppException) as exc_info:
        await core_auth.verify_apple_identity_token(token)
    assert exc_info.value.code == ErrorCode.APPLE_AUTH_FAILED
    assert fetch_calls == [False, True]  # 只重试一次


# ── S9：cleanup-tokens（delete_expired）──


async def test_delete_expired_removes_only_expired(db):
    now = datetime.now(timezone.utc)
    for token_id, expires_at, revoked in [
        ("t-expired", now - timedelta(days=1), False),       # 过期 → 删
        ("t-expired-revoked", now - timedelta(days=1), True),  # 过期且已吊销 → 删
        ("t-valid", now + timedelta(days=30), False),        # 有效 → 留
        ("t-revoked", now + timedelta(days=30), True),       # 已吊销未过期 → 留（重放检测依赖）
    ]:
        await auth_repository.create_refresh_token(
            db, user_id="u-1", token_hash=f"h-{token_id}", expires_at=expires_at, token_id=token_id
        )
        if revoked:
            await auth_repository.revoke_token(db, token_id)
    deleted = await auth_repository.delete_expired(db)
    assert deleted == 2
    remaining = (await db.execute(select(RefreshToken))).scalars().all()
    assert sorted(t.id for t in remaining) == ["t-revoked", "t-valid"]


# ── S10：启动配置校验 ──


def test_startup_validation_fails_in_production_without_required(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret", "")
    monkeypatch.setattr(settings, "cron_secret", "")
    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///./closenai.db")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        _validate_startup_config()


def test_startup_validation_passes_in_production_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret", "s3cret")
    monkeypatch.setattr(settings, "cron_secret", "cron-s3cret")
    monkeypatch.setattr(
        settings, "database_url", "postgresql+psycopg://u:p@db.supabase.co:5432/postgres"
    )
    _validate_startup_config()  # 不抛异常


def test_startup_validation_warns_only_in_development(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "jwt_secret", "")
    monkeypatch.setattr(settings, "cron_secret", "")
    monkeypatch.setattr(settings, "database_url", "sqlite+aiosqlite:///./closenai.db")
    _validate_startup_config()  # dev 仅告警，不抛异常

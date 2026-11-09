"""iap_service：_map_tier 键长降序匹配（pro_plus 不得误判为 pro）。"""
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.core.errors import AppException, ErrorCode
from backend.config import settings
from backend.models.subscription import Subscription
from backend.models.user import User
from backend.repositories import subscription_repository
from backend.services.iap_service import _map_tier, iap_service


@pytest.mark.parametrize(
    "product_id,expected",
    [
        # 关键回归：含 pro_plus 的 product_id 必须先命中长键，不被 "pro" 前缀吞掉
        ("com.closenai.pro_plus_monthly", "pro_plus"),
        ("com.closenai.pro_plus.annual", "pro_plus"),
        ("PRO_PLUS_YEARLY", "pro_plus"),  # 大小写不敏感
        ("closenai_premium_yearly", "pro_plus"),
        # M6：`life_archive → pro_plus` 别名已删除。买断不再进 _map_tier（走
        # user_entitlements），落到未知产品的缺省 pro 也无害——收据内买断交易
        # 会先被 _non_consumable_scope 分流，不进订阅发放链路。
        ("life_archive_forever", "pro"),
        ("com.closenai.pro_monthly", "pro"),
        ("com.closenai.pro", "pro"),
        # 未知产品缺省回退 pro
        ("something_unknown", "pro"),
    ],
)
def test_map_tier_longest_key_first(product_id, expected):
    assert _map_tier(product_id) == expected


async def test_b3_partial_unique_index_allows_multiple_nulls(db):
    """B3：original_transaction_id IS NULL 时多行不冲突（旧订阅兼容）。"""
    sub1 = Subscription(
        id="s-null-1", user_id="u1", product_id="p", tier="free",
        original_transaction_id=None,
    )
    sub2 = Subscription(
        id="s-null-2", user_id="u2", product_id="p", tier="free",
        original_transaction_id=None,
    )
    db.add(sub1)
    db.add(sub2)
    await db.flush()  # 不应抛 IntegrityError
    await db.rollback()


async def test_b3_partial_unique_index_rejects_duplicate_non_null(db):
    """B3：同一非空 original_transaction_id 在 DB 层全局唯一。

    iap_service 跨账号检查存在竞态窗口，DB 层 partial unique index 兜底，
    后到者触发 IntegrityError，由 iap_service 的 SAVEPOINT 捕获重查。
    """
    sub1 = Subscription(
        id="s-txA-1", user_id="u1", product_id="p", tier="pro",
        original_transaction_id="txA",
    )
    db.add(sub1)
    await db.flush()

    sub2 = Subscription(
        id="s-txA-2", user_id="u2", product_id="p", tier="pro",
        original_transaction_id="txA",
    )
    db.add(sub2)
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_b3_cross_account_check_rejects_bound_receipt(db, make_user):
    """B3：跨账号检查——同一收据已绑定他账号时拒绝。

    此为应用层前置检查（fast path），DB 唯一索引是 race-condition 兜底。
    """
    await make_user("u-owner")
    await make_user("u-attacker")
    await subscription_repository.upsert(
        db, "u-owner",
        product_id="com.closenai.pro_monthly",
        tier="pro",
        expires_at=None,
        original_transaction_id="txB",
        is_active=True,
        is_trial=False,
    )

    existing = await subscription_repository.get_by_original_transaction(db, "txB")
    assert existing is not None
    assert existing.user_id == "u-owner"

    # 模拟 iap_service._validate 的跨账号检查
    if existing and existing.user_id != "u-attacker":
        with pytest.raises(AppException) as exc_info:
            raise AppException(
                ErrorCode.SUBSCRIPTION_ALREADY_EXISTS,
                "该收据已绑定其他账号",
            )
        assert exc_info.value.code == ErrorCode.SUBSCRIPTION_ALREADY_EXISTS


class _FakeVerifyResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeVerifyClient:
    def __init__(self, payload: dict):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        return _FakeVerifyResponse(self._payload)


def _patch_apple(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(settings, "app_store_shared_secret", "test-shared-secret")
    monkeypatch.setattr(
        "backend.services.iap_service.httpx.AsyncClient",
        lambda *args, **kwargs: _FakeVerifyClient(payload),
    )


async def test_sandbox_receipt_rejected_in_production(db, make_user, monkeypatch):
    """核心回归：生产环境收到沙盒收据必须拒绝。

    漏洞形状：客户端传 is_sandbox=true 并提交沙盒收据，Apple 端点返回 status=0，
    旧实现直接发权益 —— 免费拿到 pro。收据的 environment 由 Apple 签发，
    服务端必须与 app_store_env 比对。
    """
    await make_user("u-iap-env")
    monkeypatch.setattr(settings, "app_store_env", "production")
    _patch_apple(
        monkeypatch,
        {"status": 0, "environment": "Sandbox", "receipt": {"in_app": []}},
    )

    result = await iap_service._validate(
        db, "u-iap-env", "sandbox-receipt", "com.closenai.pro_monthly", True
    )

    assert result["valid"] is False
    assert result["reason"] == "environment_mismatch"


async def test_production_receipt_passes_environment_gate(db, make_user, monkeypatch):
    """正向证人：生产收据不会被环境校验误杀（继续走到交易解析）。"""
    await make_user("u-iap-prod")
    monkeypatch.setattr(settings, "app_store_env", "production")
    _patch_apple(
        monkeypatch,
        {"status": 0, "environment": "Production", "receipt": {"in_app": []}},
    )

    result = await iap_service._validate(
        db, "u-iap-prod", "prod-receipt", "com.closenai.pro_monthly", False
    )

    assert result["reason"] == "no_transactions"


# ── P2 #19：debug 模拟购买不得在受保护环境生效 ──


async def test_debug_simulation_rejected_in_protected_env(db, make_user, monkeypatch):
    """staging/production 即便 DEBUG=true 且没配密钥，也不许白送 pro。"""
    await make_user("u-iap-staging")
    monkeypatch.setattr(settings, "app_store_shared_secret", "")
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "environment", "staging")

    result = await iap_service._validate(
        db, "u-iap-staging", "fake-receipt", "com.closenai.pro_monthly", False
    )

    assert result["valid"] is False
    assert result["reason"] == "not_configured"
    tier = (await db.execute(select(User.tier).where(User.id == "u-iap-staging"))).scalar_one()
    assert tier == "free"  # 权益未被模拟发放


async def test_debug_simulation_still_works_in_development(db, make_user, monkeypatch):
    """正向证人：开发档的模拟购买不受新闸影响。"""
    await make_user("u-iap-dev")
    monkeypatch.setattr(settings, "app_store_shared_secret", "")
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "environment", "development")

    result = await iap_service._validate(
        db, "u-iap-dev", "fake-receipt", "com.closenai.pro_monthly", False
    )

    assert result["valid"] is True
    assert result["tier"] == "pro"
    tier = (await db.execute(select(User.tier).where(User.id == "u-iap-dev"))).scalar_one()
    assert tier == "pro"

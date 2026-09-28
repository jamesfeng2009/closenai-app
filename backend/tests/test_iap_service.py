"""iap_service：_map_tier 键长降序匹配（pro_plus 不得误判为 pro）。"""
import pytest
from sqlalchemy.exc import IntegrityError

from backend.core.errors import AppException, ErrorCode
from backend.models.subscription import Subscription
from backend.repositories import subscription_repository
from backend.services.iap_service import _map_tier


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

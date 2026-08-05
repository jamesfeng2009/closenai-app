"""iap_service：_map_tier 键长降序匹配（pro_plus 不得误判为 pro）。"""
import pytest

from backend.services.iap_service import _map_tier


@pytest.mark.parametrize(
    "product_id,expected",
    [
        # 关键回归：含 pro_plus 的 product_id 必须先命中长键，不被 "pro" 前缀吞掉
        ("com.closenai.pro_plus_monthly", "pro_plus"),
        ("com.closenai.pro_plus.annual", "pro_plus"),
        ("PRO_PLUS_YEARLY", "pro_plus"),  # 大小写不敏感
        ("closenai_premium_yearly", "pro_plus"),
        ("life_archive_forever", "pro_plus"),
        ("com.closenai.pro_monthly", "pro"),
        ("com.closenai.pro", "pro"),
        # 未知产品缺省回退 pro
        ("something_unknown", "pro"),
    ],
)
def test_map_tier_longest_key_first(product_id, expected):
    assert _map_tier(product_id) == expected

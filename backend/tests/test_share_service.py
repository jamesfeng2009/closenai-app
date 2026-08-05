"""share_service：period 白名单与 Pro 权益门。"""
import pytest

from backend.core.errors import AppException, ErrorCode
from backend.services.share_service import share_service


@pytest.mark.parametrize("bad_period", ["daily", "hourly", "biweekly", "", "WEEKLY"])
async def test_export_report_rejects_invalid_period(db, make_user, bad_period):
    """period 白名单：非法值 → 400 INVALID_PARAMS（大小写敏感）。"""
    await make_user("u1")
    with pytest.raises(AppException) as exc_info:
        await share_service.export_report(db, "u1", bad_period)
    assert exc_info.value.code == ErrorCode.INVALID_PARAMS


@pytest.mark.parametrize("period", ["monthly", "quarterly", "yearly"])
async def test_export_report_pro_periods_require_pro(db, make_user, period):
    """月/季/年报为 Pro 权益：free 用户 → 40302 PRO_REQUIRED。"""
    await make_user("u1", tier="free")
    with pytest.raises(AppException) as exc_info:
        await share_service.export_report(db, "u1", period)
    assert exc_info.value.code == ErrorCode.PRO_REQUIRED

"""share_service：period 白名单、Pro 权益门、组合名片的九型维度。"""
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


# ── 组合名片的九型维度（P1-F04 §6：组合 = 16 型 × 九型两框架）──


async def test_combo_card_carries_real_enneagram_type(db, make_user):
    """dimensions.enneagram 必须是 users.enneagram_type 的真值。

    写死 None 时「双维度组合名片」实际只有一个框架，卡的名头和卡的内容不符。
    """
    await make_user("u1", tier="pro", enneagram_type="5", name="小见")

    card = await share_service.export_personality_card(db, "u1", "zh", "combo")

    assert card["layout"] == "combo"
    assert card["dimensions"]["enneagram"] == "5"
    assert set(card["dimensions"]) == {"mbti", "enneagram"}


async def test_combo_card_does_not_invent_enneagram(db, make_user):
    """没测过九型的用户：维度留空，而不是补一个看起来像真的编号。"""
    await make_user("u1", tier="pro", enneagram_type=None)

    card = await share_service.export_personality_card(db, "u1", "zh", "combo")

    assert card["dimensions"]["enneagram"] is None


async def test_enneagram_dimension_is_preview_only_not_in_share_text(db, make_user):
    """九型只在图片卡预览上；分享出去的是纯文本，前端内容清单不能多报。

    这条是跨仓契约：ShareManifest 列的四项必须等于 share_text 实际带的四项。
    """
    await make_user("u1", tier="pro", enneagram_type="5", name="小见")

    card = await share_service.export_personality_card(db, "u1", "zh", "combo")

    assert card["dimensions"]["enneagram"] == "5"
    assert "5 号" not in card["share_text"]
    assert card["user_name"] in card["share_text"]

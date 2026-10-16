"""share_service：period 白名单、Pro 权益门、组合名片的九型维度、周期报告缓存键的语种维度。

判据来源：`export_report` 的载荷整体按 `language` 生成（`_build_weekly_report` 的
`period_label`/`mood_trend`、`growth_service.generate_report` 的正文），所以缓存键必须
一起带上语种；这一族不变量的四条变异口径与年报卡同源（`scripts/mutate_share_lang_key_gate.py`）。
"""
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.core.errors import AppException, ErrorCode
from backend.models.mood import MoodCheckin
from backend.models.share import ShareExport
from backend.services.share_service import share_service

UTC = timezone.utc
# 2026-10-15 是 UTC 周四，本地日历的 ISO 周为 2026-W42；周期键的字面形态按它写死
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


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


# ── S1（tasks.md:49，2026-10-16 收口）：周期报告缓存键的语种维度 ──

WEEKLY_LABEL_ZH = "每周洞察"
WEEKLY_LABEL_EN = "Weekly Insight"


async def _checkin(db, user_id: str, at: datetime, emoji: str = "😊") -> None:
    """造一条签到。

    weekly 报告的分布只读 `mood_repository.get_history(days=7)`，窗口按 `utc_now()` 往前推，
    所以时间点点在 `time_travel` 的那个「现在」附近，不能按真实时钟给。
    `mood_label` 留空以覆盖「按语言回退标签」那一支。
    """
    db.add(
        MoodCheckin(
            id=str(uuid4()),
            user_id=user_id,
            mood_emoji=emoji,
            mood_label=None,
            ai_interpretation="interp",
            checkin_date=at.date(),
            created_at=at,
        )
    )
    await db.flush()


async def test_export_report_cache_key_is_language_scoped(db, make_user, time_travel):
    """缓存键必须含语种：否则同一用户同一周期换 UI 语言拿到的是首次生成那个语种的报告。"""
    from sqlalchemy import select as _select

    time_travel.set(NOW)
    await make_user("u1")
    await _checkin(db, "u1", NOW - timedelta(days=1))

    zh = await share_service.export_report(db, "u1", "weekly", "zh")
    await _checkin(db, "u1", NOW)
    en = await share_service.export_report(db, "u1", "weekly", "en")

    assert zh["period_label"] == WEEKLY_LABEL_ZH
    assert en["period_label"] == WEEKLY_LABEL_EN
    # 真重算而不是拿回 zh 那一条：en 读得到 zh 导出之后新增的那次签到
    assert sum(zh["mood_distribution"].values()) == 1
    assert sum(en["mood_distribution"].values()) == 2

    rows = sorted(
        (r.period, r.period_key)
        for r in (await db.execute(_select(ShareExport))).scalars().all()
    )
    assert rows == [("weekly", "2026-W42_en"), ("weekly", "2026-W42_zh")]


async def test_export_report_cache_hits_within_same_language_including_region_variant(
    db, make_user, time_travel
):
    """后缀取 `copy.locale_for` 的规范化码：zh 与 zh-CN 必须命中同一条，否则每种地区变体各算一遍。"""
    from sqlalchemy import select as _select

    time_travel.set(NOW)
    await make_user("u1")
    await _checkin(db, "u1", NOW - timedelta(days=2))

    first = await share_service.export_report(db, "u1", "weekly", "zh-CN")
    await _checkin(db, "u1", NOW - timedelta(days=1))
    again = await share_service.export_report(db, "u1", "weekly", "zh")

    assert again == first  # 命中缓存 → 中间那次签到不进报告
    rows = (await db.execute(_select(ShareExport))).scalars().all()
    assert len(rows) == 1
    assert rows[0].period_key == "2026-W42_zh"


@pytest.mark.parametrize(
    "period,key_pattern",
    [
        ("weekly", r"^2026-W\d{2}_zh$"),
        ("monthly", r"^2026-\d{2}_zh$"),
        ("quarterly", r"^2026-Q[1-4]_zh$"),
        ("yearly", r"^2026_zh$"),
    ],
)
async def test_export_report_period_key_keeps_calendar_scoping(
    db, make_user, time_travel, period, key_pattern
):
    """语种后缀不能把日历维度顶掉：键一旦退化成只带语种，同一 (user, period) 从第一次导出起
    就永久返回那一份——周报跨周不更新，与「同周期缓存」的口径正好相反。"""
    from sqlalchemy import select as _select

    time_travel.set(NOW)
    await make_user("u1", tier="pro")

    await share_service.export_report(db, "u1", period, "zh")

    rows = (await db.execute(_select(ShareExport))).scalars().all()
    assert len(rows) == 1
    assert re.match(key_pattern, rows[0].period_key), rows[0].period_key


async def test_export_report_is_cached_once_per_period(db, make_user, time_travel):
    """命中缓存必须直接返回缓存载荷：去掉这条早退，每个请求都重算并重新落库，
    缓存形同不存在（`save()` 撞唯一约束会返回旧行，但函数返回的是刚算出来的那一份）。"""
    time_travel.set(NOW)
    await make_user("u1")
    await _checkin(db, "u1", NOW - timedelta(days=2))

    first = await share_service.export_report(db, "u1", "weekly", "en")
    await _checkin(db, "u1", NOW - timedelta(days=1))
    second = await share_service.export_report(db, "u1", "weekly", "en")

    assert second["mood_distribution"] == first["mood_distribution"]
    assert sum(second["mood_distribution"].values()) == 1

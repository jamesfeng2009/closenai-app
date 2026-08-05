"""growth_service：_insight_level 晋升阈值、仪表盘周报时效过滤、规则打标、报告门控。"""
from datetime import datetime, timezone

import pytest

from backend.core.errors import AppException
from backend.repositories import growth_repository
from backend.services.growth_service import growth_service, tag_record_content


@pytest.mark.parametrize(
    "chat_count,expected",
    [
        (0, "L1 Observation"),
        (20, "L1 Observation"),
        (21, "L2 Pattern"),
        (59, "L2 Pattern"),
        (60, "L3 Forecast"),
        (89, "L3 Forecast"),
        (90, "L4 Coaching"),
        (500, "L4 Coaching"),
    ],
)
def test_insight_level_promotion_thresholds(chat_count, expected):
    """洞察等级按累计对话量晋升：21→L2 / 60→L3 / 90→L4（含边界）。"""
    assert growth_service._insight_level("u1", chat_count) == expected


async def test_dashboard_weekly_insight_only_within_current_week(
    db, make_user, time_travel
):
    """周报时效：仅本周内生成的周报在仪表盘展示，上周及更早视为过期。"""
    # 冻结到 2026-08-05（周三）UTC，本周一 = 2026-08-03
    time_travel.set(datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc))
    await make_user("u1", timezone="UTC")

    record = await growth_repository.create(db, "u1", "weekly_summary", "本周周报内容")
    record.created_at = datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc)  # 本周一
    await db.flush()
    dash = await growth_service.get_dashboard(db, "u1")
    assert dash.weekly_insight == "本周周报内容"

    record.created_at = datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc)  # 上周日
    await db.flush()
    dash2 = await growth_service.get_dashboard(db, "u1")
    assert dash2.weekly_insight is None


def test_tag_record_content_keyword_rules():
    """规则打标：中英关键词命中对应标签；无命中为空（零 LLM 成本）。"""
    tags = tag_record_content("今天工作压力很大，还和朋友吵架了")
    assert "情绪" in tags  # 压力
    assert "工作" in tags  # 工作
    assert "关系" in tags  # 朋友
    assert "沟通" in tags  # 吵架
    assert "目标" not in tags

    assert tag_record_content("plain text with no keywords at all") == []


async def test_generate_report_rejects_invalid_period(db, make_user):
    """报告周期白名单：非法 period → 400 INVALID_PARAMS。"""
    await make_user("u1")
    with pytest.raises(AppException) as exc:
        await growth_service.generate_report(db, "u1", "daily")
    assert exc.value.code == 40001


@pytest.mark.parametrize("period", ["monthly", "quarterly", "yearly"])
async def test_generate_report_pro_gate(db, make_user, period):
    """月/季/年报为 Pro 权益：免费用户 → 40302，防止绕过 share 端点的 Pro 门。"""
    await make_user("u1", tier="free")
    with pytest.raises(AppException) as exc:
        await growth_service.generate_report(db, "u1", period)
    assert exc.value.code == 40302


async def test_generate_report_weekly_free_allowed(db, make_user):
    """周报对免费用户开放（PRD P1-F02 §3：weekly 免费）。"""
    await make_user("u1", tier="free")
    report = await growth_service.generate_report(db, "u1", "weekly")
    assert report["period"] == "weekly"
    assert report["content"]

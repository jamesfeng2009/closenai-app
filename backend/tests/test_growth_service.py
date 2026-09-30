"""growth_service：_insight_level 晋升阈值、仪表盘周报时效过滤、规则打标、报告门控。"""
from datetime import date, datetime, timezone

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


async def test_dashboard_is_pro_flag(db, make_user, time_travel):
    """仪表盘带 is_pro：洞察分级呈现（§5.20）的前端判定数据源，随用户行同源。"""
    time_travel.set(datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc))
    await make_user("u1", timezone="UTC")
    assert (await growth_service.get_dashboard(db, "u1")).is_pro is False

    await make_user("u1", timezone="UTC", tier="pro")
    assert (await growth_service.get_dashboard(db, "u1")).is_pro is True


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


async def test_weekly_insight_carries_memory_ref_union(db, make_user, time_travel):
    """周报写入聚合本周各记录已引用的记忆 ID（去重），GET 出参带回（前端回链证据）。"""
    time_travel.set(datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc))
    await make_user("u1", timezone="UTC")
    await growth_repository.create(
        db, "u1", "daily_insight", "a", related_memory_ids=["m1", "m2"]
    )
    await growth_repository.create(
        db, "u1", "daily_insight", "b", related_memory_ids=["m2", "m3"]
    )

    await growth_service.generate_weekly_insight(db, "u1", "en")

    weekly = await growth_repository.get_latest(db, "u1", "weekly_summary")
    assert weekly.related_memory_ids.split(",") == ["m1", "m2", "m3"]
    resp = await growth_service.get_weekly_insight(db, "u1")
    assert resp.related_memory_ids == ["m1", "m2", "m3"]
    assert resp.content


async def test_weekly_insight_refs_capped_and_none_when_empty(db, make_user, time_travel):
    """引用并集超 5 条截断（与 reflection 同口径）；无引用记录时落 None。"""
    time_travel.set(datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc))
    await make_user("u1", timezone="UTC")
    for i, mids in enumerate((["a1", "a2"], ["a3"], ["a4", "a5"], ["a6"], ["a7"])):
        await growth_repository.create(
            db, "u1", "daily_insight", f"r{i}", related_memory_ids=mids
        )
    await growth_service.generate_weekly_insight(db, "u1", "en")
    weekly = await growth_repository.get_latest(db, "u1", "weekly_summary")
    assert weekly.related_memory_ids.split(",") == ["a1", "a2", "a3", "a4", "a5"]

    await make_user("u2", timezone="UTC")
    await growth_service.generate_weekly_insight(db, "u2", "en")
    resp = await growth_service.get_weekly_insight(db, "u2")
    assert resp.related_memory_ids is None


async def test_get_day_summary_aggregates_mood_records_chats(db, make_user, time_travel):
    """单日摘要卡片（P0-F07 §2）：情绪 + 当日记录 + 对话数 + 当日徽章。"""
    from backend.repositories import chat_repository, milestone_repository, mood_repository

    time_travel.set(datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc))
    await make_user("u1", timezone="UTC")
    target = date(2026, 8, 4)

    # 当日签到（checkin_date 即用户本地日历日）
    checkin = await mood_repository.create(db, "u1", "😊", "开心", "解读", checkin_date=target)
    checkin.created_at = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)
    # 当日洞察记录（= AI 对话摘要）
    record = await growth_repository.create(
        db, "u1", "daily_insight", "今日洞察：工作压力", tags=["工作"]
    )
    record.created_at = datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc)
    # 当日两条对话（窗口外一条不计数）
    m1 = await chat_repository.create(db, "u1", "user", "今天工作很累")
    m1.created_at = datetime(2026, 8, 4, 11, 0, tzinfo=timezone.utc)
    m2 = await chat_repository.create(db, "u1", "assistant", "我在听")
    m2.created_at = datetime(2026, 8, 4, 11, 1, tzinfo=timezone.utc)
    m3 = await chat_repository.create(db, "u1", "user", "三天前的消息")
    m3.created_at = datetime(2026, 8, 1, 11, 0, tzinfo=timezone.utc)
    # 当日达成徽章
    badge = await milestone_repository.create(db, "u1", "streak_7")
    badge.achieved_at = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)
    await db.flush()

    summary = await growth_service.get_day_summary(db, "u1", "2026-08-04", "zh")
    assert summary["has_data"] is True
    assert summary["mood"]["emoji"] == "😊"
    assert summary["mood"]["value"] == 5
    assert summary["color"] == "#E8F5E9"
    assert summary["chat_count"] == 2
    assert [r["record_type"] for r in summary["records"]] == ["daily_insight"]
    assert summary["records"][0]["tags"] == ["工作"]
    assert summary["milestones"] == [
        {"date": "2026-08-04", "milestone_type": "streak_7", "name": "一周坚持"}
    ]


async def test_get_day_summary_empty_and_invalid_date(db, make_user):
    """无数据日 has_data=False；非法日期 → 40001。"""
    await make_user("u1", timezone="UTC")
    summary = await growth_service.get_day_summary(db, "u1", "2026-08-01", "en")
    assert summary["has_data"] is False
    assert summary["mood"] is None
    assert summary["chat_count"] == 0

    with pytest.raises(AppException) as exc:
        await growth_service.get_day_summary(db, "u1", "not-a-date")
    assert exc.value.code == 40001


async def test_timeline_includes_milestone_markers(db, make_user, time_travel):
    """热力图附带里程碑标注（P0-F07 §3：本地日历日 + 本地化名称）。"""
    from backend.repositories import milestone_repository

    time_travel.set(datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc))
    await make_user("u1", timezone="UTC")
    badge = await milestone_repository.create(db, "u1", "streak_7")
    badge.achieved_at = datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc)
    await db.flush()

    timeline = await growth_service.get_timeline(db, "u1", days=30, language="en")
    assert len(timeline["days"]) == 30
    assert timeline["milestones"] == [
        {"date": "2026-08-03", "milestone_type": "streak_7", "name": "7-Day Streak"}
    ]


async def test_relationship_timeline_stats_and_event_order(db, make_user, time_travel):
    """关系时间线（P0-F07 §7）：认识天数/聊天数/事件计数 + 事件按时间倒序。"""
    from backend.repositories import chat_repository, milestone_repository

    time_travel.set(datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc))
    await make_user(
        "u1",
        timezone="UTC",
        active_days=5,
        created_at=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
    )

    badge = await milestone_repository.create(db, "u1", "streak_7")
    badge.achieved_at = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)
    record = await growth_repository.create(db, "u1", "daily_insight", "洞察内容")
    record.created_at = datetime(2026, 8, 3, 23, 0, tzinfo=timezone.utc)
    msg = await chat_repository.create(db, "u1", "user", "你好")
    msg.created_at = datetime(2026, 8, 2, 10, 0, tzinfo=timezone.utc)
    await db.flush()

    data = await growth_service.get_relationship_timeline(db, "u1", "zh")
    assert data["days_known"] == 5  # 8/1 → 8/5
    assert data["chat_count"] == 1
    assert data["event_count"] == 2
    # 倒序：徽章（8/4）在前，洞察（8/3）在后
    assert [e["type"] for e in data["events"]] == ["milestone", "record"]
    assert data["events"][0]["title"] == "一周坚持"
    assert data["events"][1]["title"] == "每日洞察"
    assert data["level"]["level"] == 1

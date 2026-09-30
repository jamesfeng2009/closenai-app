"""§5.17 Growth Curriculum（C2）：AI 每周预生成 7 天主题旅程。

覆盖：周日 21:00 本地窗口 cron 触发 / 素材收集（近 30 天 Memory + Pattern）/
Rule 优先 + LLM 周规划（一次调用出 7 卡，ref 映射 memory_refs 硬不变量）/
LLM 失败与违规卡回退 Rule / 幂等 / 首页周视图（revealed + 未读）/ 点击已读 /
跨周 day_no 编码不冲突 / 不进报告页红点（生成即 sent+acknowledged）。
"""
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.models.journey import JOURNEY_EVENT_KINDS, JourneyEvent
from backend.repositories import (
    growth_repository,
    journey_repository,
    memory_repository,
)
from backend.services import curriculum_service as cs
from backend.services.curriculum_service import curriculum_service
from backend.services.cron_service import cron_service

# 固定未来日期冻结（C3/test_commitment 同口径）：curriculum 链路查询全部走
# scheduled_at / day_no（显式设置），无 created_at 窗口依赖；记忆素材行手动挪进
# 素材窗口。2026-11-01 是周日，21:30 命中周日 21 点桶。
FROZEN_NOW = datetime(2026, 11, 1, 21, 30, tzinfo=timezone.utc)
TODAY = date(2026, 11, 1)          # 周日
NEXT_MONDAY = date(2026, 11, 2)
THIS_MONDAY = date(2026, 10, 26)

USER_KW = dict(timezone="UTC", language="zh", ai_consent=True)

LLM_WEEK_PLAN = json.dumps(
    [
        {"ref": 0, "title": "你和跑步的约定", "assertion": "你说过想开始跑步，我记到现在",
         "question": "这周它有进展吗？"},
        {"ref": None, "title": "平静的一周", "assertion": "这一周你没有太多起伏", "question": "什么让你觉得稳？"},
        {"ref": 1, "title": "睡不着的周日", "assertion": "你连续几个周日提到睡不着", "question": "今晚打算几点放下手机？"},
        {"ref": None, "title": "想聊聊工作", "assertion": "工作最近占了你的很多心神", "question": "哪一天最难熬？"},
        {"ref": 0, "title": "回到那句话", "assertion": "你上周的那句话我还在想", "question": "现在读它感觉变了？"},
        {"ref": None, "title": "给自己的空间", "assertion": "你很少为自己留时间", "question": "这周能给一天吗？"},
        {"ref": 1, "title": "一周收尾", "assertion": "这一周的画面里你都在场", "question": "想先聊哪一天？"},
    ],
    ensure_ascii=False,
)


def _day_no(day: date) -> int:
    """与实现同构的 day_no 编码（测试钉死口径：ISO 周序 * 7 + 周内序）。"""
    iso = day.isocalendar()
    return (iso[0] * 53 + iso[1]) * 7 + iso[2] - 1


async def _seed_memory(db, user_id: str, content: str, created: datetime):
    memory = await memory_repository.create(
        db, user_id, content=content, category="preference", source="self", conviction=0.6
    )
    memory.created_at = created
    await db.flush()
    return memory


async def _seed_pattern(db, user_id: str, content: str):
    record = await growth_repository.create(db, user_id, "pattern_discovery", content)
    record.created_at = FROZEN_NOW - timedelta(days=5)
    await db.flush()
    return record


async def _week_events(db, user_id: str, monday: date):
    events = await journey_repository.list_for_user(db, user_id, statuses=("pending", "sent", "seen", "skipped"), limit=200)
    days = {monday + timedelta(days=i): None for i in range(7)}
    for e in events:
        if e.event_kind != cs.KIND:
            continue
        for d in days:
            if e.day_no == _day_no(d):
                days[d] = e
    return events, days


def _utc(value: datetime) -> datetime:
    """SQLite 读回 timestamptz 丢 tzinfo → 归一化为 aware UTC 再比较。"""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ── 1. 周规划生成 ──


async def test_generate_weekly_creates_seven_cards(db, make_user, time_travel, mock_llm):
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)
    m1 = await _seed_memory(db, "u1", "想开始每天跑步", FROZEN_NOW - timedelta(days=10))
    m2 = await _seed_memory(db, "u1", "周日晚上总是睡不着", FROZEN_NOW - timedelta(days=7))
    mock_llm.state["complete_text"] = LLM_WEEK_PLAN

    result = await curriculum_service.generate_weekly(db, await _require(db, "u1"))

    assert result["status"] == "generated"
    cards = result["cards"]
    assert len(cards) == 7
    # LLM 周规划被采用：一次调用产出全部卡
    assert any(c["title"] == "你和跑步的约定" for c in cards)
    assert not any("你与" in (c["title"] or "") and "约定" in c["title"] and "跑步" not in c["title"] for c in cards)

    events, days = await _week_events(db, "u1", NEXT_MONDAY)
    assert len(events) == 7
    assert all(days[NEXT_MONDAY + timedelta(days=i)] is not None for i in range(7))
    # 生成即投递到首页池：sent + acknowledged（不进报告页红点、不被剧本 cron 扫描）
    assert all(e.status == "seen" and e.acknowledged for e in events)
    # scheduled_at = 下周一..周日各自本地起点
    assert _utc(days[NEXT_MONDAY].scheduled_at) == datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
    assert _utc(days[NEXT_MONDAY + timedelta(days=6)].scheduled_at) == datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)
    # memory_refs 硬不变量：ref 编号映射回该用户真实记忆（素材按 created_at 倒序，ref 0 = 最新一条）
    refs = json.loads(days[NEXT_MONDAY].memory_refs)
    assert [r["memory_id"] for r in refs] == [m2.id]
    empty_refs = json.loads(days[NEXT_MONDAY + timedelta(days=1)].memory_refs or "[]")
    assert empty_refs == []


async def test_generate_weekly_idempotent(db, make_user, time_travel, mock_llm):
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)
    await _seed_memory(db, "u1", "想开始每天跑步", FROZEN_NOW - timedelta(days=10))
    user = await _require(db, "u1")

    first = await curriculum_service.generate_weekly(db, user)
    mock_llm.state["complete_text"] = "GARBAGE"
    second = await curriculum_service.generate_weekly(db, user)

    assert first["status"] == "generated"
    assert second["status"] == "skipped"
    # 整周只有 7 条，重跑零 LLM 消耗（周规划不重跑）
    planned = [c for c in mock_llm.calls["complete"] if "主题卡" in c["prompt"]]
    assert len(planned) == 1


async def test_generate_weekly_without_material_skips(db, make_user, time_travel, mock_llm):
    """零素材不生成（内容全部来自用户数据，PRD §5.17 差异化根基）。"""
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)

    result = await curriculum_service.generate_weekly(db, await _require(db, "u1"))

    assert result["status"] == "skipped"
    assert mock_llm.calls["complete"] == []
    events, _ = await _week_events(db, "u1", NEXT_MONDAY)
    assert events == []


# ── 2. Rule 优先 / LLM 降级 ──


async def test_llm_invalid_json_falls_back_to_rule(db, make_user, time_travel, mock_llm):
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)
    m1 = await _seed_memory(db, "u1", "想开始每天跑步", FROZEN_NOW - timedelta(days=10))
    mock_llm.state["complete_text"] = "not a json"

    result = await curriculum_service.generate_weekly(db, await _require(db, "u1"))

    assert result["status"] == "generated"
    cards = result["cards"]
    assert len(cards) == 7
    # Rule 卡基于真实素材：断言引用原话 + refs 归属
    assert all(c["assertion"] and c["question"] for c in cards)
    assert any(m1.content[:10] in (c["assertion"] + c["title"]) for c in cards)
    refs = [r for c in cards for r in c["memory_refs"]]
    assert all(r["memory_id"] == m1.id for r in refs)


async def test_llm_card_failing_gate_falls_back_to_rule(db, make_user, time_travel, mock_llm):
    """LLM 单卡违禁（占星/医疗化词）→ 该卡回退 Rule，其余保留。"""
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)
    await _seed_memory(db, "u1", "想开始每天跑步", FROZEN_NOW - timedelta(days=10))
    bad_plan = json.loads(LLM_WEEK_PLAN)
    bad_plan[1]["assertion"] = "你的运势本周注定波动，一切都会好起来"
    mock_llm.state["complete_text"] = json.dumps(bad_plan, ensure_ascii=False)

    result = await curriculum_service.generate_weekly(db, await _require(db, "u1"))

    cards = result["cards"]
    assert len(cards) == 7
    assert cards[0]["title"] == "你和跑步的约定"  # 合规卡保留
    assert "运势" not in cards[1]["assertion"] and "注定" not in cards[1]["assertion"]
    assert cards[1]["assertion"]  # 已回退 Rule 卡


# ── 3. 周视图 + 点击已读 ──


async def test_get_week_reveals_and_unread(db, make_user, time_travel):
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)
    await _seed_memory(db, "u1", "想开始每天跑步", FROZEN_NOW - timedelta(days=10))
    await curriculum_service.generate_weekly(db, await _require(db, "u1"))
    # 下周卡对"本周视图"不可见；把 scheduled_at 挪进本周模拟"当周已生成"
    _, days = await _week_events(db, "u1", NEXT_MONDAY)
    for i, e in enumerate(days.values()):
        e.scheduled_at = datetime.combine(THIS_MONDAY + timedelta(days=i), datetime.min.time(), tzinfo=timezone.utc)
    await db.flush()

    week = await curriculum_service.get_week(db, await _require(db, "u1"))

    assert week["week_start"] == THIS_MONDAY.isoformat()
    assert len(week["days"]) == 7
    assert all(d["revealed"] for d in week["days"])  # 冻结在周日，整周已揭晓
    assert week["unread_count"] == 7
    assert week["days"][0]["title"]

    await curriculum_service.mark_read(db, await _require(db, "u1"), week["days"][0]["id"])
    week = await curriculum_service.get_week(db, await _require(db, "u1"))
    assert week["days"][0]["read"] is True
    assert week["unread_count"] == 6


async def test_get_week_empty(db, make_user, time_travel):
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)

    week = await curriculum_service.get_week(db, await _require(db, "u1"))

    assert week["days"] == []
    assert week["unread_count"] == 0


async def test_mark_read_ownership(db, make_user, time_travel):
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)
    await make_user("u2", **USER_KW)
    await _seed_memory(db, "u1", "想开始每天跑步", FROZEN_NOW - timedelta(days=10))
    await curriculum_service.generate_weekly(db, await _require(db, "u1"))
    _, days = await _week_events(db, "u1", NEXT_MONDAY)
    foreign_id = days[NEXT_MONDAY].id

    from backend.core.errors import AppException
    with pytest.raises(AppException):
        await curriculum_service.mark_read(db, await _require(db, "u2"), foreign_id)

    # 自己点击：payload.read_at 落库（幂等）
    first = await curriculum_service.mark_read(db, await _require(db, "u1"), foreign_id)
    again = await curriculum_service.mark_read(db, await _require(db, "u1"), foreign_id)
    assert first["read"] is True and again["read"] is True


# ── 4. cron：周日 21:00 本地窗口 ──


async def test_cron_generates_on_sunday_evening(db, make_user, time_travel):
    time_travel.set(FROZEN_NOW)  # 周日 21:30
    await make_user("u1", **USER_KW)
    await make_user("u2", **{**USER_KW, "timezone": "Asia/Shanghai"})  # 本地周一 05:30 → 不命中
    await _seed_memory(db, "u1", "想开始每天跑步", FROZEN_NOW - timedelta(days=10))

    result = await cron_service.dispatch_curriculum_generation(db)

    assert result["generated"] == 1
    assert result["skipped"] >= 1


async def test_cron_skips_non_sunday_and_other_hours(db, make_user, time_travel):
    await make_user("u1", **USER_KW)
    await _seed_memory(db, "u1", "想开始每天跑步", FROZEN_NOW - timedelta(days=10))

    time_travel.set(datetime(2026, 11, 2, 21, 30, tzinfo=timezone.utc))  # 周一 21:30
    result = await cron_service.dispatch_curriculum_generation(db)
    assert result["generated"] == 0

    time_travel.set(datetime(2026, 11, 1, 20, 30, tzinfo=timezone.utc))  # 周日 20:30
    result = await cron_service.dispatch_curriculum_generation(db)
    assert result["generated"] == 0


async def test_cron_user_without_material_counts_skipped(db, make_user, time_travel):
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)  # 零素材

    result = await cron_service.dispatch_curriculum_generation(db)

    assert result["generated"] == 0
    assert result["skipped"] == 1


# ── 5. day_no 编码：跨周不冲突（幂等键根基）──


def test_day_no_encoding_weeks_apart():
    monday_a = date(2026, 11, 2)
    monday_b = date(2026, 11, 9)
    assert _day_no(monday_a) + 6 == _day_no(monday_a + timedelta(days=6))  # 同周内连续
    assert _day_no(monday_b) - _day_no(monday_a) == 7  # 相邻周同星期差恰 7
    assert _day_no(date(2027, 1, 4)) > _day_no(date(2026, 12, 28))  # 跨年递增


def test_curriculum_kind_registered():
    assert cs.KIND in JOURNEY_EVENT_KINDS


async def _require(db, user_id: str):
    from backend.repositories import user_repository
    return await user_repository.require_user(db, user_id)

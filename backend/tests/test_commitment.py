"""§5.18 承诺回访（C3 收尾）。

覆盖：Rule 抽取（零 LLM）/ 回访预写与调度（习惯 +7 天、事件次日，本地推送窗口）/
投递渲染（四层公式 + memory_refs 硬不变量 + 发送即已读）/ 频控（每用户每周 ≤2、
同承诺 ≤2）/ 负向退出（muted → 沉默陪伴）。
合规红线（PRD §5.18）：不打勾、不完成率、不任务列表——用 schema 与词表断言钉死。
"""
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.models.growth import GrowthRecord
from backend.repositories import (
    chat_repository,
    journey_repository,
    memory_repository,
)
from backend.services.commitment_service import commitment_service
from backend.services.journey_service import journey_service, load_template
from backend.services.memory_service import memory_service

# 冻结时间取未来日期（晚于真实墙钟）：chat 行 created_at 走 SQLite CURRENT_TIMESTAMP
# 真实时间，若冻结在过去会被 count_today 误判"今日已有主动消息"（test_journey 同口径）
FROZEN_NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)  # 周四

USER_KW = dict(
    timezone="UTC", language="zh", ai_consent=True,
    created_at=FROZEN_NOW - timedelta(days=1),  # 沉默天数=1，不触发降频
)


def _as_utc(value: datetime) -> datetime:
    """SQLite DATETIME 丢弃 tzinfo（存的是 UTC wall-clock），读取侧统一补回。"""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _window(day: date, hour: int = 9, minute: int = 0) -> datetime:
    """UTC 用户的本地推送窗口时刻。"""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


async def _pending_commitments(db, user_id):
    return list(
        await journey_repository.pending_events_for_kind(
            db, user_id, "commitment_follow_up"
        )
    )


async def _touch(db, user_id: str, when: datetime, content: str = "今天随便写了两句"):
    """落一条用户消息并挪 created_at（重置沉默降频计数，test_journey 同口径）。

    conftest 会话 autoflush=False：改完 created_at 必须显式 flush，后续查询才可见。
    """
    message = await chat_repository.create(db, user_id, "user", content)
    message.created_at = when
    await db.flush()


# ── 1. Rule 抽取：只记录，不当场确认，不打断对话 ──


async def test_capture_zh_habit_intent(db, make_user, time_travel):
    """习惯类意图 → commitment 记忆（kind=habit）+ 首条回访预写在捕获后 7 天。"""
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)

    memory_id = await commitment_service.on_user_message(db, "u1", "我想开始跑步，每天早上跑三公里")

    assert memory_id
    row = await memory_repository.get(db, memory_id, "u1")
    labels = json.loads(row.labels)
    assert labels["labels"] == ["commitment"]
    commitment = labels["commitment"]
    assert commitment["kind"] == "habit"
    assert commitment["follow_up_count"] == 0
    assert commitment["muted"] is False
    assert "我想开始跑步" in row.content

    # 只记录不当场确认：没有 AI 回复产生
    assert await chat_repository.get_history(db, "u1") == []

    events = await _pending_commitments(db, "u1")
    assert len(events) == 1
    assert events[0].day_no == 7
    assert _as_utc(events[0].scheduled_at) == _window(date(2026, 10, 15))
    payload = json.loads(events[0].payload)
    assert payload["memory_id"] == memory_id
    assert payload["captured_day"] == "2026-10-08"


async def test_capture_event_intent_schedules_next_day(db, make_user, time_travel):
    """带具体时间词的意图 → 事件类（kind=event），次日回访。"""
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)

    memory_id = await commitment_service.on_user_message(db, "u1", "下周三我要去面试，有点紧张")

    assert memory_id
    row = await memory_repository.get(db, memory_id, "u1")
    assert json.loads(row.labels)["commitment"]["kind"] == "event"

    events = await _pending_commitments(db, "u1")
    assert len(events) == 1
    assert events[0].day_no == 1
    assert _as_utc(events[0].scheduled_at) == _window(date(2026, 10, 9))


async def test_plain_message_captures_nothing(db, make_user, time_travel):
    """无意图表述 → 不捕获、不落记忆、不预写回访。"""
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)

    assert await commitment_service.on_user_message(db, "u1", "今天天气不错") is None

    assert await memory_service.list_memories(db, "u1") == []
    assert await _pending_commitments(db, "u1") == []


async def test_capture_skips_crisis_content(db, make_user, time_travel):
    """危机内容不捕获为承诺（危机走 §5.16 转介，不做回访素材）。"""
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)

    memory_id = await commitment_service.on_user_message(db, "u1", "我不想活了，我想开始新的生活")

    assert memory_id is None
    assert await _pending_commitments(db, "u1") == []


async def test_repeated_expression_keeps_single_follow_up(
    db, make_user, time_travel, stub_semantic_search
):
    """重复表达同一承诺 → 命中既有记忆（dedup 加固），不重复预写回访。"""
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)

    first_id = await commitment_service.on_user_message(db, "u1", "我想开始跑步")
    stub_semantic_search["rows"] = [
        {
            "id": first_id,
            "user_id": "u1",
            "content": "我想开始跑步",
            "category": "goal",
            "status": "current",
            "source": "self",
            "conviction_score": 0.55,
            "created_at": FROZEN_NOW,
            "superseded_at": None,
            "transition_reason": None,
            "similarity": 0.9,
        }
    ]
    again_id = await commitment_service.on_user_message(db, "u1", "我打算开始跑步了")

    assert again_id == first_id
    assert len(await _pending_commitments(db, "u1")) == 1


# ── 2. 投递渲染：四层公式 + 引用依据不变量 + 发送即已读 ──


async def test_deliver_renders_follow_up_with_evidence(db, make_user, time_travel):
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)
    memory_id = await commitment_service.on_user_message(db, "u1", "我想开始跑步，每天早上跑")
    (event,) = await _pending_commitments(db, "u1")
    event.scheduled_at = FROZEN_NOW - timedelta(minutes=1)

    assert await journey_service.deliver(db, event.id) == "sent"

    await db.refresh(event)
    history = await chat_repository.get_history(db, "u1")
    assert len(history) == 1
    msg = history[0]
    assert msg.message_type == "proactive"
    assert msg.ai_generated is False
    assert "我想开始跑步" in msg.content  # ② 承诺回放
    assert "10/8" in msg.content  # 捕获日期
    # memory_refs 硬不变量：正文引用了原话 ⟺ refs 非空且归属该用户
    refs = json.loads(event.memory_refs)
    assert [r["memory_id"] for r in refs] == [memory_id]
    payload = json.loads(event.payload)
    assert payload["memory_id"] == memory_id
    # 消费面在对话流（PRD §5.18 研发标注）：发送即已读，不进报告页红点
    assert event.status == "seen"
    assert event.acknowledged is True
    assert await journey_repository.count_unseen(db, "u1") == 0


# ── 3. 频控与退出 ──


async def test_negative_reply_mutes_and_stops_follow_ups(db, make_user, time_travel):
    """负向回复（"没做，别问了"）→ 该承诺 muted，pending 回访到点被 skip（沉默陪伴）。"""
    time_travel.set(FROZEN_NOW)
    user = await make_user("u1", **USER_KW)
    memory_id = await commitment_service.on_user_message(db, "u1", "我想开始跑步")
    (event,) = await _pending_commitments(db, "u1")
    event.scheduled_at = FROZEN_NOW - timedelta(minutes=1)
    assert await journey_service.deliver(db, event.id) == "sent"
    # 首次投递成功后预写了第二条（count=1 < 2）
    (second,) = await _pending_commitments(db, "u1")
    assert second.id != event.id

    assert await commitment_service.handle_negative_reply(db, user, "没做，别问了") is True

    row = await memory_repository.get(db, memory_id, "u1")
    assert json.loads(row.labels)["commitment"]["muted"] is True

    time_travel.set(datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc))
    await _touch(db, "u1", datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc))
    second.scheduled_at = FROZEN_NOW - timedelta(minutes=1)
    assert await journey_service.deliver(db, second.id) == "skipped"
    history = await chat_repository.get_history(db, "u1")
    assert sum(1 for m in history if m.role == "assistant") == 1  # 只有首条回访


async def test_negative_reply_without_history_is_noop(db, make_user):
    """没有被回访过的承诺时，负向话术不误伤任何记忆。"""
    user = await make_user("u1", **USER_KW)
    assert await commitment_service.handle_negative_reply(db, user, "没做，别问了") is False


async def test_weekly_quota_defers_third_follow_up(db, make_user, time_travel, mock_llm):
    """每用户每周 ≤2 条：第三条顺延一周（pending 保留，永不删行）。"""
    await make_user("u1", **USER_KW)
    vec = 0

    async def capture(text: str):
        nonlocal vec
        mock_llm.state["embed_vectors"] = [[1.0 if i == vec else 0.0 for i in range(512)]]
        vec += 1
        return await commitment_service.on_user_message(db, "u1", text)

    await capture("我想开始跑步")  # habit
    await capture("下周三我要去面试")  # event
    await capture("我想戒掉奶茶")  # habit
    events = await _pending_commitments(db, "u1")
    assert len(events) == 3

    # 周四发第一条
    time_travel.set(datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc))
    e1 = next(e for e in events if "跑步" in (e.payload or ""))
    e1.scheduled_at = datetime(2026, 10, 8, 11, 59, tzinfo=timezone.utc)
    assert await journey_service.deliver(db, e1.id) == "sent"

    # 周五发第二条（不同本地日，避开"每日一条"门）
    time_travel.set(datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc))
    await _touch(db, "u1", datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc))
    e2 = next(e for e in events if "面试" in (e.payload or ""))
    e2.scheduled_at = datetime(2026, 10, 9, 11, 59, tzinfo=timezone.utc)
    assert await journey_service.deliver(db, e2.id) == "sent"

    # 周六第三条 → 周频控命中（本周已发 2 条）→ 顺延一周
    time_travel.set(datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc))
    await _touch(db, "u1", datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc))
    e3 = next(e for e in events if "奶茶" in (e.payload or ""))
    e3.scheduled_at = datetime(2026, 10, 10, 11, 59, tzinfo=timezone.utc)
    assert await journey_service.deliver(db, e3.id) == "deferred"
    await db.refresh(e3)
    assert e3.status == "pending"
    assert _as_utc(e3.scheduled_at) == datetime(2026, 10, 17, 11, 59, tzinfo=timezone.utc)


async def test_same_commitment_max_two_follow_ups(db, make_user, time_travel):
    """同一承诺最多回访 2 次：第二次投递后不再预写第三条。"""
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)
    memory_id = await commitment_service.on_user_message(db, "u1", "我想开始跑步")
    (first,) = await _pending_commitments(db, "u1")

    time_travel.set(datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc))
    await _touch(db, "u1", datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc))
    first.scheduled_at = datetime(2026, 10, 15, 11, 59, tzinfo=timezone.utc)
    assert await journey_service.deliver(db, first.id) == "sent"
    (second,) = await _pending_commitments(db, "u1")
    assert second.day_no == 14
    assert _as_utc(second.scheduled_at) == _window(date(2026, 10, 22))

    time_travel.set(datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc))
    await _touch(db, "u1", datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc))
    second.scheduled_at = datetime(2026, 10, 22, 11, 59, tzinfo=timezone.utc)
    assert await journey_service.deliver(db, second.id) == "sent"

    row = await memory_repository.get(db, memory_id, "u1")
    commitment = json.loads(row.labels)["commitment"]
    assert commitment["follow_up_count"] == 2
    assert await _pending_commitments(db, "u1") == []


# ── 4. 合规红线：不做打卡（schema 断言）──


@pytest.mark.parametrize("locale", ["zh", "en"])
def test_commitment_templates_avoid_tracker_language(locale):
    """回访文案禁打卡/完成率/清单类措辞（PRD §5.18 合规边界 ②）。"""
    banned = (
        "打卡", "完成率", "红叉", "待办", "清单", "任务",
        "check off", "check-in", "checkin", "to-do", "todo",
        "completion", "streak", "checkbox",
    )
    tpl = load_template(locale)["commitment_follow_up"]
    assert set(tpl) >= {"habit", "event"}
    for key in ("habit", "event"):
        assert not any(word in tpl[key].lower() for word in banned)


async def test_no_checklist_artifacts_anywhere(db, make_user, time_travel):
    """不打勾、不完成率、不任务列表：承诺状态只随记忆行，不落成长记录/打卡结构。"""
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)
    memory_id = await commitment_service.on_user_message(db, "u1", "我想开始跑步")
    (event,) = await _pending_commitments(db, "u1")
    event.scheduled_at = FROZEN_NOW - timedelta(minutes=1)
    assert await journey_service.deliver(db, event.id) == "sent"

    # 不写成长记录（承诺不是任务条目）
    n_growth = await db.scalar(select(func.count()).select_from(GrowthRecord))
    assert n_growth == 0

    # labels JSON 键集精确：只有标签与回访调度状态，无 done/完成类字段
    row = await memory_repository.get(db, memory_id, "u1")
    labels = json.loads(row.labels)
    assert set(labels) == {"labels", "commitment"}
    assert set(labels["commitment"]) == {"kind", "follow_up_count", "muted"}

    # 投递 payload 无打卡类键
    payload = json.loads(event.payload)
    forbidden = ("completion", "done", "check", "task")
    assert not any(word in key.lower() for key in payload for word in forbidden)


# ── 5. /memories 出参透传 ──


async def test_memories_response_exposes_labels(db, make_user, time_travel):
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)
    await commitment_service.on_user_message(db, "u1", "我想开始跑步")

    rows = await memory_service.list_memories(db, "u1")

    assert len(rows) == 1
    assert rows[0].labels == ["commitment"]


async def test_memories_response_labels_none_for_plain_memory(db, make_user):
    """普通记忆 labels 为 None（不空数组），前端契约清晰。"""
    await make_user("u1", **USER_KW)
    await memory_service.create_memory(db, "u1", content="喜欢深夜散步", category="preference")

    rows = await memory_service.list_memories(db, "u1")

    assert len(rows) == 1
    assert rows[0].labels is None

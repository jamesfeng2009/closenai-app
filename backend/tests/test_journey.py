"""
B2 · M2 第一周剧本（PRD §5.19.3 / 架构方案 §二 M2）。

覆盖四组验收口径：
1. **预写**：onboarding 完成事务内写满 D0–D7（幂等、过期日置 skipped 不追溯补发）。
2. **引用依据不变量**：memory_refs 非空 ⟺ 正文复述了原话；原话只在过闸后回填，
   占位符绝不外泄；跨用户记忆进不了引用。
3. **频控**：同日只留一条触达（剧本 / LLM 主动消息互斥、危机停推、沉默降频），
   全部走顺延——永不删行。
4. **措辞闸**：模板库逐条过 filter_template，命中即测试失败。
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.core.auth import get_current_user
from backend.core.middleware import register_exception_handlers
from backend.database import get_db
from backend.repositories import (
    chat_repository,
    journey_repository,
    memory_repository,
    mood_repository,
    onboarding_repository,
    safety_repository,
)
from backend.routers import chat as chat_router
from backend.routers import journey as journey_router
from backend.services.cron_service import cron_service
from backend.services.first_read_service import first_read_service
from backend.services.journey_service import (
    SCRIPT_EVENTS,
    _quote_token,
    journey_service,
    load_template,
)
from backend.services.push import push_service
from backend.services.safety_service import safety_service

TZ = "UTC"
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
_CN = timezone(timedelta(hours=8))


def _as_utc(value: datetime) -> datetime:
    """SQLite DATETIME 丢弃 tzinfo（存的是 UTC wall-clock），读取侧统一补回。"""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
ZH_ANSWERS = [
    {"q": "conflict", "option": "speak_now"},
    {"q": "uncertainty", "option": "list_out"},
    {"q": "night", "option": "sharpest"},
]


async def _memory(db, user_id, content, days_ago=1.0, hours=0):
    """写一条记忆并把 created_at 挪到 days_ago 前（UTC wall-clock，见 conftest 说明）。"""
    record = await memory_repository.create(
        db, user_id, content, "factual", "self", 0.8
    )
    record.created_at = NOW - timedelta(days=days_ago, hours=hours)
    await db.flush()
    return record


async def _checkin(db, user_id, emoji, days_ago=1.0):
    record = await mood_repository.create(
        db, user_id, emoji, "疲惫", "解读", checkin_date=(NOW - timedelta(days=days_ago)).date()
    )
    record.created_at = NOW - timedelta(days=days_ago)
    await db.flush()
    return record


async def _user_message(db, user_id, content, days_ago=0.0, hour=None):
    when = NOW - timedelta(days=days_ago)
    if hour is not None:
        when = when.replace(hour=hour)
    message = await chat_repository.create(db, user_id, "user", content)
    message.created_at = when
    await db.flush()
    return message


async def _event(db, user_id, kind):
    day_no = dict(SCRIPT_EVENTS)[kind]
    return await journey_repository.get_by_kind(db, user_id, kind, day_no)


async def _prefilled(db, make_user, user_id="u1", **overrides):
    """走完 onboarding（含剧本预写），返回用户。"""
    overrides.setdefault("ai_consent", True)
    overrides.setdefault("language", "zh")
    overrides.setdefault("timezone", TZ)
    overrides.setdefault("push_time", "09:00")
    user = await make_user(user_id, **overrides)
    await first_read_service.submit(db, user_id, "INTJ", ZH_ANSWERS, "zh")
    return user


# ── 1. 预写 ──


async def test_submit_prefills_the_whole_week(db, make_user, time_travel):
    """初读卡提交 = onboarding 完成，同一事务内写满 D0–D7 六个事件。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)

    events = await journey_repository.list_for_user(db, "u1", limit=50)
    assert sorted(e.event_kind for e in events) == sorted(k for k, _ in SCRIPT_EVENTS)
    assert {e.day_no for e in events} == {0, 1, 2, 3, 5, 7}
    assert {e.status for e in events} == {"pending"}


async def test_prefill_is_idempotent(db, make_user, time_travel):
    """重放 onboarding 不产生第二条同类事件（仓库层按 user+kind+day_no 去重）。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    await first_read_service.submit(db, "u1", "INTJ", ZH_ANSWERS, "zh")

    events = await journey_repository.list_for_user(db, "u1", limit=50)
    assert len(events) == len(SCRIPT_EVENTS)
    assert len({e.id for e in events}) == len(SCRIPT_EVENTS)


async def test_stale_script_days_are_skipped_not_backfilled(db, make_user, time_travel):
    """补写老用户：基准日在 10 天前 → 已过剧本日置 skipped，一条不追溯补发。"""
    time_travel.set(NOW)
    await make_user("u-old", ai_consent=True, language="zh", timezone=TZ,
                           push_time="09:00", created_at=NOW - timedelta(days=10))
    await first_read_service.submit(db, "u-old", None, ZH_ANSWERS, "zh")
    # 生成日挪到 10 天前，再走一次补写入口（模拟 B2 上线前完成引导的老用户）
    profile = await onboarding_repository.get(db, "u-old")
    profile.created_at = NOW - timedelta(days=10)
    await db.flush()

    await journey_service.ensure_prefilled(db, "u-old")
    events = await journey_repository.list_for_user(db, "u-old", limit=50)

    by_day = {e.day_no: e for e in events}
    assert set(by_day) == {0, 1, 2, 3, 5, 7}
    assert [by_day[d].status for d in (0, 1, 2, 3, 5, 7)] == ["skipped"] * 6
    # 行没被删掉，审计仍在
    assert len(events) == 6


async def test_push_moment_uses_user_local_window(db, make_user, time_travel):
    """计划时刻落在用户本地 push_time，不用服务器时钟的"现在"。"""
    time_travel.set(NOW)
    user = await make_user("u-cn", language="zh", timezone="Asia/Shanghai", push_time="20:30")
    await journey_service.prefill(db, user)
    event = await _event(db, "u-cn", "day2_first_outreach")
    # SQLite DATETIME 丢弃 tzinfo（存 UTC wall-clock，见 conftest 说明）
    scheduled = _as_utc(event.scheduled_at)
    local = scheduled.astimezone(_CN)
    assert (local.hour, local.minute) == (20, 30)
    assert local.date() == NOW.astimezone(_CN).date() + timedelta(days=2)


# ── 2. 引用依据不变量 ──


async def test_day2_backfills_real_quote_after_the_gate(db, make_user, time_travel):
    """D2 复述昨日原话：正文含「原话」、占位符不残留、memory_refs 落库。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    await _memory(db, "u1", "我还是把那封邮件发出去了", days_ago=1)

    event = await _event(db, "u1", "day2_first_outreach")
    assert await journey_service.deliver(db, event.id) == "sent"

    refreshed = await journey_repository.get(db, event.id)
    refs = json.loads(refreshed.memory_refs)
    body = json.loads(refreshed.payload)["body"]
    assert len(refs) == 1
    assert refs[0]["excerpt"] == "我还是把那封邮件发出去了"
    assert f"「{refs[0]['excerpt']}」" in body
    assert _quote_token("q0") not in body and _quote_token("q1") not in body
    # 消息侧同样带上引用 ID（前端「记忆依据 ▾」的数据源）
    messages = await chat_repository.get_history(db, "u1")
    proactive = [m for m in messages if m.message_type == "proactive"]
    assert len(proactive) == 1
    assert proactive[0].memory_ids == refs[0]["memory_id"]
    assert proactive[0].ai_generated is False


async def test_statistics_events_carry_no_invented_quotes(db, make_user, time_travel):
    """纯统计观察（D5）不编造引用：memory_refs 恒为空——不变量的另一半。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    for day in (1, 2, 3):
        await _user_message(db, "u1", "今天开了个长会", days_ago=day, hour=22)

    event = await _event(db, "u1", "day5_stat")
    assert await journey_service.deliver(db, event.id) == "sent"

    refreshed = await journey_repository.get(db, event.id)
    assert json.loads(refreshed.memory_refs) == []
    body = json.loads(refreshed.payload)["body"]
    assert "「" not in body
    assert _quote_token("q0") not in body


async def test_day7_report_needs_two_real_quotes(db, make_user, time_travel):
    """D7 凑不出 2 条原话 → skipped（宁可不发报告，也不发没有依据的报告）。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    await _memory(db, "u1", "只有一句", days_ago=1)

    event = await _event(db, "u1", "day7_report")
    assert await journey_service.deliver(db, event.id) == "skipped"
    assert (await journey_repository.get(db, event.id)).status == "skipped"
    assert [m for m in await chat_repository.get_history(db, "u1") if m.message_type == "proactive"] == []


async def test_day7_report_quotes_two_lines_with_dates(db, make_user, time_travel):
    """够 2 条时：两条原话 + 引用日期都进引用依据，且都是该用户自己的记忆。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    m1 = await _memory(db, "u1", "我把辞职信写好了", days_ago=1)
    m2 = await _memory(db, "u1", "凌晨三点我还醒着", days_ago=2)

    event = await _event(db, "u1", "day7_report")
    assert await journey_service.deliver(db, event.id) == "sent"

    refreshed = await journey_repository.get(db, event.id)
    refs = json.loads(refreshed.memory_refs)
    body = json.loads(refreshed.payload)["body"]
    assert {r["memory_id"] for r in refs} == {m1.id, m2.id}
    for ref in refs:
        assert f"「{ref['excerpt']}」" in body
    assert _quote_token("q0") not in body and _quote_token("q1") not in body
    # 日期标签按用户本地日渲染
    assert "8/4" in body and "8/3" in body


async def test_crisis_quote_is_never_repeated_back(db, make_user, time_travel):
    """危机原话不被复述：宁可少一条引用。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    await _memory(db, "u1", "我不想活了", days_ago=1)
    first = await _memory(db, "u1", "周末要搬家", days_ago=2)
    second = await _memory(db, "u1", "妈那通我没接", days_ago=3)

    event = await _event(db, "u1", "day7_report")
    assert await journey_service.deliver(db, event.id) == "sent"
    refreshed = await journey_repository.get(db, event.id)
    refs = json.loads(refreshed.memory_refs)
    assert [r["memory_id"] for r in refs] == [first.id, second.id]
    assert all("不想活" not in r["excerpt"] for r in refs)
    assert "不想活" not in json.loads(refreshed.payload)["body"]


async def test_other_users_memory_cannot_be_cited(db, make_user, time_travel):
    """归属校验：别人名下的记忆不会成为本用户的引用依据。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    await make_user("u2", language="zh", timezone=TZ)
    foreign = await _memory(db, "u2", "这是别人的句子", days_ago=1)

    picked = await memory_repository.list_by_ids(db, "u1", [foreign.id])
    assert picked == []
    assert await memory_repository.list_by_ids(db, "u2", [foreign.id]) != []


# ── 3. 频控：只顺延、不删行 ──


async def test_today_proactive_message_defers_script(db, make_user, time_travel):
    """同日已有 LLM 主动消息 → 剧本顺延（status 仍 pending，scheduled_at 后移）。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    await chat_repository.create(
        db, "u1", "assistant", "AI 今天已经开过口了", message_type="proactive", ai_generated=True
    )
    event = await _event(db, "u1", "day2_first_outreach")
    before = _as_utc(event.scheduled_at)

    assert await journey_service.deliver(db, event.id) == "deferred"
    refreshed = await journey_repository.get(db, event.id)
    assert refreshed.status == "pending"
    assert _as_utc(refreshed.scheduled_at) > before
    assert len(await journey_repository.list_for_user(db, "u1", limit=50)) == len(SCRIPT_EVENTS)


async def test_script_yields_no_second_touch_same_day(db, make_user, time_travel):
    """今天已发过一条剧本 → 第二条顺延（§5.19.3 每日至多一条）。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    day2 = await _event(db, "u1", "day2_first_outreach")
    assert await journey_service.deliver(db, day2.id) == "sent"

    day3 = await _event(db, "u1", "day3_weak_assert")
    assert await journey_service.deliver(db, day3.id) == "deferred"
    assert (await journey_repository.get(db, day3.id)).status == "pending"


async def test_llm_proactive_message_steps_aside_for_script(db, make_user, time_travel):
    """反向互斥：有到期剧本待发时，LLM 主动消息本轮让路（也不产生消息）。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    # 把 D2 挪到已到期
    event = await _event(db, "u1", "day2_first_outreach")
    event.scheduled_at = NOW - timedelta(minutes=1)
    await db.flush()

    result = await cron_service.process_batch(db, ["u1"])
    assert result["success"] == 0
    assert await chat_repository.count_today(
        db, "u1", role="assistant", message_type="proactive",
        start=NOW.replace(hour=0, minute=0),
    ) == 0


async def test_crisis_hit_today_defers_script(db, make_user, time_travel):
    """危机转介当日停推：剧本顺延而不是照发。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    await safety_repository.record(db, "u1", "self_harm", "user_message", ["不想活"])

    event = await _event(db, "u1", "day2_first_outreach")
    assert await journey_service.deliver(db, event.id) == "deferred"


async def test_silent_user_gets_paced_not_dropped(db, make_user, time_travel):
    """连续 2 天无记录 → 降频为隔天（顺延），不是取消。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    await _user_message(db, "u1", "已经两天没写了", days_ago=3)

    event = await _event(db, "u1", "day2_first_outreach")
    assert await journey_service.deliver(db, event.id) == "deferred"
    assert (await journey_repository.get(db, event.id)).status == "pending"


async def test_no_ai_consent_skips_script(db, make_user, time_travel):
    """合规 5.1.2(i)：未同意 AI 数据共享 → 不做主动触达。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    await make_user("u1", ai_consent=False)
    event = await _event(db, "u1", "day2_first_outreach")

    assert await journey_service.deliver(db, event.id) == "skipped"
    assert (await journey_repository.get(db, event.id)).status == "skipped"


# ── 4. 数据不足 / 措辞闸 / 推送 ──


async def test_day1_without_checkin_sends_nothing(db, make_user, time_travel):
    """D1 无签到 → 不发泛泛问候（render 返回 None）。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    event = await _event(db, "u1", "day1_checkin_echo")
    assert await journey_service.deliver(db, event.id) == "skipped"


async def test_day1_echoes_checkin_label(db, make_user, time_travel):
    """D1 复述昨日签到情绪词，且不编造记忆引用。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    await _checkin(db, "u1", "😴", days_ago=1)

    event = await _event(db, "u1", "day1_checkin_echo")
    assert await journey_service.deliver(db, event.id) == "sent"
    payload = json.loads((await journey_repository.get(db, event.id)).payload)
    assert "疲惫" in payload["body"]
    assert json.loads((await journey_repository.get(db, event.id)).memory_refs) == []


async def test_only_day2_pushes(db, make_user, time_travel, monkeypatch):
    """§5.19.3 频控：唯一走推送的是 D2，其余只留 App 内红点。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    sent = []

    async def fake_send(db, user_id, title, body, data=None, **kwargs):
        sent.append({"user_id": user_id, "data": data})
        return {"sent": True}

    monkeypatch.setattr(push_service, "send_to_user", fake_send)
    await make_user("u1", push_enabled=True)
    await _prefilled(db, make_user, "u2", push_enabled=True)

    # D2 是唯一推送的一条
    day2 = await _event(db, "u1", "day2_first_outreach")
    assert await journey_service.deliver(db, day2.id) == "sent"
    assert len(sent) == 1
    assert sent[0]["user_id"] == "u1"
    assert sent[0]["data"] == {"type": "journey"}

    # 其余事件只进对话流 + 红点
    day0 = await _event(db, "u2", "day0_read")
    assert await journey_service.deliver(db, day0.id) == "sent"
    assert len(sent) == 1
    assert await journey_repository.count_unseen(db, "u2") == 1


@pytest.mark.parametrize("locale", ["zh", "en"])
def test_every_template_passes_the_wording_gate(locale):
    """模板由我方撰写，必须逐条过 §六-1 闸；命中即说明模板本身越界。"""

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, str):
            if path.startswith("._comment"):
                return
            sample = node.replace(_quote_token("q0"), "示例原话").replace(
                _quote_token("q1"), "另一句原话"
            )
            # 未填充的槽位用占位文本补齐，验证的是句式本身而非参数
            for slot in ("{days}", "{n}", "{k}", "{msg_n}", "{mood_n}", "{mem_n}", "{d0}", "{d1}"):
                sample = sample.replace(slot, "3")
            for slot in ("{mood}", "{window}", "{closing}"):
                sample = sample.replace(slot, "疲惫")
            assert safety_service.filter_template(sample, locale) == sample, path

    walk(load_template(locale if locale == "zh" else "en"))


# ── 5. API ──


@pytest.fixture
def app(db):
    app = FastAPI()
    app.include_router(journey_router.router, prefix="/api/v1", tags=["journey"])
    app.include_router(chat_router.router, prefix="/api/v1", tags=["chat"])
    register_exception_handlers(app)

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: "u1"
    return app


async def test_events_endpoint_lists_script_and_unseen(app, db, make_user, time_travel):
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    await _memory(db, "u1", "我把辞职信写好了", days_ago=1)
    event = await _event(db, "u1", "day2_first_outreach")
    await journey_service.deliver(db, event.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/journey/events")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["unseen_count"] == 1
    sent = [e for e in data["events"] if e["status"] == "sent"]
    assert len(sent) == 1
    assert sent[0]["event_kind"] == "day2_first_outreach"
    assert sent[0]["acknowledged"] is False
    assert len(sent[0]["memory_refs"]) == 1


async def test_acknowledge_clears_the_badge(app, db, make_user, time_travel):
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    event = await _event(db, "u1", "day2_first_outreach")
    await journey_service.deliver(db, event.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/journey/events/acknowledge", json={"event_id": event.id})
        after = await client.get("/api/v1/journey/events")

    assert resp.status_code == 200
    assert after.json()["data"]["unseen_count"] == 0


async def test_acknowledge_hides_other_users_events(app, db, make_user, time_travel):
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    await _prefilled(db, make_user, "u2")
    foreign = await _event(db, "u2", "day2_first_outreach")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/journey/events/acknowledge", json={"event_id": foreign.id})

    assert resp.status_code == 404
    assert resp.json()["code"] == 40402


async def test_chat_history_exposes_memory_refs(app, db, make_user, time_travel):
    """对话流里的剧本消息带引用依据（前端「记忆依据 ▾」），用户消息则为空。"""
    time_travel.set(NOW)
    await _prefilled(db, make_user)
    memory = await _memory(db, "u1", "我还是把那封邮件发出去了", days_ago=1)
    await chat_repository.create(
        db, "u1", "assistant", f"昨天你写下过「{memory.content}」。今天这件事还在吗？",
        message_type="proactive", memory_ids=[memory.id],
    )
    await _user_message(db, "u1", "在的", days_ago=0)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/chat/history?limit=10")

    assert resp.status_code == 200
    history = resp.json()["data"]
    ai = [h for h in history if h["role"] == "ai"]
    user_lines = [h for h in history if h["role"] == "user"]
    assert len(ai) == 1
    assert ai[0]["memory_refs"][0]["memory_id"] == memory.id
    assert ai[0]["memory_refs"][0]["excerpt"] == memory.content
    assert all(h["memory_refs"] == [] for h in user_lines)

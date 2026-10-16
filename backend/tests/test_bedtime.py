"""§5.16 S1 睡前复盘（C1）。

覆盖：三步流程（step1 一句话可跳过零 LLM / step2 值得记下的 + memory_refs
硬不变量 / step3 留给明天只观察不预测）/ 当日幂等 / 危机转介挂载（拦截 +
审计 + 停推）/ 当日归档（GET /bedtime/today）/ 22 点本地窗口 cron 推送与
门控（推送偏好 / 已完成 / 危机停推）/ 措辞过闸（模板与红线词钉死）。
"""
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.models.growth import GrowthRecord
from backend.models.journey import SafetyEvent
from backend.repositories import (
    chat_repository,
    growth_repository,
    memory_repository,
    mood_repository,
    safety_repository,
)
from backend.core import copy as copy_lib
from backend.core.copy import SUPPORTED_LOCALES
from backend.services import bedtime_service as bs
from backend.services.bedtime_service import bedtime_service
from backend.services.cron_service import cron_service
from backend.services.push.copy import push_copywriter, templates_for
from backend.ai.safety import safety_service

# 动态冻结：取「真实今天的 22:30」——SQLite 行 created_at 走 CURRENT_TIMESTAMP
# 真实墙钟（落在真实今天内），冻结在同一日历日可让 service 内新建的行天然命中
# 当日窗口查询；冻结小时固定 22:30 以命中睡前推送桶（无需手动挪当日行）。
_TODAY = datetime.now(timezone.utc).date()
FROZEN_NOW = datetime(_TODAY.year, _TODAY.month, _TODAY.day, 22, 30, tzinfo=timezone.utc)
TODAY = _TODAY
YESTERDAY = _TODAY - timedelta(days=1)

USER_KW = dict(timezone="UTC", language="zh", ai_consent=True, push_enabled=True, push_frequency="daily")


def _local(day: date, hour: int, minute: int = 0) -> datetime:
    """UTC 用户的本地时刻（时区=UTC，直接拼 UTC 墙钟）。"""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


async def _seed_checkin(db, user_id: str, day: date, emoji: str = "😊", label: str = "开心"):
    checkin = await mood_repository.create(
        db, user_id, emoji, label, "AI 解读", checkin_date=day
    )
    checkin.created_at = _local(day, 9)
    await db.flush()
    return checkin


async def _seed_memory(db, user_id: str, content: str, day: date = TODAY):
    memory = await memory_repository.create(
        db, user_id, content=content, category="preference", source="self", conviction=0.6
    )
    memory.created_at = _local(day, 10)
    await db.flush()
    return memory


async def _seed_chat(db, user_id: str, content: str, day: date = TODAY):
    message = await chat_repository.create(db, user_id, "user", content)
    message.created_at = _local(day, 11)
    await db.flush()
    return message


async def _seed_review(db, user_id: str, day: date, content: str = "昨天的复盘") -> GrowthRecord:
    record = await growth_repository.create(db, user_id, bs.RECORD_TYPE, content)
    record.created_at = _local(day, 22, 40)
    await db.flush()
    return record


async def _today_record(db, user_id: str) -> GrowthRecord | None:
    records = await growth_repository.list_by_type_between(
        db, user_id, bs.RECORD_TYPE, _local(TODAY, 0), _local(TODAY, 23, 59)
    )
    return records[0] if records else None


# ── 1. step1「今天的一句话」：可跳过、零 LLM、返回当日摘要 ──


async def test_step1_saves_note_and_returns_digest_without_llm(db, make_user, time_travel, mock_llm):
    time_travel.set(FROZEN_NOW)
    user = await make_user("u1", **USER_KW)
    await _seed_checkin(db, "u1", TODAY)
    await _seed_chat(db, "u1", "今天开会还挺顺")

    result = await bedtime_service.submit_step(db, user, 1, "今天开会还挺顺，晚上想早点睡")

    assert result["skipped"] is False
    digest = result["digest"]
    assert digest["chat_count"] == 1
    assert digest["mood"]["emoji"] == "😊"
    assert digest["memory_count"] == 0
    # step1 纯规则拼装：零 LLM（不生成、不检查）
    assert mock_llm.calls["complete"] == []

    record = await _today_record(db, "u1")
    assert record is not None
    assert record.record_type == bs.RECORD_TYPE
    steps = json.loads(record.metadata_json)
    assert steps["step1"]["text"] == "今天开会还挺顺，晚上想早点睡"
    assert steps["step1"]["skipped"] is False


async def test_step1_skip_creates_record_without_text(db, make_user, time_travel):
    time_travel.set(FROZEN_NOW)
    user = await make_user("u1", **USER_KW)

    result = await bedtime_service.submit_step(db, user, 1, None)

    assert result["skipped"] is True
    record = await _today_record(db, "u1")
    steps = json.loads(record.metadata_json)
    assert steps["step1"]["text"] is None
    assert steps["step1"]["skipped"] is True


async def test_step1_resubmit_overwrites_note(db, make_user, time_travel):
    """step1 重复提交覆盖第一句话（用户改口很正常，无成本）。"""
    time_travel.set(FROZEN_NOW)
    user = await make_user("u1", **USER_KW)

    await bedtime_service.submit_step(db, user, 1, "第一版")
    await bedtime_service.submit_step(db, user, 1, "第二版")

    record = await _today_record(db, "u1")
    records = await growth_repository.list_by_type_between(
        db, "u1", bs.RECORD_TYPE, _local(TODAY, 0), _local(TODAY, 23, 59)
    )
    assert len(records) == 1  # 不产生第二条记录
    assert json.loads(record.metadata_json)["step1"]["text"] == "第二版"


# ── 2. 危机转介挂载（step1 用户输入）──


async def test_step1_crisis_intercept_blocks_and_audits(db, make_user, time_travel, mock_llm):
    time_travel.set(FROZEN_NOW)
    user = await make_user("u1", **USER_KW)

    result = await bedtime_service.submit_step(db, user, 1, "我真的不想活了，撑不下去了")

    assert result["intercepted"] is True
    crisis = result["crisis"]
    assert crisis["kind"] == "self_harm"
    assert crisis["hotline"]
    assert "12356" in crisis["reply"] or crisis["hotline"] in crisis["reply"]

    # 审计：SafetyEvent source 预留位 bedtime_review 落库
    events = list((await db.execute(select(SafetyEvent))).scalars())
    assert len(events) == 1
    assert events[0].source == "bedtime_review"
    assert events[0].kind == "self_harm"

    # 危机内容不做复盘素材：不落记录、不走 LLM
    assert await _today_record(db, "u1") is None
    assert mock_llm.calls["complete"] == []


async def test_step1_safe_text_does_not_record_safety_event(db, make_user, time_travel):
    time_travel.set(FROZEN_NOW)
    user = await make_user("u1", **USER_KW)

    await bedtime_service.submit_step(db, user, 1, "今天很平静")

    events = list((await db.execute(select(SafetyEvent))).scalars())
    assert events == []


# ── 3. step2「你今天值得记下的」：memory_refs 硬不变量 ──


async def test_step2_extracts_worth_noting_with_memory_refs(db, make_user, time_travel, mock_llm):
    time_travel.set(FROZEN_NOW)
    user = await make_user("u1", **USER_KW)
    m1 = await _seed_memory(db, "u1", "开始每天跑步三公里")
    m2 = await _seed_memory(db, "u1", "和妈妈聊了很久，感觉轻松多了")
    await _seed_checkin(db, "u1", TODAY)
    await _seed_chat(db, "u1", "今天做到了坚持早起")

    result = await bedtime_service.submit_step(db, user, 2, None)

    assert result["content"] == "MOCK_LLM_REPLY"
    # memory_refs 硬不变量：引用即当日真实记忆，全部归属该用户
    refs = result["memory_refs"]
    assert [r["memory_id"] for r in refs] == [m1.id, m2.id]
    for ref in refs:
        assert await memory_repository.get(db, ref["memory_id"], "u1") is not None
    # prompt 携带当日真实素材（记忆原话 + 对话）
    prompts = [c["prompt"] for c in mock_llm.calls["complete"]]
    assert any("开始每天跑步三公里" in p for p in prompts)
    # 落库：related_memory_ids 与 metadata_json.step2
    record = await _today_record(db, "u1")
    assert record.related_memory_ids.split(",") == [m1.id, m2.id]
    steps = json.loads(record.metadata_json)
    assert steps["step2"]["content"] == "MOCK_LLM_REPLY"
    assert steps["step2"]["memory_refs"] == refs


async def test_step2_no_data_falls_back_to_template_without_llm(db, make_user, time_travel, mock_llm):
    """当日无任何素材 → 直接模板回退（零 LLM 成本），refs 为空。"""
    time_travel.set(FROZEN_NOW)
    user = await make_user("u1", **USER_KW)

    result = await bedtime_service.submit_step(db, user, 2, None)

    assert result["content"] == copy_lib.t("bedtime", "step2", "zh")
    assert result["memory_refs"] == []
    assert mock_llm.calls["complete"] == []
    record = await _today_record(db, "u1")
    assert record.related_memory_ids is None


async def test_step2_idempotent_returns_saved_content(db, make_user, time_travel, mock_llm):
    """step2 重复调用返回已存内容（不重复消耗 LLM）。"""
    time_travel.set(FROZEN_NOW)
    user = await make_user("u1", **USER_KW)
    await _seed_memory(db, "u1", "开始每天跑步三公里")

    first = await bedtime_service.submit_step(db, user, 2, None)
    mock_llm.state["complete_text"] = "SECOND_ANSWER"
    second = await bedtime_service.submit_step(db, user, 2, None)

    assert second["content"] == first["content"] == "MOCK_LLM_REPLY"
    generated = [c for c in mock_llm.calls["complete"] if "值得记下" in c["prompt"]]
    assert len(generated) == 1


# ── 4. step3「留给明天」：只观察、不预测、不承诺 ──


async def test_step3_observation_point_completes_review(db, make_user, time_travel):
    time_travel.set(FROZEN_NOW)
    user = await make_user("u1", **USER_KW)
    await bedtime_service.submit_step(db, user, 1, "今天还好")

    result = await bedtime_service.submit_step(db, user, 3, None)

    assert result["completed"] is True
    assert result["content"]
    record = await _today_record(db, "u1")
    steps = json.loads(record.metadata_json)
    assert steps["step3"]["content"] == result["content"]

    today = await bedtime_service.get_today(db, "u1")
    assert today["completed"] is True
    assert today["record"]["steps"]["step1"]["text"] == "今天还好"


async def test_step3_idempotent_returns_saved_content(db, make_user, time_travel, mock_llm):
    time_travel.set(FROZEN_NOW)
    user = await make_user("u1", **USER_KW)

    first = await bedtime_service.submit_step(db, user, 3, None)
    mock_llm.state["complete_text"] = "SECOND_ANSWER"
    second = await bedtime_service.submit_step(db, user, 3, None)

    assert second["content"] == first["content"]
    assert second["completed"] is True


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_step3_fallback_avoids_prediction_language(lang):
    """step3 模板红线：只观察、不预测、不承诺（PRD §5.16 ③）。"""
    banned = ("预测", "注定", "运势", "明天你会", "一定会", "将会",
              "predict", "destiny", "forecast", "you will become")
    text = copy_lib.t("bedtime", "step3", lang)
    assert text
    assert not any(word in text.lower() for word in banned)
    # 静态模板出口过确定性闸
    assert safety_service.filter_template(text, lang) == text


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_step2_fallback_passes_gate(lang):
    text = copy_lib.t("bedtime", "step2", lang)
    assert text
    assert safety_service.filter_template(text, lang) == text


async def test_invalid_step_rejected(db, make_user, time_travel):
    time_travel.set(FROZEN_NOW)
    user = await make_user("u1", **USER_KW)

    with pytest.raises(Exception):
        await bedtime_service.submit_step(db, user, 4, None)


# ── 5. 当日归档（GET /bedtime/today）──


async def test_get_today_returns_yesterday_archive_and_status(db, make_user, time_travel):
    time_travel.set(FROZEN_NOW)
    user = await make_user("u1", **USER_KW)
    await _seed_review(db, "u1", YESTERDAY, "昨天的复盘内容")
    await _seed_checkin(db, "u1", TODAY)
    await _seed_memory(db, "u1", "开始每天跑步三公里")

    today = await bedtime_service.get_today(db, "u1")

    assert today["date"] == TODAY.isoformat()
    assert today["completed"] is False
    assert today["yesterday"]["content"] == "昨天的复盘内容"
    # digest：step1 前的当日摘要参考
    assert today["digest"]["mood"]["emoji"] == "😊"
    assert today["digest"]["memory_count"] == 1

    await bedtime_service.submit_step(db, user, 1, "今天不错")
    await bedtime_service.submit_step(db, user, 2, None)
    await bedtime_service.submit_step(db, user, 3, None)

    today = await bedtime_service.get_today(db, "u1")
    assert today["completed"] is True
    assert set(today["record"]["steps"]) == {"step1", "step2", "step3"}


async def test_get_today_empty_state(db, make_user, time_travel):
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)

    today = await bedtime_service.get_today(db, "u1")

    assert today["completed"] is False
    assert today["yesterday"] is None
    assert today["record"] is None
    assert today["digest"]["has_data"] is False


# ── 6. 22:00–23:30 本地窗口 cron 推送 ──


async def test_cron_pushes_at_local_bedtime_window(db, make_user, time_travel):
    time_travel.set(FROZEN_NOW)  # UTC 用户本地 22:30
    await make_user("u1", **USER_KW)
    await make_user("u2", **{**USER_KW, "timezone": "Asia/Shanghai"})  # 本地 06:30 → 不命中

    result = await cron_service.dispatch_bedtime_reminders(db)

    assert result["sent"] == 1
    assert result["skipped"] >= 1


async def test_cron_skips_completed_review(db, make_user, time_travel):
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)
    record = await _seed_review(db, "u1", TODAY)
    steps = json.loads(record.metadata_json or "{}")
    steps["step3"] = {"content": "done"}
    record.metadata_json = json.dumps(steps, ensure_ascii=False)
    await db.flush()

    result = await cron_service.dispatch_bedtime_reminders(db)

    assert result["sent"] == 0
    assert result["skipped"] == 1


async def test_cron_skips_incomplete_review_still_pushes(db, make_user, time_travel):
    """当日已建记录但只完成 step1 → 仍在窗口内提醒（复盘未完成）。"""
    time_travel.set(FROZEN_NOW)
    user = await make_user("u1", **USER_KW)
    await bedtime_service.submit_step(db, user, 1, "只写了一句话")

    result = await cron_service.dispatch_bedtime_reminders(db)

    assert result["sent"] == 1


async def test_cron_respects_push_preferences(db, make_user, time_travel):
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **{**USER_KW, "push_enabled": False})
    await make_user("u2", **{**USER_KW, "push_frequency": "important"})
    await make_user("u3", **USER_KW)

    result = await cron_service.dispatch_bedtime_reminders(db)

    assert result["sent"] == 1  # 仅 u3


async def test_cron_skips_recent_crisis_hit(db, make_user, time_travel):
    """近 24h 危机命中 → 停推（营销/仪式类推送挂起，PRD §5.16 ④）。"""
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **USER_KW)
    await safety_repository.record(
        db, "u1", kind="self_harm", source="user_message", matched_terms=["不想活"]
    )

    result = await cron_service.dispatch_bedtime_reminders(db)

    assert result["sent"] == 0
    assert result["skipped"] == 1


async def test_cron_sends_bedtime_copy(db, make_user, time_travel, monkeypatch):
    """推送文案走 bedtime 场景模板，data.type=bedtime_review。"""
    time_travel.set(FROZEN_NOW)
    await make_user("u1", **{**USER_KW, "language": "zh"})
    captured = {}

    async def fake_send(db_, user_id, title, body, data=None):
        captured["title"], captured["body"], captured["data"] = title, body, data
        return {"sent": 1, "failed": 0}

    monkeypatch.setattr("backend.services.cron_service.push_service.send_to_user", fake_send)

    await cron_service.dispatch_bedtime_reminders(db)

    zh_pool = templates_for("bedtime", "zh")
    assert captured["title"] in [t for t, _ in zh_pool]
    assert captured["body"] in [b.format(name="朋友") for _, b in zh_pool]
    assert captured["data"] == {"type": "bedtime_review"}


# ── 7. 推送文案与场景描述 ──


@pytest.mark.parametrize("lang", SUPPORTED_LOCALES)
def test_bedtime_push_templates_exist_and_pass_gate(lang):
    pool = templates_for("bedtime", lang)
    assert len(pool) >= 2
    for title, body in pool:
        assert title and body
        assert safety_service.filter_template(title, lang)
        assert safety_service.filter_template(body, lang)


async def test_build_copy_bedtime_scenario(db, make_user):
    user = await make_user("u1", timezone="UTC", language="zh", tier="free")
    title, body = await push_copywriter.build_copy(db, user, "bedtime")
    zh_pool = templates_for("bedtime", "zh")
    assert title in [t for t, _ in zh_pool]
    assert body in [b.format(name="朋友") for _, b in zh_pool]


# ── 8. 复盘不进对话配额池（独立于 chat 配额）──


async def test_review_does_not_write_chat_or_quota(db, make_user, time_travel):
    time_travel.set(FROZEN_NOW)
    user = await make_user("u1", **USER_KW)
    await _seed_memory(db, "u1", "开始每天跑步三公里")

    await bedtime_service.submit_step(db, user, 1, "今天不错")
    await bedtime_service.submit_step(db, user, 2, None)
    await bedtime_service.submit_step(db, user, 3, None)

    # 不写对话流
    assert await chat_repository.get_history(db, "u1") == []
    # growth_records 恰好一条复盘记录（不产生 daily_insight 等副产物）
    n = await db.scalar(select(func.count()).select_from(GrowthRecord))
    assert n == 1

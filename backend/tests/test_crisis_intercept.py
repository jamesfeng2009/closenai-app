"""D2 · crisis_intercept 危机转介实装（PRD §5.16 ④ / 措辞规范 §二 + 架构方案 §六-2）。

覆盖规范 §二 2.3 的三条验收：
1. 暴力样本 → 禁归因/禁沟通建议（不进 LLM）+ 必须出现安全转介 + 写审计
2. 命中当日该用户营销/主动消息队列挂起
3. 检测/审计自身异常不阻断关怀文案（文案缺失才是 P1）
"""
import pytest
from datetime import timedelta

from backend.repositories import chat_repository, memory_repository, safety_repository
from backend.services.cron_service import cron_service
from backend.services.message_generator import message_generator
from backend.services.safety_service import safety_service

VIOLENCE_INPUT = "TA 动手打了我，我是不是说话太大声了"
SELF_HARM_INPUT = "我真的不想活了，撑不下去了"


async def _collect(db, user_id, text, language="zh"):
    chunks = []
    async for c in message_generator.generate_chat_stream(
        db=db, user_id=user_id, user_input=text, language=language
    ):
        chunks.append(c)
    return "".join(chunks)


# ── 触发词检测 ─────────────────────────────────────────────


def test_crisis_intercept_self_harm():
    hit = safety_service.crisis_intercept(SELF_HARM_INPUT, "zh", "Asia/Shanghai")
    assert hit["kind"] == "self_harm"
    assert hit["advice_blocked"] is False
    assert "12356" in hit["hotline"] and "12356" in hit["reply"]


def test_crisis_intercept_relationship_violence():
    hit = safety_service.crisis_intercept(VIOLENCE_INPUT, "zh", "Asia/Shanghai")
    assert hit["kind"] == "relationship_violence"
    assert hit["matched"] == ["动手打"]
    assert hit["advice_blocked"] is True  # §二 铁律：禁沟通改进类建议


def test_crisis_intercept_self_harm_takes_priority():
    """同句同时命中两类时按生命风险优先自伤类（crisis_intercept kind 判定顺序）。"""
    hit = safety_service.crisis_intercept("他动手打我，我已经不想活了", "zh")
    assert hit["kind"] == "self_harm"


def test_crisis_intercept_english_violence():
    hit = safety_service.crisis_intercept(
        "He hits me and controls my money", "en", "America/Denver"
    )
    assert hit["kind"] == "relationship_violence"
    assert "988" in hit["hotline"]


@pytest.mark.parametrize(
    "text",
    [
        "他的话打动了我，我想跟他学钢琴",
        "今天和朋友打了个电话，聊了很久",
        "这部电影的打斗场面很精彩",
        "I want to learn to dance, not to argue",
    ],
)
def test_crisis_intercept_no_false_positive(text):
    """误报兜底：单字/近形词不触发（词表取多字信号词，见 CRISIS_TRIGGERS 注释）。"""
    language = "en" if text.isascii() else "zh"
    assert safety_service.crisis_intercept(text, language) is None


def test_resolve_hotline_by_timezone():
    """按 users.timezone 推断地区热线；无匹配走兜底（模型无地区列，user.py:25-27）。"""
    assert "12356" in safety_service.resolve_hotline("Asia/Shanghai")
    assert "988" in safety_service.resolve_hotline("America/New_York")
    assert "988" in safety_service.resolve_hotline("Canada/Eastern")
    assert "112" in safety_service.resolve_hotline("Europe/Paris")
    assert safety_service.resolve_hotline(None) == safety_service.HOTLINES["fallback"]


def test_crisis_copy_passes_wording_blacklist():
    """转介文案自身不得含 §5.16 ① 医疗化红线词与 D 级词（A 级事实陈述）。"""
    for kind in ("self_harm", "relationship_violence"):
        for lang in ("zh", "en"):
            copy = safety_service.CRISIS_COPY[kind][lang].format(hotline="HOTLINE")
            assert safety_service.banned_hit(copy, lang) is None, f"{kind}/{lang} 含黑名单词"


# ── 验收 1：用户回复路径 ───────────────────────────────────


async def test_violence_input_blocks_advice_and_records_audit(db, make_user, mock_llm):
    """暴力样本：不进 LLM（因此不可能输出沟通行为归因）、出安全转介、写审计。"""
    await make_user("u1", ai_consent=True, timezone="Asia/Shanghai")

    text = await _collect(db, "u1", VIOLENCE_INPUT)

    assert not mock_llm.calls["stream"], "命中危机词后不得调用 LLM"
    assert "不是你的错" in text and "12356" in text
    for forbidden in ("更温柔", "再沟通", "沟通方式", "说明 TA"):
        assert forbidden not in text

    hit = await safety_repository.latest_hit(db, "u1", within_hours=24)
    assert hit is not None
    assert hit.kind == "relationship_violence"
    assert hit.advice_blocked is True
    assert hit.source == "user_message"
    assert "动手打" in hit.matched_terms
    assert "动手打了" in hit.excerpt


async def test_self_harm_input_returns_hotline(db, make_user, mock_llm):
    await make_user("u1", ai_consent=True, timezone="Asia/Shanghai")
    text = await _collect(db, "u1", SELF_HARM_INPUT)
    assert "12356" in text
    assert not mock_llm.calls["stream"]


async def test_normal_input_still_goes_to_llm(db, make_user, mock_llm):
    """未命中：对话链路完全不变（用户发送路径零阻塞铁律）。"""
    await make_user("u1", ai_consent=True, timezone="Asia/Shanghai")
    mock_llm.state["complete_text"] = "SAFE"

    async def fake_stream(prompt, **kwargs):
        yield "今天过得怎么样？"

    import backend.core.llm as llm_mod

    orig = llm_mod.llm.stream
    llm_mod.llm.stream = fake_stream
    try:
        text = await _collect(db, "u1", "今天和同事聊了项目进度")
    finally:
        llm_mod.llm.stream = orig
    assert "今天过得怎么样？" in text
    assert await safety_repository.latest_hit(db, "u1") is None


async def test_audit_failure_still_delivers_copy(db, make_user, monkeypatch):
    """验收 3：审计写失败只记日志，关怀文案照常下发（文案缺失才是 P1）。"""
    await make_user("u1", ai_consent=True, timezone="Asia/Shanghai")

    async def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(safety_repository, "record", _boom)
    text = await _collect(db, "u1", SELF_HARM_INPUT)
    assert "12356" in text


# ── 验收 2：命中当日营销/主动消息队列挂起 ──────────────────


async def test_proactive_message_paused_after_hit(db, make_user, time_travel):
    """命中当日：主动消息生成入口静默，且挂起窗口只覆盖用户本地当日（非永久停推）。"""
    from datetime import datetime, timezone as dt_tz

    from backend.core.time_utils import local_day_start_utc

    NOW = datetime(2026, 9, 20, 3, 0, tzinfo=dt_tz.utc)
    time_travel.set(NOW)
    await make_user(
        "u1", ai_consent=True, tier="pro", timezone="UTC",
        created_at=NOW - timedelta(days=10),
    )
    await safety_repository.record(
        db, user_id="u1", kind="relationship_violence",
        matched_terms=["动手打"], hotline="12356", language="zh", excerpt="TA 动手打了我",
    )
    assert await safety_repository.has_hit_since(db, "u1", local_day_start_utc("UTC")) is True

    msg = await message_generator.generate_proactive_message(db, "u1", "zh")
    assert msg is None


async def test_proactive_message_flows_without_hit(db, make_user, time_travel):
    """无命中记录：主动消息照常生成（停推不是默认状态）。"""
    from datetime import datetime, timezone as dt_tz

    NOW = datetime(2026, 9, 20, 3, 0, tzinfo=dt_tz.utc)
    time_travel.set(NOW)
    await make_user("u1", ai_consent=True, timezone="UTC",
                    created_at=NOW - timedelta(days=10))
    await chat_repository.create(db, "u1", "user", "最近睡眠很差")
    await memory_repository.create(db, "u1", "最近睡眠很差", "mood", "chat", 0.8)

    msg = await message_generator.generate_proactive_message(db, "u1", "zh")
    assert msg is not None


async def test_cron_batch_skips_after_hit(db, make_user, monkeypatch):
    """cron 队列级挂起：命中当日连沉默唤回类轻关心也不下发。"""
    await make_user("u1", ai_consent=True, timezone="UTC", push_enabled=False)

    async def fake_generate(db, user_id, language):
        return "不该出现在命中当日的消息"

    monkeypatch.setattr(message_generator, "generate_proactive_message", fake_generate)
    await safety_repository.record(
        db, user_id="u1", kind="self_harm", matched_terms=["不想活"],
        hotline="12356", language="zh", excerpt="不想活了",
    )

    result = await cron_service.process_batch(db, ["u1"])
    assert result["success"] == 0
    history = await chat_repository.get_history(db, "u1")
    assert not history

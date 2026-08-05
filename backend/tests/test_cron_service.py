"""cron_service：±15min 确定性抖动、今日已发去重、正常发送链路。"""
from datetime import date, timedelta
from types import SimpleNamespace

from backend.repositories import chat_repository
from backend.services.cron_service import cron_service
from backend.services.message_generator import message_generator


def test_jitter_deterministic_same_user_same_day():
    """同一用户同一日抖动值恒定（sha256，无进程级随机盐）。"""
    user = SimpleNamespace(id="u-123")
    day = date(2026, 8, 4)
    assert cron_service._jitter_minutes(user, day) == cron_service._jitter_minutes(user, day)


def test_jitter_always_within_range():
    """多用户 × 多日抖动值均在 [-15, 15]。"""
    for i in range(50):
        user = SimpleNamespace(id=f"user-{i}")
        for offset in range(31):
            day = date(2026, 8, 1) + timedelta(days=offset)
            jitter = cron_service._jitter_minutes(user, day)
            assert -15 <= jitter <= 15


def test_target_minutes_uses_push_time_plus_jitter():
    """目标时刻 = push_time + 抖动（取模 24h）。"""
    user = SimpleNamespace(id="u-123", push_time="09:30")
    day = date(2026, 8, 4)
    expected = (9 * 60 + 30 + cron_service._jitter_minutes(user, day)) % (24 * 60)
    assert cron_service._target_minutes(user, day) == expected


async def test_process_batch_skips_when_already_sent_today(db, make_user):
    """今日已发去重：该用户本地今日已有 proactive 消息则跳过（cron 重复触发不重发）。"""
    await make_user("u1", ai_consent=True, timezone="UTC")
    await chat_repository.create(
        db, "u1", "assistant", "今天已发过的主动消息", message_type="proactive", ai_generated=True
    )

    result = await cron_service.process_batch(db, ["u1"])

    assert result["skipped_already_sent"] == 1
    assert result["success"] == 0
    # 未新增消息
    history = await chat_repository.get_history(db, "u1")
    assert len(history) == 1


async def test_process_user_generates_and_persists_message(db, make_user, monkeypatch):
    """正常链路：未发过且配额未超 → 生成主动消息并落库。"""
    await make_user("u1", ai_consent=True, timezone="UTC", push_enabled=False)

    async def fake_generate(db, user_id, language):
        return "早安，今天感觉怎么样？"

    monkeypatch.setattr(message_generator, "generate_proactive_message", fake_generate)

    result = await cron_service.process_batch(db, ["u1"])

    assert result["success"] == 1
    history = await chat_repository.get_history(db, "u1")
    assert len(history) == 1
    assert history[0].content == "早安，今天感觉怎么样？"
    assert history[0].message_type == "proactive"

    # 去重生效：同批再处理一次则跳过
    result2 = await cron_service.process_batch(db, ["u1"])
    assert result2["skipped_already_sent"] == 1

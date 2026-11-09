"""quota_service：双口径配额（对话轮数 vs 统一池）与 tier 分层。"""
import asyncio
from datetime import datetime, time as dt_time, timedelta, timezone
from uuid import uuid4

import pytest

from backend.core.errors import AppException, ErrorCode
from backend.core.time_utils import local_day_start_utc, local_today
from backend.models.generation_run import AiGenerationRun
from backend.models.mood import MoodCheckin
from backend.repositories import chat_repository, mood_repository
from backend.services.mood_service import mood_service
from backend.services.quota_service import (
    MOOD_DEEP_INTERPRETATION_PURPOSE,
    MOOD_INTERPRETATION_PURPOSES,
    MOOD_INTERPRETATION_PURPOSE,
    REFLECTION_DRAFT_PURPOSE,
    REFLECTION_DRAFT_PURPOSES,
    quota_service,
)
from backend.services.reflection_service import reflection_service


def _seed_run(db, user_id: str, purpose: str, status: str = "ok") -> None:
    """直接落一条生成留痕（mock_llm 不写真实留痕，配额测试需自己补）。"""
    db.add(
        AiGenerationRun(
            id=str(uuid4()),
            user_id=user_id,
            op="complete",
            purpose=purpose,
            status=status,
            output_chars=10,
        )
    )


async def _insert_checkin_days_ago(db, user_id: str, emoji: str, days_ago: int) -> None:
    """在 N 天前（UTC 正午，避免跨日边界）落一条签到，用于深度解读触发条件。"""
    day = local_today("UTC") - timedelta(days=days_ago)
    db.add(
        MoodCheckin(
            id=str(uuid4()),
            user_id=user_id,
            mood_emoji=emoji,
            mood_label="label",
            ai_interpretation="interp",
            checkin_date=day,
            created_at=datetime.combine(day, dt_time(12, 0), tzinfo=timezone.utc),
        )
    )
    await db.flush()


async def test_free_tier_unified_pool_exceeded(db, make_user):
    """免费层统一池（主动消息+签到解读）打满 5 条后超限。"""
    user = await make_user("u1")
    for _ in range(5):
        await chat_repository.create(db, "u1", "assistant", "早安", message_type="proactive")
    assert await quota_service.daily_usage(db, user) == 5
    assert await quota_service.is_proactive_quota_exceeded(db, user) is True
    assert await quota_service.is_daily_quota_exceeded(db, user) is True


async def test_pro_tier_not_exceeded_with_same_usage(db, make_user):
    """pro 层走高限（≈不限）：同样 5 条用量不超限。"""
    user = await make_user("u2", tier="pro")
    for _ in range(5):
        await chat_repository.create(db, "u2", "assistant", "早安", message_type="proactive")
    assert await quota_service.is_proactive_quota_exceeded(db, user) is False
    assert await quota_service.is_daily_quota_exceeded(db, user) is False


async def test_dual_scope_chat_rounds_independent_of_unified_pool(db, make_user):
    """双口径：对话轮数（user 消息）打满 → 共用入口拒绝，但统一池口径仍未超限。"""
    user = await make_user("u3")
    for _ in range(10):  # free_daily_chat_rounds = 10
        await chat_repository.create(db, "u3", "user", "你好")
    assert await quota_service.chat_rounds_today(db, user) == 10
    assert await quota_service.daily_usage(db, user) == 0
    assert await quota_service.is_proactive_quota_exceeded(db, user) is False
    assert await quota_service.is_daily_quota_exceeded(db, user) is True


async def test_b2_chat_entry_not_blocked_by_unified_pool(db, make_user):
    """B2 回归：统一池（主动消息）耗尽时对话入口不应被阻断。

    旧 is_daily_quota_exceeded 把"任一池耗尽"作为对话入口判据，导致 5 条主动
    消息发完后用户无法继续对话。修复后 chat 入口走 is_chat_quota_exceeded，
    仅看对话轮数；mood/reflection 入口走 is_proactive_quota_exceeded，
    仅看统一池。
    """
    user = await make_user("u_b2")
    # 统一池打满 5 条主动消息（free_daily_messages = 5）
    for _ in range(5):
        await chat_repository.create(db, "u_b2", "assistant", "早安", message_type="proactive")
    assert await quota_service.daily_usage(db, user) == 5
    assert await quota_service.chat_rounds_today(db, user) == 0

    # 对话入口：未耗尽（0 < 10）→ 不阻断
    assert await quota_service.is_chat_quota_exceeded(db, user) is False
    # 签到/反思入口：统一池已耗尽 → 阻断
    assert await quota_service.is_proactive_quota_exceeded(db, user) is True
    # 合集判定（兼容历史）：任一池耗尽即 True
    assert await quota_service.is_daily_quota_exceeded(db, user) is True


async def test_b2_chat_entry_blocked_when_chat_rounds_exhausted(db, make_user):
    """B2 回归：对话轮数耗尽时对话入口阻断，但签到/反思入口不受影响。"""
    user = await make_user("u_b2b")
    for _ in range(10):  # free_daily_chat_rounds = 10
        await chat_repository.create(db, "u_b2b", "user", "你好")
    assert await quota_service.chat_rounds_today(db, user) == 10
    assert await quota_service.daily_usage(db, user) == 0

    # 对话入口：已耗尽 → 阻断
    assert await quota_service.is_chat_quota_exceeded(db, user) is True
    # 签到/反思入口：统一池未耗尽 → 不阻断
    assert await quota_service.is_proactive_quota_exceeded(db, user) is False


async def test_unified_pool_counts_mood_checkins_and_proactive(db, make_user):
    """统一池口径 = 今日主动消息数 + 今日签到解读数（不含对话轮数）。"""
    user = await make_user("u4")
    day_start = local_day_start_utc(user.timezone)
    for _ in range(3):
        await mood_repository.create(db, "u4", "😊", "Happy", "解读")
    await chat_repository.create(db, "u4", "assistant", "主动", message_type="proactive")
    await chat_repository.create(db, "u4", "user", "对话不计入统一池")
    assert await mood_repository.count_since(db, "u4", day_start) == 3
    assert await quota_service.daily_usage(db, user) == 4


async def test_same_day_repeat_checkin_charges_each_llm_call(db, make_user):
    """同日重签只留一行签到，但每次带备注的签到都真实调了 LLM：按留痕取大值。"""
    user = await make_user("u-repeat", ai_consent=True, timezone="UTC")
    # 生产里每次 _interpret / _deep_interpretation 成功都会落一行留痕
    _seed_run(db, "u-repeat", MOOD_INTERPRETATION_PURPOSE)
    _seed_run(db, "u-repeat", MOOD_INTERPRETATION_PURPOSE)
    await db.flush()
    await mood_repository.create(db, "u-repeat", "😊", "Happy", "解读")
    # 行数 1、留痕 2 → 取大值 2，而不是把两次真实消耗压成一次
    assert await quota_service.daily_usage(db, user) == 2


async def test_repeat_checkin_blocked_once_interpretation_runs_fill_pool(db, make_user):
    """留痕打满统一池后同日再签到被拒：旧口径只看签到行（0/1）会永远放行。"""
    await make_user("u-repeat-blocked", ai_consent=True, timezone="UTC")
    for _ in range(5):  # free_daily_messages = 5
        _seed_run(db, "u-repeat-blocked", MOOD_INTERPRETATION_PURPOSE)
    await db.flush()
    with pytest.raises(AppException) as exc:
        await mood_service.checkin(db, "u-repeat-blocked", "😊", "再来一次", "en")
    assert exc.value.code == ErrorCode.QUOTA_EXCEEDED


async def test_reflection_draft_counts_against_unified_pool(db, make_user):
    """反思草稿此前没有可数行＝免费不限次；现按 purpose 计入统一池。"""
    user = await make_user("u-reflect", timezone="UTC")
    for _ in range(5):  # free_daily_messages = 5
        _seed_run(db, "u-reflect", REFLECTION_DRAFT_PURPOSE)
    await db.flush()
    assert await quota_service.daily_usage(db, user) == 5
    assert await quota_service.is_proactive_quota_exceeded(db, user) is True
    with pytest.raises(AppException) as exc:
        await reflection_service.generate_draft(db, "u-reflect", "en")
    assert exc.value.code == ErrorCode.QUOTA_EXCEEDED


async def test_mood_purposes_match_quota_registry(
    db, make_user, mock_llm, stub_semantic_search
):
    """服务传给 llm.complete 的 purpose 必须逐字在册（写岔＝配额数不到）。"""
    await make_user("u-mood-purpose", ai_consent=True, timezone="UTC")
    await _insert_checkin_days_ago(db, "u-mood-purpose", "😊", 2)
    await _insert_checkin_days_ago(db, "u-mood-purpose", "😊", 1)

    await mood_service.checkin(db, "u-mood-purpose", "😊", "今天有点累", "en")

    # 安全分类器也走 llm.complete（purpose=safety_service.check_text），只筛业务调用
    purposes = [
        c["purpose"]
        for c in mock_llm.calls["complete"]
        if c["purpose"] in MOOD_INTERPRETATION_PURPOSES
    ]
    assert purposes == [MOOD_INTERPRETATION_PURPOSE, MOOD_DEEP_INTERPRETATION_PURPOSE]
    assert set(purposes) == set(MOOD_INTERPRETATION_PURPOSES)


async def test_reflection_draft_purpose_matches_quota_registry(db, make_user, mock_llm):
    """反思草稿的 purpose 同样必须逐字在册。"""
    await make_user("u-reflect-purpose", timezone="UTC")
    await mood_repository.create(db, "u-reflect-purpose", "😊", "Happy", "解读")

    await reflection_service.generate_draft(db, "u-reflect-purpose", "en")

    purposes = [
        c["purpose"]
        for c in mock_llm.calls["complete"]
        if c["purpose"] in REFLECTION_DRAFT_PURPOSES
    ]
    assert purposes == [REFLECTION_DRAFT_PURPOSE]


# ═══ KG-28 第三池：原子预占（P2 #17）═══

async def test_reserve_mood_analysis_never_overbooks_concurrent_requests(
    db, make_user, monkeypatch
):
    """免费档 1 次/天：三个并发请求同时进入，只能有一个拿到额度。

    旧口径是「读 DB 已用 → 比上限」，三个请求都读到 0 就都能通过；
    现在先原子自增在途数，再按 used+inflight 判定，超额立即归还。
    """
    user = await make_user("u-race", timezone="UTC")

    async def _slow_used(_db, _user):
        await asyncio.sleep(0)  # 制造 DB 读取的交错点
        return 0

    monkeypatch.setattr(quota_service, "mood_analysis_used_today", _slow_used)
    results = await asyncio.gather(
        *(quota_service.reserve_mood_analysis(db, user) for _ in range(3))
    )
    assert results == [True, False, False]

    # 获胜那次归还后，额度回到可用；失败请求没有留下在途计数
    await quota_service.release_mood_analysis(user)
    assert await quota_service.reserve_mood_analysis(db, user) is True
    await quota_service.release_mood_analysis(user)


async def test_release_never_drives_inflight_below_zero(db, make_user, monkeypatch):
    """重复归还不能把在途数减成负数（负数会让配额重新变成可超用）。"""
    user = await make_user("u-floor", timezone="UTC")

    async def _zero_used(_db, _user):
        return 0

    monkeypatch.setattr(quota_service, "mood_analysis_used_today", _zero_used)
    for _ in range(3):  # 没有占用也归还：必须停在 0
        await quota_service.release_mood_analysis(user)
    assert await quota_service.reserve_mood_analysis(db, user) is True
    await quota_service.release_mood_analysis(user)

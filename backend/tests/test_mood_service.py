"""mood_service：同日重复签到走更新、深度解读触发（3 天同情绪 / 2 天负面 / 不触发）。"""
from datetime import datetime, time as dt_time, timedelta, timezone
from uuid import uuid4

from backend.core.time_utils import local_today
from backend.models.mood import MoodCheckin
from backend.repositories import mood_repository
from backend.services.mood_service import mood_service

TZ = "UTC"


async def _insert_checkin_days_ago(db, user_id: str, emoji: str, days_ago: int) -> None:
    """在 N 天前（UTC 正午，避免跨日边界）直接落一条签到记录。"""
    day = local_today(TZ) - timedelta(days=days_ago)
    db.add(
        MoodCheckin(
            id=str(uuid4()),
            user_id=user_id,
            mood_emoji=emoji,
            mood_label="label",
            ai_interpretation="interp",
            created_at=datetime.combine(day, dt_time(12, 0), tzinfo=timezone.utc),
        )
    )
    await db.flush()


async def test_same_day_checkin_updates_instead_of_insert(db, make_user):
    """同一本地日重复签到：更新已有记录而非新增，streak 不重复累加。"""
    await make_user("u1")
    first = await mood_service.checkin(db, "u1", "😊", None, "en")
    second = await mood_service.checkin(db, "u1", "😢", None, "en")

    assert second.id == first.id
    records = await mood_repository.get_recent(db, "u1", limit=10)
    assert len(records) == 1
    assert records[0].mood_emoji == "😢"
    # 首日签到 streak=1，同日重签不累加
    assert first.streak_updated == 1
    assert second.streak_updated == 1


async def test_deep_interpretation_triggered_by_3_same_emoji_days(db, make_user, mock_llm):
    """连续 3 天同一情绪 → 触发深度解读（LLM 文案透传）。"""
    await make_user("u1", ai_consent=True, timezone=TZ)
    await _insert_checkin_days_ago(db, "u1", "😊", 2)
    await _insert_checkin_days_ago(db, "u1", "😊", 1)

    resp = await mood_service.checkin(db, "u1", "😊", None, "en")
    assert resp.deep_interpretation == "MOCK_LLM_REPLY"


async def test_deep_interpretation_triggered_by_2_negative_days(db, make_user, mock_llm):
    """连续 2 天负面（情绪值 ≤2）→ 触发深度解读。"""
    await make_user("u1", ai_consent=True, timezone=TZ)
    await _insert_checkin_days_ago(db, "u1", "😢", 1)  # 难过 = 1

    resp = await mood_service.checkin(db, "u1", "😰", None, "en")  # 焦虑 = 2
    assert resp.deep_interpretation == "MOCK_LLM_REPLY"


async def test_deep_interpretation_not_triggered(db, make_user, mock_llm):
    """不触发场景：情绪模式不满足条件；满足条件但未同意 AI 数据共享。"""
    # 模式不满足：昨天开心、今天平静（无连续同情绪、无连续负面）
    await make_user("u-mixed", ai_consent=True, timezone=TZ)
    await _insert_checkin_days_ago(db, "u-mixed", "😊", 1)
    resp = await mood_service.checkin(db, "u-mixed", "😌", None, "en")
    assert resp.deep_interpretation is None

    # 模式满足但未同意 AI 数据共享（合规 5.1.2(i)）：不触发
    await make_user("u-noconsent", ai_consent=False, timezone=TZ)
    await _insert_checkin_days_ago(db, "u-noconsent", "😢", 1)
    resp2 = await mood_service.checkin(db, "u-noconsent", "😢", None, "en")
    assert resp2.deep_interpretation is None

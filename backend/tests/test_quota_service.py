"""quota_service：双口径配额（对话轮数 vs 统一池）与 tier 分层。"""
from backend.core.time_utils import local_day_start_utc
from backend.repositories import chat_repository, mood_repository
from backend.services.quota_service import quota_service


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

"""user_service：update_streak 连续/断签/冻结券、月度发放、里程碑幂等。"""
from datetime import timedelta

from backend.core.time_utils import local_today
from backend.repositories import user_repository
from backend.services.user_service import user_service

TZ = "UTC"


def _month() -> str:
    return local_today(TZ).strftime("%Y-%m")


async def test_update_streak_consecutive_day_increments(db, make_user):
    """昨天已签到 → streak +1，冻结券不消耗。"""
    today = local_today(TZ)
    await make_user(
        "u1",
        last_active_date=today - timedelta(days=1),
        streak_count=3,
        active_days=3,
        streak_freeze_count=1,
        streak_freeze_grant_month=_month(),
    )
    result = await user_service.update_streak(db, "u1")
    assert result["streak_count"] == 4
    assert result["freeze_used"] == 0
    assert result["streak_freeze_count"] == 1


async def test_update_streak_same_day_idempotent(db, make_user):
    """今天已签到 → 不重复累加。"""
    today = local_today(TZ)
    await make_user(
        "u1",
        last_active_date=today,
        streak_count=5,
        active_days=5,
        streak_freeze_count=1,
        streak_freeze_grant_month=_month(),
    )
    result = await user_service.update_streak(db, "u1")
    assert result["streak_count"] == 5
    assert result["freeze_used"] == 0


async def test_update_streak_broken_without_freeze_resets(db, make_user):
    """断签且无冻结券 → streak 重置为 1。"""
    today = local_today(TZ)
    await make_user(
        "u1",
        last_active_date=today - timedelta(days=3),
        streak_count=10,
        active_days=10,
        streak_freeze_count=0,
        streak_freeze_grant_month=_month(),
    )
    result = await user_service.update_streak(db, "u1")
    assert result["streak_count"] == 1
    assert result["freeze_used"] == 0


async def test_update_streak_gap_covered_by_freeze_coupon(db, make_user):
    """断签 1 天且持 1 张冻结券 → 自动消耗 1 张，streak 延续 +1。"""
    today = local_today(TZ)
    await make_user(
        "u1",
        last_active_date=today - timedelta(days=2),  # 缺勤昨天 1 天
        streak_count=4,
        active_days=4,
        streak_freeze_count=1,
        streak_freeze_grant_month=_month(),
    )
    result = await user_service.update_streak(db, "u1")
    assert result["streak_count"] == 5
    assert result["freeze_used"] == 1
    assert result["streak_freeze_count"] == 0
    user = await user_repository.get_user(db, "u1")
    assert user.streak_freeze_count == 0


async def test_freeze_monthly_grant_by_tier(db, make_user):
    """冻结券月度发放：free 1 张 / pro 3 张；当月已发放不重复重置。"""
    free_user = await make_user("u-free", tier="free", streak_freeze_count=0,
                                streak_freeze_grant_month="2000-01")
    await user_service.ensure_freeze_grant(db, free_user)
    assert free_user.streak_freeze_count == 1
    assert free_user.streak_freeze_grant_month == _month()

    pro_user = await make_user("u-pro", tier="pro", streak_freeze_count=0,
                               streak_freeze_grant_month="2000-01")
    await user_service.ensure_freeze_grant(db, pro_user)
    assert pro_user.streak_freeze_count == 3

    # 当月已发放：即便券已用完也不重置
    granted = await make_user("u-granted", tier="pro", streak_freeze_count=0,
                              streak_freeze_grant_month=_month())
    await user_service.ensure_freeze_grant(db, granted)
    assert granted.streak_freeze_count == 0


async def test_milestones_achieved_idempotent(db, make_user):
    """里程碑 7/30/100/365：跨过阈值达成且幂等（重复同步不重复发放）。"""
    today = local_today(TZ)
    await make_user(
        "u1",
        last_active_date=today - timedelta(days=1),
        streak_count=6,
        active_days=6,
        streak_freeze_count=1,
        streak_freeze_grant_month=_month(),
    )
    result = await user_service.update_streak(db, "u1")  # streak 6 → 7
    assert result["streak_count"] == 7
    assert result["new_milestones"] == ["streak_7"]

    # 幂等：再次同步不重复达成
    assert await user_service.sync_milestones(db, "u1", 7) == []

    milestones = await user_service.get_milestones(db, "u1")
    achieved = {m.milestone_type: m.achieved for m in milestones}
    assert achieved == {
        "streak_7": True,
        "streak_30": False,
        "streak_100": False,
        "streak_365": False,
    }

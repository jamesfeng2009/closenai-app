"""
§5.15a 等级情感回报（A3 / 架构方案 §二 M3）。

覆盖两件事：
1. LEVEL_UNLOCKS 不再是纯功能奖励（L2/L3/L4 首位为情感回报，等级页逐档带 reward）；
2. 等级晋升在 journey_events 预写 kind=level_reward 的 pending 行
   （与 §5.19.3 第一周剧本共用同一张表和同一条调度管线）。
"""
import json
from datetime import datetime, timedelta, timezone

from backend.core.time_utils import local_today
from backend.repositories import journey_repository
from backend.services.user_service import UserService, user_service

TZ = "Asia/Shanghai"


def _month(day=None) -> str:
    return (day or local_today(TZ)).strftime("%Y-%m")


async def test_l2_unlocks_lead_with_emotional_reward():
    """L2/L3/L4 解锁表首位是情感回报，功能项顺序不变（§5.15a 的验收口径）。"""
    assert UserService.LEVEL_UNLOCKS[1]["zh"] == ["基础对话", "情绪签到"]
    assert UserService.LEVEL_UNLOCKS[2]["zh"][0] == "AI 第一次提起你说过的事"
    assert UserService.LEVEL_UNLOCKS[3]["zh"][0] == "首份 Monthly Story 预览"
    assert UserService.LEVEL_UNLOCKS[4]["zh"][0] == "一封 90 天的信：这 90 天我看到的你"
    # 原功能项仍在表内，且每条回报都有 en 对应（等级页双语）
    assert UserService.LEVEL_UNLOCKS[2]["zh"][1:] == ["AI 主动跟进", "记忆引用"]
    assert all(r["en"] for r in UserService.LEVEL_REWARDS.values())


async def test_level_up_schedules_level_reward_event(db, make_user, time_travel):
    """L1→L2：写一条 pending 的 level_reward 事件，day_no 取等级阈值天数。"""
    now = datetime(2026, 8, 5, 3, 0, tzinfo=timezone.utc)  # 本地 11:00
    time_travel.set(now)
    await make_user(
        "u1",
        language="zh",
        timezone=TZ,
        push_time="09:00",
        last_active_date=local_today(TZ) - timedelta(days=1),
        streak_count=6,
        active_days=6,
        streak_freeze_count=1,
        streak_freeze_grant_month=_month(),
    )
    result = await user_service.update_streak(db, "u1")
    assert result["level_up"]["reward_key"] == "memory_echo"

    events = await journey_repository.list_for_user(db, "u1")
    rewards = [e for e in events if e.event_kind == "level_reward"]
    assert len(rewards) == 1
    event = rewards[0]
    assert event.day_no == 7  # LEVEL_THRESHOLDS[2]
    assert event.status == "pending"
    payload = json.loads(event.payload)
    assert payload["reward_key"] == "memory_echo"
    assert payload["level"] == 2
    assert payload["label"] == "熟悉 L2"
    # 计划时刻必须落在用户本地推送窗口。SQLite DATETIME 丢弃 tzinfo（存 UTC wall-clock，
    # 见 conftest 说明），因此读取侧统一补 UTC 后再断言。
    scheduled = event.scheduled_at
    scheduled = scheduled if scheduled.tzinfo else scheduled.replace(tzinfo=timezone.utc)
    assert scheduled > now
    local_slot = scheduled.astimezone(timezone(timedelta(hours=8)))
    assert (local_slot.hour, local_slot.minute) == (9, 0)
    assert local_slot.date() == (now + timedelta(days=1)).date()  # 当日 09:00 已过 → 顺延次日


async def test_l1_promotion_writes_no_reward_event(db, make_user, time_travel):
    """未到 L2 的活跃（active_days 1→2）不写事件：L1 没有情感回报档。"""
    time_travel.set(datetime(2026, 8, 5, 3, 0, tzinfo=timezone.utc))
    await make_user(
        "u2",
        timezone=TZ,
        last_active_date=local_today(TZ) - timedelta(days=1),
        streak_count=1,
        active_days=1,
        streak_freeze_count=1,
        streak_freeze_grant_month=_month(),
    )
    result = await user_service.update_streak(db, "u2")
    assert result["level_up"] is None
    assert await journey_repository.list_for_user(db, "u2") == []


async def test_l3_and_l4_use_their_own_reward_keys(db, make_user, time_travel):
    """L3 / L4 各自的回报键与 day_no（30 / 90），供调度管线分派正文模板。"""
    time_travel.set(datetime(2026, 8, 5, 3, 0, tzinfo=timezone.utc))
    for user_id, from_days, expected_level, expected_key, expected_day in (
        ("u3", 29, 3, "monthly_story_preview", 30),
        ("u4", 89, 4, "letter_90d", 90),
    ):
        await make_user(
            user_id,
            timezone=TZ,
            last_active_date=local_today(TZ) - timedelta(days=1),
            streak_count=from_days,
            active_days=from_days,
            streak_freeze_count=1,
            streak_freeze_grant_month=_month(),
        )
        result = await user_service.update_streak(db, user_id)
        assert result["level_up"]["level"] == expected_level
        assert result["level_up"]["reward_key"] == expected_key
        event = (await journey_repository.list_for_user(db, user_id))[0]
        assert (event.event_kind, event.day_no) == ("level_reward", expected_day)


async def test_reward_event_is_idempotent_within_same_level(db, make_user, time_travel):
    """同档重复触发（cron 重试/同日重放）不写出第二条 level_reward 行。

    update_streak 同日幂等已挡住 level_up 再触发，这里直接重放庆祝路径本身，
    验证仓库层 (user_id, kind, day_no) 去重是唯一的写入口。
    """
    now = datetime(2026, 8, 5, 3, 0, tzinfo=timezone.utc)
    time_travel.set(now)
    user = await make_user(
        "u5",
        language="en",
        timezone=TZ,
        active_days=7,
    )
    await user_service._celebrate_level_up(db, user, 2, 7)
    await user_service._celebrate_level_up(db, user, 2, 7)
    events = await journey_repository.list_for_user(db, "u5")
    assert len([e for e in events if e.event_kind == "level_reward"]) == 1


async def test_celebration_message_mentions_reward_without_duplicating_features(
    db, make_user, time_travel
):
    """庆祝消息同时讲清功能解锁与情感回报，且回报不因合并进 unlocks 而被重复列出。"""
    time_travel.set(datetime(2026, 8, 5, 3, 0, tzinfo=timezone.utc))
    await make_user(
        "u6",
        language="en",
        timezone=TZ,
        last_active_date=local_today(TZ) - timedelta(days=1),
        streak_count=6,
        active_days=6,
        streak_freeze_count=1,
        streak_freeze_grant_month=_month(),
    )
    result = await user_service.update_streak(db, "u6")
    message = result["level_up"]["message"]
    assert "AI brings up something you once said" in message
    assert "Proactive AI follow-ups" in message
    # 回报只出现在专门的句子中，不混进 "Newly unlocked:" 列表
    assert message.count("AI brings up something you once said") == 1
    assert "Newly unlocked: Proactive AI follow-ups, Memory references." in message


async def test_level_detail_exposes_reward_per_level(db, make_user):
    """等级页逐档返回 reward 文案（L1 为 None），前端据此渲染"关系上得到了什么"。"""
    await make_user("u7", active_days=31, timezone=TZ)
    detail = await user_service.get_level_detail(db, "u7", "zh")
    assert [row["reward"] for row in detail["levels"]] == [
        None,
        "AI 第一次提起你说过的事",
        "首份 Monthly Story 预览",
        "一封 90 天的信：这 90 天我看到的你",
    ]
    assert detail["current"]["reward"] == "首份 Monthly Story 预览"

    detail_en = await user_service.get_level_detail(db, "u7", "en")
    assert detail_en["levels"][3]["reward"] == "A 90-day letter: the you I've seen"

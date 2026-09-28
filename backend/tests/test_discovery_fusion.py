"""P0-F10 Daily Discovery 融入主动消息：价值分级 + 融合规则 + 疲劳降级 + Day1-2 跳过。

覆盖：
- discovery_service._compare 的 priority 分级（数据变化幅度 / 新发现模式 / 里程碑）
- generate_discovery 返回 DiscoveryResult（content + priority）
- message_generator 融合规则：high 开头 / medium 中段 / low 跳过且不计层数
- 连续 3 天高价值发现自动降级 medium（Redis 滑动窗口，fail-open）
- 新用户 Day 1-2 强制无 Discovery
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.core import redis as redis_client
from backend.repositories import chat_repository, memory_repository, mood_repository
from backend.services import message_generator as mg_module
from backend.services.discovery_service import DiscoveryResult, discovery_service
from backend.services.message_generator import message_generator

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clear_redis_mem():
    """Redis 未配置时回落内存 dict（进程级），用例间必须清空防串扰。"""
    redis_client._mem_store.clear()
    yield
    redis_client._mem_store.clear()


async def _checkin(db, user_id: str, emoji: str, days_ago: float):
    """造一条指定时间的签到（覆盖 server_default 的 created_at）。"""
    record = await mood_repository.create(db, user_id, emoji, "标签", "")
    record.created_at = NOW - timedelta(days=days_ago)
    await db.flush()
    return record


def _fake_discovery(monkeypatch, result):
    """替换 message_generator 模块内绑定的 discovery_service 为可控假实现。"""
    fake = SimpleNamespace(
        generate_discovery=lambda db, user_id, language="en": _async_return(result)
    )
    monkeypatch.setattr(mg_module, "discovery_service", fake)


async def _async_return(value):
    return value


# ═══ discovery_service：priority 分级 ═══


async def test_priority_milestone_first_week_recording_is_high(db, make_user, time_travel):
    """里程碑：上周无记录、本周开始记录 → high。"""
    time_travel.set(NOW)
    await make_user("u-ms", timezone="UTC")
    await _checkin(db, "u-ms", "😊", 2)

    result = await discovery_service.generate_discovery(db, "u-ms", "zh")
    assert result is not None
    assert result.priority == "high"
    assert result.comparison_type == "checkin_frequency"
    assert "开始记录" in result.content


async def test_priority_frequency_large_change_is_high(db, make_user, time_travel):
    """数据变化幅度：签到频率 |Δ| ≥ 3 → high。"""
    time_travel.set(NOW)
    await make_user("u-fh", timezone="UTC")
    for i in range(5):
        await _checkin(db, "u-fh", "😌", i * 0.5)
    await _checkin(db, "u-fh", "😌", 9)

    result = await discovery_service.generate_discovery(db, "u-fh", "en")
    assert result is not None
    assert result.priority == "high"
    assert result.comparison_type == "checkin_frequency"


async def test_priority_frequency_medium_change(db, make_user, time_travel):
    """数据变化幅度：签到频率 |Δ| = 2 → medium。"""
    time_travel.set(NOW)
    await make_user("u-fm", timezone="UTC")
    for i in range(3):
        await _checkin(db, "u-fm", "😌", i)
    await _checkin(db, "u-fm", "😌", 8)

    result = await discovery_service.generate_discovery(db, "u-fm", "zh")
    assert result is not None
    assert result.priority == "medium"
    assert result.comparison_type == "checkin_frequency"


async def test_priority_interruption_is_medium(db, make_user, time_travel):
    """记录中断（本周无记录、上周有）→ medium。"""
    time_travel.set(NOW)
    await make_user("u-gap", timezone="UTC")
    await _checkin(db, "u-gap", "😌", 8)
    await _checkin(db, "u-gap", "😌", 10)

    result = await discovery_service.generate_discovery(db, "u-gap", "zh")
    assert result is not None
    assert result.priority == "medium"
    assert "没有再记录" in result.content


async def test_priority_mood_shift_new_pattern_is_high(db, make_user, time_travel):
    """新发现模式：情绪均值显著偏移（≥0.8）→ high（改善与低落双向）。"""
    time_travel.set(NOW)
    await make_user("u-up", timezone="UTC")
    await _checkin(db, "u-up", "😊", 1)  # 5
    await _checkin(db, "u-up", "😊", 2)  # 5
    await _checkin(db, "u-up", "😢", 8)  # 1
    await _checkin(db, "u-up", "😢", 9)  # 1
    result = await discovery_service.generate_discovery(db, "u-up", "zh")
    assert result is not None and result.priority == "high"
    assert result.comparison_type == "mood_improvement"

    await make_user("u-down", timezone="UTC")
    await _checkin(db, "u-down", "😢", 1)
    await _checkin(db, "u-down", "😢", 2)
    await _checkin(db, "u-down", "😊", 8)
    await _checkin(db, "u-down", "😊", 9)
    result = await discovery_service.generate_discovery(db, "u-down", "en")
    assert result is not None and result.priority == "high"
    assert result.comparison_type == "mood_decline"


async def test_priority_mild_mood_drift_is_low(db, make_user, time_travel):
    """数据变化幅度不足：情绪均值轻微漂移（0.4 ≤ |Δ| < 0.8）→ low。"""
    time_travel.set(NOW)
    await make_user("u-mild", timezone="UTC")
    await _checkin(db, "u-mild", "😌", 1)  # 4
    await _checkin(db, "u-mild", "😴", 2)  # 3 → 本周均值 3.5
    await _checkin(db, "u-mild", "😴", 8)  # 3
    await _checkin(db, "u-mild", "😴", 9)  # 3 → 上周均值 3.0（Δ = 0.5）

    result = await discovery_service.generate_discovery(db, "u-mild", "zh")
    assert result is not None
    assert result.priority == "low"
    assert result.comparison_type == "mood_improvement"


async def test_no_significant_change_returns_none(db, make_user, time_travel):
    """两周数据几乎一致 → 无发现（None）。"""
    time_travel.set(NOW)
    await make_user("u-flat", timezone="UTC")
    await _checkin(db, "u-flat", "😌", 1)
    await _checkin(db, "u-flat", "😌", 8)

    assert await discovery_service.generate_discovery(db, "u-flat", "zh") is None


# ═══ message_generator：融合规则 ═══


async def _proactive_setup(db, make_user, monkeypatch, time_travel, discovery, user_id="u-f", tier="free"):
    """造一个老用户 + 记忆 + 对话历史（①④ 层），Discovery 由 fake 控制。"""
    time_travel.set(NOW)
    await make_user(
        user_id, timezone="UTC", tier=tier,
        created_at=NOW - timedelta(days=10),
    )
    await memory_repository.create(db, user_id, "项目压力大", "goal", "chat", 0.8)
    await chat_repository.create(db, user_id, "user", "昨天聊到项目截止日临近")
    _fake_discovery(monkeypatch, discovery)
    return user_id


async def test_fusion_high_priority_leads_message(db, make_user, monkeypatch, time_travel):
    """high：免费用户消息以 Discovery 开头（发现即开场）。"""
    discovery = DiscoveryResult("你这周记录了5次心情，上周是1次——最近生活有变化吗？", "checkin_frequency", "high")
    uid = await _proactive_setup(db, make_user, monkeypatch, time_travel, discovery)

    msg = await message_generator.generate_proactive_message(db, uid, "zh")
    assert msg is not None
    assert msg.startswith(discovery.content)


async def test_fusion_medium_priority_sits_in_middle(db, make_user, monkeypatch, time_travel):
    """medium：先记忆开场，Discovery 融入中段（不出现在开头）。"""
    discovery = DiscoveryResult("这一周你记录了 3 次心情，比上周更多。", "checkin_frequency", "medium")
    uid = await _proactive_setup(db, make_user, monkeypatch, time_travel, discovery)

    msg = await message_generator.generate_proactive_message(db, uid, "zh")
    assert msg is not None
    assert discovery.content in msg
    assert not msg.startswith(discovery.content)
    assert msg.index("项目压力大") < msg.index(discovery.content)


async def test_fusion_low_priority_is_skipped(db, make_user, monkeypatch, time_travel):
    """low：跳过 Discovery，消息用①④层补足（不含发现内容）。"""
    discovery = DiscoveryResult("这一周的心情比上周略有起色。", "mood_improvement", "low")
    uid = await _proactive_setup(db, make_user, monkeypatch, time_travel, discovery)

    msg = await message_generator.generate_proactive_message(db, uid, "zh")
    assert msg is not None
    assert discovery.content not in msg
    assert "项目压力大" in msg  # 由①层记忆补足


async def test_fusion_low_priority_not_counted_as_layer(db, make_user, monkeypatch, time_travel):
    """low 不计入四层命中数：仅 low 发现 + ④历史 = 1 层 → 当日静默。"""
    discovery = DiscoveryResult("这一周的心情比上周略有起色。", "mood_improvement", "low")
    time_travel.set(NOW)
    await make_user("u-silent", timezone="UTC", created_at=NOW - timedelta(days=10))
    await chat_repository.create(db, "u-silent", "user", "随便聊聊")
    _fake_discovery(monkeypatch, discovery)

    msg = await message_generator.generate_proactive_message(db, "u-silent", "zh")
    assert msg is None


async def test_fusion_day2_user_has_no_discovery(db, make_user, monkeypatch, time_travel):
    """新用户 Day 1-2（注册 30h、已有对话）强制无 Discovery，即使发现有 high 价值。"""
    discovery = DiscoveryResult("高价值发现内容", "mood_improvement", "high")
    time_travel.set(NOW)
    await make_user("u-d2", timezone="UTC", created_at=NOW - timedelta(hours=30))
    await memory_repository.create(db, "u-d2", "喜欢独处", "preference", "chat", 0.8)
    await chat_repository.create(db, "u-d2", "user", "第一天聊了挺多")
    _fake_discovery(monkeypatch, discovery)

    msg = await message_generator.generate_proactive_message(db, "u-d2", "zh")
    assert msg is not None
    assert discovery.content not in msg


def _gen_prompts(mock_llm) -> list:
    """提取主动消息生成 prompt（过滤掉 L3 安全检查的分类器调用）。"""
    return [c["prompt"] for c in mock_llm.calls["complete"] if "数据洞察" in c["prompt"]]


async def test_fusion_pro_prompt_carries_placement_hint(db, make_user, monkeypatch, time_travel, mock_llm):
    """Pro 用户：high/medium 的融合位置指令注入 LLM prompt；low 不注入发现内容。"""
    discovery = DiscoveryResult("高价值发现", "mood_improvement", "high")
    uid = await _proactive_setup(db, make_user, monkeypatch, time_travel, discovery, user_id="u-pro", tier="pro")
    await message_generator.generate_proactive_message(db, uid, "zh")
    prompt = _gen_prompts(mock_llm)[-1]
    assert "作为消息开头" in prompt and "高价值发现" in prompt

    discovery = DiscoveryResult("中价值发现", "checkin_frequency", "medium")
    _fake_discovery(monkeypatch, discovery)
    await message_generator.generate_proactive_message(db, uid, "zh")
    prompt = _gen_prompts(mock_llm)[-1]
    assert "融入消息中段" in prompt and "中价值发现" in prompt

    discovery = DiscoveryResult("低价值发现", "mood_improvement", "low")
    _fake_discovery(monkeypatch, discovery)
    await message_generator.generate_proactive_message(db, uid, "zh")
    prompt = _gen_prompts(mock_llm)[-1]
    assert "低价值发现" not in prompt


# ═══ 疲劳控制：连续 3 天 high 自动降级 ═══


async def test_fatigue_third_consecutive_high_downgrades_to_medium(db, make_user, monkeypatch, time_travel):
    """连续第 3 天高价值发现：high → medium（记忆开场、发现居中）。"""
    discovery = DiscoveryResult("连续高价值发现", "mood_improvement", "high")
    uid = await _proactive_setup(db, make_user, monkeypatch, time_travel, discovery, user_id="u-fatigue")

    # 模拟已连续 2 天 high
    await redis_client.setex(f"discovery:high_streak:{uid}", 48 * 3600, "2")

    msg = await message_generator.generate_proactive_message(db, uid, "zh")
    assert msg is not None
    assert not msg.startswith(discovery.content)  # 被降级为 medium：不以发现开头
    assert discovery.content in msg
    assert await redis_client.get(f"discovery:high_streak:{uid}") == "3"


async def test_fatigue_first_two_high_days_stay_high(db, make_user, monkeypatch, time_travel):
    """连续前 2 天 high 不降级：仍以发现开头，计数累加。"""
    discovery = DiscoveryResult("高价值发现", "mood_improvement", "high")
    uid = await _proactive_setup(db, make_user, monkeypatch, time_travel, discovery, user_id="u-fresh")

    msg1 = await message_generator.generate_proactive_message(db, uid, "zh")
    assert msg1 is not None and msg1.startswith(discovery.content)
    assert await redis_client.get(f"discovery:high_streak:{uid}") == "1"

    msg2 = await message_generator.generate_proactive_message(db, uid, "zh")
    assert msg2 is not None and msg2.startswith(discovery.content)
    assert await redis_client.get(f"discovery:high_streak:{uid}") == "2"


async def test_fatigue_counter_resets_on_non_high_day(db, make_user, monkeypatch, time_travel):
    """当日发现非 high（medium/low）：连续计数清零，次日 high 重新起算。"""
    uid = await _proactive_setup(
        db, make_user, monkeypatch, time_travel,
        DiscoveryResult("中价值发现", "checkin_frequency", "medium"),
        user_id="u-reset",
    )
    await redis_client.setex(f"discovery:high_streak:{uid}", 48 * 3600, "2")

    await message_generator.generate_proactive_message(db, uid, "zh")
    assert await redis_client.get(f"discovery:high_streak:{uid}") is None

    # 次日再度 high：从 1 重新计数，不降级
    _fake_discovery(monkeypatch, DiscoveryResult("再度高价值", "mood_improvement", "high"))
    msg = await message_generator.generate_proactive_message(db, uid, "zh")
    assert msg is not None and msg.startswith("再度高价值")
    assert await redis_client.get(f"discovery:high_streak:{uid}") == "1"

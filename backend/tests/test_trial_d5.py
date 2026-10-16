"""F2 · PRD §5.21 试用 D5 资产盘点触点（trial_d5 journey 事件）。

覆盖四组验收口径：
1. **预写**：iap verify 判定试用收据（is_trial）→ journey 预写一条 trial_d5，
   落在试用开始日 +5 天的本地推送窗口；幂等（Apple intro offer 一人一次）；
   时刻已过（恢复购买时试用已过第 5 天）不追溯补发。
2. **渲染**：{n} 条记录 / {m} 个情绪模式全部来自真实数据统计（零 LLM、确定性）；
   无签到数据回退纯盘点；无任何记录走 plain。
3. **频控**：trial_d5 与剧本/主动消息同链路——同日已有触达时顺延不删行。
4. **文案**：24h 到期提醒改盘点式正向损失（提"留着/继续生长"，不催续订）。
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.core.copy import SUPPORTED_LOCALES
from backend.models.journey import JOURNEY_EVENT_KINDS
from backend.repositories import (
    chat_repository,
    journey_repository,
    mood_repository,
)
from backend.services.cron_service import _trial_reminder_template
from backend.services.iap_service import iap_service
from backend.services.journey_service import (
    TRIAL_D5_OFFSET,
    journey_service,
    load_template,
)
from backend.ai.safety import safety_service

TZ = "UTC"
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """SQLite DATETIME 丢弃 tzinfo（存的是 UTC wall-clock），读取侧统一补回。"""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _user(db, make_user, user_id="u1", **overrides):
    overrides.setdefault("ai_consent", True)
    overrides.setdefault("language", "zh")
    overrides.setdefault("timezone", TZ)
    overrides.setdefault("push_time", "09:00")
    return await make_user(user_id, **overrides)


# ── 1. 预写 ──────────────────────────────────────────────────────────


async def test_trial_d5_kind_registered():
    """kind 常量登记（模型层白名单，遗漏则仓库/路由全链路失效）。"""
    assert "trial_d5" in JOURNEY_EVENT_KINDS


async def test_schedule_trial_d5_prewrites_at_push_window(db, make_user, time_travel):
    """试用收据 → trial_d5 预写：开始日+5 天的本地推送窗口（push_time）。"""
    time_travel.set(NOW)
    user = await _user(db, make_user)
    # 试用开始 = NOW - 2 天（8/3）→ 第 5 天 = 8/8 09:00，尚未到期
    trial_start = NOW - timedelta(days=2)
    await journey_service.schedule_trial_d5(db, user, trial_start)

    event = await journey_repository.get_by_kind(db, user.id, "trial_d5", TRIAL_D5_OFFSET)
    assert event is not None
    assert event.status == "pending"
    assert _as_utc(event.scheduled_at) == datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc)


async def test_schedule_trial_d5_is_idempotent(db, make_user, time_travel):
    """重复 verify（重装/重试）不产生第二条 trial_d5（intro offer 一人一次）。"""
    time_travel.set(NOW)
    user = await _user(db, make_user)
    trial_start = NOW - timedelta(days=2)
    await journey_service.schedule_trial_d5(db, user, trial_start)
    await journey_service.schedule_trial_d5(db, user, trial_start)

    events = [
        e for e in await journey_repository.list_for_user(db, user.id, limit=50)
        if e.event_kind == "trial_d5"
    ]
    assert len(events) == 1


async def test_schedule_trial_d5_past_moment_not_backfilled(db, make_user, time_travel):
    """试用已过第 5 天（恢复购买等）：时刻已过不追溯补发。"""
    time_travel.set(NOW)
    user = await _user(db, make_user)
    # 开始日 = NOW - 6 天 → 第 5 天 = 昨天，已过
    await journey_service.schedule_trial_d5(db, user, NOW - timedelta(days=6))

    event = await journey_repository.get_by_kind(db, user.id, "trial_d5", TRIAL_D5_OFFSET)
    assert event is None


async def test_iap_touchpoint_written_only_for_trial(db, make_user, time_travel):
    """iap 触点预写：试用收据写、非试用不写、无过期时间不写。"""
    time_travel.set(NOW)
    user = await _user(db, make_user)

    await iap_service._schedule_trial_touchpoint(db, user.id, NOW + timedelta(days=7))
    event = await journey_repository.get_by_kind(db, user.id, "trial_d5", TRIAL_D5_OFFSET)
    assert event is not None

    await iap_service._schedule_trial_touchpoint(db, "u2", NOW + timedelta(days=7))
    assert await journey_repository.get_by_kind(db, "u2", "trial_d5", TRIAL_D5_OFFSET) is None

    await iap_service._schedule_trial_touchpoint(db, user.id, None)
    events = [
        e for e in await journey_repository.list_for_user(db, user.id, limit=50)
        if e.event_kind == "trial_d5"
    ]
    assert len(events) == 1


# ── 2. 渲染 ──────────────────────────────────────────────────────────


def _event(kind="trial_d5"):
    return SimpleNamespace(event_kind=kind, day_no=TRIAL_D5_OFFSET)


async def test_render_counts_real_data_zh(db, make_user, time_travel):
    """/{m}/情绪模式来自真实签到与记录统计；zh 用户中文标签。"""
    time_travel.set(NOW)
    user = await _user(db, make_user)
    await mood_repository.create(db, user.id, "😌", "平静", "解读", checkin_date=NOW.date())
    await mood_repository.create(
        db, user.id, "😰", "焦虑", "解读", checkin_date=(NOW - timedelta(days=1)).date()
    )
    await chat_repository.create(db, user.id, "user", "今天有点乱")

    rendered = await journey_service.render(db, _event(), user)
    assert rendered is not None
    # entries 口径 = 用户消息 + 签到（2 签到 + 1 消息 = 3）
    assert "3 条记录" in rendered.text
    assert "2 个" in rendered.text
    assert "平静" in rendered.text and "焦虑" in rendered.text
    # 盘点式正向措辞：不出现到期/续订恐吓词
    assert "到期" not in rendered.text and "续订" not in rendered.text
    assert rendered.stats["entries"] == 3
    assert rendered.stats["mood_patterns"] == 2


async def test_render_en_uses_english_labels(db, make_user, time_travel):
    time_travel.set(NOW)
    user = await _user(db, make_user, language="en")
    await mood_repository.create(db, user.id, "😌", "calm", "note", checkin_date=NOW.date())

    rendered = await journey_service.render(db, _event(), user)
    assert rendered is not None
    assert "Calm" in rendered.text


async def test_render_falls_back_to_plain_when_no_data(db, make_user, time_travel):
    """零记录用户不编造数字：走 plain 盘点模板。"""
    time_travel.set(NOW)
    user = await _user(db, make_user)

    rendered = await journey_service.render(db, _event(), user)
    assert rendered is not None
    assert rendered.text == load_template("zh")["trial_d5"]["plain"]


async def test_trial_d5_templates_pass_safety_gate():
    """模板库逐条过闸（含 trial_d5 新段），且正向措辞不含恐吓词。"""
    for locale in ("zh", "en"):
        section = load_template(locale)["trial_d5"]
        assert {"with_moods", "with_stats", "plain"} <= set(section)
        for key, text in section.items():
            if key.startswith("_"):
                continue  # 注释字段不进渲染出口
            assert safety_service.filter_template(text, locale), text


# ── 3. 频控 ──────────────────────────────────────────────────────────


async def test_deliver_defers_when_day_already_taken(db, make_user, time_travel):
    """同日已有剧本触达 → trial_d5 顺延（永不删行），与剧本/主动消息同链路。"""
    time_travel.set(NOW)
    user = await _user(db, make_user)
    await journey_service.schedule_trial_d5(db, user, NOW - timedelta(days=2))
    event = await journey_repository.get_by_kind(db, user.id, "trial_d5", TRIAL_D5_OFFSET)
    # 8/8 才到期，把计划时刻拨到 NOW 使其进入本轮扫描
    await journey_repository.defer(db, event.id, NOW)

    other = await journey_repository.schedule(
        db, user_id=user.id, event_kind="day5_stat", day_no=5, scheduled_at=NOW
    )
    await journey_repository.mark_sent(db, other.id, NOW)

    result = await journey_service.deliver(db, event.id)
    assert result == "deferred"
    reloaded = await journey_repository.get(db, event.id)
    assert reloaded.status == "pending"
    assert _as_utc(reloaded.scheduled_at) == NOW + timedelta(days=1)


# ── 4. 24h 到期提醒：盘点式正向损失 ─────────────────────────────────


def test_trial_reminder_copy_is_inventory_style():
    zh = _trial_reminder_template("zh")
    en = _trial_reminder_template("en")
    # 正向损失：说数据留着、继续生长，不说"续订即可享受"式交易话术
    assert "留着" in zh and "继续生长" in zh
    assert "keep growing" in en
    for loc in SUPPORTED_LOCALES:
        text = _trial_reminder_template(loc)
        assert "续订即可" not in text
        assert safety_service.filter_template(text, "en")

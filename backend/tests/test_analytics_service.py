"""analytics_service：consent 校验 + 匿名化 + PostHog 中继。"""
from datetime import datetime, timezone

import pytest

from backend.models.schemas import AnalyticsEvent
from backend.services import analytics_service as analytics_module
from backend.services.analytics_service import analytics_service


def _ev(event="screen_view", properties=None, occurred_at=None):
    return AnalyticsEvent(
        event=event,
        properties=properties,
        occurred_at=occurred_at or datetime(2026, 8, 7, 1, 30, tzinfo=timezone.utc),
    )


def _capture_stub(monkeypatch):
    """替换 _relay_to_posthog 为桩，记录调用。"""
    calls = []
    monkeypatch.setattr(
        analytics_module,
        "_relay_to_posthog",
        lambda distinct_id, event, properties, occurred_at: calls.append(
            {
                "distinct_id": distinct_id,
                "event": event,
                "properties": properties,
                "occurred_at": occurred_at,
            }
        ),
    )
    return calls


async def test_relay_batch_drops_all_without_consent(db, make_user, monkeypatch):
    """未同意 analytics_consent → 整批丢弃，不触发中继。"""
    await make_user("u1", analytics_consent=False)
    calls = _capture_stub(monkeypatch)
    result = await analytics_service.relay_batch(
        db, "u1", [_ev("screen_view"), _ev("checkin")]
    )
    assert result == {"relayed": 0, "dropped": 2}
    assert calls == []


async def test_relay_batch_drops_all_for_unknown_user(db, monkeypatch):
    """用户不存在 → 整批丢弃，不触发中继。"""
    calls = _capture_stub(monkeypatch)
    result = await analytics_service.relay_batch(
        db, "u-missing", [_ev("screen_view")]
    )
    assert result == {"relayed": 0, "dropped": 1}
    assert calls == []


async def test_relay_batch_relays_with_consent(db, make_user, monkeypatch):
    """已同意 consent → 全部中继，distinct_id 为 user_id，occurred_at 透传真实时间。"""
    await make_user("u1", analytics_consent=True)
    calls = _capture_stub(monkeypatch)
    ts = datetime(2026, 8, 7, 1, 30, tzinfo=timezone.utc)
    result = await analytics_service.relay_batch(
        db, "u1", [_ev("screen_view", {"screen": "Home"}, ts)]
    )
    assert result == {"relayed": 1, "dropped": 0}
    assert len(calls) == 1
    assert calls[0]["distinct_id"] == "u1"
    assert calls[0]["event"] == "screen_view"
    assert calls[0]["occurred_at"] == ts


async def test_anonymize_strips_content_fields(db, make_user, monkeypatch):
    """匿名化：白名单外的内容字段（反思/记忆/对话原文/情绪）一律丢弃。"""
    await make_user("u1", analytics_consent=True)
    calls = _capture_stub(monkeypatch)
    await analytics_service.relay_batch(
        db,
        "u1",
        [
            _ev(
                "reflection_saved",
                {
                    "feature": "reflection",
                    "tier": "free",
                    "duration_ms": 1200,
                    "reflection_text": "今天我很焦虑……",
                    "memory_content": "私人记忆",
                    "mood_emoji": "😢",
                    "message": "AI 原始回复内容",
                    "email": "user@example.com",
                },
            )
        ],
    )
    assert calls[0]["properties"] == {
        "feature": "reflection",
        "tier": "free",
        "duration_ms": 1200,
    }


async def test_anonymize_drops_none_values(db, make_user, monkeypatch):
    """值为 None 的属性同样剔除。"""
    await make_user("u1", analytics_consent=True)
    calls = _capture_stub(monkeypatch)
    await analytics_service.relay_batch(
        db, "u1", [_ev("screen_view", {"screen": None, "tier": "pro"})]
    )
    assert calls[0]["properties"] == {"tier": "pro"}


async def test_anonymize_empty_properties(db, make_user, monkeypatch):
    """properties 为空/None → 中继空属性 dict。"""
    await make_user("u1", analytics_consent=True)
    calls = _capture_stub(monkeypatch)
    await analytics_service.relay_batch(db, "u1", [_ev("checkin", None)])
    assert calls[0]["properties"] == {}

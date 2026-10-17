"""analytics_service：consent 校验 + 匿名化 + PostHog 中继。"""
import sys
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
    """替换 _relay_to_posthog 为桩，记录调用并回报「这条送出去了」。

    D-G18（2026-10-17）之后，**返回值就是 relayed/dropped 的唯一依据**，所以桩必须回
    True：这条链上被测的是「已同意 + 中继成功 ⇒ 计数如实」，桩回 None（append 的返回值）
    会把整批算成 dropped，上面那些 relayed 断言立刻红。
    """
    calls = []

    def _stub(distinct_id, event, properties, occurred_at):
        calls.append(
            {
                "distinct_id": distinct_id,
                "event": event,
                "properties": properties,
                "occurred_at": occurred_at,
            }
        )
        return True

    monkeypatch.setattr(analytics_module, "_relay_to_posthog", _stub)
    return calls


def _result_stub(monkeypatch, results):
    """按调用次序决定每条到底送没送出去；results 短于事件数即红。

    这条断言不是保险丝好看：`relayed + dropped == len(events)` 这个不变量只有在
    「每条事件都被试过」时才有意义，桩少回一次就等于少测一条。
    """
    seen = []

    def _stub(distinct_id, event, properties, occurred_at):
        assert len(seen) < len(results), (
            f"桩被调第 {len(seen) + 1} 次，但只给了 {len(results)} 个返回值"
            "⇒ 调用次数与事件数没对上"
        )
        seen.append(event)
        return results[len(seen) - 1]

    monkeypatch.setattr(analytics_module, "_relay_to_posthog", _stub)
    return seen


class _FakePosthog:
    """假 posthog 模块：记 capture 调用，可按需抛错。

    为什么用它而不是直接用真库：本机能装/不装 posthog 都不是这条闸要证的东西，
    「SDK 收下 / SDK 抛错」两种结果必须由测试自己造（见下面三条 _relay_to_posthog 直测）。
    """

    def __init__(self, *, raises=False):
        self.project_api_key = None
        self.host = None
        self.captured = []
        self._raises = raises

    def capture(self, **kwargs):
        if self._raises:
            raise RuntimeError("capture 端点 502")
        self.captured.append(kwargs)


def _use_fake_posthog(monkeypatch, *, raises=False, api_key="phk_test"):
    fake = _FakePosthog(raises=raises)
    # sys.modules 里给了这个对象，`import posthog` 就直接拿它，不会真去装包
    monkeypatch.setitem(sys.modules, "posthog", fake)
    monkeypatch.setattr(analytics_module.settings, "posthog_api_key", api_key)
    return fake


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


# ── D-G18：计数只认真正送出去的那几条 ──────────────────────────────────────────


async def test_relay_batch_counts_only_the_events_that_left(db, make_user, monkeypatch):
    """部分成功：三条里两条真送出去了 ⇒ relayed=2 / dropped=1，而不是全算成功。

    改造前这里必然是 `{"relayed": 3, "dropped": 0}`——那三条里没送出去的两条
    既不在分析平台、也已被前端当成 synced 清掉，事后无任何痕迹。
    """
    await make_user("u1", analytics_consent=True)
    seen = _result_stub(monkeypatch, [True, False, True])
    result = await analytics_service.relay_batch(
        db, "u1", [_ev("a"), _ev("b"), _ev("c")]
    )
    assert result == {"relayed": 2, "dropped": 1}
    assert seen == ["a", "b", "c"]


async def test_relay_batch_never_invents_a_relay(db, make_user, monkeypatch):
    """全失败 ≠ 全成功：一条都没送出去时 relayed 必须是 0，但每条都确实试过。"""
    await make_user("u1", analytics_consent=True)
    seen = _result_stub(monkeypatch, [False, False])
    result = await analytics_service.relay_batch(db, "u1", [_ev("a"), _ev("b")])
    assert result == {"relayed": 0, "dropped": 2}
    assert len(seen) == 2


async def test_relay_batch_without_posthog_reports_nothing_relayed(db, make_user, monkeypatch):
    """未安装 posthog（本机实测就没装）→ 整批落 dropped，但仍回 200 不阻塞主流程。

    这条是 D-G18 的正面证人，且**不打桩** `_relay_to_posthog`：走的是真的
    `import posthog` 失败分支。sys.modules 里放 None 让 import 必然抛 ImportError，
    所以这条不依赖本机到底装没装这个包。
    """
    await make_user("u1", analytics_consent=True)
    monkeypatch.setitem(sys.modules, "posthog", None)
    result = await analytics_service.relay_batch(
        db, "u1", [_ev("a"), _ev("b"), _ev("c")]
    )
    assert result == {"relayed": 0, "dropped": 3}
    assert result["relayed"] + result["dropped"] == 3


def test_relay_returns_false_when_posthog_missing(monkeypatch):
    """SDK 没装：False（＝没送出去），不是静默 no-op。"""
    monkeypatch.setitem(sys.modules, "posthog", None)
    assert analytics_module._relay_to_posthog("u1", "e", {}, _ev().occurred_at) is False


def test_relay_returns_false_without_api_key(monkeypatch):
    """装了 SDK 但没配 key：同样 False，而且一次 capture 都不该发。"""
    fake = _use_fake_posthog(monkeypatch, api_key="")
    assert analytics_module._relay_to_posthog("u1", "e", {}, _ev().occurred_at) is False
    assert fake.captured == []


def test_relay_returns_false_when_capture_raises(monkeypatch):
    """capture 抛异常：不阻塞主流程，但不算送出去。"""
    _use_fake_posthog(monkeypatch, raises=True)
    assert analytics_module._relay_to_posthog("u1", "e", {"screen": "Home"},
                                              _ev().occurred_at) is False


def test_relay_returns_true_when_capture_succeeds(monkeypatch):
    """正向对照：这一支必须是 True，否则上面三条红得没有意义。

    顺带钉住两件事——配置在每次调用时落到 SDK（posthog-python 的模块级全局），
    以及 occurred_at 是以 `timestamp=` 传给 capture 的真实发生时间。
    """
    ts = datetime(2026, 8, 7, 1, 30, tzinfo=timezone.utc)
    fake = _use_fake_posthog(monkeypatch, raises=False)
    assert analytics_module._relay_to_posthog("u1", "checkin", {"tier": "free"}, ts) is True
    assert fake.captured == [
        {"distinct_id": "u1", "event": "checkin", "properties": {"tier": "free"},
         "timestamp": ts}
    ]
    assert fake.project_api_key == "phk_test"

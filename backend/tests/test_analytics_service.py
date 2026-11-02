"""analytics_service：consent 校验 + 匿名化 + PostHog 中继。"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.models.analytics import AnalyticsEventReceipt
from backend.models.schemas import AnalyticsEvent
from backend.services import analytics_service as analytics_module
from backend.services.analytics_service import analytics_service

# 三侧（Python / Go / React Native）共读同一份逐事件确认用例。
ACK_CASES_PATH = (
    Path(__file__).resolve().parents[2] / "contracts" / "analytics_ack_cases.json"
)


def _ack_cases():
    doc = json.loads(ACK_CASES_PATH.read_text(encoding="utf-8"))
    return doc["cases"]


def _ev(event="screen_view", properties=None, occurred_at=None, event_id=None):
    return AnalyticsEvent(
        event=event,
        properties=properties,
        occurred_at=occurred_at or datetime(2026, 8, 7, 1, 30, tzinfo=timezone.utc),
        event_id=event_id,
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


# ── T7-02：逐事件确认 + event_id 幂等 ─────────────────────────────────────────


def _statuses(result):
    return [(item["event_id"], item["status"]) for item in result["results"]]


async def test_ack_mode_mixed_statuses_and_counts(db, make_user, monkeypatch):
    """同一批 accepted / retryable 混合：返回逐条结果与分项计数，且总数守恒。"""
    await make_user("u1", analytics_consent=True)
    seen = _result_stub(monkeypatch, [True, False])
    result = await analytics_service.relay_batch(
        db,
        "u1",
        [_ev("a", event_id="e-a"), _ev("b", event_id="e-b")],
    )
    assert seen == ["a", "b"]
    assert _statuses(result) == [
        ("e-a", "accepted"),
        ("e-b", "retryable"),
    ]
    assert result["relayed"] == result["accepted"] == 1
    assert result["retryable"] == 1 and result["permanent"] == 0
    assert result["dropped"] == 1
    assert result["accepted"] + result["duplicate"] + result["dropped"] == 2
    # 失败那条的认领行必须退掉，否则下一次重试永远撞 in_flight。
    from backend.models.analytics import AnalyticsEventReceipt
    from sqlalchemy import select
    rows = (await db.execute(select(AnalyticsEventReceipt))).scalars().all()
    assert [(r.event_id, r.status) for r in rows] == [("e-a", "accepted")]


async def test_ack_mode_duplicate_does_not_relay_twice(db, make_user, monkeypatch):
    """重复提交同一个 event_id：第二次落 duplicate，分析平台只收到一次。"""
    await make_user("u1", analytics_consent=True)
    calls = _capture_stub(monkeypatch)
    first = await analytics_service.relay_batch(
        db, "u1", [_ev("a", event_id="e-a")]
    )
    second = await analytics_service.relay_batch(
        db, "u1", [_ev("a", event_id="e-a")]
    )
    assert first["results"] == [{"event_id": "e-a", "status": "accepted"}]
    assert second["results"] == [{"event_id": "e-a", "status": "duplicate"}]
    assert first["accepted"] == 1 and second["duplicate"] == 1
    assert len(calls) == 1


async def test_ack_mode_retry_after_relay_failure_is_not_a_duplicate(db, make_user, monkeypatch):
    """可重试失败留下的不是「已接受」：下一次必须真的重发，而不是被幂等键挡成 duplicate。"""
    await make_user("u1", analytics_consent=True)
    seen = _result_stub(monkeypatch, [False, True])
    first = await analytics_service.relay_batch(
        db, "u1", [_ev("a", event_id="e-a")]
    )
    second = await analytics_service.relay_batch(
        db, "u1", [_ev("a", event_id="e-a")]
    )
    assert first["results"][0]["status"] == "retryable"
    assert first["results"][0]["reason"] == "relay_failed"
    assert second["results"][0]["status"] == "accepted"
    assert seen == ["a", "a"]


async def test_ack_mode_duplicate_within_one_batch_relays_once(db, make_user, monkeypatch):
    """同一批里同一 event_id 出现两次：第二条 duplicate，不发起第二次外发。"""
    await make_user("u1", analytics_consent=True)
    calls = _capture_stub(monkeypatch)
    result = await analytics_service.relay_batch(
        db, "u1", [_ev("a", event_id="e-a"), _ev("a", event_id="e-a")]
    )
    assert [item["status"] for item in result["results"]] == ["accepted", "duplicate"]
    assert len(calls) == 1


async def test_ack_mode_in_batch_duplicate_relays_once_even_after_failure(
    db, make_user, monkeypatch
):
    """首条中继失败也不许让同批的第二条再发一次。

    这是「同批去重」真正的靶：首条成功时，台账已经是 accepted，第二条就算漏过去
    也会被 claim 判成 duplicate；只有首条失败（认领行被 release）时才分得清——
    去掉 seen_in_batch 检查，第二条会重新认领并再外发一次，同一事件翻倍。
    """
    await make_user("u1", analytics_consent=True)
    calls = []

    def _stub(distinct_id, event, properties, occurred_at):
        calls.append(event)
        return False

    monkeypatch.setattr(analytics_module, "_relay_to_posthog", _stub)
    result = await analytics_service.relay_batch(
        db, "u1", [_ev("a", event_id="e-a"), _ev("a", event_id="e-a")]
    )
    assert [item["status"] for item in result["results"]] == ["retryable", "duplicate"]
    assert len(calls) == 1


async def test_ack_mode_permanent_reasons(db, make_user, monkeypatch):
    """consent 关 / 用户不存在 / event_id 无效 ⇒ 逐条 permanent，带机器码 reason。"""
    calls = _capture_stub(monkeypatch)

    await make_user("u1", analytics_consent=False)
    result = await analytics_service.relay_batch(
        db, "u1", [_ev("a", event_id="e-a")]
    )
    assert result["results"] == [
        {"event_id": "e-a", "status": "permanent", "reason": "consent_off"}
    ]
    assert result["permanent"] == 1 and result["dropped"] == 1

    result = await analytics_service.relay_batch(
        db, "u-missing", [_ev("a", event_id="e-a")]
    )
    assert result["results"][0]["reason"] == "user_not_found"

    await make_user("u2", analytics_consent=True)
    result = await analytics_service.relay_batch(
        db,
        "u2",
        [
            _ev("a", event_id=""),
            _ev("a", event_id=123),
            _ev("a", event_id="x" * 129),
        ],
    )
    assert [item["reason"] for item in result["results"]] == [
        "invalid_event_id", "invalid_event_id", "invalid_event_id"
    ]
    assert calls == []


async def test_ack_mode_null_event_id_only_stays_legacy(db, make_user, monkeypatch):
    """event_id 全是 None（缺省/null）⇒ 仍是旧形状，与「整批没这个键」同义。"""
    await make_user("u1", analytics_consent=True)
    _capture_stub(monkeypatch)
    result = await analytics_service.relay_batch(
        db, "u1", [_ev("a"), _ev("b", event_id=None)]
    )
    assert result == {"relayed": 2, "dropped": 0}


async def test_ack_mode_keeps_property_whitelist(db, make_user, monkeypatch):
    """逐事件模式不得顺手放宽匿名化：内容字段仍然出不了网。"""
    await make_user("u1", analytics_consent=True)
    calls = _capture_stub(monkeypatch)
    await analytics_service.relay_batch(
        db,
        "u1",
        [_ev("checkin", {"screen": "home", "reflection_text": "隐私正文"},
             event_id="e-a")],
    )
    assert calls[0]["properties"] == {"screen": "home"}


# ── 跨层共享契约用例（contracts/analytics_ack_cases.json）─────────────────────


@pytest.mark.parametrize("case", _ack_cases(), ids=lambda c: c["name"])
async def test_shared_ack_contract_cases(case, db, make_user, monkeypatch):
    """服务端响应必须逐条命中共享用例，且内容字段一次都不许外发。

    同一份文件的 `expected.local` 由 frontend/__tests__/analyticsAckContract.test.ts
    消费，`expected.results` 由 go-core 的解析测试消费。三侧读同一份期望，
    「前后端各自觉得自己对」才会被这份文件戳穿。
    """
    if case["user_found"]:
        await make_user("u1", analytics_consent=case["consent"])
    for event_id, status in case["prior"].items():
        db.add(AnalyticsEventReceipt(user_id="u1", event_id=event_id, status=status))
    await db.flush()

    attempts = []
    failing = set(case["relay_fail"])

    def _stub(distinct_id, event, properties, occurred_at):
        ok = event not in failing
        attempts.append({"event": event, "properties": properties, "ok": ok})
        return ok

    monkeypatch.setattr(analytics_module, "_relay_to_posthog", _stub)

    events = [
        _ev(
            raw["event"],
            raw.get("properties"),
            datetime.fromisoformat(raw["occurred_at"]),
            raw.get("event_id"),
        )
        for raw in case["events"]
    ]
    result = await analytics_service.relay_batch(db, "u1", events)

    expected = case["expected"]
    assert result["results"] == expected["results"]
    assert {key: result[key] for key in expected["counts"]} == expected["counts"]
    # 守恒：任何一条都恰好落进 accepted / duplicate / dropped 之一。
    assert result["accepted"] + result["duplicate"] + result["dropped"] == len(events)

    relayed_attempts = [a for a in attempts if a["ok"]]
    accepted = [r for r in expected["results"] if r["status"] == "accepted"]
    assert len(relayed_attempts) == len(accepted)
    if not accepted:
        assert attempts == []
    for attempt in attempts:
        assert attempt["properties"] == {"screen": "home"}, (
            f"{case['name']}: 白名单外的 reflection_text 出网了"
        )


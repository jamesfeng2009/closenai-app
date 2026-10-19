"""匿名行为分析同意（analytics_consent）：端点、撤回落地、与 ai_consent 互不牵连。

背景（三节 · ProfileScreen 数据共享二值 → 四态）：
users 表一直有独立的 analytics_consent 列、后端按它整批丢弃上报，但接口不返回它、
也没有修改入口 —— 界面上的「数据共享」只能显示 ai_consent 一个布尔，
用户既看不见自己同意了几件事，也没有地方撤回分析那一项。

2026-10-18 T5-01：写台账与 PostHog 擦除的真源搬到了 consent_service（旧端点改为代理），
下面的 monkeypatch 点跟着搬——patch 仍打在 user_service 上会静默打空：擦除没被拦住、
`posthog_erased` 恒 False，撤回这条合规判据就再也证不了。
"""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.core.auth import get_current_user
from backend.database import get_db
from backend.models.user import User
from backend.repositories import user_repository
from backend.routers import analytics as analytics_router
from backend.routers import user as user_router
from backend.services import analytics_service as analytics_module
from backend.services import consent_service as consent_service_module
from backend.services.user_service import user_service


@pytest.fixture
def app(db):
    app = FastAPI()
    app.include_router(user_router.router, prefix="/api/v1", tags=["user"])
    app.include_router(analytics_router.router, prefix="/api/v1", tags=["analytics"])

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: "u1"
    return app


async def _request(app, method, path, payload=None):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, json=payload)


async def _analytics_consent(db) -> bool:
    user = await user_repository.get_user(db, "u1")
    return bool(user.analytics_consent)


# ── profile 必须把第二项同意也回出来 ──

async def test_profile_exposes_analytics_consent(app, db, make_user):
    await make_user("u1", analytics_consent=True)
    resp = await _request(app, "GET", "/api/v1/user/profile")
    assert resp.json()["data"]["analytics_consent"] is True


async def test_profile_analytics_consent_defaults_false(app, db, make_user):
    """默认不收：建号未显式同意 → False（不是 None、也不是跟着 ai_consent 走）。"""
    await make_user("u1", ai_consent=True)
    resp = await _request(app, "GET", "/api/v1/user/profile")
    data = resp.json()["data"]
    assert data["analytics_consent"] is False
    assert data["ai_consent"] is True


# ── 设置端点 ──

async def test_set_analytics_consent_on_does_not_erase(app, db, make_user, monkeypatch):
    calls = []
    monkeypatch.setattr(
        consent_service_module, "erase_person_data",
        _make_recording_erase(calls),
    )
    await make_user("u1", analytics_consent=False)

    resp = await _request(app, "POST", "/api/v1/user/analytics-consent", {"consent": True})

    assert resp.json()["data"] == {"analytics_consent": True, "posthog_erased": False}
    assert await _analytics_consent(db) is True
    assert calls == []  # 刚同意就去抹除人是荒谬的


async def test_revoke_analytics_consent_erases_person_data(app, db, make_user, monkeypatch):
    """撤回必须真的把已上报的抹掉：只翻标志位不叫可撤回。"""
    calls = []
    monkeypatch.setattr(
        consent_service_module, "erase_person_data",
        _make_recording_erase(calls, erased=True),
    )
    await make_user("u1", analytics_consent=True)

    resp = await _request(app, "POST", "/api/v1/user/analytics-consent", {"consent": False})

    assert resp.json()["data"] == {"analytics_consent": False, "posthog_erased": True}
    assert calls == ["u1"]
    assert await _analytics_consent(db) is False


async def test_revoke_survives_erase_failure_and_says_so(app, db, make_user, monkeypatch):
    """外部平台没配 key / 删除失败：撤回照样成功，但 posthog_erased=false 如实回。

    界面按这个值决定说「已删除历史数据」还是「已停止上报，历史待删除」，
    不能一律宣称已删除。
    """
    async def pending_erase(distinct_id: str) -> dict:
        return {"erased": False, "reason": "not_configured"}

    monkeypatch.setattr(consent_service_module, "erase_person_data", pending_erase)
    await make_user("u1", analytics_consent=True)

    resp = await _request(app, "POST", "/api/v1/user/analytics-consent", {"consent": False})

    assert resp.json()["code"] == 200
    assert resp.json()["data"] == {"analytics_consent": False, "posthog_erased": False}
    assert await _analytics_consent(db) is False


async def test_revoke_analytics_does_not_touch_ai_consent(app, db, make_user, monkeypatch):
    """两项同意互不牵连：撤分析不能让 AI 功能一起停用（反之亦然）。"""
    monkeypatch.setattr(
        consent_service_module, "erase_person_data",
        _make_recording_erase([]),
    )
    await make_user("u1", ai_consent=True, analytics_consent=True)

    await _request(app, "POST", "/api/v1/user/analytics-consent", {"consent": False})

    user = await user_repository.get_user(db, "u1")
    assert user.ai_consent is True
    assert user.analytics_consent is False


# ── 撤回后的上报链路：整批丢弃（既有 consent 门），且不再中继 ──

async def test_batch_after_revoke_is_dropped(app, db, make_user, monkeypatch):
    await make_user("u1", analytics_consent=True)
    monkeypatch.setattr(
        consent_service_module, "erase_person_data",
        _make_recording_erase([], erased=True),
    )
    await _request(app, "POST", "/api/v1/user/analytics-consent", {"consent": False})

    relayed = []
    # 桩回 True＝「这条真送出去了」：D-G18 之后返回值就是计数依据。这里断的是
    # 「撤回后一条都不该走到中继」，所以桩压根不会被调用——把它写成成功，
    # 是为了让这条闸不会因为「撤回前/撤回后」两种计数口径而假绿。
    monkeypatch.setattr(
        analytics_module, "_relay_to_posthog",
        lambda *a, **k: relayed.append(a) or True,
    )
    resp = await _request(app, "POST", "/api/v1/analytics/batch", {
        "events": [
            {"event": "funnel_first_checkin", "properties": {"feature": "funnel"},
             "occurred_at": "2026-09-29T12:00:00Z"}
        ]
    })

    assert resp.json()["data"] == {"relayed": 0, "dropped": 1}
    assert relayed == []


def _make_recording_erase(sink: list, erased: bool = False):
    async def fake_erase(distinct_id: str) -> dict:
        sink.append(distinct_id)
        return {"erased": erased, "status": 202} if erased else {"erased": False, "reason": "stub"}

    return fake_erase

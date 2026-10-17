"""/analytics/batch 端点：consent 校验 + 统一响应包装。"""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.core.auth import get_current_user
from backend.database import get_db
from backend.routers import analytics as analytics_router
from backend.services import analytics_service as analytics_module


@pytest.fixture
def app(db):
    app = FastAPI()
    app.include_router(analytics_router.router, prefix="/api/v1", tags=["analytics"])

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: "u1"
    return app


async def _post(app, payload):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/v1/analytics/batch", json=payload)


async def test_endpoint_drops_without_consent(app, make_user, monkeypatch):
    """未同意 consent → 整批丢弃（relayed=0），仍返回成功响应。"""
    await make_user("u1", analytics_consent=False)
    calls = []
    # 桩必须回 True（＝这条真送出去了）：D-G18 之后返回值就是 relayed 的唯一依据。
    monkeypatch.setattr(analytics_module, "_relay_to_posthog",
                        lambda *a, **k: calls.append(a) or True)
    resp = await _post(app, {
        "events": [
            {"event": "screen_view", "properties": {"screen": "Home"},
             "occurred_at": "2026-08-07T01:30:00Z"}
        ]
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert body["data"] == {"relayed": 0, "dropped": 1}
    assert calls == []


async def test_endpoint_relays_with_consent(app, make_user, monkeypatch):
    """已同意 consent 且中继成功 → relayed=1。"""
    await make_user("u1", analytics_consent=True)
    calls = []
    monkeypatch.setattr(analytics_module, "_relay_to_posthog",
                        lambda *a, **k: calls.append(a) or True)
    resp = await _post(app, {
        "events": [
            {"event": "checkin", "properties": {"feature": "mood"},
             "occurred_at": "2026-08-07T01:30:00Z"}
        ]
    })
    assert resp.status_code == 200
    assert resp.json()["data"] == {"relayed": 1, "dropped": 0}
    assert len(calls) == 1


async def test_endpoint_reports_failure_as_dropped(app, make_user, monkeypatch):
    """端点面同样不许说谎：SDK 收下失败 ⇒ 200 体里落 dropped，而不是 relayed=1。

    这一条与上面那条互为对照：两条的差别只有桩的返回值，
    所以「计数到底跟不跟发送结果走」在 HTTP 面上被单独钉住（改造前两条给的是同一个体）。
    """
    await make_user("u1", analytics_consent=True)
    calls = []
    monkeypatch.setattr(analytics_module, "_relay_to_posthog",
                        lambda *a, **k: calls.append(a) or False)
    resp = await _post(app, {
        "events": [
            {"event": "checkin", "properties": {"feature": "mood"},
             "occurred_at": "2026-08-07T01:30:00Z"},
            {"event": "screen_view", "properties": {"screen": "Home"},
             "occurred_at": "2026-08-07T01:31:00Z"},
        ]
    })
    assert resp.status_code == 200
    assert resp.json()["data"] == {"relayed": 0, "dropped": 2}
    assert len(calls) == 2

"""T4-02：目标／行动面的 BFF 代理测（六条端点）。

与 `test_v3_proxy.py` 同一口径：这里**只**测转发，不测业务。状态机哪一转移合法、
`title` 多长算超限、`action` 动词认哪三枚，全部在 Go 的 `domain`／`httpapi` 各有闸；
这一面要证明的只有四件事：

1. 每条端点拨到**逐字正确**的内部 path 与方法（goal_id 从 path 来，不从 body 猜）；
2. query 只转白名单，且 `limit=abc` 这种坏值原样带走（校验只发生在 Go）；
3. body 不被拆包——`user_id` 一路带到 Go 才被 `DisallowUnknownFields` 挡掉，
   BFF 若提前"顺手删掉身份键"，ADR-002 那条判据就失去了它的靶；
4. Go 的错误码／HTTP 状态与不可用面都按形状透传。
"""
import httpx
import pytest
from fastapi import FastAPI

from backend.config import settings
from backend.core.auth import get_current_user
from backend.core.errors import ErrorCode
from backend.core.middleware import register_exception_handlers
from backend.routers import v3 as v3_router
from backend.services import v3_proxy

GO_BASE = "http://go-cluster.internal"
GO_TOKEN = "stub-service-token-stub-service-token"


class GoStub:
    def __init__(self, monkeypatch, *, url=GO_BASE, token=GO_TOKEN):
        self.calls: list[httpx.Request] = []
        self.response = httpx.Response(200, json=_envelope({"items": [], "truncated": False}))
        monkeypatch.setattr(settings, "go_api_url", url, raising=False)
        monkeypatch.setattr(settings, "go_service_token", token, raising=False)

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            return self.response

        monkeypatch.setattr(v3_proxy, "_client_factory",
                            lambda timeout: httpx.AsyncClient(
                                transport=httpx.MockTransport(handler), timeout=timeout))


def _envelope(data, code=200, message="success"):
    return {"code": code, "message": message, "data": data,
            "trace_id": "go-trace", "user_id": "go-user"}


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(v3_router.router, prefix="/api/v1", tags=["v3"])
    register_exception_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: "u-owner"
    return app


async def _call(app, method, path, **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://client.test") as client:
        return await client.request(method, path, **kwargs)


# ── 1. 六条端点各拨到逐字正确的目标 ──────────────────────────────────────────

async def test_six_routes_dial_the_six_internal_paths(app, monkeypatch):
    stub = GoStub(monkeypatch)
    await _call(app, "POST", "/api/v1/goals", json={"title": "早睡", "description": ""})
    await _call(app, "GET", "/api/v1/goals")
    await _call(app, "PATCH", "/api/v1/goals/g9", json={"action": "complete"})
    await _call(app, "POST", "/api/v1/goals/g9/actions", json={"title": "提前上床"})
    await _call(app, "GET", "/api/v1/actions")
    await _call(app, "PATCH", "/api/v1/actions/a9", json={"action": "skip"})

    got = [(c.method, c.url.path) for c in stub.calls]
    assert got == [
        ("POST", "/internal/v3/goals"),
        ("GET", "/internal/v3/goals"),
        ("PATCH", "/internal/v3/goals/g9"),
        ("POST", "/internal/v3/goals/g9/actions"),
        ("GET", "/internal/v3/actions"),
        ("PATCH", "/internal/v3/actions/a9"),
    ], f"内部面的 path 或方法变了：{got}"


async def test_goal_id_comes_from_path_not_body(app, monkeypatch):
    # `/goals/{id}/actions` 那一格：path 上的 id 是唯一的目标来源。
    # body 里再给一次 goal_id 会被 Go 的 DisallowUnknownFields 挡成 400，
    # 而 BFF 若在这里"帮忙"把 path 的 id 塞进 body，就制造了一个第二入口。
    stub = GoStub(monkeypatch)
    resp = await _call(app, "POST", "/api/v1/goals/g7/actions", json={"title": "t"})
    assert resp.status_code == 200
    call = stub.calls[0]
    assert call.url.path == "/internal/v3/goals/g7/actions"
    assert b'"goal_id"' not in call.read()


# ── 2. query 白名单与「不校验」 ──────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/api/v1/goals", "/api/v1/actions"])
async def test_list_forwards_status_and_limit_only(app, monkeypatch, path):
    stub = GoStub(monkeypatch)
    await _call(app, "GET", path,
                params={"status": "active", "limit": "20", "cursor": "x", "user_id": "victim"})
    assert stub.calls[0].url.query == b"status=active&limit=20"


async def test_bad_limit_is_forwarded_not_rejected(app, monkeypatch):
    # 与洞察面同一格：`limit=abc` 在 BFF 不是 422，而是 Go 的 40001。
    # 两侧各校验一次的结果是同一个坏请求有两种说法，而前端只认一种。
    stub = GoStub(monkeypatch)
    resp = await _call(app, "GET", "/api/v1/goals", params={"limit": "abc"})
    assert resp.status_code == 200
    assert stub.calls[0].url.query == b"limit=abc"


# ── 3. path 段转义（身份不能从 path 里长出来）─────────────────────────────────

async def test_goal_path_segment_is_escaped(app, monkeypatch):
    stub = GoStub(monkeypatch)
    await _call(app, "PATCH", "/api/v1/goals/x%3Fuser_id%3Dvictim", json={"action": "cancel"})
    call = stub.calls[0]
    assert call.url.query == b""
    assert "user_id=victim" not in str(call.url)


async def test_action_path_segment_is_escaped(app, monkeypatch):
    # 同一条判据的行动那一半：只测目标面等于给 `/actions/{id}` 留了一个没转义的洞。
    stub = GoStub(monkeypatch)
    await _call(app, "PATCH", "/api/v1/actions/x%26limit%3D1", json={"action": "complete"})
    call = stub.calls[0]
    assert call.url.query == b""
    assert "limit=1" not in str(call.url)


# ── 4. body 原样、身份只从 header ────────────────────────────────────────────

async def test_body_is_forwarded_verbatim_including_identity_key(app, monkeypatch):
    # BFF 不删 `user_id`：那条键由 Go 的 DisallowUnknownFields 判死才有证人
    # （见 test_v3_goals 那一侧的 TestV3GoalsIgnoreIdentityFromQueryAndBody）。
    stub = GoStub(monkeypatch)
    await _call(app, "POST", "/api/v1/goals",
                json={"title": "t", "user_id": "victim", "target_at": "2026-11-01T00:00:00Z"})
    call = stub.calls[0]
    assert call.headers["X-User-Id"] == "u-owner"
    assert call.headers["X-Service-Token"] == GO_TOKEN
    body = call.read()
    assert b'"user_id": "victim"' in body or b'"user_id":"victim"' in body
    assert b"2026-11-01T00:00:00Z" in body


async def test_transition_verb_reaches_go_untouched(app, monkeypatch):
    stub = GoStub(monkeypatch)
    await _call(app, "PATCH", "/api/v1/actions/a1", json={"action": "skip"})
    assert b'"action": "skip"' in stub.calls[0].read() or b'"action":"skip"' in stub.calls[0].read()


# ── 5. 错误与不可用的透传 ────────────────────────────────────────────────────

@pytest.mark.parametrize("go_http,go_code,go_msg", [
    (409, int(ErrorCode.CONFLICT), "当前的状态不允许这个动作"),
    (404, int(ErrorCode.RESOURCE_NOT_FOUND), "资源不存在 / Resource not found"),
    (400, int(ErrorCode.INVALID_PARAMS), "标题不合形"),
])
async def test_go_errors_pass_through_on_goal_face(app, monkeypatch, go_http, go_code, go_msg):
    # 工单 §验收那三格（409／404／400）经过两跳之后必须还是那三个数：
    # BFF 若在这里把 409 折成 500，前端就分不出「已经完成了」与「服务挂了」。
    stub = GoStub(monkeypatch)
    stub.response = httpx.Response(go_http, json=_envelope(None, code=go_code, message=go_msg))
    resp = await _call(app, "PATCH", "/api/v1/goals/g1", json={"action": "complete"})
    assert resp.status_code == go_http
    assert resp.json()["code"] == go_code


async def test_invalid_json_body_is_400_before_dialing(app, monkeypatch):
    stub = GoStub(monkeypatch)
    resp = await _call(app, "POST", "/api/v1/goals",
                       content=b"{oops", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert stub.calls == [], "坏 body 还去拨内部面 ⇒ 白烧一次跨服务调用"


async def test_unavailable_go_gives_503_envelope_on_goal_face(app, monkeypatch):
    stub = GoStub(monkeypatch, url="")
    resp = await _call(app, "GET", "/api/v1/goals")
    assert resp.status_code == 503
    body = resp.json()
    assert set(body) == {"code", "message", "data", "trace_id", "user_id"}


async def test_data_is_returned_verbatim_from_goal_list(app, monkeypatch):
    stub = GoStub(monkeypatch)
    payload = {"items": [{"id": "g1", "title": "t", "target_at": None}], "truncated": True}
    stub.response = httpx.Response(200, json=_envelope(payload))
    body = (await _call(app, "GET", "/api/v1/goals")).json()
    assert body["data"] == payload
    assert body["code"] == 200

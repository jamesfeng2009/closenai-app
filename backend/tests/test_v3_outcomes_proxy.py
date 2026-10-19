"""T4-03：行动结果面的 BFF 代理测（一条端点）。

与 `test_v3_goals_proxy.py` 同一口径：这里**只**测转发。`outcome_type` 认哪九类、
`result` 认哪六值、同日重复提交回什么，全部在 Go 的 `domain`／`httpapi` 各有闸；
这一面要证明的只有四件事：

1. 端点拨到**逐字正确**的内部 path 与方法（action_id 从 path 来，不从 body 猜）；
2. path 段转义——结果那条端点的 path 比行动面多一段，转义漏在 `{}` 之前就会长出
   一条打不通的内部 path；
3. body 不被拆包：`user_id`／`origin` 一路带到 Go 才被 `DisallowUnknownFields` 挡掉。
   这一格在结果面上尤其要紧：`origin` 决定「这条算不算模型相信的事」（O-5），
   BFF 若顺手补一个 `origin`，客户端就间接拿到了那支笔；
4. Go 的错误码／HTTP 状态与不可用面按形状透传（§验收 的 400／404 两跳之后还是那两个数）。
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
        self.response = httpx.Response(200, json=_envelope(_receipt()))
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


def _receipt():
    """Go 结果面的十键回执（这里重述一遍而不是 import 生产类型：两侧同源会恒真）。"""
    return {"outcome_id": "o1", "action_id": "a9", "outcome_type": "commitment_completed",
            "result": "better", "status": "recorded", "changed": True,
            "model_version": 8, "completed_target": "a9", "operation_id": "op-1",
            "detail": "记下了"}


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


# ── 1. 目标逐字 ───────────────────────────────────────────────────────────────

async def test_outcome_route_dials_the_internal_path_verbatim(app, monkeypatch):
    stub = GoStub(monkeypatch)
    resp = await _call(app, "POST", "/api/v1/actions/a9/outcomes",
                       json={"outcome_type": "commitment_completed", "result": "better",
                             "note": "这周真的去了三次", "operation_id": "op-1"})
    call = stub.calls[0]
    assert len(stub.calls) == 1
    assert call.method == "POST"
    assert call.url.path == "/internal/v3/actions/a9/outcomes"
    assert resp.status_code == 200
    # data 原样：十个键一个不少，BFF 不重新拼回执（`changed`/`detail` 是客户端
    # 分辨「记下了」与「今天已经记过」的唯一依据）。
    assert resp.json()["data"] == _receipt()


# ── 2. path 段转义 ────────────────────────────────────────────────────────────

async def test_action_path_segment_is_escaped_on_outcome_face(app, monkeypatch):
    # `/actions/{id}/outcomes` 的 `{}` 后面还跟着一段，所以 `quote(..., safe="")`
    # 只作用在 id 那一格才够：写成整条 path 转义会把 `/outcomes` 也吃掉（打不通），
    # 完全不转义则让 `a9?user_id=victim` 在 URL 解析阶段长出第二个 query。
    stub = GoStub(monkeypatch)
    await _call(app, "POST", "/api/v1/actions/x%3Fuser_id%3Dvictim/outcomes",
                json={"outcome_type": "commitment_completed"})
    call = stub.calls[0]
    assert call.url.query == b""
    assert "user_id=victim" not in str(call.url)
    assert call.url.path.endswith("/outcomes"), \
        f"后缀被转义吃掉了：{call.url.path}"


# ── 3. body 原样、身份只从 header ─────────────────────────────────────────────

async def test_body_is_forwarded_verbatim_including_identity_and_origin(app, monkeypatch):
    stub = GoStub(monkeypatch)
    await _call(app, "POST", "/api/v1/actions/a9/outcomes",
                json={"outcome_type": "commitment_completed", "result": "better",
                      "user_id": "victim", "origin": "llm_inference",
                      "occurred_at": "2026-10-11T02:03:04Z"})
    call = stub.calls[0]
    assert call.headers["X-User-Id"] == "u-owner"
    assert call.headers["X-Service-Token"] == GO_TOKEN
    body = call.read()
    # 两个坏键都必须还在体里：删掉任何一个都是 BFF 替 Go 把判据吞掉，
    # 而 Go 那侧的 400 恰好是靠「未知键一律拒」证出来的。
    assert b'"user_id"' in body
    assert b'"origin"' in body
    assert b"2026-10-11T02:03:04Z" in body


async def test_invalid_json_body_is_400_before_dialing(app, monkeypatch):
    stub = GoStub(monkeypatch)
    resp = await _call(app, "POST", "/api/v1/actions/a9/outcomes",
                       content=b"{oops", headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert stub.calls == [], "坏 body 还去拨内部面 ⇒ 白烧一次跨服务调用"


# ── 4. 错误与不可用的透传 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("go_http,go_code,go_msg", [
    (404, int(ErrorCode.RESOURCE_NOT_FOUND), "资源不存在 / Resource not found"),
    (400, int(ErrorCode.INVALID_PARAMS), "结果类型或取值不合形 / Invalid outcome_type or result"),
])
async def test_go_errors_pass_through_on_outcome_face(app, monkeypatch, go_http, go_code, go_msg):
    # §验收 的两格（非法 outcome_type 400／跨用户 404）经过两跳之后必须还是那两个数：
    # BFF 若把 404 折成 500，前端就只会说「服务挂了」，而用户其实点的是别人的行动。
    stub = GoStub(monkeypatch)
    stub.response = httpx.Response(go_http, json=_envelope(None, code=go_code, message=go_msg))
    resp = await _call(app, "POST", "/api/v1/actions/a9/outcomes",
                       json={"outcome_type": "astrology_reading"})
    assert resp.status_code == go_http
    assert resp.json()["code"] == go_code


async def test_unavailable_go_gives_503_envelope(app, monkeypatch):
    stub = GoStub(monkeypatch, url="")
    resp = await _call(app, "POST", "/api/v1/actions/a9/outcomes",
                       json={"outcome_type": "commitment_completed"})
    assert resp.status_code == 503
    body = resp.json()
    assert set(body) == {"code", "message", "data", "trace_id", "user_id"}


async def test_idempotent_receipt_shape_survives_the_hop(app, monkeypatch):
    # 同日重复提交那一格的用户后果全在 `changed=false`＋`detail`：
    # BFF 若在这里只回 data 的某个子集，客户端就会把「今天已经记过」渲染成「新记了一条」。
    stub = GoStub(monkeypatch)
    stub.response = httpx.Response(200, json=_envelope({
        "outcome_id": "o-first", "action_id": "a9",
        "outcome_type": "commitment_completed", "result": "better",
        "status": "recorded", "changed": False, "model_version": 8,
        "completed_target": "", "operation_id": "op-retry",
        "detail": "今天已经记过一条 commitment_completed 结果（outcome=o-first），这次不重复记账"}))
    body = (await _call(app, "POST", "/api/v1/actions/a9/outcomes",
                        json={"outcome_type": "commitment_completed",
                              "result": "worse"})).json()
    assert body["data"]["changed"] is False
    assert body["data"]["result"] == "better"
    assert body["data"]["detail"].startswith("今天已经记过")

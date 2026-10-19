"""T3-03：BFF 代理的功能测（工单 §验收：只测转发、头透传、错误映射）。

这里刻意**不**断言任何业务语义（哪种状态可见、哪种反馈改哪一行）：那些判据在 Go 的
`domain` / `store` 各有自己的闸。这一面只需要证明它没有替 Go 做决定，以及
Go 不在时它对用户说的话是形状正确的 503。
"""
import json

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
    """假内部面：记下每一次出站请求，按脚本回响应或直接抛错。"""

    def __init__(self, monkeypatch, *, url=GO_BASE, token=GO_TOKEN):
        self.calls: list[httpx.Request] = []
        self.timeouts: list[float] = []
        self.raise_exc: Exception | None = None
        self.response = httpx.Response(200, json=_envelope({"items": [], "next_cursor": ""}))
        monkeypatch.setattr(settings, "go_api_url", url, raising=False)
        monkeypatch.setattr(settings, "go_service_token", token, raising=False)

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            if self.raise_exc is not None:
                raise self.raise_exc
            return self.response

        def factory(timeout: float) -> httpx.AsyncClient:
            self.timeouts.append(timeout)
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)

        monkeypatch.setattr(v3_proxy, "_client_factory", factory)


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


def _ok(monkeypatch, **kwargs) -> GoStub:
    return GoStub(monkeypatch, **kwargs)


# ── 1. 配置不齐 ⇒ 拨号之前就 503 ────────────────────────────────────────────

async def test_missing_go_url_never_dials(app, monkeypatch):
    stub = _ok(monkeypatch)
    monkeypatch.setattr(settings, "go_api_url", "", raising=False)
    resp = await _call(app, "GET", "/api/v1/insights")
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["code"] == int(ErrorCode.SERVICE_UNAVAILABLE)
    assert body["message"] == v3_proxy.UNAVAILABLE_MESSAGE
    # 五键信封：前端按这五个键读，少一个键就是一屏空白。
    assert set(body) == {"code", "message", "data", "trace_id", "user_id"}
    # 这一格是本文件最贵的断言：配置缺失**不许**变成一次真的连接尝试。
    assert stub.calls == [], "配置为空仍然拨号 ⇒ 用户等满 5 秒才看到 503"
    assert stub.timeouts == []


async def test_missing_service_token_never_dials(app, monkeypatch):
    # 与上一格成对：`if not base or not token` 少查任何一半，这一条就红。
    stub = _ok(monkeypatch)
    monkeypatch.setattr(settings, "go_service_token", "", raising=False)
    resp = await _call(app, "GET", "/api/v1/insights")
    assert resp.status_code == 503
    assert stub.calls == []


# ── 2. 头与路径逐字转发 ──────────────────────────────────────────────────────

async def test_headers_path_and_query_are_verbatim(app, monkeypatch):
    stub = _ok(monkeypatch)
    resp = await _call(app, "GET", "/api/v1/insights",
                       params={"status": "published", "limit": "5", "cursor": "abc"})
    assert resp.status_code == 200, resp.text
    assert len(stub.calls) == 1
    call = stub.calls[0]
    assert call.method == "GET"
    assert call.url.path == "/internal/v3/insights"
    assert call.url.query == b"status=published&limit=5&cursor=abc"
    # 三个头名逐字（ADR-002 与 Go 侧 httpapi/v3.go 的常量），值各有出处：
    # 密钥来自配置、身份来自认证依赖。
    assert call.headers["X-Service-Token"] == GO_TOKEN
    assert call.headers["X-User-Id"] == "u-owner"
    # 无 trace 上下文时**不带**这个头，而不是带一个空串：Go 对空值会另生成一条 trace，
    # 带空串等于让两侧各有一份"这条请求没有 trace"的解释。
    assert "X-Trace-Id" not in call.headers


async def test_trace_id_is_forwarded_when_the_request_has_one(app, monkeypatch):
    # 打补丁在**代理读的那一格**上（`v3_proxy.get_trace_id`），而不是 set_trace_id：
    # contextvar 一旦在测试里真设过就会漏给同线程的后续用例，那批
    # 「没有 trace 就不发头」的断言会莫名其妙变绿或变红。
    stub = _ok(monkeypatch)
    monkeypatch.setattr(v3_proxy, "get_trace_id", lambda: "t-abc.123")
    await _call(app, "GET", "/api/v1/insights")
    assert stub.calls[0].headers["X-Trace-Id"] == "t-abc.123"


def test_headers_are_built_once_with_two_guards_and_one_omission(monkeypatch):
    """`build_headers` 是出站的唯一构造点，它自己那一格要有直接证人。

    四条各挡一种坏法：把三键写成两处各拼一遍会漂移；摘掉密钥判据会让缺配置变成
    一次 403；摘掉身份判据会让 Go 收到一个空 `X-User-Id` 而回 401（用户看到的是
    「我没登录」）；带一个空 trace 头则让两侧各有一份「这条请求没有 trace」的解释。
    """
    from backend.core.errors import AppException
    monkeypatch.setattr(settings, "go_service_token", GO_TOKEN, raising=False)
    monkeypatch.setattr(v3_proxy, "get_trace_id", lambda: "")
    assert v3_proxy.build_headers("u-owner") == {
        "X-Service-Token": GO_TOKEN, "X-User-Id": "u-owner"}
    monkeypatch.setattr(v3_proxy, "get_trace_id", lambda: "  t-9 ")
    assert v3_proxy.build_headers("u-owner")["X-Trace-Id"] == "t-9"
    with pytest.raises(AppException) as excinfo:
        v3_proxy.build_headers("")
    assert excinfo.value.code == int(ErrorCode.MISSING_TOKEN)
    monkeypatch.setattr(settings, "go_service_token", "   ", raising=False)
    with pytest.raises(AppException) as excinfo:
        v3_proxy.build_headers("u-owner")
    assert excinfo.value.http_status == 503


def test_dial_target_shapes_the_url_and_refuses_a_blank_base(monkeypatch):
    # 尾部斜杠要被吃掉：`http://go/` + `/internal/...` 拼出 `//internal`，
    # 而 Go 的 mux 上没有那条 pattern ⇒ 404 信封，长得像"内部面不存在"。
    monkeypatch.setattr(settings, "go_api_url", GO_BASE + "/", raising=False)
    monkeypatch.setattr(settings, "go_service_token", GO_TOKEN, raising=False)
    monkeypatch.setattr(v3_proxy, "get_trace_id", lambda: "")
    url, headers = v3_proxy.dial_target("/internal/v3/insights", "u-owner")
    assert url == GO_BASE + "/internal/v3/insights"
    assert headers == {"X-Service-Token": GO_TOKEN, "X-User-Id": "u-owner"}
    monkeypatch.setattr(settings, "go_api_url", "   ", raising=False)
    with pytest.raises(Exception) as excinfo:
        v3_proxy.dial_target("/internal/v3/insights", "u-owner")
    assert excinfo.value.http_status == 503


async def test_unknown_query_keys_are_not_forwarded(app, monkeypatch):
    # 白名单（LIST_QUERY_KEYS）的正向证人：只转三键，其余进不去内部面。
    stub = _ok(monkeypatch)
    await _call(app, "GET", "/api/v1/insights", params={"user_id": "victim", "limit": "3"})
    assert stub.calls[0].url.query == b"limit=3"


async def test_detail_and_feedback_paths_target_go(app, monkeypatch):
    stub = _ok(monkeypatch)
    await _call(app, "GET", "/api/v1/insights/i42")
    assert stub.calls[0].url.path == "/internal/v3/insights/i42"
    await _call(app, "POST", "/api/v1/insights/i42/feedback", json={"type": "confirm"})
    last = stub.calls[-1]
    assert last.method == "POST"
    assert last.url.path == "/internal/v3/insights/i42/feedback"


async def test_path_segment_is_escaped_not_concatenated(app, monkeypatch):
    # `?` / `&` 若不转义会在 URL 解析阶段长出第二个 query ⇒ 客户端能替自己声明筛选条件，
    # 而这一层存在的意义之一就是不替客户端做决定。
    stub = _ok(monkeypatch)
    await _call(app, "GET", "/api/v1/insights/x%3Fuser_id%3Dvictim")
    url = str(stub.calls[0].url)
    assert stub.calls[0].url.query == b""
    assert "user_id=victim" not in url


async def test_feedback_path_segment_is_escaped_too(app, monkeypatch):
    # 同一条判据的另一半：POST 那条也走 path。只测 GET 的话，
    # `feedback_path` 去掉 quote() 照样全绿，而客户端就多了一个能塞身份的入口。
    stub = _ok(monkeypatch)
    await _call(app, "POST", "/api/v1/insights/x%3Fuser_id%3Dvictim/feedback",
                json={"type": "confirm"})
    call = stub.calls[0]
    assert call.method == "POST"
    assert call.url.query == b""
    assert "user_id=victim" not in str(call.url)


# ── 3. 成功只回 data，失败原样透传 ───────────────────────────────────────────

async def test_success_returns_only_gos_data(app, monkeypatch):
    stub = _ok(monkeypatch)
    stub.response = httpx.Response(200, json=_envelope(
        {"items": [{"id": "i1"}], "next_cursor": "cur"}, ))
    body = (await _call(app, "GET", "/api/v1/insights")).json()
    assert body["data"] == {"items": [{"id": "i1"}], "next_cursor": "cur"}
    assert body["code"] == 200
    # trace_id/user_id 由 BFF 自己的上下文填（两侧信封同形，值各归两侧）。
    assert set(body) == {"code", "message", "data", "trace_id", "user_id"}


@pytest.mark.parametrize("go_http,go_code,go_msg", [
    (409, int(ErrorCode.CONFLICT), "重复反馈 / duplicate feedback"),
    (404, int(ErrorCode.RESOURCE_NOT_FOUND), "资源不存在 / not found"),
    (400, int(ErrorCode.INVALID_PARAMS), "limit 必须是正整数"),
    (403, 403, "服务凭据无效"),
])
async def test_go_errors_pass_through_both_http_and_code(
        app, monkeypatch, go_http, go_code, go_msg):
    stub = _ok(monkeypatch)
    stub.response = httpx.Response(go_http, json=_envelope(None, code=go_code, message=go_msg))
    resp = await _call(app, "POST", "/api/v1/insights/i1/feedback", json={"type": "confirm"})
    assert resp.status_code == go_http, "HTTP 状态被折成 200 ⇒ 前端按码分支全断"
    body = resp.json()
    assert body["code"] == go_code
    assert body["message"] == go_msg


async def test_error_data_is_passed_through_too(app, monkeypatch):
    # Go 会把校验细节放 data；丢掉它等于把可读错误降级成一句"参数错误"。
    stub = _ok(monkeypatch)
    detail = {"errors": [{"loc": ["body", "type"], "msg": "unknown type"}]}
    stub.response = httpx.Response(400, json=_envelope(detail, code=40001, message="参数错误"))
    body = (await _call(app, "POST", "/api/v1/insights/i1/feedback",
                        json={"type": "meh"})).json()
    assert body["data"] == detail


async def test_body_identity_field_is_forwarded_unchanged(app, monkeypatch):
    # 身份**只**从依赖（=JWT/X-User-Id 认证面）进 header；body 里的 user_id 原样转发，
    # 由 Go 的 DisallowUnknownFields 拒成 400。BFF 不"顺手"删字段：删了就等于
    # 悄悄改掉两侧契约，客户端会以为自己的身份生效了。
    stub = _ok(monkeypatch)
    stub.response = httpx.Response(400, json=_envelope(
        None, code=40001, message="请求体含未知字段 / unknown field"))
    resp = await _call(app, "POST", "/api/v1/insights/i1/feedback",
                       json={"type": "confirm", "user_id": "victim"})
    assert resp.status_code == 400
    assert json.loads(stub.calls[0].content) == {"type": "confirm", "user_id": "victim"}
    assert stub.calls[0].headers["X-User-Id"] == "u-owner"


# ── 4. 网络与形状失败一律 503 ────────────────────────────────────────────────

@pytest.mark.parametrize("exc", [
    httpx.ConnectError("connection refused"),
    httpx.ConnectTimeout("timed out"),
    httpx.ReadTimeout("read timed out"),
])
async def test_transport_failures_map_to_503(app, monkeypatch, exc):
    stub = _ok(monkeypatch)
    stub.raise_exc = exc
    resp = await _call(app, "GET", "/api/v1/insights")
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == int(ErrorCode.SERVICE_UNAVAILABLE)
    assert body["message"] == v3_proxy.UNAVAILABLE_MESSAGE
    assert len(stub.calls) == 1


async def test_non_json_response_maps_to_503(app, monkeypatch):
    # Go 挂了被 ingress 兜成 HTML 错误页，是这一格最现实的形状；解析成功再回 200 才是造假。
    stub = _ok(monkeypatch)
    stub.response = httpx.Response(502, text="<html>502 Bad Gateway</html>",
                                  headers={"content-type": "text/html"})
    resp = await _call(app, "GET", "/api/v1/insights")
    assert resp.status_code == 503
    assert resp.json()["message"] == v3_proxy.UNAVAILABLE_MESSAGE


@pytest.mark.parametrize("payload", [
    [1, 2, 3],
    {"message": "success"},
    "a string",
])
async def test_json_without_code_envelope_maps_to_503(app, monkeypatch, payload):
    stub = _ok(monkeypatch)
    stub.response = httpx.Response(200, json=payload)
    resp = await _call(app, "GET", "/api/v1/insights")
    assert resp.status_code == 503
    assert resp.json()["code"] == int(ErrorCode.SERVICE_UNAVAILABLE)


async def test_empty_message_from_go_still_gets_unavailable_text(app, monkeypatch):
    # Go 若回一条空 message，用户会看到"操作失败："后面什么都没有；兜一句确定的文案。
    stub = _ok(monkeypatch)
    stub.response = httpx.Response(400, json=_envelope(None, code=40001, message=""))
    body = (await _call(app, "GET", "/api/v1/insights")).json()
    assert body["message"] == v3_proxy.UNAVAILABLE_MESSAGE
    assert body["code"] == 40001


# ── 5. 校验只发生在 Go（limit=abc 不许被 BFF 挡下）───────────────────────────

async def test_non_numeric_limit_is_forwarded_not_validated(app, monkeypatch):
    stub = _ok(monkeypatch)
    stub.response = httpx.Response(400, json=_envelope(
        None, code=40001, message="limit 必须是正整数 / limit must be a positive integer"))
    resp = await _call(app, "GET", "/api/v1/insights", params={"limit": "abc"})
    assert len(stub.calls) == 1, "BFF 自己拦掉了 limit ⇒ Go 不再是唯一校验面"
    assert stub.calls[0].url.query == b"limit=abc"
    # 错误码来自 Go，而不是 BFF 的 RequestValidationError（那条也会回 400，但文案不同）。
    assert resp.json()["message"].startswith("limit 必须是正整数")


# ── 6. 超时常量的出处 ────────────────────────────────────────────────────────

async def test_client_is_built_with_the_go_timeout_constant(app, monkeypatch):
    stub = _ok(monkeypatch)
    await _call(app, "GET", "/api/v1/insights")
    assert stub.timeouts == [v3_proxy.GO_TIMEOUT_SECONDS]
    # 5s 是工单 §怎么做.2 的值；改成别的数字时这条会红，逼改的人回工单登记。
    assert v3_proxy.GO_TIMEOUT_SECONDS == 5.0


# ── 7. list_params 的丢空语义（转发面的唯一"判断"，所以要有证人）──────────────

def test_list_params_drops_absent_keys_only():
    assert v3_proxy.list_params({"status": None, "limit": None, "cursor": None}) == {}
    assert v3_proxy.list_params({"status": "", "limit": "0"}) == {"status": "", "limit": "0"}
    assert v3_proxy.list_params({"unknown": "x"}) == {}

"""T3-03 · BFF → Go 内部面的跨进程 E2E（真 Go 进程，真 HTTP，真停机）。

工单 §验收 那句逐字是「Go 停掉 ⇒ `/api/v1/insights` 返回 503 且信封形状正确」。
`test_v3_proxy.py` 里那条 ConnectError 用例证的是**我认为** Go 停了会长什么样；
本文件杀掉一个真进程，看用户实际拿到什么。同理，头门禁、错误码与 HTTP 状态的配对、
keyset 游标、`DisallowUnknownFields` 这几格都由对面那台真 Go 回答，而不是由我这侧的
假 transport 回答——断言两侧因此不同源。

夹具数据不是真库数据：G-05／G-06（无证据不上线、纠错真落库）由 `internal/store`
的集成测在真 PG 上钉，这里再造一份只会造出第二个事实源。本文件钉的是**线上形状**。

跑法（本机 go 是混合安装，须点名；缺工具链就 skip 并写明少跑了几条）：

    export GOROOT=/Users/fengyu/sdk/go1.27.1 PATH=$GOROOT/bin:$PATH
    .venv/bin/pytest backend/tests/test_v3_proxy_e2e.py -q
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Set

import httpx
import pytest
from fastapi import FastAPI

from backend.config import settings
from backend.core.errors import ErrorCode
from backend.core.middleware import TraceIdMiddleware, register_exception_handlers
from backend.routers import v3 as v3_router
from backend.services import v3_proxy

ROOT = Path(__file__).resolve().parents[2]
GO_CORE = ROOT / "go-core"
GO_PKG = "./tools/v3surface"
BUILD_TIMEOUT = 300

TOKEN = "e2e-service-token-e2e-service-token"
WRONG_TOKEN = "wrong-service-token-wrong-service-token"
E2E_USER = "u-e2e"

#: 本文件的跨进程用例清单；最后一条断言「跑过的 == 这张表」，少跑一条就红。
CASE_PLAN: Dict[str, str] = {
    "list_shape_survives_two_hops": "Go 的 JSON tag（含 evidence_count）逐字活到客户端",
    "list_cursor_round_trip": "keyset 游标由真 Go 编解码，第二页真的换行了",
    "list_status_filter_narrows": "过滤器取交集：corrected 只剩一条，draft 回空列表",
    "list_bad_limit_is_gos_error": "limit=abc 由 Go 判成 400/40001，BFF 不代判",
    "list_bad_cursor_is_gos_error": "坏游标显式 400，不静默回第一页",
    "detail_evidence_and_identity": "依据正文逐字回链 + 信封 user_id 来自 header 那一跳",
    "detail_unknown_is_single_404": "别人的行与不存在的行回同一个 40402",
    "feedback_receipt_echos_path_and_header": "领域层收到的身份/目标只可能来自 header+path",
    "feedback_correction_text_reaches_domain": "纠错正文活着走到领域层（截掉就留零字）",
    "feedback_duplicate_maps_to_409": "Go 的 409 折到客户端仍是 409，不是 200 也不是 500",
    "foreign_identity_sees_nothing": "陌生身份：列表空＋详情 404＋反馈 404（身份确实转发到了对面）",
    "feedback_body_identity_rejected": "body 里塞 user_id 被 Go 的 DisallowUnknownFields 拒掉",
    "goal_and_action_lists_keep_gos_wire_shape": "目标/行动列表的键集与 truncated 逐字活到客户端",
    "goal_write_echoes_header_and_body": "新建/转移：身份来自 header、正文来自 body 的那一跳",
    "goal_skip_is_409_across_two_hops": "§验收逐字「非法转移 409」＋「未知动词 400」跨两跳",
    "goal_body_identity_rejected_and_foreign_sees_nothing": "目标面 body 身份被拒＋陌生身份 404/空列表",
    "wrong_service_token_403": "凭据不符的 403 原样透传（401 才是错的那格）",
    "stopped_server_gives_503_envelope": "§验收逐字：Go 停掉 ⇒ 503 且五键信封（九条端点里抽四条）",
}

_RAN: Set[str] = set()


def _go_candidates() -> List[str]:
    raw = [
        os.environ.get("GO_BIN"),
        os.environ.get("GOROOT") and str(Path(os.environ["GOROOT"]) / "bin" / "go"),
        shutil.which("go"),
    ]
    out: List[str] = []
    for item in raw:
        if item and item not in out:
            out.append(item)
    return out


@pytest.fixture(scope="module")
def go_binary() -> str:
    """编译一次 `tools/v3surface`；找不到/编不动 go ⇒ skip（理由里写清少跑几条）。"""
    out = Path(tempfile.mkdtemp(prefix="t303surface-")) / "v3surface"
    failures: List[str] = []
    for candidate in _go_candidates():
        try:
            proc = subprocess.run(
                [candidate, "build", "-o", str(out), GO_PKG],
                cwd=str(GO_CORE), capture_output=True, text=True,
                env=dict(os.environ), timeout=BUILD_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"{candidate}: {exc}")
            continue
        if proc.returncode == 0 and out.exists():
            return str(out)
        failures.append(
            f"{candidate}: rc={proc.returncode} {(proc.stderr or proc.stdout).strip()[:300]}")
    pytest.skip(
        f"没有能编译 {GO_PKG} 的 go 工具链 ⇒ 本文件 {len(CASE_PLAN)} 条跨进程用例"
        "**一条都没跑**，别把它当绿。\n" + "\n".join(failures))


class Surface:
    """一个真 Go 进程：起在随机端口，stdin 关掉就是停机。"""

    def __init__(self, binary: str, token: str = TOKEN):
        self.token = token
        self.env = dict(os.environ, GO_SERVICE_TOKEN=token)
        self.proc = subprocess.Popen(
            [binary, "-addr", "127.0.0.1:0"], cwd=str(GO_CORE), env=self.env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True)
        line = self.proc.stdout.readline()
        if not line:
            err = (self.proc.stderr.read() or "")[:400]
            pytest.fail(f"Go 侧没报端口（rc={self.proc.returncode}）：{err}")
        self.base_url = "http://" + json.loads(line)["addr"]

    def stop(self) -> None:
        if self.proc.poll() is None:
            try:
                assert self.proc.stdin is not None
                self.proc.stdin.close()
            except OSError:
                pass
            self.proc.wait(timeout=15)


@pytest.fixture
def surface(go_binary: str):
    srv = Surface(go_binary)
    yield srv
    srv.stop()


def make_app() -> FastAPI:
    # 不覆盖 get_current_user：这一面的身份链要**真走一遍**
    # （客户端 X-User-Id → BFF 认证依赖 → 转发头 → Go 的归属判据 → 回吐的信封 user_id）。
    # 覆盖掉依赖就等于把这条链最关键的一跳测成了自己的桩。
    app = FastAPI()
    # 与真 main.py 同一层数：TraceIdMiddleware 在最外层，它**同时**是 user_id 的
    # 设置点（`resolve_user_id_from_request` 在外层 context 里跑，而 `get_current_user`
    # 是同步依赖，被 anyio 丢进线程池 ⇒ 它那次 set_user_id 回不到请求上下文）。
    # 少加这一层，信封的 user_id 就恒为空串——那是测法的坑，不是产品的形状。
    app.add_middleware(TraceIdMiddleware)
    app.include_router(v3_router.router, prefix="/api/v1", tags=["v3"])
    register_exception_handlers(app)
    return app


async def _call(app: FastAPI, method: str, path: str, *, user: str = E2E_USER,
                **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://client.test", timeout=20.0,
                                 headers={"X-User-Id": user}) as client:
        return await client.request(method, path, **kwargs)


@pytest.fixture
def wired(monkeypatch, surface: Surface):
    """把 BFF 的配置指向那台真 Go（拨号方向：pytest → FastAPI → httpx → Go）。"""
    monkeypatch.setattr(settings, "go_api_url", surface.base_url, raising=False)
    monkeypatch.setattr(settings, "go_service_token", surface.token, raising=False)
    # development 才允许 X-User-Id 透传（生产要 JWT）；这一格与身份链的测法绑定。
    monkeypatch.setattr(settings, "environment", "development", raising=False)
    return surface


def _mark(name: str) -> None:
    _RAN.add(name)


# ── 1. 列表：形状、翻页与两个显式失败 ─────────────────────────────────────────

async def test_list_shape_survives_two_hops(wired):
    body = (await _call(make_app(), "GET", "/api/v1/insights")).json()
    assert body["code"] == 200, body
    items = body["data"]["items"]
    assert [i["id"] for i in items] == ["i-e2e-1", "i-e2e-2"], items
    assert items[0]["evidence_count"] == 3
    assert items[0]["published_at"].startswith("2026-10-19T08:00:00")
    assert items[1]["status"] == "corrected"
    # 少一个键＝Go 改了 tag 而 BFF 照单全收，前端要等到渲染那天才发现。
    assert set(items[0]) == {"id", "statement", "claim_type", "status",
                             "confidence", "published_at", "evidence_count"}
    assert body["data"]["next_cursor"] == ""
    assert body["user_id"] == E2E_USER
    _mark("list_shape_survives_two_hops")


async def test_list_cursor_round_trip(wired):
    app = make_app()
    first = (await _call(app, "GET", "/api/v1/insights", params={"limit": "1"})).json()
    assert [i["id"] for i in first["data"]["items"]] == ["i-e2e-1"]
    cursor = first["data"]["next_cursor"]
    assert cursor, "limit=1 却没发游标 ⇒ 用户看不到第二页"
    second = (await _call(app, "GET", "/api/v1/insights",
                          params={"limit": "1", "cursor": cursor})).json()
    assert [i["id"] for i in second["data"]["items"]] == ["i-e2e-2"], second
    assert second["data"]["next_cursor"] == ""
    _mark("list_cursor_round_trip")


async def test_list_status_filter_narrows(wired):
    app = make_app()
    only = (await _call(app, "GET", "/api/v1/insights",
                        params={"status": "corrected"})).json()
    assert [i["id"] for i in only["data"]["items"]] == ["i-e2e-2"]
    empty = (await _call(app, "GET", "/api/v1/insights", params={"status": "draft"})).json()
    assert empty["code"] == 200 and empty["data"]["items"] == [], \
        "内部态过滤器回落成全集 ⇒ 这是授权判据的反面"
    _mark("list_status_filter_narrows")


async def test_list_bad_limit_is_gos_error(wired):
    resp = await _call(make_app(), "GET", "/api/v1/insights", params={"limit": "abc"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == int(ErrorCode.INVALID_PARAMS)
    assert "limit 必须是正整数" in body["message"], body["message"]
    _mark("list_bad_limit_is_gos_error")


async def test_list_bad_cursor_is_gos_error(wired):
    resp = await _call(make_app(), "GET", "/api/v1/insights", params={"cursor": "★"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == int(ErrorCode.INVALID_PARAMS)
    assert "翻页游标无法识别" in body["message"], body["message"]
    _mark("list_bad_cursor_is_gos_error")


# ── 2. 详情：依据正文与身份的那一跳 ───────────────────────────────────────────

async def test_detail_evidence_and_identity(wired):
    resp = await _call(make_app(), "GET", "/api/v1/insights/i-e2e-1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    data = body["data"]
    assert data["id"] == "i-e2e-1"
    assert data["evidence"][0]["excerpt"] == "今晚又三点才睡"
    assert data["evidence"][0]["memory_ref"]["memory_id"] == "m-e2e"
    assert data["patterns"][0]["id"] == "p-e2e"
    # 详情面上「裁剪后的活依据」与「总数」是两个数：count=1（活的那条）、
    # withdrawn_count=2（总数 3 减活的 1），与列表行的 evidence_count=3 能对上账。
    assert data["evidence_summary"]["count"] == 1, data["evidence_summary"]
    assert data["evidence_summary"]["withdrawn_count"] == 2, data["evidence_summary"]
    # 信封里的 user_id 由 BFF 的认证依赖填（客户端 X-User-Id → get_current_user →
    # contextvar），Go 侧另一份 user_id 来自转发头 ⇒ 两侧同源的那一格不在这里，
    # 而在「陌生身份看不到任何一行」那条用例里。
    assert body["user_id"] == E2E_USER
    _mark("detail_evidence_and_identity")


async def test_detail_unknown_is_single_404(wired):
    for path in ("/api/v1/insights/i-nope", "/api/v1/insights/i-e2e-2"):
        resp = await _call(make_app(), "GET", path)
        assert resp.status_code == 404, (path, resp.text)
        assert resp.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)
    _mark("detail_unknown_is_single_404")


# ── 3. 反馈：回执、409、跨用户与被拒的 body 身份 ──────────────────────────────

async def test_feedback_receipt_echos_path_and_header(wired):
    resp = await _call(make_app(), "POST", "/api/v1/insights/i-e2e-1/feedback",
                       json={"type": "confirm", "operation_id": "op-77"})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["feedback_id"] == "fb-e2e"
    detail = data["detail"]
    assert "user=u-e2e" in detail, detail
    assert "target=i-e2e-1" in detail, detail
    assert "type=confirm" in detail and "op=op-77" in detail, detail
    _mark("feedback_receipt_echos_path_and_header")


async def test_feedback_correct_text_reaches_domain(wired):
    # 纠错正文必须活着走到领域层——它在这一层被截掉或丢掉，权威表里就留下零字。
    resp = await _call(make_app(), "POST", "/api/v1/insights/i-e2e-1/feedback",
                       json={"type": "correct", "correction_text": "其实是周五，不是周一"})
    assert resp.status_code == 200, resp.text
    assert "text=\"其实是周五，不是周一\"" in resp.json()["data"]["detail"]
    _mark("feedback_correction_text_reaches_domain")


async def test_feedback_duplicate_maps_to_409(wired):
    resp = await _call(make_app(), "POST", "/api/v1/insights/i-e2e-1/feedback",
                       json={"type": "reject"})
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == int(ErrorCode.CONFLICT)
    _mark("feedback_duplicate_maps_to_409")


async def test_foreign_identity_sees_nothing_at_all(wired):
    """换一个真认证进来的陌生身份：列表空、详情 404、反馈 404。

    这一格才是「转发头里的身份确实生效」的证人——上面几条用 u-e2e 跑通，
    只能证明 BFF 有把头发出去；只有陌生身份**看不到**，才能证明 Go 收到的
    是 u-e2e 而不是别的（拿 u-e2e 的正向结果反推，是断言两侧同源那一类假绿）。
    """
    app = make_app()
    resp = await _call(app, "GET", "/api/v1/insights", user="u-intruder")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["items"] == [], "陌生身份看到了别人的洞察"
    resp = await _call(app, "GET", "/api/v1/insights/i-e2e-1", user="u-intruder")
    assert resp.status_code == 404, resp.text
    resp = await _call(app, "POST", "/api/v1/insights/i-e2e-1/feedback",
                       json={"type": "confirm"}, user="u-intruder")
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)
    _mark("foreign_identity_sees_nothing")


async def test_feedback_body_identity_rejected(wired):
    resp = await _call(make_app(), "POST", "/api/v1/insights/i-e2e-1/feedback",
                       json={"type": "confirm", "user_id": "victim"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == int(ErrorCode.INVALID_PARAMS)
    _mark("feedback_body_identity_rejected")


# ── 4. 目标／行动面（T4-02）：六条里的四格跨进程判据 ──────────────────────────

async def test_goal_and_action_lists_keep_gos_wire_shape(wired):
    body = (await _call(make_app(), "GET", "/api/v1/goals")).json()
    assert body["code"] == 200, body
    items = body["data"]["items"]
    assert [i["id"] for i in items] == ["g-e2e"], items
    # 键集逐字：这一面没有 next_cursor（截断用 truncated），多一个键少一个键都要在这里红。
    assert set(items[0]) == {"id", "title", "description", "status", "started_at",
                             "target_at", "completed_at", "created_at", "updated_at"}, items[0]
    assert items[0]["target_at"] is None, "没有截止的目标给出了一个时间 ⇒ 假装有值"
    assert body["data"]["truncated"] is False, body["data"]
    acts = (await _call(make_app(), "GET", "/api/v1/actions")).json()["data"]
    assert [a["id"] for a in acts["items"]] == ["a-e2e"], acts
    assert acts["items"][0]["goal_id"] == "g-e2e", "挂在目标下的行动读出来成了没挂"
    _mark("goal_and_action_lists_keep_gos_wire_shape")


async def test_goal_write_echoes_header_and_body(wired):
    # 领域层（这里是夹具）报回来的 detail 是唯一能证明「身份来自 header、
    # 正文来自 body」跨了两跳的位置——BFF 自己拆包也能让本进程的单测全绿。
    body = (await _call(make_app(), "POST", "/api/v1/goals",
                        json={"title": "早睡一小时", "target_at": "2026-11-01T00:00:00Z"}
                        )).json()
    assert body["code"] == 200, body
    data = body["data"]
    assert data["goal_id"] == "g-e2e" and data["changed"] is True, data
    assert 'title="早睡一小时"' in data["detail"] and "target=true" in data["detail"], data
    rec = (await _call(make_app(), "PATCH", "/api/v1/goals/g-e2e",
                       json={"action": "cancel"})).json()["data"]
    assert rec["detail"] == "goal verb=cancel", rec
    assert rec["status"] == "cancelled", rec
    _mark("goal_write_echoes_header_and_body")


async def test_goal_skip_is_409_across_two_hops(wired):
    """§验收逐字那一格「状态机非法转移 409」跨进程的版本。

    夹具在这里回的是 `domain.ErrGoalIllegalTransition`（不是状态码），
    所以红点落在 Go 的错误映射表上：那一格若把归属错误与转移错误混折，
    本进程的桩测有可能看不出来（桩直接返回哨兵，而真面还要过一层 mux 与信封）。
    """
    resp = await _call(make_app(), "PATCH", "/api/v1/goals/g-e2e", json={"action": "skip"})
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == int(ErrorCode.CONFLICT)
    # 未知动词是另一格：400 而不是 409（HTTP 层判动词，domain 判转移）。
    bad = await _call(make_app(), "PATCH", "/api/v1/actions/a-e2e", json={"action": "undo"})
    assert bad.status_code == 400, bad.text
    assert bad.json()["code"] == int(ErrorCode.INVALID_PARAMS)
    _mark("goal_skip_is_409_across_two_hops")


async def test_goal_body_identity_rejected_and_foreign_sees_nothing(wired):
    app = make_app()
    resp = await _call(app, "POST", "/api/v1/goals",
                       json={"title": "t", "user_id": "victim"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == int(ErrorCode.INVALID_PARAMS)
    # 陌生身份：列表空、改别人的目标 404（与「不存在」同码，防枚举）。
    empty = await _call(app, "GET", "/api/v1/goals", user="u-intruder")
    assert empty.status_code == 200 and empty.json()["data"]["items"] == [], empty.text
    off = await _call(app, "PATCH", "/api/v1/goals/g-e2e", json={"action": "cancel"},
                      user="u-intruder")
    assert off.status_code == 404, off.text
    assert off.json()["code"] == int(ErrorCode.RESOURCE_NOT_FOUND)
    _mark("goal_body_identity_rejected_and_foreign_sees_nothing")


# ── 5. 门禁与停机 ─────────────────────────────────────────────────────────────

async def test_wrong_service_token_403(wired, monkeypatch):
    monkeypatch.setattr(settings, "go_service_token", WRONG_TOKEN, raising=False)
    resp = await _call(make_app(), "GET", "/api/v1/insights")
    assert resp.status_code == 403, resp.text
    # 403 而不是 401：401 的语义是「请重新认证」，而这里的客户端是我们的 BFF，
    # 它没有重新认证这个动作可做（Go 的 v3.go 同一条理由，跨进程再钉一次）。
    assert resp.json()["code"] == int(ErrorCode.FORBIDDEN)
    _mark("wrong_service_token_403")


async def test_stopped_server_gives_503_envelope(go_binary, monkeypatch):
    srv = Surface(go_binary)
    monkeypatch.setattr(settings, "go_api_url", srv.base_url, raising=False)
    monkeypatch.setattr(settings, "go_service_token", srv.token, raising=False)
    app = make_app()
    assert (await _call(app, "GET", "/api/v1/insights")).status_code == 200
    assert (await _call(app, "GET", "/api/v1/goals")).status_code == 200
    srv.stop()
    # 真停机的形状。这里不重试、不降级、不拿缓存假装成功：
    resp = await _call(app, "GET", "/api/v1/insights")
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["code"] == int(ErrorCode.SERVICE_UNAVAILABLE)
    assert body["message"] == v3_proxy.UNAVAILABLE_MESSAGE
    assert set(body) == {"code", "message", "data", "trace_id", "user_id"}, body
    # 同一颗洞察的详情走同一条失败路径（三条端点都得是 503，不能只有列表会）。
    assert (await _call(app, "GET", "/api/v1/insights/i-e2e-1")).status_code == 503
    assert (await _call(app, "POST", "/api/v1/insights/i-e2e-1/feedback",
                        json={"type": "confirm"})).status_code == 503
    # T4-02 那六条同样不许在自己挂了的时候回 200 空集：
    # 「目标都消失了」看起来像真的，那比一次 503 难归因得多。
    assert (await _call(app, "GET", "/api/v1/goals")).status_code == 503
    assert (await _call(app, "PATCH", "/api/v1/goals/g-e2e",
                        json={"action": "cancel"})).status_code == 503
    _mark("stopped_server_gives_503_envelope")


# ── 6. 跑过的必须等于清单 ─────────────────────────────────────────────────────

def test_every_cross_process_case_ran():
    # 这一条是本文件的「不算绿」证人：夹具被改名、用例被 skip、断言被注释掉，
    # 都会让 _RAN 少一格，而逐条用例自己永远是绿的。
    # 但它的依赖是 go 工具链：本机没有可编译的 go 时，其余 14 条全在 skip，
    # 这一条若照样红，红的就是「这台机器没有 go」而不是「判据没跑到」——
    # 那是把环境记成缺陷。所以这里同样 skip，并在理由里把少跑几条写明。
    if not any(Path(candidate).exists() for candidate in _go_candidates()):
        pytest.skip(f"没有 go 工具链（GO_BIN／GOROOT／PATH 都没有）⇒ 本文件 {len(CASE_PLAN)} 条"
                    "跨进程用例**一条都没跑**，记「未跑」，不算绿")
    missing = set(CASE_PLAN) - _RAN
    assert not missing, f"这些跨进程判据没有真的跑到：{sorted(missing)}"

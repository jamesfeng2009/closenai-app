"""G-03（闸）：Python BFF 必须是**纯转发**，不许长成第二业务层。

工单 T3-03 §怎么做.5 点名这一条，理由是 ADR-001/002 把「谁说了算」写死了：
可见性、状态集合、错误映射、权益判断在 Go 与 `quota_service` 各有一份真身。
转发层一旦开始判断，坏法不是报错，而是**同一个用户在两个面上看到两个答案**。

闸的形状（按 2026-10-19 的口径）：
- **反向归零**：两个文件里不许出现业务判断关键字、不许出现数据库会话、不许读业务配置；
- **正向证人**：身份入口、内部 path 常量、超时常量、`main.py` 注册行都要数得出次数。
  只有反向归零的闸会因为「文件被删空」而永远绿，那是最坏的一种绿。
"""
import re
from pathlib import Path

from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]
PROXY_SRC = (REPO / "backend" / "services" / "v3_proxy.py").read_text(encoding="utf-8")
ROUTER_SRC = (REPO / "backend" / "routers" / "v3.py").read_text(encoding="utf-8")
MAIN_SRC = (REPO / "backend" / "main.py").read_text(encoding="utf-8")
BOTH = PROXY_SRC + "\n" + ROUTER_SRC

# 工单点名的四个业务判断关键字。`status ==` 用正则而不是子串：空格与换行都会躲过子串匹配，
# 而"顺手加一个状态分支"正是这条闸唯一要挡的动作。
# 其余三个按**子串**扫（2026-10-19 由变异自证收紧）：`\bis_pro\b` 这类带左边界的形式
# 放得过 `_is_pro(user_id)` 与 `tier_name`，而那两个恰恰是"顺手加一个判断"最常见的写法。
BUSINESS_JUDGEMENT = [
    (r"\bstatus\s*==", "对洞察状态做分支 ⇒ 状态判据只许在 Go 的 domain"),
    (r"tier", "订阅档位判断 ⇒ 归 quota_service"),
    (r"entitlement", "权益判断 ⇒ 归 quota_service"),
    (r"is_pro", "付费用户判断 ⇒ 归 quota_service"),
]

# 第二数据源：这一面的数据只许从 Go 的 HTTP 响应里来。
SECOND_SOURCE = [
    (r"\bget_db\b", "拿 ORM 会话 ⇒ 开始自己取数"),
    (r"\bAsyncSession\b", "自己开会话 ⇒ 有了第二条读路径"),
    (r"\bsessionmaker\b", "同上"),
    (r"\bSELECT\b", "自己写 SQL ⇒ canonical 出现第二个读面"),
    (r"backend\.models", "引 ORM 模型 ⇒ 引出了第二事实源"),
    (r"backend\.repositories", "引仓库层 ⇒ 绕过了 Go"),
    (r"\bhttpx\b", "只有 v3_proxy 可以碰 httpx；handler 直连＝绕过转发层"),
]


def _assert_absent(source: str, label: str, patterns):
    for pattern, why in patterns:
        hit = re.search(pattern, source)
        if hit is not None:
            line = source[:hit.start()].count("\n") + 1
            raise AssertionError(f"{label} 第 {line} 行出现 {pattern} ⇒ {why}")


def test_proxy_source_has_no_business_judgement():
    _assert_absent(PROXY_SRC, "services/v3_proxy.py", BUSINESS_JUDGEMENT)


def test_router_source_has_no_business_judgement():
    _assert_absent(ROUTER_SRC, "routers/v3.py", BUSINESS_JUDGEMENT)


def test_proxy_and_router_have_no_second_data_source():
    # SECOND_SOURCE 里 httpx 只许出现在代理侧，所以逐文件查。
    _assert_absent(PROXY_SRC, "services/v3_proxy.py", SECOND_SOURCE[:-1])
    _assert_absent(ROUTER_SRC, "routers/v3.py", SECOND_SOURCE)


def test_proxy_reads_only_its_own_two_config_keys():
    """`settings.<键>` 只许是 go_api_url / go_service_token。

    这一格不是洁癖：转发层一旦能读业务配置（免费额度、语言、导出目录…），
    它就有了不问 Go 自己决定的能力，而那条能力一旦写下来就不会再被删掉。
    """
    keys = set(re.findall(r"settings\.([A-Za-z_][A-Za-z0-9_]*)", PROXY_SRC))
    assert keys <= {"go_api_url", "go_service_token"}, \
        f"代理读了额外配置键 {sorted(keys - {'go_api_url', 'go_service_token'})} ⇒ 开始替 Go 决定"


def test_identity_enters_only_from_the_auth_dependency():
    # ADR-002 的 Python 侧落点。十条 handler 各要一次 Depends(get_current_user)：
    # 少一次＝那一条端点没有身份入口（要么匿名要么从 body 取）。
    # 3→9 是 T4-02（目标/行动六条）接线那一批改的，9→10 是 T4-03（行动结果一条）；
    # 这一格的数量必须由工单驱动，不能由「凑个绿的数字」驱动——每加一条端点都要同批改这一行。
    assert ROUTER_SRC.count("Depends(get_current_user)") == 10, \
        "身份入口数量变了 ⇒ 逐条核对十个 handler"
    for pattern, why in [
        (r"""body\s*(?:\[|\.get\(\s*["'])user_id""", "从请求体里取 user_id ⇒ ADR-002 反面"),
        (r"""params\s*(?:\[|\.get\(\s*["'])user_id""", "从 query 里取 user_id ⇒ 同上"),
    ]:
        assert re.search(pattern, ROUTER_SRC) is None, why


def test_forward_surface_is_registered_on_the_client_prefix():
    # 正向证人（否则前面几条会因为文件被删空而全绿）。
    # 行首锚定（2026-10-19 由变异自证收紧）：不锚定的话把那一行**注释掉**照样匹配，
    # 而"取消注册"正是这条证人唯一要挡的坏法。
    registered = re.findall(
        r'^app\.include_router\(v3\.router,\s*prefix="/api/v1"', MAIN_SRC, re.M)
    assert len(registered) == 1, \
        "main.py 没有把 v3 面挂到 /api/v1 ⇒ 客户端看不见，而闸会因为空文件全绿"
    for constant in ["/internal/v3/insights", '/internal/v3/insights/"', "{}", "feedback",
                    "/internal/v3/goals", "/internal/v3/actions",
                    "/internal/v3/actions/{}/outcomes"]:
        assert constant in PROXY_SRC, f"内部 path 常量缺 {constant!r} ⇒ 转发目标被改掉"
    assert "X-Service-Token" in PROXY_SRC and "X-User-Id" in PROXY_SRC, \
        "两个头名逐字是 ADR-002 的字面契约"
    assert re.search(r"GO_TIMEOUT_SECONDS\s*=\s*5\.0", PROXY_SRC), \
        "超时值（工单 §怎么做.2 ＝ 5s）被改掉却没登记"
    assert "_client_factory(GO_TIMEOUT_SECONDS)" in PROXY_SRC, \
        "超时常量没被真正使用 ⇒ 它变成了一个假旋钮（D-G12）"
    assert PROXY_SRC.count("raise AppException(ErrorCode.SERVICE_UNAVAILABLE") >= 3, \
        "不可用面被收窄 ⇒ 某一种失败开始假装成功"


def test_the_two_forwarding_keys_default_to_empty_in_config():
    """工单 §怎么做.3：`go_api_url` / `go_service_token` 的默认值必须是**空串**。

    写成 `http://go-api:8080` 这种"看起来能用"的默认值，配置缺失就长成一次 5 秒空等
    加一条 httpx 协议错误——那长得像 Go 挂了，而真相是没人配地址。两侧不同源：
    一侧是 `config.py` 的源码字面量，另一侧是写死的 `""`，不是拿 settings 比 settings。
    """
    src = (REPO / "backend" / "config.py").read_text(encoding="utf-8")
    for key in ("go_api_url", "go_service_token"):
        assert re.search(rf"^\s+{key}: str = \"\"$", src, re.M), \
            f"config.py 的 {key} 默认值不是空串 ⇒ 缺配置不再在拨号之前失败"


def test_forwarded_surface_has_ten_handlers_and_no_extra_verbs():
    """正向证人：`routers/v3.py` 恰好十个 handler，且动词只用 GET/POST/PATCH。

    为什么单独一条而不是靠上面那条 `Depends` 计数：`Depends` 数的是身份入口，
    有人新加一条**没有身份入口**的端点时那条闸会变红，但这一格还看得见「少了一条」
    与「多了一条没人鉴权的」是两种坏法。动词集合也在这里钉：Go 那侧没有 DELETE，
    BFF 若长出 `@router.delete` 就是一条**打不通**的对外端点——它进客户端契约、
    前端能调，而内部面回 404，那是最难归因的一种死线。
    """
    decorators = re.findall(r"^@router\.(get|post|patch|put|delete)\(", ROUTER_SRC, re.M)
    assert len(decorators) == 10, (
        f"handler 数量 = {len(decorators)}，期望 10（洞察 3＋目标 4＋行动 2＋结果 1）")
    assert set(decorators) <= {"get", "post", "patch"}, \
        f"出现了内部面没有的动词 {sorted(set(decorators) - {'get', 'post', 'patch'})}"


def test_client_contract_exposes_the_forwarded_paths_and_no_internal():
    """这一面的对外形状：十条端点（九条路径）都在，且**没有任何** `/internal` 路径。

    `/api/v1/goals` 一条路径挂 GET＋POST 两种方法，所以是「十条端点、九条路径」。

    两侧不同源：断言拿的是 FastAPI 实际生成的路由表，不是常量表——抄一份期望值
    等于用同一个事实源证明自己。
    """
    from backend.main import app

    paths = {p for p in app.openapi()["paths"]}
    assert {"/api/v1/insights", "/api/v1/insights/{insight_id}",
            "/api/v1/insights/{insight_id}/feedback",
            "/api/v1/goals", "/api/v1/goals/{goal_id}",
            "/api/v1/goals/{goal_id}/actions",
            "/api/v1/actions", "/api/v1/actions/{action_id}",
            "/api/v1/actions/{action_id}/outcomes"} <= paths, \
        f"客户端契约里缺了转发面：{sorted(p for p in paths if 'insight' in p or 'goal' in p or 'action' in p)}"
    assert not [p for p in paths if p.startswith("/internal")], \
        "FastAPI 上挂了 /internal 路径 ⇒ 内部面被暴露到客户端可达的面上"


def test_thin_surface_still_returns_gos_payload():
    """正向证人的功能面：薄到只搬 data，但这条链跑起来真的搬到了。

    上一条只证明"文件里那些字符串还在"，这一条证明成功路径没被改坏：
    Go 的 data 原样出现在信封里，其余四键由 BFF 自己填。
    """
    import httpx
    from fastapi import FastAPI

    from backend.config import settings
    from backend.core.auth import get_current_user
    from backend.core.middleware import register_exception_handlers
    from backend.routers import v3 as v3_router
    from backend.services import v3_proxy

    body = {"code": 200, "message": "success",
            "data": {"items": [{"id": "i1", "status": "published"}], "next_cursor": ""},
            "trace_id": "t", "user_id": "u"}
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=body)

    app = FastAPI()
    app.include_router(v3_router.router, prefix="/api/v1", tags=["v3"])
    register_exception_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: "u-frontend"
    original = v3_proxy._client_factory
    v3_proxy._client_factory = lambda timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=timeout)
    old_url, old_token = settings.go_api_url, settings.go_service_token
    settings.go_api_url, settings.go_service_token = "http://go.test", "tok"
    try:
        resp = TestClient(app).get("/api/v1/insights")
    finally:
        v3_proxy._client_factory = original
        settings.go_api_url, settings.go_service_token = old_url, old_token
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == body["data"]
    assert seen[0].url.path == "/internal/v3/insights"
    assert seen[0].headers["X-User-Id"] == "u-frontend"

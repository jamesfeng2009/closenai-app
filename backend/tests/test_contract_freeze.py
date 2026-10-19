"""
N0 契约冻结验收测试。

被验对象：`contracts/openapi.json`（HTTP 契约基线）与 `contracts/endpoints.md`（端点登记表），
二者由 `.venv/bin/python scripts/export_contract.py` 生成。

验收口径（tasks.md N0）：**端点清单条数 == router 装饰器计数 + 1 条 app 级路由，无未登记端点**
（条数逐批写在 `test_register_count_equals_decorator_count` 的变更说明里，改路由就要同步）。
这里把「无未登记」做成集合相等（既不漏也不多），并额外锁三条 Go 侧最容易改坏的事实：
SSE 端点唯一且事件名齐、PDF 端点按源码计数、业务错误码与 `ErrorCode` 逐值一致。
改了路由没重跑生成脚本 ⇒ 本文件失败，而不是等到 App 上线才发现契约漂移。
"""
import json
import re
from pathlib import Path

from backend.core.errors import ErrorCode
from backend.core.response import success
from backend.main import app

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
METHODS = ("get", "post", "put", "patch", "delete")
DECORATOR_RE = re.compile(
    r"^@(router|app)\.(%s)\(\s*\"([^\"]+)\"" % "|".join(METHODS)
)
ROUTER_PREFIX_RE = re.compile(r"app\.include_router\((\w+)\.router,\s*prefix=\"([^\"]+)\"")


def count_decorators() -> tuple[int, int]:
    """返回 (router 装饰器数, app 级装饰器数)，与 N0 验收用的 grep 计数同口径。"""
    prefixes = dict(
        (m.group(1), m.group(2))
        for m in ROUTER_PREFIX_RE.finditer((ROOT / "backend" / "main.py").read_text())
    )
    router_ops, app_ops = [], []
    for p in sorted((ROOT / "backend" / "routers").glob("*.py")):
        for line in p.read_text().splitlines():
            m = DECORATOR_RE.match(line)
            if m and m.group(1) == "router":
                router_ops.append(f"{prefixes.get(p.stem, '')}{m.group(3)}")
    for line in (ROOT / "backend" / "main.py").read_text().splitlines():
        m = DECORATOR_RE.match(line)
        if m and m.group(1) == "app":
            app_ops.append(m.group(3))
    assert "" not in prefixes.values(), "router 未登记 include_router 前缀"
    return len(router_ops), len(app_ops)


def spec_operations() -> set[tuple[str, str]]:
    spec = json.loads((CONTRACTS / "openapi.json").read_text())
    return {
        (method.upper(), path)
        for path, item in spec["paths"].items()
        for method in item
        if method in METHODS
    }


def table_rows(section_header: str) -> list[list[str]]:
    """取登记表中某个小节的数据行（去掉表头与分隔行）。"""
    text = (CONTRACTS / "endpoints.md").read_text()
    body = text.split(section_header, 1)[1]
    rows = []
    for line in body.splitlines():
        if line.startswith("## "):
            break
        if line.startswith("|") and not re.match(r"^\|\s*[-—]+", line.replace("|", "").strip()):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells[0] in ("#", "端点", "code", "事件") or set(cells[0]) == {"-"}:
                continue
            rows.append(cells)
    return rows


def test_openapi_baseline_is_current():
    """contracts/openapi.json 必须与当前 app.openapi() 逐字节一致。"""
    committed = (CONTRACTS / "openapi.json").read_text()
    current = json.dumps(
        app.openapi(), ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
    assert committed == current, (
        "契约基线漂移：重跑 .venv/bin/python scripts/export_contract.py"
    )


def test_register_count_equals_decorator_count():
    """N0 验收：清单条数 == 104 个 router 端点 + app 级路由。

    97 → 99：W5 T5-01（2026-10-18）新增 GET/POST /user/consents 两条合规台账端点。
    99 → 104：T1-01（2026-10-18）新增 /user/export 申请·列表·状态·下载四条，
    外加 GET /cron/export-jobs 一条队列认领（每 5 分钟）。
    104 → 105：T1-04（2026-10-18）新增 GET /cron/delivery-audit 一条每日交付审计。
    105 → 108：T3-03（2026-10-19）新增洞察面三条纯转发端点
    （GET /insights、GET /insights/{id}、POST /insights/{id}/feedback）。
    """
    router_ops, app_ops = count_decorators()
    assert router_ops == 108, f"router 端点计数变化：{router_ops}（登记表须同步 N/O 工时口径）"
    assert app_ops == 1, f"app 级路由应为 1（GET /health），实得 {app_ops}"
    rows = table_rows("## 五、端点登记表")
    assert len(rows) == router_ops + app_ops == 109


def test_no_unregistered_or_stale_endpoint():
    """登记表与 openapi 的 (方法, 路径) 集合完全相等：无未登记、无已删还在表里。"""
    ops = spec_operations()
    registered = {(r[1].strip("`"), r[2].strip("`")) for r in table_rows("## 五、端点登记表")}
    assert registered == ops, (
        f"仅表里有={sorted(registered - ops)} 仅 openapi 有={sorted(ops - registered)}"
    )


def test_every_row_points_to_an_existing_handler_line():
    """每行 handler 的 file:line 必须真实存在且就是该端点的装饰器行。"""
    for row in table_rows("## 五、端点登记表"):
        ref = row[9].split("`")[1]  # 单元格形如 `file:line` `func`
        file_part, _, line_no = ref.rpartition(":")
        path = ROOT / file_part
        assert path.exists(), f"handler 文件不存在：{ref}"
        line = path.read_text().splitlines()[int(line_no) - 1]
        assert DECORATOR_RE.match(line), f"handler 行不是路由装饰器：{ref} -> {line}"


def test_sse_contract_is_exactly_one_and_events_registered():
    """全仓唯一 SSE 端点必须是 /api/v1/chat/stream，且其 4 个事件名逐个可查。"""
    shapes = {
        (r[1].strip("`"), r[2].strip("`")): r[3] for r in table_rows("## 五、端点登记表")
    }
    sse = [k for k, v in shapes.items() if v == "SSE 流式"]
    assert sse == [("POST", "/api/v1/chat/stream")], f"SSE 端点集合变化：{sse}"
    section = (CONTRACTS / "endpoints.md").read_text().split("## 六、SSE 契约")[1]
    chat_src = (ROOT / "backend" / "routers" / "chat.py").read_text()
    for event in ("memory_context", "token", "done", "error"):
        assert f"`{event}`" in section, f"SSE 事件 {event} 未登记"
        assert f'"{event}"' in chat_src, f"SSE 事件 {event} 源码已不存在，登记表需勘误"


def test_pdf_endpoints_match_source_count():
    """PDF 端点条数与源码里 application/pdf 的 handler 数一致（按装饰器去重）。"""
    rows = [r for r in table_rows("## 五、端点登记表") if r[3] == "PDF 二进制"]
    assert len(rows) == 5, f"PDF 端点数变化：{len(rows)}"
    share_src = (ROOT / "backend" / "routers" / "share.py").read_text()
    assert share_src.count('media_type="application/pdf"') == len(rows)
    pdf_section = (CONTRACTS / "endpoints.md").read_text().split("## 七、二进制")[1]
    for row in rows:
        assert row[2].strip("`") in pdf_section, f"PDF 端点未登记文件名：{row[2]}"


def test_business_error_codes_are_frozen_in_register():
    """业务错误码全集（含 HTTP 归一）必须在登记表里逐值出现。"""
    rows = table_rows("## 四、业务错误码全集")
    codes_in_doc = {int(r[0]) for r in rows}
    assert codes_in_doc == {int(e) for e in ErrorCode}, (
        f"错误码集合与 ErrorCode 不一致：仅文档={sorted(codes_in_doc - {int(e) for e in ErrorCode})} "
        f"仅代码={sorted({int(e) for e in ErrorCode} - codes_in_doc)}"
    )
    by_code = {int(e): e.name for e in ErrorCode}
    for r in rows:
        assert r[1].strip("`") == by_code[int(r[0])], f"错误码名称错位：{r}"


def test_response_envelope_keys_are_unchanged():
    """信封 5 键逐字固定 —— Go 侧与已发布 App 都按这 5 个键名解包。"""
    assert set(success({"x": 1})) == {"code", "message", "data", "trace_id", "user_id"}


def test_frontend_call_sites_are_reconciled():
    """登记表的前端列必须真扫到调用点（防止对账逻辑坏掉时静默显示「无人用」）。"""
    rows = table_rows("## 五、端点登记表")
    with_calls = [r for r in rows if "api.ts" in r[5]]
    assert len(with_calls) >= 40, f"前端对账命中数异常：{len(with_calls)}"
    by_path = {(r[1], r[2].strip("`")): r for r in rows}
    checkin = by_path[("POST", "/api/v1/mood/checkin")]
    assert "api.ts" in checkin[5], "签到端点必须标记为前端在用"
    doc = (CONTRACTS / "endpoints.md").read_text()
    assert "-（无）" in doc.split("悬空检查")[1], "前端存在悬空调用路径"


def test_frontend_compat_baseline_exists():
    """字段级基线（openapi 空 schema 的那一半）必须有文件，且带逐字兼容最小集结论。"""
    text = (CONTRACTS / "frontend-compat.md").read_text()
    assert "逐字兼容最小集" in text
    for mandatory in ("access_token", "message_type", "unseen_count", "data: "):
        assert mandatory in text, f"字段基线缺登记：{mandatory}"

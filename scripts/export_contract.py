"""
N0 契约冻结：把 FastAPI 的对外 HTTP 契约导出为可审计基线。

用法：
    .venv/bin/python scripts/export_contract.py
产物：
    contracts/openapi.json  —— app.openapi() 逐字节基线（sort_keys + 固定缩进）
    contracts/endpoints.md  —— 端点逐条登记表（method/path/handler/响应形态/请求字段/业务码）

两个产物都不可手改：改了路由必须重跑本脚本，否则
backend/tests/test_contract_freeze.py 会失败（条数、集合差、逐字节一致三重校验）。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ROUTERS_DIR = ROOT / "backend" / "routers"
MAIN_PY = ROOT / "backend" / "main.py"
CONTRACTS_DIR = ROOT / "contracts"

METHODS = ("get", "post", "put", "patch", "delete")
DECORATOR_RE = re.compile(
    r'^@(router|app)\.(%s)\(\s*"([^"]+)"(.*)\)$' % "|".join(METHODS)
)
DEF_RE = re.compile(r"^(?:async )?def (\w+)")
INCLUDE_RE = re.compile(
    r"app\.include_router\((\w+)\.router,\s*prefix=\"([^\"]+)\""
)

DOC_HEADER = """# ClosenAI HTTP 契约基线（N0 契约冻结）

> 本文件由 `.venv/bin/python scripts/export_contract.py` 生成，**不要手改**。
> 校验：`backend/tests/test_contract_freeze.py` 断言
> ① 本表条数 == router 装饰器计数 + app 级路由计数；
> ② 表内 (方法, 路径) 集合与 `app.openapi()` 完全相等（无未登记、无多余）；
> ③ `contracts/openapi.json` 与当前 `app.openapi()` 逐字节一致。
>
> 迁移口径（D-1 改判，2026-10-12）：**Go Core 接管对外 HTTP 与权威态**，Python 只留 agent/AI。
> 已打包/已提审的 App 只认本文件登记的契约，因此**本表是 Go 侧的实现清单，不是建议**。
> P1 结论：端点与响应结构不变 ⇒ 客户端零改动 ⇒ 迁移与提审可并行。

## 一、统一响应信封（所有 JSON 端点）

`backend/core/response.py:17` `success()` 固定产出以下 5 个键，**键名逐字不可改**：

| 键 | 类型 | 说明 |
| --- | --- | --- |
| `code` | int | 成功固定 200；失败为业务错误码（见第四节） |
| `message` | str | 人类可读，中英双语以 ` / ` 分隔 |
| `data` | object\\|null | 业务载荷 |
| `trace_id` | str\\|null | 请求追踪 ID（`X-Trace-Id` 透传，`backend/core/middleware.py:38`） |
| `user_id` | str | 未登录为 `""` |

失败响应由全局异常处理器统一构造（`backend/core/middleware.py:52` 起三个 handler），
业务代码只抛 `AppException`，**不手动构造失败体**。

## 二、HTTP 状态码口径

- 装饰器上不写 `status_code=`（实测 `grep -c "status_code=" backend/routers/*.py` = 0）⇒ 成功一律 **200**，无 201/204 语义。
- 业务码是 5 位整数，不能当 HTTP 状态；`backend/core/errors.py:107` `_derive_http_status()` 按段位归一：
  `4xxxx → code//100`（如 40301 → 403），`5xxxx` 同理，其余原值。
- 校验失败（缺字段/类型错/越界）走 FastAPI `RequestValidationError` → **HTTP 400** + `code=40001`
  （注意：不是默认 422；`backend/core/middleware.py:73`）。
- 未处理异常 → **HTTP 500** + `code=500`。

## 三、鉴权与语言参数

- 私有端点靠 `Depends(get_current_user)` 解析 Bearer JWT；公开端点在 `backend/core/auth.py` 白名单内。
- `language` 由 `Depends(get_request_language)` 从查询参/`Accept-Language` 推导，**不属于响应契约**。
- 这两个是 FastAPI 依赖，不体现在 openapi 的 `security` 字段里（`grep -c security contracts/openapi.json` = 0），
  Go 侧需按本表「鉴权」列实现，不能只读 openapi。

## 四、业务错误码全集（Go 侧必须逐值复现）

由 `backend/core/errors.py` 的 `ErrorCode` 生成，共 {code_count} 个：

| code | 名称 | HTTP |
| --- | --- | --- |
"""


def scan_decorators(path: Path, prefix: str):
    """扫一个源文件里的路由装饰器，返回端点记录列表。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = []
    for i, line in enumerate(lines):
        m = DECORATOR_RE.match(line)
        if not m:
            continue
        target, method, sub_path = m.group(1), m.group(2), m.group(3)
        full = f"{prefix}{sub_path}" if target == "router" else sub_path
        # 函数名：装饰器之后第一个 def
        name = ""
        body_end = len(lines)
        for j in range(i + 1, len(lines)):
            dm = DEF_RE.match(lines[j])
            if dm:
                name = dm.group(1)
                break
        for j in range(i + 2, len(lines)):
            if DECORATOR_RE.match(lines[j]) or lines[j].startswith("@"):
                body_end = j
                break
        body = "\n".join(lines[i:body_end])
        rows.append(
            {
                "method": method.upper(),
                "path": full,
                "file": str(path.relative_to(ROOT)),
                "line": i + 1,
                "func": name,
                "body": body,
            }
        )
    return rows


def router_prefixes() -> dict:
    """从 main.py 的 include_router 调用解析各 router 模块的路径前缀（不硬编码）。"""
    text = MAIN_PY.read_text(encoding="utf-8")
    return {mod: prefix for mod, prefix in INCLUDE_RE.findall(text)}


def collect_endpoints():
    prefixes = router_prefixes()
    rows = []
    for p in sorted(ROUTERS_DIR.glob("*.py")):
        if p.stem == "__init__":
            continue
        rows.extend(scan_decorators(p, prefixes.get(p.stem, "")))
    rows.extend(scan_decorators(MAIN_PY, ""))
    return rows


def response_shape(row: dict) -> str:
    body = row["body"]
    if 'media_type="text/event-stream"' in body:
        return "SSE 流式"
    if 'media_type="application/pdf"' in body:
        return "PDF 二进制"
    if "return success(" in body or "success(" in body:
        return "JSON 信封"
    return "其他（需人工确认）"


def pdf_table(rows: list) -> str:
    """从 handler 源码提取 PDF 端点的下载文件名，登记为逐字兼容项。"""
    out = ["\n| 端点 | Content-Disposition filename | 出处 |\n| --- | --- | --- |\n"]
    for r in sorted(rows, key=lambda x: x["path"]):
        if response_shape(r) != "PDF 二进制":
            continue
        m = re.search(r'filename = f?"([^"]+)"', r["body"]) or re.search(
            r'filename="([^"]+)"', r["body"]
        )
        name = m.group(1) if m else "（未声明）"
        if "{" in name:
            name += "（按路径/查询参数拼接）"
        out.append(
            f"| {r['method']} `{r['path']}` | `{name}` | "
            f"`{r['file']}:{r['line']}` |\n"
        )
    return "".join(out)


def frontend_calls() -> list[tuple[str, str, int]]:
    """扫前端调用点：`request<T>('/path')` 与 `${API_BASE}/path`，返回 (原始路径, 文件, 行号)。"""
    src = ROOT / "frontend" / "src"
    call_re = re.compile(
        r"(?:request<[^(]*>\(\s*|API_BASE\}[`'\"]?)[`'\"](/[^\s`'\"]*)"
    )
    found = []
    for p in sorted(src.rglob("*.ts*")):
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in call_re.finditer(line):
                found.append((m.group(1), str(p.relative_to(ROOT)), n))
    return found


def normalize_frontend_path(raw: str) -> str:
    """去查询串，把 `${x}` 归一为 `{param}`，与 openapi 的 `{x}` 占位对齐。"""
    path = raw.split("?")[0]
    return re.sub(r"\$\{[^}]*\}", "{param}", path)


def wrapper_is_used(raw_calls: list) -> dict:
    """`file:line` -> 该调用点所在 api.ts 封装函数是否被任何屏/hook 引用。

    用途：Go 切流排序要区分「前端在用」与「封装存在但没接线」，
    后者改了响应字段也不会立刻炸 App，可后置（P1 硬约束面只含前者）。
    """
    src = ROOT / "frontend" / "src"
    api_text = (src / "services" / "api.ts").read_text(encoding="utf-8").splitlines()
    fn_re = re.compile(r"^export (?:async )?function (\w+)")
    cache: dict[str, list[str]] = {}
    out: dict = {}

    def callers(name: str) -> list[str]:
        if name not in cache:
            hits = []
            for p in src.rglob("*.ts*"):
                if p.name == "api.ts":
                    continue
                for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                    # 只认「被调用」：import 语句里的函数名不带左括号，不计
                    if re.search(rf"\b{re.escape(name)}\s*\(", line):
                        hits.append(f"{p.relative_to(ROOT)}:{n}")
            cache[name] = hits
        return cache[name]

    for file, line in {(c[1], c[2]) for c in raw_calls}:
        name = ""
        for j in range(int(line) - 1, -1, -1):
            m = fn_re.match(api_text[j])
            if m:
                name = m.group(1)
                break
        out[f"{file}:{line}"] = bool(name) and bool(callers(name))
    return out


def match_frontend(rows: list) -> tuple[dict, list]:
    """登记表 ↔ 前端调用点对账。返回 (登记索引 -> 前端出处列表, 无后端对应的前端调用)。

    前端写的是相对路径（`/mood/checkin`），登记表是带挂载前缀的全路径，
    故两边都剥到「router 内声明的路径」再比；参数段 `${id}` 与 `{memory_id}` 归一后按模式匹配。
    """
    prefixes = set(router_prefixes().values())
    calls = frontend_calls()
    used_map = wrapper_is_used(calls)
    by_register: dict = {}
    unmatched = []
    for raw, file, line in calls:
        norm = normalize_frontend_path(raw)
        pattern = None
        if "{" in norm:
            pattern = re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", norm) + "$")
        hit = None
        for i, r in enumerate(rows):
            candidates = {r["path"]} | {
                r["path"][len(prefix):]
                for prefix in prefixes
                if r["path"].startswith(prefix)
            }
            if norm in candidates or (pattern and any(pattern.match(c) for c in candidates)):
                hit = i
                break
        if hit is None:
            unmatched.append(f"`{raw}`（{file}:{line}）")
        else:
            ref = f"{file}:{line}"
            if not used_map.get(ref, True):
                ref += "（封装未接线）"
            by_register.setdefault(hit + 1, []).append(ref)
    return by_register, sorted(set(unmatched))


def spec_index(spec: dict) -> dict:
    """(METHOD, path) -> 该操作的响应码、请求体模型与其字段/必填、其余参数。"""
    out = {}
    schemas = spec.get("components", {}).get("schemas", {})
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method not in METHODS:
                continue
            codes = sorted(op.get("responses", {}))
            props, required = [], []
            rb = op.get("requestBody", {})
            content = rb.get("content", {})
            schema = content.get("application/json", {}).get("schema", {})
            ref = schema.get("$ref", "")
            model = ref.rsplit("/", 1)[-1] if ref else ""
            if model in schemas:
                props = sorted(schemas[model].get("properties", {}))
                required = sorted(schemas[model].get("required", []))
            params = [
                f"{q['name']}({'query' if q['in'] == 'query' else q['in']})"
                for q in op.get("parameters", [])
            ]
            out[(method.upper(), path)] = {
                "codes": codes,
                "model": model,
                "props": props,
                "required": required,
                "params": params,
            }
    return out


def build_register(rows: list, spec: dict) -> str:
    from backend.core.errors import ErrorCode, _derive_http_status

    ops = spec_index(spec)
    missing = [f"{m} {p}" for (m, p) in ops if (m, p) not in {(r["method"], r["path"]) for r in rows}]
    extra = [f"{r['method']} {r['path']}" for r in rows if (r["method"], r["path"]) not in ops]
    if missing or extra:
        raise SystemExit(
            f"端点集合与 openapi 不一致：仅代码有={extra} 仅 openapi 有={missing}"
        )

    rows = sorted(rows, key=lambda x: (x["path"], x["method"]))
    by_register, unmatched = match_frontend(rows)

    parts = [DOC_HEADER.replace("{code_count}", str(len(ErrorCode)))]
    for e in ErrorCode:
        parts.append(f"| {int(e)} | `{e.name}` | {_derive_http_status(int(e))} |\n")

    parts.append(
        "\n## 五、端点登记表\n\n"
        "> 422 列 = openapi 中该操作声明的响应码集合（装饰器无 status_code，故只可能是 `200`/`200+422`）。\n"
        "> 鉴权列：`auth` = handler 依赖 `get_current_user`；`anon` = 无该依赖（含 cron 走 `X-Cron-Secret` 与公开端点）。\n"
        "> 前端列 = 已打包 App 的实际调用点（自动对账 `frontend/src`），**有出处 ⇒ 该端点属 P1 零改动范围，切流必须先保它**。\n\n"
        "| # | 方法 | 路径 | 响应形态 | 鉴权 | 前端调用 | openapi 响应码 | 请求体模型(字段·必填) | 其余参数 | handler |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
    )
    for i, r in enumerate(rows, 1):
        info = ops[(r["method"], r["path"])]
        fields = "-"
        if info["model"]:
            req = set(info["required"])
            fields = f"`{info['model']}`: " + ", ".join(
                f"{p}*" if p in req else p for p in info["props"]
            )
        elif any(k in r["body"] for k in ("req.", "request.")):
            fields = "（无声明模型，按 handler 读取）"
        other = ", ".join(info["params"]) or "-"
        auth = "auth" if "get_current_user" in r["body"] else "anon"
        used = "<br>".join(by_register.get(i, [])) or "—"
        parts.append(
            f"| {i} | {r['method']} | `{r['path']}` | {response_shape(r)} | {auth} | {used} | "
            f"{'+' .join(info['codes'])} | {fields} | {other} | "
            f"`{r['file']}:{r['line']}` `{r['func']}` |\n"
        )

    parts.append(SSE_SECTION)
    parts.append(PDF_SECTION.replace("{pdf_rows}", pdf_table(rows)))
    parts.append(NOTES_SECTION)
    refs = {i: v for i, v in by_register.items()}
    wired = sum(1 for v in refs.values() if not any("未接线" in r for r in v))
    parts.append(
        FRONTEND_SECTION.replace("{total}", str(len(rows)))
        .replace("{called}", str(len(refs)))
        .replace("{wired}", str(wired))
        .replace(
            "{unmatched}",
            "\n".join(f"- {u}" for u in unmatched) if unmatched else "-（无）",
        )
    )
    return "".join(parts)


def main() -> None:
    from backend.main import app

    CONTRACTS_DIR.mkdir(exist_ok=True)
    spec = app.openapi()
    (CONTRACTS_DIR / "openapi.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    rows = collect_endpoints()
    (CONTRACTS_DIR / "endpoints.md").write_text(
        build_register(rows, spec), encoding="utf-8"
    )
    print(
        f"contracts/openapi.json: {len(spec['paths'])} paths / {len(spec_index(spec))} operations\n"
        f"contracts/endpoints.md: {len(rows)} 条端点登记"
    )


SSE_SECTION = """
## 六、SSE 契约（唯一一条流式端点）

`POST /api/v1/chat/stream`（`backend/routers/chat.py:53`）——前端逐事件解析，**事件名与字段名逐字不可改**。

- 响应头（`backend/routers/chat.py:44` `_stream_headers()`）：
  `Cache-Control: no-cache`、`Connection: keep-alive`、`X-Accel-Buffering: no`
- 帧格式（`backend/routers/chat.py:39` `_sse_event()`）：每帧一行 `data: {"type": <事件名>, ...}\\n\\n`
- 事件类型全集：

| 事件 | 载荷字段 | 出处 |
| --- | --- | --- |
| `memory_context` | `memories: [str]`（截前 3 条） | `backend/routers/chat.py:102` |
| `token` | `content: str` | `backend/routers/chat.py:114` |
| `done` | 无 | `backend/routers/chat.py:130` |
| `error` | `code: int`、`message: str` | `backend/routers/chat.py:136` |

⚠️ 进入流之前仍走信封错误（403 `40303` AI_CONSENT_REQUIRED、429 `40301/42901` 配额），
即前端要同时处理「HTTP 非 200 + JSON 信封」和「200 + SSE `error` 帧」两条失败路径。
"""

PDF_SECTION = """
## 七、二进制（PDF）端点

以下端点返回 `StreamingResponse(iter([pdf]))`，**不是** JSON 信封，前端按字节流落盘：
`Content-Type: application/pdf` + `Content-Disposition: attachment; filename="<登记值>"`。
Go 侧接管后必须保持文件名逐字一致（分享/归档链路按文件名展示）。

{pdf_rows}
"""

FRONTEND_SECTION = """
## 九、前端调用面对账（自动扫描 `frontend/src`）

- 扫法：`request<T>('/path')` 与 `${API_BASE}/path` 两类调用点（前端只有 api.ts 一处出口发请求，
  `services/api.ts:201` 统一 `fetch`、`:296` SSE 走 XHR，其余 service 均转调 api.ts）。
- 结果：**{total} 个端点里 {called} 个有前端调用点，其中 {wired} 个的封装确实被屏/hook 接线**。
  「前端调用」列带 `（封装未接线）` 的 = api.ts 里有函数但全仓无人调用
  ⇒ 改这些端点的响应不会立刻炸 App，Go 切流可排在后；无标记的才是 P1 硬约束面。
- 悬空检查（前端调了但登记表里没有的路径）：

{unmatched}

字段级（`data` 里的键名）逐字兼容清单不在本文件——openapi 的 200 响应是空 schema（见第八节），
基线来自 `contracts/frontend-compat.md`。
"""


NOTES_SECTION = """
## 八、登记缺口（带日期，按「发现缺陷不顺手修」只记不补）

- ↳ 2026-10-13 **响应载荷无类型**：全部 98 个端点的 200 响应在 openapi 里都是空 schema
  （`grep -c "response_model=" backend/routers/*.py` = 0，因 handler 返回 `success(dict)`）。
  ⇒ `contracts/openapi.json` 只能冻结**请求侧 + 信封**，`data` 内字段基线只能来自
  `contracts/frontend-compat.md`（前端实际消费面）。归属 N2 建 canonical schema 时用
  `response_model` 补齐，不在 N0 逐端点加注解（会与 N8 按域切流重复劳动）。
- ↳ 2026-10-13 **业务错误码不在 openapi**：错误只由全局异常处理器产生，规范里查不到 4xx 码。
  本文件第四节是替代基线，Go 侧需逐值复现。
- ↳ 2026-10-13 **`GET /health`（app 级）与 `GET /api/v1/cron/health` 同名 operationId 前缀**，
  前者 98 条里唯一不挂 router，故与「97 个 router 端点」计数相差 1 的原因即此（不是漏登记）。
"""

if __name__ == "__main__":
    main()

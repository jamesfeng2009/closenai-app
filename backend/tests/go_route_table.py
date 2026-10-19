"""Go 路由声明表（`internal/httpapi/routes.go` 的 `routeTable`）的 Python 侧读法。

T6-02（2026-10-19）把 `server.go` 里散着的十二行 `s.mux.HandleFunc("GET /…", …)` 抽成了一张
声明表——因为切流矩阵要按 path × method 判「这个域补齐了没有」，而那需要一个**可枚举的真源**。
副作用是：所有 grep `server.go` 文本找 `"GET /api/v1/…"` 的既有闸会一起失效，
而**不会报错**（文本匹配不上只是断言红，最怕的是有人把它改成更松的形状）。
所以这里只放两种读法，让那些闸继续判同一件事：

* `go_ops()`：「Go 答得了哪些 (方法, 路径)」——读生成物 `contracts/cutover-matrix.md`。
  它由 `go run ./tools/cutovermatrix -write` 从 routeTable 生成，
  而「md 与现账同源」由 `go-core/internal/cutover/path_method_matrix_test.go` 钉住；
  在 Python 再解析一遍 Go 源码＝两套口径（本仓铁律：断言两侧不许同源）。
* `route_row(...)`：某条声明**在表里的形状**（方法／路径／域键／挂在哪个批次开关下）。
  这是只有读源码才拿得到的部分——生成物里没有「gated 是哪个 batch」这一列。

两者一起用：`go_ops()` 证「这个操作 Go 答得了」，`route_row()` 证「它登记在正确的域与开关下」。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MATRIX = ROOT / "contracts" / "cutover-matrix.md"
ROUTES_GO = ROOT / "go-core" / "internal" / "httpapi" / "routes.go"

METHODS = ("GET", "POST", "PATCH", "PUT", "DELETE")

# T6-04（2026-10-20）· 审批位的键名**登记在这里一处**。
# `CLOSEN_CUTOVER_APPROVED` 长得像域键，但它不是域：它是「这批放量有没有人签字」那一位。
# 本文件下面那两面从 ConfigMap 推「切流域集合」的对账
# （test_k8s_manifests.py 的 key_only、test_n8_batch2_contract.py 的 configmap_domains）
# 一旦把它当成一个叫 `approved` 的域，红会红得像「登记面漂移」而不是「多了个审批位」。
# 所以两处都调 cutover_domain_keys()，不在各自文件里写第二份排除条件。
CUTOVER_APPROVAL_KEY = "CLOSEN_CUTOVER_APPROVED"
CUTOVER_KEY_PREFIX = "CLOSEN_CUTOVER_"


def cutover_domain_keys(data: dict[str, str]) -> list[str]:
    """ConfigMap（或任何键面）里那些**真是切流域**的键，按字典序返回。

    排除项只有 CUTOVER_APPROVAL_KEY 那一格，且按逐字名排除——不用前缀长度、
    不用「值是不是 0..100」那种猜法：猜法会把下一个非域键静默收进来。
    """
    return sorted(
        key for key in data
        if key.startswith(CUTOVER_KEY_PREFIX) and key != CUTOVER_APPROVAL_KEY
    )


def normalize_param(path: str) -> str:
    """`{memory_id}` → `{}`：路径参数改名不该打红切流闸（与 Go 的 NormalizePath 同口径）。"""
    return re.sub(r"\{[^/}]+\}", "{}", path)


def go_ops() -> set[tuple[str, str]]:
    """生成物里「Go 已注册（方法 路径）」那一列的全集（8 行矩阵并起来）。"""
    text = MATRIX.read_text(encoding="utf-8")
    out: set[tuple[str, str]] = set()
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 8 or not cells[0].startswith("`CLOSEN_CUTOVER_"):
            continue
        for token in cells[3].split("、"):
            m = re.fullmatch(r"(%s) (/\S+)" % "|".join(METHODS), token)
            if m:
                out.add((m.group(1), normalize_param(m.group(2))))
    assert out, (
        f"{MATRIX.relative_to(ROOT)} 里一格「Go 已注册」都没解析出来＝生成物形状变了，"
        "所有依赖本函数的对账会空转（不是绿，是瞎）"
    )
    return out


def _flat() -> str:
    """把 routes.go 的空白压平成单行风格，好让断言不钉缩进与换行（GR 型绿闸的教训）。"""
    src = ROUTES_GO.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = "".join(
        ln for ln in src.splitlines(True) if not ln.strip().startswith("//")
    )
    return re.sub(r"\s+", " ", src)


def route_row(method: str, path: str, domain: str, batch: str | None) -> bool:
    """声明表里是否有 `{method, path, domain, gated=该批次}` 这一条（逐字段比，不靠子串运气）。"""
    gate = (
        "gated: func(s *Server) bool { return %s }" % batch
        if batch
        else ""
    )
    row = re.escape(
        f'{{method: "{method}", path: "{path}"'
        + (f', domain: "{domain}"' if domain else "")
        + (f", {gate}" if gate else "")
    )
    # 字段顺序是 gofmt 的结果（method→path→domain→gated→handler），
    # 所以这里比的是**前缀**：后面的 handler 不归本函数管。
    return re.search(row, _flat()) is not None


def domain_consts() -> dict[str, str]:
    """`domain: cutoverMood` 那种**常量名**到字面值的映射（常量住在 batch5.go／batch6.go，
    所以扫整个 httpapi 包，而不是只扫 routes.go）。"""
    httpapi = ROUTES_GO.parent
    assign = re.compile(r'\b((?:\w*Cutover\w*|cutover\w*))\s*=\s*"([a-z][a-z0-9-]*)"')
    out: dict[str, str] = {}
    for go in sorted(httpapi.glob("*.go")):
        if go.name.endswith("_test.go"):
            continue
        for m in assign.finditer(go.read_text(encoding="utf-8")):
            out.setdefault(m.group(1), m.group(2))
    return out


def go_table_ops() -> set[tuple[str, str]]:
    """声明表里**带切流域键**的全部 (方法, 归一 path)，直接读 `routes.go` 的源码形状。

    与 `go_ops()` 分两侧是有意的：那一侧是生成物，这一侧是源码。
    只读生成物的话，「删掉一条声明但没重跑生成器」这种退步在 Python 侧看不见
    （2026-10-19 由 mutate_n8_gates 的 #227／#228 抓到——它们撤的就是源码那张表）。
    """
    consts = domain_consts()
    row = re.compile(
        r'\{method: "(%s)", path: "([^"]+)", domain: (?:("([^"]+)")|(\w+))'
        % "|".join(METHODS)
    )
    out: set[tuple[str, str]] = set()
    for m in row.finditer(_flat()):
        domain = m.group(4) or consts.get(m.group(5), "")
        if domain:
            out.add((m.group(1), normalize_param(m.group(2))))
    assert out, (
        f"{ROUTES_GO.relative_to(ROOT)} 的声明表里一条带域键的路由都没解析出来＝本函数在空转"
        "（不是绿，是瞎）"
    )
    return out


def domain_paths(domain: str) -> set[tuple[str, str]]:
    """某个域在声明表里注册的全部 (方法, 路径)——从生成物的行里取，保证与 `go_ops()` 同源。"""
    text = MATRIX.read_text(encoding="utf-8")
    prefix = f"`CLOSEN_CUTOVER_{domain.upper().replace('-', '_')}`"
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 8 and cells[0] == prefix:
            out: set[tuple[str, str]] = set()
            for token in cells[3].split("、"):
                m = re.fullmatch(r"(%s) (/\S+)" % "|".join(METHODS), token)
                if m:
                    out.add((m.group(1), normalize_param(m.group(2))))
            return out
    raise AssertionError(f"矩阵里没有 {prefix} 这一行")

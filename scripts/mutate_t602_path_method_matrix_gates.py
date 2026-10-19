#!/usr/bin/env python3
"""T6-02（切流 path × method 矩阵）的变异自证（2026-10-19）。

这一批交付的是**决策点 D-10／D-5 从注释变成代码**：
`deploy/k8s/50-ingress.yaml`（谁上了刀）、`contracts/endpoints.md`（一条 path 上有几种方法）、
`internal/httpapi/routes.go` 的声明表（Go 答得了哪些）三张真源，
由 `internal/cutover/matrix.go::Violations()` 逐条对账，生成 `contracts/cutover-matrix.md`。

为什么必须逐条打红**自己那一条判据**（[[feedback-mutation-anchor-must-reach-target]]）：
这类「清单对账」闸最容易死在两种地方，本脚本两侧各造一份坏料——

* **对账判不动**：一条刀指错了后端、一个域方法不全却上了刀，矩阵如果只按 path 归堆而不按
  (path × 契约方法集) 判，就永远不会红 ⇒ M1…M4；
* **判据看着有其实扫空集**：三张源里任何一张解析成空（形状漂了、正则漏了），
  所有「归零型」判据会在空集上永远成立，看起来像「全部对齐」
  ⇒ M5…M7 各从键面、装配面、域列三个方向把证人打红，证明证人不是摆设。

靶面按**违例码**分工，一条变异只算它自己那条测的红（闸测用 `filterByCode` 只认自己的码）：

- M1 反代加一条 `Exact /api/v1/reflections → go-api`（工作单点名的那条）⇒ `VMissing`
  ⇒ `TestNoBoundContractOperationLacksGoRoute`：这条 path 上契约里还有 POST，
  Go 只注册了 GET，一刀下去 POST 一起搬来 ⇒ 405，**与灰度百分比无关**（0% 也救不回来，
  转交发生在 handler 内部，路径没匹配上根本进不到 handler）；
- M2 同一把刀，判**裁决面** ⇒ `TestMethodIncompleteDomainsAreNotBound`：
  矩阵不许把方法不全的域标成「可切」，原因里必须留着 D-10 这个出处；
- M3 mood 的 `Exact /api/v1/mood/history` 写成 `Prefix /api/v1/mood` ⇒ `VPrefix`
  ⇒ `TestPrefixRulesOnlyOnMethodCompleteDomains`：D-5「域级键 × 端点级刀」里
  前缀那一刀会把这个域**将来新增的端点**自动搬进没注册它们的 mux。
  ↳ 这一条第一次跑的时候**没打红**：`ParseIngressRules` 的八行前视窗口读进了下一条规则的
  `pathType: Exact`，把坏料自己"改回"了 Exact——判据在真坏料上绿。已修（加 `- path:` 边界
  break，2026-10-19），并由 M13 专门盯这个 break、由 `TestIngressRuleBoundaryIsPerRule`
  拿一段合成清单钉住边界（真清单里 mood 两条恰好都写 Exact，靠它蒙对不算证）；
- M4 Caddyfile（对公网那层）加一条 `reverse_proxy go-api:8080` ⇒ `VPublic`
  ⇒ `TestPublicProxyNeverBindsGoApi`：go-api 只应集群内可达（方案 §12／T0-01），
  公网这层出现它＝清单写错，跟方法齐全与否无关；
- M5 `cmd/go-api/main.go` 的 `cutover.New` 里删掉 `"memories"` 那格 ⇒ `VAssembly`
  ⇒ `TestEveryCutoverKeyHasImplementationAndAssembly`：这就是 D-G12 踩过的坑——
  缺格的语义是「当成满额自答」，上刀那天把键调回 0 也回滚不了；
- M6 同一格改成 `"memories": cfg.CutoverMood`（键名与字段名对不上）⇒ 同一测：
  装配表自己写错时 `ParseAssemblyKeys` **不许替它猜一个**，得让对称判据去红；
- M7 `internal/config/config.go` 删掉 `CLOSEN_CUTOVER_MEMORIES` 那行 ⇒ 矩阵少一行
  ⇒ `TestMatrixInputsAreNonEmpty`：抓的是「键面解析漂了但所有对账照旧绿」这一口；
- M8 routes.go 抹掉 memories 两条的 `domain:` 字段 ⇒ `VPhantom` ⇒ 那条域键对账红：
  键能拨、声明表里却没有这个域的任何一条路由＝又一个假旋钮；
- M9 routes.go 删掉 `GET /api/v1/mood/trend` 整条声明（Ingress 那把刀没动）
  ⇒ `TestCutoverMatrixMarkdownIsUpToDate`：生成物与现账不再逐字节同源，
  「实现变了但没人重跑生成器」这条路必须红；
- M10 routes.go 把批 2 那条的 `gated` 掏成 `nil` ⇒ `TestRouteTableRegistersEverySpec`：
  机制没接线时 Go 也会抢先回答这一域，声明面 ⇄ 注册面从此不是一张表。
  ↳ 这一条第一次跑也**没打红**：判据① 的「应注册条数」是从同一张表数出来的（同源，
  两边一起加一）⇒ 补了判据⑤「带域键的声明一条都不许出现在批次全 nil 的注册面里」
  （拿运行时的 registered ⇄ 表里的 domain 字段对账，[[feedback-orm-identity-map-gate]] 那一口）；
- M11 routes.go 删掉 curriculum 那条声明 ⇒ Python 侧同一张表的对账红
  （`test_n8_batch2_contract.py::test_go_registers_both_curriculum_routes`）：
  它读的是同一张表的形状，不是 `server.go` 的文本——证明 T6-02 之后两侧真的同源；
- M12 手改生成物（把 memories 那行的「未上刀」写成已上刀）⇒ 与现账逐字节对不上；
- M13 拿掉 `ParseIngressRules` 里那条 `- path:` 边界 break ⇒ `TestIngressRuleBoundaryIsPerRule`：
  钉的是**解析器本身**——没有它，M3 那类清单坏料会被读回好料，整条 D-5 判据形同虚设。

不该打红的证人（GR1／GR2／GR3）：判据读的是**语义与集合**，不钉行号也不钉注释——
往 `matrix.go` 的判据函数上方、往 `routes.go` 的表上方、往 Ingress 的批注区各插 15 行注释，
三面都该照旧全绿。

纪律：串行执行、copy2 备份＋`filecmp(shallow=False)` 逐字节写回、不用 git checkout
（[[feedback-mutation-restore-no-git-checkout]]、[[feedback-mutation-gates-no-concurrency]]）；
每条变异前先跑基线（按 (runner,target,marker) 缓存），基线本来就不绿的记「未跑」，
不把环境的红记成闸的红。

用法：
    ./.venv/bin/python scripts/mutate_t602_path_method_matrix_gates.py --check
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t602_path_method_matrix_gates.py
    ./.venv/bin/python scripts/mutate_t602_path_method_matrix_gates.py --only M1,M4
"""
from __future__ import annotations

import argparse
import ast
import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GO_CORE = REPO / "go-core"
VENV_PY = REPO / ".venv" / "bin" / "python"
GOROOT = os.environ.get("GOROOT", "/Users/fengyu/sdk/go1.27.1")
GO_BIN = os.environ.get("GO_BIN", f"{GOROOT}/bin/go")
GO_ENV = {
    **os.environ,
    "GOROOT": GOROOT,
    "PATH": f"{GOROOT}/bin:{os.environ.get('PATH', '')}",
    "GO_BIN": GO_BIN,
    "GOTOOLCHAIN": "local",
}

ING = REPO / "deploy" / "k8s" / "50-ingress.yaml"
CADDY = REPO / "deploy" / "compose" / "Caddyfile"
MAIN = GO_CORE / "cmd" / "go-api" / "main.go"
CFG = GO_CORE / "internal" / "config" / "config.go"
RC = GO_CORE / "internal" / "httpapi" / "routes.go"
MD = REPO / "contracts" / "cutover-matrix.md"
MX = GO_CORE / "internal" / "cutover" / "matrix.go"

CUT_PKG = "./internal/cutover/"
HTTP_PKG = "./internal/httpapi/"
B2 = "backend/tests/test_n8_batch2_contract.py"

# ── 锚点（逐字从文件里取，--check 会先当场数命中次数）────────────────────────
ING_MOOD_HIST = (
    "          - path: /api/v1/mood/history\n"
    "            pathType: Exact\n"
)
#: M1／M2 的刀：一条**契约上同 path 还挂 POST** 的 path 被指给 go-api。
ING_REFLECTIONS_BOUND = (
    "          - path: /api/v1/reflections\n"
    "            pathType: Exact\n"
    "            backend:\n"
    "              service:\n"
    "                name: go-api\n"
    "                port:\n"
    "                  name: http\n"
)
ING_MOOD_PREFIX = (
    "          - path: /api/v1/mood\n"
    "            pathType: Prefix\n"
)

CADDY_HANDLE = (
    "\thandle /api/* {\n"
    "\t\treverse_proxy py-api:8000\n"
    "\t}\n"
)
CADDY_GO_API = (
    "\thandle /api/v1/mood/trend {\n"
    "\t\treverse_proxy go-api:8080\n"
    "\t}\n" + CADDY_HANDLE
)

MAIN_MEMORIES_CELL = '\t\t"memories": cfg.CutoverMemories,\n'
MAIN_MEMORIES_WRONG = '\t\t"memories": cfg.CutoverMood,\n'

CFG_MEMORIES_KEY = '\t\tCutoverMemories:        envInt("CLOSEN_CUTOVER_MEMORIES", 0),\n'

RC_MEM_LIST = (
    '\t{method: "GET", path: "/api/v1/memories", domain: cutoverMemories,\n'
)
RC_MEM_LIST_NODOMAIN = '\t{method: "GET", path: "/api/v1/memories",\n'
RC_MEM_DETAIL = (
    '\t{method: "GET", path: "/api/v1/memories/{memory_id}", domain: cutoverMemories,\n'
)
RC_MEM_DETAIL_NODOMAIN = '\t{method: "GET", path: "/api/v1/memories/{memory_id}",\n'
RC_MOOD_TREND_ROW = (
    '\t{method: "GET", path: "/api/v1/mood/trend", domain: cutoverMood,\n'
    "\t\tgated:   func(s *Server) bool { return s.batch5 != nil },\n"
    "\t\thandler: func(s *Server) http.HandlerFunc { return s.handleMoodTrend }},\n"
)
RC_B2_WEEK = (
    '\t{method: "GET", path: "/api/v1/curriculum/week", domain: "curriculum",\n'
    "\t\tgated:   func(s *Server) bool { return s.batch2 != nil },\n"
)
RC_B2_WEEK_UNGATED = (
    '\t{method: "GET", path: "/api/v1/curriculum/week", domain: "curriculum",\n'
    "\t\tgated:   nil,\n"
)
RC_B2_WEEK_ROW = RC_B2_WEEK + (
    "\t\thandler: func(s *Server) http.HandlerFunc { return s.handleCurriculumWeek }},\n"
)

#: M13：解析器那条「一条规则到此为止」的边界。拿掉它，八行窗口会读进下一条规则，
#: 于是「Exact 改成 Prefix」这类清单改动会被解析器自己改回 Exact——判据在真坏料上绿。
MX_BREAK = (
    "\t\t\tif rulePathRE.MatchString(lines[j]) {\n"
    "\t\t\t\tbreak\n"
    "\t\t\t}\n"
)

#: M9 之外的靶：md 是生成物，手改一格「不可切」⇒ 与现账不再同源。
MD_UNCUT_ROW = "| `CLOSEN_CUTOVER_MEMORIES` | 0% | —（未上刀） |"
MD_CUT_ROW = "| `CLOSEN_CUTOVER_MEMORIES` | 0% | Exact /api/v1/memories |"

GR_COMMENT_GO = "// 绿闸注脚 {i:02d}：这条判据读的是集合与语义，不钉行号也不钉注释。\n"
GR_COMMENT_YAML = "# 绿闸注脚 {i:02d}：这条判据读的是 path × backend 的归属，不钉注释与行号。\n"
GR_ING_ANCHOR = "          - path: /api/v1/onboarding\n"
MX_ANCHOR = "func Violations(rows []DomainRow, rules []ProxyRule, plan []GoRoute, goConfigSrc string,\n"
RC_TABLE_ANCHOR = "var routeTable = []routeSpec{\n"


def _comments(single: str, n: int = 15) -> str:
    """序号必须留在**注释行内**：写到换行后面就变成裸语句，红的是编译器而不是判据。"""
    return "".join(single.format(i=i) for i in range(1, n + 1))


MUTATIONS: list[tuple] = [
    (
        "M1", "反代加一条 Exact /api/v1/reflections ⇒ 已上刀的 path 上有 Go 答不了的方法",
        [(ING, ING_MOOD_HIST, ING_REFLECTIONS_BOUND + ING_MOOD_HIST)],
        "go", CUT_PKG, "^TestNoBoundContractOperationLacksGoRoute$", False,
    ),
    (
        "M2", "同一把刀 ⇒ 方法不全的域被上刀／被标可切，裁决面当场红",
        [(ING, ING_MOOD_HIST, ING_REFLECTIONS_BOUND + ING_MOOD_HIST)],
        "go", CUT_PKG, "^TestMethodIncompleteDomainsAreNotBound$", False,
    ),
    (
        "M3", "mood 的端点级 Exact 被写成域级 Prefix ⇒ D-5 那条「只许按端点列」红",
        [(ING, ING_MOOD_HIST, ING_MOOD_PREFIX)],
        "go", CUT_PKG, "^TestPrefixRulesOnlyOnMethodCompleteDomains$", False,
    ),
    (
        "M4", "Caddyfile（公网那层）反代到 go-api ⇒ 「go-api 不对公网暴露」红",
        [(CADDY, CADDY_HANDLE, CADDY_GO_API)],
        "go", CUT_PKG, "^TestPublicProxyNeverBindsGoApi$", False,
    ),
    (
        "M5", "装配表删掉 memories 那格 ⇒ D-G12 的「缺格＝当成满额自答」红",
        [(MAIN, MAIN_MEMORIES_CELL, "")],
        "go", CUT_PKG, "^TestEveryCutoverKeyHasImplementationAndAssembly$", False,
    ),
    (
        "M6", '装配表写成 "memories": cfg.CutoverMood ⇒ 键名与字段名不对账，判据不许替它猜',
        [(MAIN, MAIN_MEMORIES_CELL, MAIN_MEMORIES_WRONG)],
        "go", CUT_PKG, "^TestEveryCutoverKeyHasImplementationAndAssembly$", False,
    ),
    (
        "M7", "config.go 删掉一个放量键 ⇒ 矩阵行数证人红（键面解析漂了不能静默）",
        [(CFG, CFG_MEMORIES_KEY, "")],
        "go", CUT_PKG, "^TestMatrixInputsAreNonEmpty$", False,
    ),
    (
        "M8", "routes.go 抹掉 memories 两条的 domain ⇒ 键能拨但声明表里这个域不存在",
        [(RC, RC_MEM_LIST, RC_MEM_LIST_NODOMAIN), (RC, RC_MEM_DETAIL, RC_MEM_DETAIL_NODOMAIN)],
        "go", CUT_PKG, "^TestEveryCutoverKeyHasImplementationAndAssembly$", False,
    ),
    (
        "M9", "routes.go 删掉一条已上刀的实现（刀没动）⇒ 生成物与现账不再逐字节同源",
        [(RC, RC_MOOD_TREND_ROW, "")],
        "go", CUT_PKG, "^TestCutoverMatrixMarkdownIsUpToDate$", False,
    ),
    (
        "M10", "routes.go 把批 2 的 gated 掏成 nil ⇒ 声明表 ⇄ 实际注册面从此两张表",
        [(RC, RC_B2_WEEK, RC_B2_WEEK_UNGATED)],
        "go", HTTP_PKG, "^TestRouteTableRegistersEverySpec$", False,
    ),
    (
        "M11", "routes.go 删掉 curriculum 那条声明 ⇒ Python 侧同一张表的对账红（两侧真同源）",
        [(RC, RC_B2_WEEK_ROW, "")],
        "pytest", f"{B2}::test_go_registers_both_curriculum_routes", "", False,
    ),
    (
        "M12", "手改生成物：把 memories 那行的「未上刀」写成已上刀 ⇒ 与现账对不上",
        [(MD, MD_UNCUT_ROW, MD_CUT_ROW)],
        "go", CUT_PKG, "^TestCutoverMatrixMarkdownIsUpToDate$", False,
    ),
    (
        "M13", "拿掉 Ingress 解析器的 `- path:` 边界 break ⇒ 一条规则的字段会读进下一条",
        [(MX, MX_BREAK, "")],
        "go", CUT_PKG, "^TestIngressRuleBoundaryIsPerRule$", False,
    ),
]

GREEN: list[tuple] = [
    (
        "GR1", "matrix.go 判据函数上方插 15 行注释 ⇒ cutover 整包照旧全绿（它不钉排版）",
        [(MX, MX_ANCHOR, _comments(GR_COMMENT_GO) + MX_ANCHOR)],
        "go", CUT_PKG, "", False,
    ),
    (
        "GR2", "routes.go 表上方插 15 行注释 ⇒ 声明表自证照旧全绿",
        [(RC, RC_TABLE_ANCHOR, _comments(GR_COMMENT_GO) + RC_TABLE_ANCHOR)],
        "go", HTTP_PKG, "^TestRouteTableRegistersEverySpec$", False,
    ),
    (
        "GR3", "Ingress 批注区插 15 行注释 ⇒ cutover 整包照旧全绿（归属读的是 path × backend）",
        [(ING, GR_ING_ANCHOR, _comments(GR_COMMENT_YAML) + GR_ING_ANCHOR)],
        "go", CUT_PKG, "", False,
    ),
]


def _apply(edits: list) -> list[Path]:
    """就地改写锚点；锚点命中数必须恰好 1（新建靶由调用方删除）。"""
    created: list[Path] = []
    for path, old, new in edits:
        if old == "":
            assert not path.exists(), f"{path.name}：新建靶已存在（上一轮没清干净？）"
            path.write_text(new, encoding="utf-8")
            created.append(path)
            continue
        assert path.exists(), f"{path}：靶文件不存在"
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:90]!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")
    return created


def _verify_anchor(edits: list) -> str | None:
    for path, old, _new in edits:
        if old == "":
            if path.exists():
                return "新建靶已存在"
            continue
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"命中 {n} 次（要 1）"
    return None


def _sanity(edits: list) -> str | None:
    """靶被改成语法不通时，红的是解释器／编译器而不是判据——先当场拦下。"""
    for path, _old, new in edits:
        if not path.exists():
            continue
        if path.suffix == ".py":
            try:
                ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                return f"{path.name}：变异体不是合法 Python（{exc}）"
        if path.suffix == ".go":
            src = path.read_text(encoding="utf-8")
            if src.count("{") < src.count("}"):
                return f"{path.name}：变异体花括号不配对，Go 编译会先红"
    return None


def go_available() -> bool:
    return Path(GO_BIN).is_file()


def run(runner: str, target: str, marker: str) -> tuple[int, str]:
    if runner == "go":
        if not go_available():
            return -2, f"{GO_BIN} 不存在：Go 侧用例没跑，不算绿"
        cmd = [GO_BIN, "test", target, "-count=1", "-timeout", "300s"]
        if marker:
            cmd += ["-run", marker]
        cwd = GO_CORE
    elif runner == "pytest":
        cmd = [str(VENV_PY), "-m", "pytest", target, "-q", "--no-header", "-x"]
        cwd = REPO
    else:
        raise AssertionError(f"未知 runner：{runner}")
    proc = subprocess.run(cmd, cwd=cwd, env=GO_ENV, capture_output=True,
                          text=True, timeout=1800)
    return proc.returncode, proc.stdout + proc.stderr


def _report_name(marker: str) -> str:
    """把 `-run` 的正则还原成**用例名**：只剥两端的锚点。"""
    name = marker[1:] if marker.startswith("^") else marker
    return name[:-1] if name.endswith("$") else name


def is_red(code: int, out: str, runner: str, target: str, marker: str) -> bool:
    """「红」＝**那条判据自己**失败；工具链坏／没匹配到用例／skip 都不算。"""
    if code == 0:
        return False
    if runner == "go":
        if "[build failed]" in out or "no tests to run" in out or "no test files" in out:
            return False
        if marker:
            return f"--- FAIL: {_report_name(marker)}" in out
        # 全包口径（GR 用的基线）：任何一条 FAIL 都算红。
        return "--- FAIL:" in out
    if "no tests ran" in out:
        return False
    return f"FAILED {target}" in out


BASELINE: dict[tuple[str, str, str], tuple[int, str]] = {}


def baseline(runner: str, target: str, marker: str) -> tuple[int, str]:
    key = (runner, target, marker)
    if key not in BASELINE:
        BASELINE[key] = run(runner, target, marker)
    return BASELINE[key]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    cases = [*MUTATIONS, *GREEN]
    if args.check:
        bad = 0
        for case in cases:
            problem = _verify_anchor(case[2])
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}（共 {len(cases)} 条）")
        return 1 if bad else 0

    if not VENV_PY.is_file():
        print(f"解释器不存在：{VENV_PY}（变异自证要求先备好 .venv）")
        return 2
    if not go_available():
        print(f"提示：没找到 {GO_BIN}，Go 侧靶会被记「未跑」，不算绿")

    paths = sorted({e[0] for case in cases for e in case[2] if e[1] != ""})
    with tempfile.TemporaryDirectory(prefix="mutate-t602-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            rel = path.relative_to(REPO).as_posix().replace("/", "+")
            bak = Path(tmp) / f"{idx:02d}-{rel}"
            assert bak not in originals.values(), f"备份名碰撞：{bak}"
            shutil.copy2(path, bak)
            originals[path] = bak

        def restore() -> None:
            for path, bak in originals.items():
                shutil.copy2(bak, path)
                assert filecmp.cmp(bak, path, shallow=False), f"{path} 恢复后字节不一致"

        red_ok = red_fail = green_ok = green_fail = 0
        unrun: list[str] = []
        failures: list[str] = []
        try:
            for cid, desc, edits, runner, target, marker, _db in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(runner, target, marker)
                if base_code == -2:
                    unrun.append(cid)
                    print(f"[{cid}] 未跑（{base_out}）：缺工具链不是绿，也不把它的红记在闸头上")
                    continue
                if base_code != 0:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）："
                          "不能把环境的红记成闸的红\n  "
                          + "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                created: list[Path] = []
                try:
                    created = _apply(edits)
                    broken = _sanity(edits)
                    code, out = (-1, broken) if broken else run(runner, target, marker)
                finally:
                    for path in created:
                        path.unlink(missing_ok=True)
                    restore()
                if code == -1:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（变异体自身不合法）：{out}")
                    continue
                if is_red(code, out, runner, target, marker):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓（{runner}）  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（exit={code}，闸没咬住）：{desc}\n  "
                          + "\n  ".join(out.splitlines()[-12:]))

            for cid, desc, edits, runner, target, marker, _db in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(runner, target, marker)
                if base_code != 0:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 跳过（基线本来就不绿，不能把环境的红记成闸的红）："
                          f"{base_out[-300:]}")
                    continue
                try:
                    _apply(edits)
                    broken = _sanity(edits)
                    code, out = (-1, broken) if broken else run(runner, target, marker)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 照旧绿 ✓（{runner}）  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 绿闸失守 ✗（exit={code}）：{desc}\n  "
                          + "\n  ".join(out.splitlines()[-12:]))
        finally:
            restore()
            for path, bak in originals.items():
                assert filecmp.cmp(bak, path, shallow=False), f"{path} 收尾字节不一致"

        total_red = red_ok + red_fail
        total_green = green_ok + green_fail
        print(f"\n红闸 {red_ok}/{total_red}｜绿闸 {green_ok}/{total_green}｜"
              f"未跑 {len(unrun)}｜残留 0"
              + (f"（未跑清单：{','.join(unrun)}）" if unrun else ""))
        if failures:
            print(f"失败用例：{', '.join(failures)}")
        return 0 if red_fail == 0 and green_fail == 0 and not unrun else 1


GRP_LABEL = "GR"

if __name__ == "__main__":
    sys.exit(main())

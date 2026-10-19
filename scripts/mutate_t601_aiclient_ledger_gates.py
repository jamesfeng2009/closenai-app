#!/usr/bin/env python3
"""T6-01（`internal/aiclient` 登记为内部能力、未启用）的变异自证（2026-10-19）。

这一批交付的不是功能而是一句**口径**：「接口已经实现 ≠ 功能已经上线」（Baseline §10.4），
而这句话由两侧不同源地看住——台账是人填的（`docs/feature-status.json`），调用方是代码里的事实
（`go-core/internal/aiclient/production_caller_ledger_test.go` 用 `go/parser` 现读 AST）。
这种「登记＋对账」的形状最容易死在两条上，所以每条都必须打红**它自己的那一条判据**
（[[feedback-mutation-anchor-must-reach-target]]）：

* **登记本身没人核**：整条被改名／删掉之后，如果只剩「features 十行覆盖」那种归零闸，
  扫描面会变成空集而照样绿 ⇒ M1／M8／M9 各从一侧抓这一口；
* **判据看着有其实判不动**：状态封顶、处置日期、条款出处、删除动作、impl 指向真文件，
  逐条各造一份坏料（M2…M7），另一条（M12）直接把判据的阈值掏空，证明这条测咬的是判据而不是文案。

靶面分两侧：

台账面（`docs/feature-status.json`）——M2…M7 改的是**真文件**，所以红的必须是
`test_feature_status_truth.py` 里那六条「现账字段闸」（它们读真实 JSON 的那个值），
而不是②③那批只改 deepcopy 的反面用例（那些证的是判据判得动，证不了现账是对的）：
- M1 那条登记的 id 被改名 ⇒ Go 侧「台账里没有这一条」先红（登记消失不会消失得无声）；
- M2 状态抬成 WIRED ⇒ 内部能力没有用户入口，按 §15 末句封顶 IMPLEMENTED；
- M3 处置日期写成过去 ⇒ 「90 天内没调用方就删」必须有牙齿，过期即红；
- M4 处置动作里的「删除」被抹掉 ⇒ 到期动作退化成「到时候再看」；
- M5 理由里两处 `§x.y` 条款锚点都没了 ⇒ 结论必须标出处，没有出处的登记没法复查；
- M6 impl 点名一个不存在的 .go 文件 ⇒ 「登记的接口实现指得回真实代码」这条存在性判据；
- M7 tests 指向一个**真存在但没有测试声明**的文件（`backend/services/cron_service.py`）⇒
  形状上像证据、内容上没测过，这正是 tasks.md 那种死账的原型。
判据本身由 M12（掏空阈值）与反面用例盯住，两个方向各有一条红路。

调用方实测面（`production_caller_ledger_test.go`）——
- M8 台账把 probe 工具写成不存在的文件 ⇒ AST 现读的集合对不上台账点名的集合；
- M9 台账把 probe 工具清空 ⇒ 「今天谁在引用这个包」从此没人答，实测集合里那个真工具变成未登记；
- M10 在 `internal/httpapi` 里造一条**真实的生产 import**（blank import，能编译）⇒
  「零生产调用方」这句话当场失效，必须点名那个文件；
- M11 walk 根漂一级（只扫 `internal/`）⇒ 两条正向证人（扫描面计数、`tools/` 里确有引用者）
  抓得住「扫空集也算绿」这一口——这条变异打的不是断言，是**证人**。

判据掏空面（`scripts/feature_status.py`）——
- M12 把「状态封顶」的比较阈值从 IMPLEMENTED 掏到 VALUE_MEASURED ⇒ 六态里除顶端之外任何虚高
  都放行；反面用例（它自己把 state 改成 WIRED）从此判不动。

不该打红的证人（GR1／GR2）：闸判的是**语义与集合**，不钉行号也不钉注释——
往 Go 那条分类函数上方插 15 行注释、往 Python 判据函数上方插 15 行注释，两侧都该照旧全绿。

纪律：串行执行、copy2 备份＋`filecmp(shallow=False)` 逐字节写回、新建的靶文件跑完即删、
不用 git checkout（[[feedback-mutation-restore-no-git-checkout]]、
[[feedback-mutation-gates-no-concurrency]]）。每条变异前先跑基线（按 (runner,target,marker) 缓存），
基线本来就不绿的记「未跑」，不把环境的红记成闸的红。

用法：
    ./.venv/bin/python scripts/mutate_t601_aiclient_ledger_gates.py --check
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t601_aiclient_ledger_gates.py
    ./.venv/bin/python scripts/mutate_t601_aiclient_ledger_gates.py --only M3,M10
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

LEDGER = REPO / "docs" / "feature-status.json"
FS = REPO / "scripts" / "feature_status.py"
GO_LEDGER_TEST = (
    GO_CORE / "internal" / "aiclient" / "production_caller_ledger_test.go"
)
#: M10 造的「生产调用方」是个**新建文件**，跑完由脚本自己删（不是备份写回）。
GO_MUTANT_CALLER = GO_CORE / "internal" / "httpapi" / "t601_mutation_probe.go"

TRUTH = "backend/tests/test_feature_status_truth.py"
GO_PKG = "./internal/aiclient/"
LEDGER_TEST = "^TestAiclientLedgerEntryMatchesReality$"
CALLER_TEST = "^TestAiclientHasNoProductionCaller$"

# ── 锚点（逐字从文件里取，--check 会先当场数命中次数）────────────────────────
ID_OLD = '      "id": "ai.grpc.client",\n'
ID_RENAMED = '      "id": "ai.grpc.client.renamed-away",\n'
PROBE_LINE = '      "probe_tools": ["go-core/tools/aiclientprobe/main.go"],\n'
STATE_LINE = '      "state": "IMPLEMENTED",\n'
REVIEW_DATE_OLD = '      "review_by": "2027-01-17",\n'
REVIEW_ACTION_OLD = '"review_action": "到期（2027-01-17）仍无生产调用方 ⇒ 删除 '
REVIEW_ACTION_VAGUE = '"review_action": "到期（2027-01-17）仍无生产调用方 ⇒ 再看 '
REASON_CLAUSE_1_OLD = '"reason": "Baseline §10.4：'
REASON_CLAUSE_1_NEW = '"reason": "Baseline 口径：'
REASON_CLAUSE_2_OLD = "；§21 要的「谁是生产调用方」"
REASON_CLAUSE_2_NEW = "；第二十一条要的「谁是生产调用方」"
IMPL_BREAKER_OLD = '        "go-core/internal/aiclient/breaker.go",\n'
IMPL_BREAKER_WITH_GHOST = (
    IMPL_BREAKER_OLD + '        "go-core/internal/aiclient/deleted_gopher.go",\n'
)
TEST_GRPC_OLD = '        "go-core/internal/aiclient/grpc_test.go",\n'
TEST_HOLLOW_FILE = '        "backend/services/cron_service.py",\n'

#: M10 的靶文件内容：一条**真实**的生产 import。用 blank import 是为了让
#: `go test ./internal/aiclient/` 之外的包体也能编译通过——本条变异要证的不是编译，
#: 而是「调用方集合里冒出了一个既不在包内也不在 tools/ 的引用者」。
MUTANT_CALLER_SRC = """package httpapi

// 本文件由 scripts/mutate_t601_aiclient_ledger_gates.py 创建、跑完即删。
// 它存在的唯一目的：让「internal/aiclient 零生产调用方」这句话当场变成假的。
import _ "github.com/jamesfeng2009/closenai-app/go-core/internal/aiclient"
"""

CAP_RULE_OLD = '        elif RANK[state] > RANK["IMPLEMENTED"]:\n'
CAP_RULE_TOOTHLESS = '        elif RANK[state] > RANK["VALUE_MEASURED"]:\n'

GR_COMMENT_BLOCK = "".join(
    f"// 绿闸注脚 {i:02d}：这条闸判的是分类语义与集合，不钉行号也不钉注释。\n"
    for i in range(1, 16)
)
GR_GO_ANCHOR = "// callerKind 决定一个引用本包的 .go 文件算不算「生产调用方」。\n"
GR_FS_ANCHOR = "def verify_internal_capabilities(ledger: dict) -> list[str]:\n"

MUTATIONS: list[tuple] = [
    (
        "M1", "台账里那条登记的 id 被改名 ⇒ Go 侧「登记消失」当场红",
        [(LEDGER, ID_OLD, ID_RENAMED)],
        "go", GO_PKG, LEDGER_TEST, False,
    ),
    (
        "M2", "状态抬成 WIRED ⇒ 内部能力冒充「前端已接线」被拒",
        [(LEDGER, PROBE_LINE + STATE_LINE, PROBE_LINE + '      "state": "WIRED",\n')],
        "pytest", f"{TRUTH}::test_registered_capability_state_stays_capped", "", False,
    ),
    (
        "M3", "处置日期写成过去 ⇒ 「到期就删」的牙齿还在",
        [(LEDGER, REVIEW_DATE_OLD, '      "review_by": "2026-01-01",\n')],
        "pytest", f"{TRUTH}::test_registered_capability_review_date_is_still_future", "", False,
    ),
    (
        "M4", "处置动作里的「删除」被抹成「再看」⇒ 到期动作不能是空话",
        [(LEDGER, REVIEW_ACTION_OLD, REVIEW_ACTION_VAGUE)],
        "pytest",
        f"{TRUTH}::test_registered_capability_review_action_still_deletes", "", False,
    ),
    (
        "M5", "理由里两处条款锚点都没了 ⇒ 没有出处的登记不能继续绿",
        [
            (LEDGER, REASON_CLAUSE_1_OLD, REASON_CLAUSE_1_NEW),
            (LEDGER, REASON_CLAUSE_2_OLD, REASON_CLAUSE_2_NEW),
        ],
        "pytest",
        f"{TRUTH}::test_registered_capability_reason_still_cites_clauses", "", False,
    ),
    (
        "M6", "impl 点名一个不存在的实现文件 ⇒ 存在性判据咬住",
        [(LEDGER, IMPL_BREAKER_OLD, IMPL_BREAKER_WITH_GHOST)],
        "pytest", f"{TRUTH}::test_registered_capability_impl_points_at_real_files", "", False,
    ),
    (
        "M7", "tests 指向一个真存在但没有测试声明的文件 ⇒ 空壳证据被拒",
        [(LEDGER, TEST_GRPC_OLD, TEST_HOLLOW_FILE)],
        "pytest", f"{TRUTH}::test_registered_capability_tests_are_declared_files", "", False,
    ),
    (
        "M8", "probe_tools 写成不存在的工具文件 ⇒ 台账与 AST 实测对不上",
        [(LEDGER, PROBE_LINE, '      "probe_tools": ["go-core/tools/crosscheck/nobody.go"],\n')],
        "go", GO_PKG, LEDGER_TEST, False,
    ),
    (
        "M9", "probe_tools 清空 ⇒ 真在引用的那个工具从此没人登记",
        [(LEDGER, PROBE_LINE, '      "probe_tools": [],\n')],
        "go", GO_PKG, LEDGER_TEST, False,
    ),
    (
        "M10", "httpapi 里冒出一条真实生产 import ⇒ 「零生产调用方」当场失效",
        [(GO_MUTANT_CALLER, "", MUTANT_CALLER_SRC)],
        "go", GO_PKG, CALLER_TEST, False,
    ),
    (
        "M11", "walk 根漂一级（只扫 internal/）⇒ 正向证人抓住扫空集",
        [
            (GO_LEDGER_TEST,
             'func goCoreRoot() string { return filepath.Join("..", "..") }\n',
             'func goCoreRoot() string { return filepath.Join("..") }\n'),
        ],
        "go", GO_PKG, CALLER_TEST, False,
    ),
    (
        "M12", "把状态封顶的比较阈值掏到 VALUE_MEASURED ⇒ 除顶端外任何虚高都放行",
        [(FS, CAP_RULE_OLD, CAP_RULE_TOOTHLESS)],
        "pytest", f"{TRUTH}::test_internal_capability_cannot_claim_wired", "", False,
    ),
]

GREEN: list[tuple] = [
    (
        "GR1", "Go 那条分类函数上方插 15 行注释 ⇒ 调用方闸照旧全绿（它不钉排版）",
        [(GO_LEDGER_TEST, GR_GO_ANCHOR,
          "".join(f"// 绿闸注脚 {i:02d}：这条闸判的是分类语义与集合，不钉行号也不钉注释。\n"
                  for i in range(1, 16)) + GR_GO_ANCHOR)],
        "go", GO_PKG, CALLER_TEST, False,
    ),
    (
        "GR2", "Python 判据函数上方插 15 行注释 ⇒ 现账对账照旧全绿（它不钉行号）",
        [(FS, GR_FS_ANCHOR,
          "".join("# 绿闸注脚：%s\n" % ("这条判据读的是台账字段与仓库文件，不读自己的行号")
                  for _ in range(15)) + GR_FS_ANCHOR)],
        "pytest", f"{TRUTH}::test_current_ledger_has_no_evidence_violation", "", False,
    ),
]


def _apply(edits: list) -> list[Path]:
    """返回本次**新建**的文件（跑完由调用方删除）。锚点命中数必须恰好 1。"""
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
    for path, _old, _new in edits:
        if not path.exists():
            continue
        if path.suffix == ".py":
            try:
                ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                return f"{path.name}：变异体不是合法 Python（{exc}）"
        elif path.suffix == ".json":
            import json

            try:
                json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:
                return f"{path.name}：变异体不是合法 JSON（{exc}）"
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
    proc = subprocess.run(cmd, cwd=cwd, env=GO_ENV, capture_output=True, text=True, timeout=1800)
    return proc.returncode, proc.stdout + proc.stderr


def _report_name(marker: str) -> str:
    """把 `-run` 的正则还原成**用例名**：只剥两端的锚点，中间的 `^`/`$` 不是锚点。"""
    name = marker[1:] if marker.startswith("^") else marker
    return name[:-1] if name.endswith("$") else name


def is_red(code: int, out: str, runner: str, target: str, marker: str) -> bool:
    """「红」＝**那条判据自己**失败；工具链坏／没匹配到用例／skip 都不算。"""
    if code == 0:
        return False
    if runner == "go":
        if "[build failed]" in out or "no tests to run" in out or "no test files" in out:
            return False
        return f"--- FAIL: {_report_name(marker)}" in out
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
    with tempfile.TemporaryDirectory(prefix="mutate-t601-") as tmp:
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
                    print(f"[{cid}] 打红 ✗  {desc}\n  " + "\n  ".join(out.splitlines()[-12:]))

            for cid, desc, edits, runner, target, marker, _db in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(runner, target, marker)
                if base_code != 0:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（基线就不绿，exit={base_code}）：{desc}")
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
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓（{runner}）  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 被误伤 ✗  {desc}\n  " + "\n  ".join(out.splitlines()[-12:]))
        finally:
            for path in paths:
                path.unlink(missing_ok=True) if not path.exists() else None
            restore()
            residue = [p for p in paths
                       if not filecmp.cmp(originals[p], p, shallow=False)]
            for path in originals:
                if not path.exists():
                    residue.append(path)
            extra = [p for p in (GO_MUTANT_CALLER,) if p.exists()]
            print(f"\n残留复核：{'0 个靶文件与原件逐字节一致' if not residue and not extra else ''}")
            if residue:
                print("  以下靶文件恢复后仍不一致：" + "、".join(str(p) for p in residue))
            if extra:
                print("  新建靶未删除：" + "、".join(str(p) for p in extra))

        total_red = red_ok + red_fail
        total_green = green_ok + green_fail
        print(f"\n汇总：红闸 {red_ok}/{total_red}｜绿闸 {green_ok}/{total_green}｜"
              f"未跑 {len(unrun)}{'（' + ','.join(unrun) + '）' if unrun else ''}")
        if failures:
            print("失败条目：" + ",".join(failures))
        if unrun:
            print("ⓘ 有靶未跑：缺工具链／缺库一律不算绿")
        return 1 if (red_fail or green_fail or unrun) else 0


if __name__ == "__main__":
    sys.exit(main())

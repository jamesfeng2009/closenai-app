#!/usr/bin/env python3
"""canonical 只读例外（`CANONICAL_READ_ONLY`）的变异自证（2026-11-04）。

为什么要单独一条 harness：2026-11-04 把 `metrics_export.py` 登记成第三格只读例外时，
闸从红转绿——而「登记之后绿」这件事本身有两种绿：判据还在守着，判据被我一起放开了。
所以这里逐格破坏，要求它每次都红在**该红的那半句**上：

- 撤掉登记（例外条目整格删）⇒ 红在 offender 那一侧：证明这格登记是承重的，不是装饰。
- 在已登记的只读文件里塞一个写形态 ⇒ 红在「却出现了写形态」：证明例外只扩**读**，
  写权威态（不变量 7）仍然有人拦——这正是登记第三格时最需要保住的东西。
- 新造第四个直连 canonical 的文件 ⇒ 红在 offender 那一侧：证明 docstring 里
  「第四个引用点仍算扩大例外 ⇒ 红」不是空话（登记面只许逐格拍板，不许自动放行）。
- 把登记的类名清单改得比文件真 import 的少 ⇒ 红在「登记的类名与文件里真 import 的不一致」：
  这一条是本轮自己加判据时补的覆盖面——写形态断言只扫清单里的名字，清单少一个名字，
  那个类的写形态就静默扫不到，例外格会变成「登记了但不管用」。

绿闸证明它不是「见字就红」：往只读例外里再加一处**只读**（`select(...)`）必须仍绿，
把 `Outcome.origin == "user_statement"` 改写成 `.in_(...)` 也必须仍绿——
判据跟的是「有没有写形态」，不是「文件里出现了几个 canonical 类名」，也不是 `==` 这个字。

口径照抄既有纪律（[[feedback-mutation-gates-no-concurrency]]、
[[feedback-mutation-restore-no-git-checkout]]、[[feedback-definition-of-done-tests]]、
[[feedback-mutation-anchor-must-reach-target]]）：**串行跑**、copy2 备份写回 ＋ `filecmp`
逐字节复核、新建的探针文件跑完必须删掉、**不用 `git checkout`**、基线不绿一律记「未跑」。

用法：
    ./.venv/bin/python scripts/mutate_canonical_exception_gates.py --check
    ./.venv/bin/python scripts/mutate_canonical_exception_gates.py
    ./.venv/bin/python scripts/mutate_canonical_exception_gates.py --only R1,G1
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "backend" / "tests" / "test_canonical_schema.py"
METRICS = REPO / "backend" / "services" / "metrics_export.py"
PROBE = REPO / "backend" / "services" / "_canonical_exception_probe.py"

#: 这条 harness 守的闸文件。写成**单个连续字符串**是有意的：
#: `scripts/mutation_sample.py` 的 `gate_witnesses()` 用一条正则从脚本源码里抽「点名到的 pytest 闸」
#: 当正向证人，`"...py::" + "test_x"` 那种拆分写法它抽不到，抽样时会判「缺正向证人，不判绿」。
GATE_FILE = "backend/tests/test_canonical_schema.py"
TARGET = GATE_FILE + "::test_python_reaches_canonical_models_only_through_deletion"
VENV_PY = REPO / ".venv" / "bin" / "python"

#: 三格例外的登记条目本身（删掉它＝回到「metrics_export 是未登记的 offender」）。
REGISTRY_ENTRY = (
    '    ROOT / "backend" / "services" / "metrics_export.py": (\n'
    '        "Insight", "InsightEvidence", "Outcome", "PersonalCommitment",\n'
    "    ),\n"
)
#: 只读装载函数的定义行——红/绿用例都往它前面插一段函数体外的顶层代码。
LOAD_ANCHOR = (
    "async def load_journeys(db, *, since: datetime, until: datetime) -> tuple[list[Journey], int]:\n"
)
#: 读形态里最容易被误判成「改字段」的一处 `==`。
EQ_ANCHOR = 'Outcome.origin == "user_statement"'

WRITE_WITNESS = (
    "def _canonical_write_witness(session) -> None:  # 变异证人：只读例外里出现写形态\n"
    "    session.add(Outcome())\n\n\n"
)
READ_WITNESS = (
    "def _canonical_read_witness(db, select) -> object:  # 变异证人：只读例外里再加一处只读\n"
    "    return db.execute(select(PersonalCommitment.id))\n\n\n"
)
PROBE_SOURCE = (
    '"""变异探针：第四个直连 canonical 的文件（跑完必须删掉，不留进工作区）。"""\n'
    "from backend.models.canonical import Insight\n\n\n"
    "def read(_user_id):\n"
    "    return Insight\n"
)

MUTATIONS: list[tuple[str, str, list[tuple[Path, str, str]], str, str]] = [
    (
        "R1", "撤掉第三格登记：`metrics_export.py` 变回未登记的 offender",
        [(GATE, REGISTRY_ENTRY, "")],
        TARGET, "越过那三条登记过的例外",
    ),
    (
        "R2", "在已登记的只读文件里加一个写形态：例外只扩读，写权威态必须红（不变量 7）",
        [(METRICS, LOAD_ANCHOR, WRITE_WITNESS + LOAD_ANCHOR)],
        TARGET, "却出现了写形态",
    ),
    (
        "R3", "新造第四个直连 canonical 的文件：登记面只许逐格拍板，不许自动放行",
        [],
        TARGET, "越过那三条登记过的例外",
    ),
    (
        "R4", "登记的类名比文件真 import 的少一个：漏登记的名字扫不到写形态，判据覆盖面空转",
        [(GATE, '"Insight", "InsightEvidence", "Outcome", "PersonalCommitment",',
          '"Insight", "InsightEvidence", "PersonalCommitment",')],
        TARGET, "登记的类名与文件里真 import 的不一致",
    ),
]

GREEN: list[tuple[str, str, list[tuple[Path, str, str]]]] = [
    (
        "G1", "只读例外里再加一处只读（`select(...)`）：判据只拦写，不拦读",
        [(METRICS, LOAD_ANCHOR, READ_WITNESS + LOAD_ANCHOR)],
    ),
    (
        "G2", "把一处 `==` 比较改写成 `.in_(...)`：仍是读，判据不该红在写法排版上",
        [(METRICS, EQ_ANCHOR, 'Outcome.origin.in_(("user_statement",))')],
    ),
]


def run(target: str) -> tuple[int, str]:
    cmd = [str(VENV_PY), "-m", "pytest", target, "-q", "--no-header", "-p", "no:randomly"]
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=600)
    return proc.returncode, proc.stdout + proc.stderr


def is_red(code: int, out: str, marker: str) -> bool:
    return code != 0 and f"FAILED {TARGET}" in out and marker in out


def _apply(edits: list[tuple[Path, str, str]]) -> None:
    for path, old, new in edits:
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        assert n == 1, f"{path.name}：锚点命中 {n} 次（要求 1 次）\n{old[:90]!r}"
        path.write_text(src.replace(old, new), encoding="utf-8")


def _verify_anchor(edits: list[tuple[Path, str, str]]) -> str | None:
    for path, old, _new in edits:
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"锚点命中 {n} 次（要求 1 次）：{old[:60]!r}"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    if args.check:
        bad = 0
        for case in MUTATIONS:
            problem = _verify_anchor(case[2])
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        for case in GREEN:
            problem = _verify_anchor(case[2])
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}"
              f"（共 {len(MUTATIONS) + len(GREEN)} 条）")
        return 1 if bad else 0

    if not VENV_PY.is_file():
        print(f"解释器不存在：{VENV_PY}（变异自证要求先备好 .venv）")
        return 2

    base_code, base_out = run(TARGET)
    if base_code != 0:
        print("基线不是绿的，先修基线再谈变异：")
        print("  " + "\n  ".join(base_out.splitlines()[-15:]))
        return 2
    print(f"基线：{TARGET} 绿")

    paths = sorted({e[0] for case in [*MUTATIONS, *GREEN] for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-canonical-exception-") as tmp:
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
            if PROBE.exists():
                PROBE.unlink()

        red_ok = red_fail = green_ok = green_fail = 0
        failures: list[str] = []
        try:
            for cid, desc, edits, target, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                try:
                    _apply(edits)
                    if cid == "R3":
                        PROBE.write_text(PROBE_SOURCE, encoding="utf-8")
                    code, out = run(target)
                finally:
                    restore()
                if is_red(code, out, marker):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（exit={code}，名片段 {marker!r} 命中={marker in out}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))

            for cid, desc, edits in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(edits)
                try:
                    code, out = run(TARGET)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))

            restore()
            code, out = run(TARGET)
            print(f"复核 {TARGET}：{'绿' if code == 0 else '红（残留！）'}")
            if code != 0:
                print("  " + "\n  ".join(out.splitlines()[-15:]))
                failures.append("复核")

        finally:
            restore()

        total_red = len(MUTATIONS) if not wanted else red_ok + red_fail
        total_green = len(GREEN) if not wanted else green_ok + green_fail
        print(f"\n汇总：打红 {red_ok}/{total_red}，保持绿 {green_ok}/{total_green}"
              f"｜未打红 {red_fail}｜被误伤 {green_fail}")
        if failures:
            print("红清单：" + " ".join(failures))
        return 0 if red_fail == 0 and green_fail == 0 and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

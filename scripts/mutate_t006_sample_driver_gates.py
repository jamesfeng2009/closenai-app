#!/usr/bin/env python3
"""T0-06 变异抽样驱动器自身的变异自证（2026-10-18）。

正题：`scripts/mutation_sample.py` 是「证明别的闸会红」的那层。它自己坏掉时，
CI 的 `mutation-sample` 作业会安静地变成一件装饰品——所以四条判据逐条掏空试试：

- D1 选择面不再排除通用文件名（`index.ts`／`schemas.py`）：任何改动都命中一堆无关脚本，
  抽样变成「每次都跑不完」，最后被人关掉；
- D2 正向证人不再检查文件是否存在：把闸文件删掉，驱动器仍然列出这个「证人」，
  于是「变异打红」可以由一个不存在的测试提供——这正是本批要防的假绿；
- D3 汇总行解析不出分数时放行／分数不齐平时放行：两条各一靶，因为这两个分支
  一个是「没证据」一个是「证据不足」，缺一个都会漏；
- D4 恢复核验整个删掉：变异残留在工作区里没人发现（下一条 PR 就在被污染的文件上跑）。

绿闸 G1/G2 证明判据钉的是行为不是字样：把 `run_sample()` 里的局部变量整体改名、
给 `main()` 加一行无关打印，四条判据都该照常工作。

恢复一律 **copy2 + filecmp 逐字节校验**，不用 git checkout
（见 [[feedback-mutation-restore-no-git-checkout]]）；**严禁并发跑**
（见 [[feedback-mutation-gates-no-concurrency]]）。

用法：
    ./.venv/bin/python scripts/mutate_t006_sample_driver_gates.py --check
    ./.venv/bin/python scripts/mutate_t006_sample_driver_gates.py
    ./.venv/bin/python scripts/mutate_t006_sample_driver_gates.py --only D2,D4
"""
from __future__ import annotations

import argparse
import filecmp
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DRIVER = REPO / "scripts" / "mutation_sample.py"
GATE = "backend/tests/test_mutation_sample_driver.py"

MUTATIONS: list[tuple[str, str, list[tuple[str, str]], str]] = [
    (
        "D1",
        "目标名不再排除 GENERIC 通用文件名：改一个 index.ts 就抽中十几个无关脚本，抽样失去意义",
        [("    if \".\" not in name or Path(name).suffix not in FILE_SUFFIXES or name in GENERIC:",
          "    if \".\" not in name or Path(name).suffix not in FILE_SUFFIXES:")],
        "通用文件名被当成选择依据",
    ),
    (
        "D2",
        "正向证人不再核对文件在不在盘上：被删掉的测试文件也能冒充证人，「变异打红」失去出处",
        [("        if (REPO / full).is_file():",
          "        if True:  # 演示：删掉存在性核对")],
        "不存在的闸被当成证人了",
    ),
    (
        "D3",
        "汇总行解析不出分数时放行：脚本换掉文案后，「有没有打红到位」这件事再没人管",
        [('    if not hits:\n        return False, "脚本没打印可解析的红/绿闸分数（汇总行格式变了，判据要跟着改）"',
          '    if not hits:\n        return True, "无分数按通过处理（演示）"')],
        "没有分数就放行",
    ),
    (
        "D4",
        "分数不齐平也放行（红闸 9/10 当通过）：有变异打不红时流水线静默点头",
        [("    bad = [f\"{label} {num}/{den}\" for label, num, den in hits if int(num) != int(den)]",
          "    bad = []")],
        "9/10",
    ),
    (
        "D5",
        "恢复核验整个删掉：变异残留在工作区里，下一条 PR 就在被污染的文件上跑",
        [("    if porcelain() != dirty_before:",
          "    if False:")],
        "变异残留没被发现",
    ),
]

GREEN: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "G1",
        "把 run_sample() 的局部变量 dirty_before 整体改名：判据钉的是「有没有比对」，不是变量名",
        [("    dirty_before = porcelain()", "    state_before = porcelain()"),
         ("    if porcelain() != dirty_before:", "    if porcelain() != state_before:")],
    ),
    (
        "G2",
        "给 main() 多打一行无关日志：汇总/选择/证人三层判据与输出行数无关",
        [('    ids = [x.strip() for x in args.only.split(",") if x.strip()]',
          '    print("（T0-06 演示：多一行日志）")\n'
          '    ids = [x.strip() for x in args.only.split(",") if x.strip()]')],
    ),
]


def pytest(node: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(REPO / ".venv/bin/python"), "-m", "pytest", node, "-q", "--no-header"],
        cwd=REPO, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode, proc.stdout + proc.stderr


def apply(edits: list[tuple[str, str]]) -> None:
    src = DRIVER.read_text(encoding="utf-8")
    for old, new in edits:
        n = src.count(old)
        assert n == 1, f"锚点命中 {n} 次（要求恰好 1 次）\n{old[:90]!r}"
        src = src.replace(old, new, 1)
    DRIVER.write_text(src, encoding="utf-8")
    compile(src, str(DRIVER), "exec")  # 语法坏了会让所有用例一起红，那是噪声不是判据


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()

    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    if args.check:
        src = DRIVER.read_text(encoding="utf-8")
        bad = 0
        for case in [*MUTATIONS, *GREEN]:
            for old, _ in case[2]:
                n = src.count(old)
                if n != 1:
                    print(f"坏锚点 {case[0]}：命中 {n} 次 → {old[:70]!r}")
                    bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}"
              f"（共 {sum(len(c[2]) for c in MUTATIONS + GREEN)} 处编辑）")
        return 1 if bad else 0

    code, out = pytest(GATE)
    if code != 0:
        print("基线就是红的，变异结果无法归因——先修驱动器再跑本脚本：")
        print("\n".join(out.splitlines()[-15:]))
        return 1
    print("基线绿 ✓（驱动器 11 条判据全过）")

    tmp = Path(tempfile.mkdtemp(prefix="t006-gate-backup-"))
    bak = tmp / DRIVER.name
    shutil.copy2(DRIVER, bak)

    def restore() -> None:
        shutil.copy2(bak, DRIVER)
        assert filecmp.cmp(bak, DRIVER, shallow=False), "驱动器恢复后字节不一致"

    red_ok = red_fail = green_ok = green_fail = 0
    failures: list[str] = []
    try:
        for cid, desc, edits, marker in MUTATIONS:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                apply(edits)
                code, out = pytest(GATE)
                hit = marker in out and re.search(r"\d+ (failed|error)", out)
                if code != 0 and hit:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（exit={code}，marker={bool(hit)}）{desc}")
                    print("  " + "\n".join(out.splitlines()[-10:]))
            finally:
                restore()
        for cid, desc, edits in GREEN:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                apply(edits)
                code, out = pytest(GATE)
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 应保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 应保持绿 ✗（exit={code}）{desc}")
                    print("  " + "\n".join(out.splitlines()[-12:]))
            finally:
                restore()
    finally:
        restore()
        shutil.rmtree(tmp, ignore_errors=True)

    total = red_ok + red_fail + green_ok + green_fail
    print(
        f"\n变异自证合计：{total} 条｜红闸 {red_ok}/{red_ok + red_fail}"
        f"｜绿闸 {green_ok}/{green_ok + green_fail}"
    )
    if failures:
        print(f"未如预期：{', '.join(failures)}")
    print("scripts/mutation_sample.py 已按备份写回并逐字节校验一致。")
    return 0 if red_fail == 0 and green_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

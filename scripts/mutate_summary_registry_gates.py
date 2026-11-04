#!/usr/bin/env python3
"""「变异脚本的汇总行必须能被抽样器解析」这条存量登记闸的变异自证（2026-11-04）。

被验的判据在 `backend/tests/test_mutation_sample_driver.py` 的最后两条用例里。它管的是
`scripts/mutation_sample.py::parse_summary()` 读不到分数时**判不过**这一格——而全量面
（`mutation-nightly`）会把每一条脚本都跑一遍，所以「文案里没有齐平分数」这条脚本在 CI 上恒红。
这类脚本在盘上存量登记为一张只许缩不许涨的欠账清单（见那条用例里的登记常量与理由），
本脚本证明这张清单不是装饰品：

- S1 把一条**当前合规**的脚本的汇总行换成没有分数的文案（保留其它带字样的打印）：
  红要落在「解析不出分数、又不在存量登记里」——证明判据真读盘上每条脚本的打印，
  而不是只对着登记表自说自话；
- S2 把同一条脚本里所有带那些字样的打印连同汇总一起换成无字样文案：判据在它身上
  候选行为空，红必须落在「扫的是空集」那一格，而不是安静地把它当成合规；
- S3 从登记表里删掉一条真欠账的名字：同一格「登记之外」的红，证明登记确实被读到；
- S4 往登记表里塞一条此刻并不违规的名字：红要落在「该删这条登记」与「盘上没有这条脚本」，
  即这张表只许缩不许涨，写宽了同样红。

绿靶证明钉的是行为不是字样：G1 把一条**已登记**脚本的汇总行换成另一种同样解析不出的写法
（欠账换了文案还是欠账），G2 给候选行抽取函数加一行无关注释——两者都必须保持绿；
否则这条闸会在与判据无关的文案整理上拦人。

口径照抄既有纪律（[[feedback-mutation-gates-no-concurrency]]、
[[feedback-mutation-restore-no-git-checkout]]、[[feedback-definition-of-done-tests]]）：
**串行跑**、copy2 备份写回 ＋ `filecmp` 逐字节复核、**不用 `git checkout`**。

用法：
    ./.venv/bin/python scripts/mutate_summary_registry_gates.py --check
    ./.venv/bin/python scripts/mutate_summary_registry_gates.py
    ./.venv/bin/python scripts/mutate_summary_registry_gates.py --only S1,S3
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
W5 = REPO / "scripts" / "mutate_w5_gates.py"
N9 = REPO / "scripts" / "mutate_n9_gates.py"
GATE_PATH = REPO / "backend" / "tests" / "test_mutation_sample_driver.py"

# 连续一整条路径：mutation_sample.gate_witnesses() 认的是「一个引号包住的路面」，
# 拆开写的字面量它抽不出来，本脚本就会在正向证人那一格被记「没点名任何可跑的闸」。
GATE_FILE = "backend/tests/test_mutation_sample_driver.py"
TARGET = GATE_FILE

# ── 锚点 ────────────────────────────────────────────────────────────────
W5_SUMMARY = (
    '    print(\n'
    '        f"\\n变异自证合计：{total} 条｜红闸 {red_ok}/{red_ok + red_fail}"\n'
    '        f"｜绿闸 {green_ok}/{green_ok + green_fail}"\n'
    "    )\n"
)
# 带字样、但没有齐平分数的汇总行（S1）。
W5_SUMMARY_NO_FRACTION = (
    '    print(\n'
    '        f"\\n计数：{red_ok} 打红 / {red_fail} 未打红（演示：汇总行没有分数）"\n'
    "    )\n"
)
# 把每一条带字样的打印都换成无字样文案（S2）——候选行抽取扫不到任何东西。
W5_CASE_PRINTS = [
    ('                    print(f"[{cid}] 打红 ✓  {desc}")',
     '                    print(f"[{cid}] RED ok  {desc}")'),
    ('                    print(f"[{cid}] 打红 ✗（exit={code}，marker={bool(hit)}）{desc}")',
     '                    print(f"[{cid}] RED bad（exit={code}，marker={bool(hit)}）{desc}")'),
    ('                    print(f"[{cid}] 应保持绿 ✓  {desc}")',
     '                    print(f"[{cid}] GREEN ok  {desc}")'),
    ('                    print(f"[{cid}] 应保持绿 ✗（exit={code}）{desc}")',
     '                    print(f"[{cid}] GREEN bad（exit={code}）{desc}")'),
]
W5_SUMMARY_NO_HINT = (
    '    print(\n'
    '        f"\\n总计：{total} 条（演示：连字样一起没了）"\n'
    "    )\n"
)
# 已登记欠账的一条汇总行（G1）：换成另一种同样解析不出的写法，欠账还是欠账。
N9_SUMMARY = '        print(f"\\n{attempted - len(failures)}/{attempted} 条变异打红")\n'
N9_SUMMARY_REWORDED = (
    '        print(f"\\n计数：{attempted - len(failures)} 条变异打红，'
    '{len(failures)} 条没打红")\n'
)
# 登记表里的一条真欠账（S3 删它）、登记表的表头第一项（S4 在它后面塞一条不违规的）。
N9_REGISTRY_LINE = '    "mutate_n9_gates.py",\n'
REGISTRY_HEAD_LINE = '    "mutate_batch3_gates.py",\n'
PARSE_HELPER_ANCHOR = "    tree = ast.parse(script.read_text(encoding=\"utf-8\"))\n"

# 每条：(id, 说明, [(文件, 原文, 坏文), …], 名片段)
MUTATIONS: list[tuple[str, str, list[tuple[Path, str, str]], str]] = [
    (
        "S1",
        "把合规脚本 mutate_w5_gates.py 的汇总行换成「计数：5 打红 / 0 未打红」这类没有齐平分数的"
        "文案：判据必须把它当成**登记之外**的新欠账——它读的是盘上每条脚本的打印，不是登记表本身",
        [(W5, W5_SUMMARY, W5_SUMMARY_NO_FRACTION)],
        "又不在存量登记里",
    ),
    (
        "S2",
        "把 mutate_w5_gates.py 里所有带汇总字样的打印连汇总行一起换成无字样文案："
        "候选行抽不出来时，红必须落在「判据在它身上扫的是空集」，"
        "而不是安静地判它合规——那是把「文案换了字」当成「脚本没了」",
        [(W5, old, new) for old, new in W5_CASE_PRINTS]
        + [(W5, W5_SUMMARY, W5_SUMMARY_NO_HINT)],
        "扫的是空集",
    ),
    (
        "S3",
        "从存量登记里删掉一条真欠账（mutate_n9_gates.py）：欠账还在盘上、表里却没记 ⇒ 同一格红。"
        "这条证明登记表确实被读到，删一行就是漏账",
        [(GATE_PATH, N9_REGISTRY_LINE, "")],
        "又不在存量登记里",
    ),
    (
        "S4",
        "往存量登记里塞一条此刻并不违规的名字（mutate_w5_gates.py）：红要落在「已经能解析了，"
        "把这条登记删掉」——这张表是欠账清单不是免罪符，写宽了同样拦人",
        [(GATE_PATH,
          REGISTRY_HEAD_LINE, REGISTRY_HEAD_LINE + '    "mutate_w5_gates.py",\n')],
        "该删这条登记",
    ),
]

GREEN: list[tuple[str, str, list[tuple[Path, str, str]]]] = [
    (
        "G1",
        "把**已登记**的 mutate_n9_gates.py 汇总行换成另一种同样解析不出的写法（把分数写成"
        "「5 条变异打红，0 条没打红」）：欠账换了文案还是欠账，登记认的是文件名不是字样——"
        "否则日后任何一次文案整理都会红在这条闸上",
        [(N9, N9_SUMMARY, N9_SUMMARY_REWORDED)],
    ),
    (
        "G2",
        "给候选行抽取函数加一行无关注释（`tree = ast.parse(…)` 之前）：判据读的是 print 的参数，"
        "不读注释与排版——这条绿同时自证 S1 的红不是「在文件里找一个字符串」造成的",
        [(GATE_PATH,
          PARSE_HELPER_ANCHOR, "    # 演示：注释不参与判据\n" + PARSE_HELPER_ANCHOR)],
    ),
]


def pytest(node: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(REPO / ".venv/bin/python"), "-m", "pytest", node, "-q", "--no-header"],
        cwd=REPO, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _verify(edits: list[tuple[Path, str, str]]) -> str | None:
    for path, old, _new in edits:
        if not path.exists():
            return f"{path.name} 不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"{path.name} 锚点命中 {n} 次（要求恰好 1 次）→ {old[:70]!r}"
    return None


def _apply(edits: list[tuple[Path, str, str]]) -> None:
    for path, old, new in edits:
        src = path.read_text(encoding="utf-8")
        assert src.count(old) == 1, f"{path.name}：锚点命中数变了"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    all_cases = [*MUTATIONS, *GREEN]
    if args.check:
        bad = 0
        for case in all_cases:
            problem = _verify(case[2])
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}"
              f"（共 {len(all_cases)} 条用例）")
        return 1 if bad else 0

    code, out = pytest(TARGET)
    if code != 0:
        print("基线不是绿的，先修基线再谈变异：")
        print("  " + "\n  ".join(out.splitlines()[-15:]))
        return 2
    print(f"基线：{TARGET} 绿")

    paths = sorted({e[0] for case in all_cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-summary-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            bak = Path(tmp) / f"{idx:02d}-{path.parent.name}-{path.name}"
            shutil.copy2(path, bak)
            originals[path] = bak

        def restore() -> None:
            for path, bak in originals.items():
                shutil.copy2(bak, path)
                assert filecmp.cmp(bak, path, shallow=False), f"{path} 恢复后字节不一致"

        red_ok = red_fail = green_ok = green_fail = 0
        failures: list[str] = []
        try:
            for cid, desc, edits, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(edits)
                try:
                    code, out = pytest(TARGET)
                finally:
                    restore()
                if code != 0 and marker in out:
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
                    code, out = pytest(TARGET)
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
        except Exception as exc:  # noqa: BLE001
            restore()
            print(f"异常中止，已恢复：{exc!r}")
            return 2

        code, out = pytest(TARGET)
        print(f"复核 {TARGET}：{'绿' if code == 0 else '红（残留！）'}")
        if code != 0:
            print("  " + "\n  ".join(out.splitlines()[-12:]))
            return 1

        print(f"\n汇总：打红 {red_ok}/{red_ok + red_fail}，绿闸 {green_ok}/{green_ok + green_fail}"
              f"｜未打红 {red_fail}｜被误伤 {green_fail}")
        if failures:
            print("未通过：" + ",".join(failures))
        return 1 if (red_fail or green_fail) else 0


if __name__ == "__main__":
    sys.exit(main())

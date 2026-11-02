#!/usr/bin/env python3
"""T7-06（核心循环指标字典与漏斗）的变异自证（2026-10-26）。

7 条红靶各打一条判据：第二次循环被降级成「多数一条结果」、时间顺序失控、
零数据冒充正常、测试账号混进正式指标、未知的 dropped 用 0/台账顶替、
抽样漂移不报、看板插值不转义。2 条绿靶证明闸门不是「见字就红」。

口径照抄既有纪律（[[feedback-mutation-gates-no-concurrency]]、
[[feedback-mutation-restore-no-git-checkout]]、[[feedback-definition-of-done-tests]]）：
**串行跑**、copy2 备份写回 ＋ `filecmp` 逐字节复核、**不用 `git checkout`**。

用法：
    ./.venv/bin/python scripts/mutate_t706_gates.py --check
    ./.venv/bin/python scripts/mutate_t706_gates.py
    ./.venv/bin/python scripts/mutate_t706_gates.py --only M1,M7
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
MODULE = REPO / "backend" / "services" / "metrics_export.py"
DOC = REPO / "docs" / "metrics-dictionary.md"

TARGET = "backend/tests/test_metrics_export.py"
VENV_PY = REPO / ".venv" / "bin" / "python"

# 锚点出现次数不是 1 的用例，在这里显式登记期望次数。
ANCHOR_COUNT: dict[str, int] = {}

# 每条：(id, 说明, (文件, 原文, 坏文), target, 名片段)
MUTATIONS = [
    (
        "M1",
        "第二次循环退化成「又多了一条结果」：补记旧行动的结果也会被算成重启，"
        "第二次核心循环转化被虚高",
        (MODULE,
         "        window = (first_outcome, outcome)\n"
         "        if any(window[0] < t <= window[1] for t in journey.reflections) and any(\n"
         "            window[0] < t <= window[1] for t in journey.insights\n"
         "        ) and any(window[0] < t <= window[1] for t in journey.actions):\n"
         "            return outcome\n"
         "    return None",
         "        return outcome\n"
         "    return None"),
        TARGET, "test_second_cycle_requires_a_restart_not_a_second_outcome",
    ),
    (
        "M2",
        "撤掉反思 ≤ 洞察 ≤ 行动的时间顺序判据：乱序的补记也能凑成一次完整循环",
        (MODULE, "    if not (reflection <= insight <= action):", "    if False:"),
        TARGET, "test_out_of_order_milestones_do_not_complete_a_loop",
    ),
    (
        "M3",
        "data_status 恒为 ok：窗口内一个真实用户都没有时，看板不再明说",
        (MODULE,
         '        "data_status": "no_users" if not cohort else "ok",',
         '        "data_status": "ok",'),
        TARGET, "test_empty_batch_is_zero_and_says_so",
    ),
    (
        "M4",
        "关掉测试账号排除：@closenai.test 种子数据污染正式漏斗",
        (MODULE, "        if journey.user_id in excluded:", "        if False:"),
        TARGET, "test_test_users_are_excluded_and_counted",
    ),
    (
        "M5",
        "拿台账里的 retryable+permanent 顶替批次 dropped：未知被 0/台账冒充，"
        "恒等式在对不上时也会被判通过",
        (MODULE,
         "    dropped = relay_dropped\n",
         "    dropped = ledger_dropped if relay_dropped is None else relay_dropped\n"),
        TARGET, "test_delivery_mixed_batch_identity",
    ),
    (
        "M6",
        "抽样对账不再看漂移：设备端与服务端差多少都报 clean",
        (MODULE, "        if abs(drift) > tolerance:", "        if False:"),
        TARGET, "test_sampling_reconciliation_flags_drift",
    ),
    (
        "M7",
        "看板插值不转义：标签里的 <script> 会被当成 HTML 执行",
        (MODULE,
         "html.escape(str(labels.get(key, key)))",
         "str(labels.get(key, key))"),
        TARGET, "test_dashboard_zero_state_is_explicit_and_escaped",
    ),
]

GREEN = [
    (
        "G1",
        "在模块里加一行注释（不改判据）：闸判的是行为，不读注释",
        (MODULE,
         "def _second_cycle(journey: Journey, first_outcome: datetime | None)",
         "# 注释不改变判据。\n"
         "def _second_cycle(journey: Journey, first_outcome: datetime | None)"),
        TARGET,
    ),
    (
        "G2",
        "在字典表格前加一行 HTML 注释（不改内容）：文档闸逐行比对的是表格行",
        (DOC,
         "| 机器码 | 中文 | owner | UI 触发点 | API 函数 | 服务端入口 | 权威表 | 隐私边界 |",
         "<!-- 注释不改变判据 -->\n"
         "| 机器码 | 中文 | owner | UI 触发点 | API 函数 | 服务端入口 | 权威表 | 隐私边界 |"),
        TARGET,
    ),
]


def _expected(case_id: str) -> int:
    return ANCHOR_COUNT.get(case_id, 1)


def _apply(mutation: tuple, expect: int) -> None:
    path, old, new = mutation
    src = path.read_text(encoding="utf-8")
    n = src.count(old)
    assert n == expect, f"{path.name}：锚点命中 {n} 次（要求 {expect} 次）\n{old[:90]!r}"
    path.write_text(src.replace(old, new), encoding="utf-8")


def _verify_anchor(mutation: tuple, expect: int) -> str | None:
    path, old, _new = mutation
    if not path.exists():
        return "文件不存在"
    n = path.read_text(encoding="utf-8").count(old)
    return None if n == expect else f"命中 {n} 次（要求 {expect} 次）"


def run(target: str) -> tuple[int, str]:
    cmd = [str(VENV_PY), "-m", "pytest", target, "-q", "--no-header", "-x"]
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=600)
    return proc.returncode, proc.stdout + proc.stderr


def is_red_pytest(code: int, out: str, target: str) -> bool:
    if code == 0:
        return False
    if "no tests ran" in out or "1 skipped" in out:
        return False
    if "error" in out.split("= ")[-1]:
        return False
    return f"FAILED {target}" in out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    if args.check:
        bad = 0
        for case in [*MUTATIONS, *GREEN]:
            problem = _verify_anchor(case[2], _expected(case[0]))
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中' if bad == 0 else f'{bad} 条有问题'}"
              f"（共 {len(MUTATIONS) + len(GREEN)} 条）")
        return 1 if bad else 0

    code, out = run(TARGET)
    if code != 0:
        print("基线不是绿的，先修基线再谈变异：")
        print("  " + "\n  ".join(out.splitlines()[-15:]))
        return 2
    print(f"基线：{TARGET} 绿")

    paths = sorted({case[2][0] for case in [*MUTATIONS, *GREEN]})
    with tempfile.TemporaryDirectory(prefix="mutate-t706-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
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
            for cid, desc, mutation, target, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(mutation, _expected(cid))
                try:
                    code, out = run(target)
                finally:
                    restore()
                hit = is_red_pytest(code, out, target)
                if hit and marker in out:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（exit={code}，"
                          f"名片段 {marker!r} 命中={marker in out}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))

            for cid, desc, mutation, target in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(mutation, _expected(cid))
                try:
                    code, out = run(target)
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

        code, out = run(TARGET)
        print(f"复核 {TARGET}：{'绿' if code == 0 else '红（残留！）'}")
        if code != 0:
            print("  " + "\n  ".join(out.splitlines()[-12:]))
            return 1

        print(f"\n计数：红闸 {red_ok} 打红 / {red_fail} 未打红；"
              f"绿闸 {green_ok} 保持绿 / {green_fail} 被误伤；异常 0")
        if failures:
            print("未通过：" + ",".join(failures))
        return 1 if (red_fail or green_fail) else 0


if __name__ == "__main__":
    sys.exit(main())

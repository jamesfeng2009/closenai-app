#!/usr/bin/env python3
"""CI 工具链版本闸（backend/tests/test_ci_workflow_versions.py）的变异自证（2026-11-04）。

判据不是「测试跑过」，而是「每条纪律都能被打红一次，且红在它该红的闸上」。
红靶覆盖：三份 node 钉版各退回 engines 下限之下（ci.yml 两处作业 ＋ nightly 一处）、
装了 npm 依赖却把原生自检删掉。
绿靶证明这不是「见字就红」：把 node 抬到 24（高于下限）必须仍绿——闸读的是锁算出的下限，
不是一句写死的 22；node-version 去掉引号也必须仍绿——判据认的是版本号，不是 YAML 写法。

口径照抄既有纪律（[[feedback-mutation-gates-no-concurrency]]、
[[feedback-mutation-restore-no-git-checkout]]、[[feedback-definition-of-done-tests]]）：
**串行跑**、copy2 备份写回 ＋ `filecmp` 逐字节复核、**不用 `git checkout`**。

用法：
    ./.venv/bin/python scripts/mutate_ci_workflow_gates.py --check
    ./.venv/bin/python scripts/mutate_ci_workflow_gates.py
    ./.venv/bin/python scripts/mutate_ci_workflow_gates.py --only M1,M4
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
CI = REPO / ".github" / "workflows" / "ci.yml"
NIGHTLY = REPO / ".github" / "workflows" / "mutation-nightly.yml"

TARGET = "backend/tests/test_ci_workflow_versions.py"
VENV_PY = REPO / ".venv" / "bin" / "python"

# 锚点出现次数不是 1 的用例，在这里显式登记期望次数。
ANCHOR_COUNT = {"M4": 2}

NODE_LINE = 'node-version: "22"'
SELFCHECK = (
    '          node -e "const pkg=require(\'better-sqlite3/package.json\');'
    "const D=require('better-sqlite3');const db=new D(':memory:');"
    "db.exec('create table t(x)');db.prepare('insert into t values (?)').run(7);"
    "if(db.prepare('select x from t').get().x!==7)throw new Error('roundtrip 不符');"
    "console.log('better-sqlite3 '+pkg.version+' 可用，node '+process.version)\""
)

# 每条：(id, 说明, (文件, 原文, 坏文), target, 名片段)
MUTATIONS = [
    (
        "M1",
        "ci.yml 的 frontend 作业把 node 钉回 20（better-sqlite3 声明的下限是 22）："
        "npm 对 engines 不符只 warn，装出 ABI 不匹配的原生二进制，"
        "红最终以 13 个 suite 的 jest worker SIGSEGV 出现——本闸要把它按在提交上",
        (CI,
         f"          # 下限不猜：由 backend/tests/test_ci_workflow_versions.py 现读锁里逐包 engines 算出来。\n"
         f"          {NODE_LINE}",
         '          # 下限不猜：由 backend/tests/test_ci_workflow_versions.py 现读锁里逐包 engines 算出来。\n'
         '          node-version: "20"'),
        TARGET, "才是依赖要求的下限",
    ),
    (
        "M2",
        "ci.yml 的 mutation-sample 作业把 node 钉回 20：这个作业要跑 jest 侧的闸，"
        "同样会 require 原生包；只补 frontend 作业会漏掉这一条腿",
        (CI,
         '          # 而变异脚本里那些要跑 jest 的闸正好会 require 它（红在段错误上，看不出根因）。\n'
         f"          {NODE_LINE}",
         '          # 而变异脚本里那些要跑 jest 的闸正好会 require 它（红在段错误上，看不出根因）。\n'
         '          node-version: "20"'),
        TARGET, "才是依赖要求的下限",
    ),
    (
        "M3",
        "nightly 作业把 node 钉回 20：全量抽样走的这条流水线跟 ci.yml 用同一套前端依赖，"
        "版本闸必须覆盖两份 workflow 而不是只盯 PR 面",
        (NIGHTLY,
         '          # 下限由 backend/tests/test_ci_workflow_versions.py 现读锁算，不写死。\n'
         f"          {NODE_LINE}",
         '          # 下限由 backend/tests/test_ci_workflow_versions.py 现读锁算，不写死。\n'
         '          node-version: "20"'),
        TARGET, "才是依赖要求的下限",
    ),
    (
        "M4",
        "把 ci.yml 两处自检的 `node -e …require('better-sqlite3')…` 各换成一句 echo："
        "钉版够了不代表装出来的东西能用，删掉自检就回到「npm ci 全绿、jest 里 13 个 suite 全死」",
        (CI, SELFCHECK, '          echo "（原生自检这一格被删掉了）"'),
        TARGET, "装了 npm 依赖却没有原生模块自检",
    ),
]

GREEN = [
    (
        "G1",
        "把 ci.yml frontend 作业的 node 抬到 24（高于下限）：闸必须仍绿——"
        "它比的是锁里算出的 engines 下限，不是写死的 22；否则依赖抬版本时闸会反过来拦升级",
        (CI,
         f"          # 下限不猜：由 backend/tests/test_ci_workflow_versions.py 现读锁里逐包 engines 算出来。\n"
         f"          {NODE_LINE}",
         '          # 下限不猜：由 backend/tests/test_ci_workflow_versions.py 现读锁里逐包 engines 算出来。\n'
         '          node-version: "24"'),
        TARGET,
    ),
    (
        "G2",
        "nightly 的 node-version 去掉引号（写成 22 而不是带引号的版本串）：判据读的是版本号，"
        "不该因为 YAML 写法换了就红——否则这条闸会红在无关的排版改动上",
        (NIGHTLY, f"          {NODE_LINE}", "          node-version: 22"),
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
    with tempfile.TemporaryDirectory(prefix="mutate-ci-node-") as tmp:
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

#!/usr/bin/env python3
"""T7-03（MQ 体内身份键契约收紧）的变异自证（2026-10-25）。

判据不是「测试跑过」，而是「每条纪律都能被打红一次，且红在它该红的闸上」。
7 条红靶覆盖：Python 归一的连字符缺口、Python 拒绝门本身、Go 候选面拒绝门、
Go 事件面拒绝门、Go 归一规则、事件面允许面（只准 `userId`）、以及跨语言码值漂移。
2 条绿靶证明这些闸不是「见字就红」的假红制造机。

口径照抄既有纪律（[[feedback-mutation-gates-no-concurrency]]、
[[feedback-mutation-restore-no-git-checkout]]、[[feedback-definition-of-done-tests]]）：
**串行跑**、copy2 备份写回 ＋ `filecmp` 逐字节复核、**不用 `git checkout`**。

用法：
    ./.venv/bin/python scripts/mutate_t703_gates.py --check
    ./.venv/bin/python scripts/mutate_t703_gates.py
    ./.venv/bin/python scripts/mutate_t703_gates.py --only M1,M3
"""
from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GO_CORE = REPO / "go-core"

CONTRACT_PY = REPO / "backend" / "ai" / "mq" / "contract.py"
CANDIDATE_GO = GO_CORE / "internal" / "messaging" / "inbound" / "candidate_decode.go"
SOURCE_GO = GO_CORE / "internal" / "messaging" / "inbound" / "source_deleted.go"
IDENTITY_GO = GO_CORE / "internal" / "messaging" / "inbound" / "identity_keys.go"

IDENTITY_TEST_PY = "backend/tests/test_mq_identity_contract.py"
CONSUMER_TEST_PY = "backend/tests/test_ai_mq_consumer.py"
INBOUND_PKG = "./internal/messaging/inbound/"

GOROOT = "/Users/fengyu/sdk/go1.27.1"
GO_ENV = {**os.environ, "GOROOT": GOROOT,
          "PATH": f"{GOROOT}/bin:{os.environ.get('PATH', '')}"}
VENV_PY = REPO / ".venv" / "bin" / "python"

# 每条：(id, 说明, (文件, 原文, 坏文), runner, target, node, 名片段)
MUTATIONS = [
    (
        "M1",
        "Python 归一退回到「只剥下划线」：user-id 不再被判成身份键。"
        "这正是 T7-03 立项时登记的缺口，用户后果是一条自带别人归属的命令被当合法放行",
        (CONTRACT_PY,
         '_IDENTITY_KEY_SEPARATORS = str.maketrans("", "", "_-")',
         '_IDENTITY_KEY_SEPARATORS = str.maketrans("", "", "_")'),
        "pytest", IDENTITY_TEST_PY, "", "user-id",
    ),
    (
        "M2",
        "摘掉 Python 命令面的身份键拒绝块：变体全部放行到 handler，"
        "「按 A 用户跑、产出记到 B 头上」重新有第二处入口",
        (CONTRACT_PY,
         "    foreign = foreign_identity_keys(parsed)\n    if foreign:",
         "    foreign = foreign_identity_keys(parsed)\n    if False and foreign:"),
        "pytest", CONSUMER_TEST_PY, "", "foreign_user_id",
    ),
    (
        "M3",
        "摘掉 Go 候选面的身份键门：userId 退回 protojson 的未知字段，"
        "同一件事被记成 candidate_payload_invalid，跨语言码面分叉",
        (CANDIDATE_GO,
         "\tif bad := rejectForeignIdentity(d.Body); bad != nil {\n\t\treturn nil, bad\n\t}",
         "\t_ = rejectForeignIdentity"),
        "go", INBOUND_PKG,
        "^TestCandidateBodyIdentityVariantsAreRejectedBeforeAnyDomainWrite$", "期望",
    ),
    (
        "M4",
        "摘掉 Go 事件面的身份键门：user_id 形态退回 DisallowUnknownFields，"
        "被记成 payload_not_json（看起来像「体不是 JSON」）",
        (SOURCE_GO,
         "\tif bad := rejectForeignIdentity(d.Body, sourceDeletedUserIDKey); bad != nil {\n\t\treturn nil, bad\n\t}",
         "\t_ = rejectForeignIdentity"),
        "go", INBOUND_PKG,
        "^TestSourceDeletedIdentityVariantsAreRejectedBeforeAnyDomainWrite$", "期望",
    ),
    (
        "M5",
        "Go 归一不剥连字符：user-id 在 Go 侧不被认成身份键，而 Python 侧认——"
        "同一条消息两个结论",
        (IDENTITY_GO,
         '\treturn strings.ReplaceAll(key, "-", "")',
         '\treturn key'),
        "go", INBOUND_PKG,
        "^TestCandidateBodyIdentityVariantsAreRejectedBeforeAnyDomainWrite$", "user-id",
    ),
    (
        "M6",
        "事件面把允许面放宽成「任何身份键」：合法的 userId 也被拒，"
        "删除事件全部进 DLQ，账停在翻转前",
        (SOURCE_GO,
         "rejectForeignIdentity(d.Body, sourceDeletedUserIDKey)",
         "rejectForeignIdentity(d.Body)"),
        "go", INBOUND_PKG,
        "^TestSourceDeletedLegalShapeAndHeaderBodyConflict$", "合法事件被拒",
    ),
    (
        "M7",
        "Go 的 foreign_user_id 码值改一个字母：两侧留痕面从此各说各话，"
        "运维一条搜索只能命中一半",
        (CANDIDATE_GO,
         'ReasonForeignUserID   = "foreign_user_id"',
         'ReasonForeignUserID   = "foreign_user_identity"'),
        "go", INBOUND_PKG,
        "^TestCandidateBodyIdentityVariantsAreRejectedBeforeAnyDomainWrite$", "共享契约",
    ),
]

GREEN = [
    (
        "G1",
        "在 Python 契约里加两行注释（不改代码）：这些闸判的是行为，不读注释",
        (CONTRACT_PY,
         "# ── 体内身份键（T7-03）",
         "# 注释不改变判据。\n# ── 体内身份键（T7-03）"),
        "pytest", IDENTITY_TEST_PY, "",
    ),
    (
        "G2",
        "在 Go 归一文件里加一行注释（不改代码）：Go 侧闸判的是返回值",
        (IDENTITY_GO,
         "const identityKeyCanonical = \"userid\"",
         "// 注释不改变判据。\nconst identityKeyCanonical = \"userid\""),
        "go", INBOUND_PKG,
        "^TestIdentityNormaliserMatchesSharedCases$",
    ),
]


def _apply(mutation: tuple) -> None:
    path, old, new = mutation
    src = path.read_text(encoding="utf-8")
    n = src.count(old)
    assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:90]!r}"
    path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _verify_anchor(mutation: tuple) -> str | None:
    path, old, _new = mutation
    if not path.exists():
        return "文件不存在"
    n = path.read_text(encoding="utf-8").count(old)
    return None if n == 1 else f"命中 {n} 次"


def run(runner: str, target: str, node: str) -> tuple[int, str]:
    if runner == "go":
        cmd = ["go", "test", target, "-count=1", "-timeout", "180s"]
        if node:
            cmd += ["-run", node]
        cwd, timeout = GO_CORE, 1800
    else:
        assert node == "", "pytest 的节点 id 就写在 target 里，node 留空"
        cmd = [str(VENV_PY), "-m", "pytest", target, "-q", "--no-header", "-x"]
        cwd, timeout = REPO, 1800
    proc = subprocess.run(cmd, cwd=cwd, env=GO_ENV, capture_output=True, text=True,
                          timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def is_red(code: int, out: str) -> bool:
    if code == 0:
        return False
    if "[build failed]" in out or "no tests to run" in out or "no test files" in out:
        return False
    return "--- FAIL:" in out


def is_red_pytest(code: int, out: str, target: str) -> bool:
    if code == 0:
        return False
    if "no tests ran" in out or "1 skipped" in out or "error" in out.split("= ")[-1]:
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
            problem = _verify_anchor(case[2])
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}"
              f"（共 {len(MUTATIONS) + len(GREEN)} 条）")
        return 1 if bad else 0

    paths = sorted({case[2][0] for case in [*MUTATIONS, *GREEN]})
    with tempfile.TemporaryDirectory(prefix="mutate-t703-") as tmp:
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
            for cid, desc, mutation, runner, target, node, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(mutation)
                try:
                    code, out = run(runner, target, node)
                finally:
                    restore()
                hit = (is_red(code, out) if runner == "go"
                       else is_red_pytest(code, out, target))
                if hit and marker in out:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓（{runner}）  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（{runner} exit={code}，"
                          f"名片段 {marker!r} 命中={marker in out}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))

            for cid, desc, mutation, runner, target, node in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(mutation)
                try:
                    code, out = run(runner, target, node)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓（{runner}）  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（{runner} exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))
        except Exception as exc:  # noqa: BLE001
            restore()
            print(f"异常中止，已恢复：{exc!r}")
            return 2

        for target in (IDENTITY_TEST_PY, CONSUMER_TEST_PY):
            code, out = run("pytest", target, "")
            print(f"复核 {target}：{'绿' if code == 0 else '红（残留！）'}")
            if code != 0:
                print("  " + "\n  ".join(out.splitlines()[-12:]))
                return 1
        code, out = run("go", INBOUND_PKG, "")
        print(f"复核 {INBOUND_PKG}：{'绿' if code == 0 else '红（残留！）'}")
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

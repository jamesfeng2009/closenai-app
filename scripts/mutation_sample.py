#!/usr/bin/env python3
"""T0-06 变异自证的 CI 入口：按改动抽样跑 `scripts/mutate_*.py`，把「闸能红」变成机器义务。

为什么要有这个驱动器（而不是把 30+ 个脚本各写一遍 CI 步骤）：变异脚本此前只在人工执行时
证明「这条闸会红」。人工跑的证据是**一次性的**——它证明「2026-10-18 那天我记得跑过」，
不证明「明天有人把断言删了，流水线会响」。本驱动器不知道任何业务对错，只认四件事：

1. **选中**：变更文件的 basename 出现在哪个 `mutate_*.py` 的源码字面量里就跑哪个。
   按文件名而非全路径匹配：脚本里有的写 `REPO / "backend" / ... / "cron_service.py"`、
   有的写 `FRONT / PROFILE_TS`、有的直接写 `"src/..."`，路径拼法不统一，文件名一定对得上。
2. **正向证人**：跑脚本之前先把该脚本自己点名的闸跑一遍**要求全绿**（见
   [[feedback-gate-needs-positive-witness]]）。缺这一步，「变异打红」可以靠一个本来就坏了
   的测试文件假绿——文件不存在时 pytest/jest 的退出码也是非零。
3. **打红到位**：脚本退出码 0，且汇总行的分数齐平（`红闸 10/10`）。汇总文案在各脚本里不统一
   （`红闸 x/y`、`x 打红 / y`），所以 `parse_summary()` 认**分数形状**不认固定文案；
   解析不出分数本身就是失败，不放行。
4. **恢复核验**：脚本跑完后 `git status --porcelain` 与跑前完全一致。脚本自己声称
   copy2＋filecmp 恢复了，这里用 git 独立复核一遍；残留一格就红
   （也兜住 [[feedback-mutation-gates-no-concurrency]]：本驱动器一律串行）。

用法：
    .venv/bin/python scripts/mutation_sample.py --list                 # 只看选择映射
    .venv/bin/python scripts/mutation_sample.py --changed-from HEAD~1  # 按本次改动抽样
    .venv/bin/python scripts/mutation_sample.py --files scripts/mutate_t005_feature_status_gates.py
    .venv/bin/python scripts/mutation_sample.py --all                  # 夜间全量
退出码 0＝选中样本全部「证人绿＋打红到位＋恢复一致」；1＝任一环没做到（含解析不出汇总行）。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
FRONT = REPO / "frontend"

FILE_SUFFIXES = {
    ".py", ".ts", ".tsx", ".go", ".sql", ".md", ".json",
    ".yaml", ".yml", ".astro", ".sh", ".toml", ".ini",
}
# 太通用的文件名：拿它们当选择依据会让任何改动都命中一堆无关脚本，抽样就失去意义
GENERIC = {
    "index.ts", "types.ts", "__init__.py", "config.go", "service.py", "schemas.py",
    "schema.py", "utils.py", "main.go", "main.py", "README.md", "package.json", "ci.yml",
}
FRACTION_RE = re.compile(r"(红闸|绿闸|打红|打绿)\D*?(\d+)\s*/\s*(\d+)")
QUOTED_FILE_RE = re.compile(r"[\"'`]([\w./_-]+\.(?:py|ts|tsx|go|sql|md|json|ya?ml|astro|sh|toml|ini))[\"'`]")
PYTEST_GATE_RE = re.compile(r"[\"']((?:backend/)?tests/test_\w+\.py)(?:::[\w.-]+)?[\"']")
JEST_GATE_RE = re.compile(r"[\"'](__tests__/[\w.]+\.test\.tsx?)[\"']")


def basename_of(token: str) -> str | None:
    """把源码字面量归成一个可用于选择的名字；通用名一律不收（收了就等于每次都命中）。

    GENERIC 只在这一头过滤（靶与证人两条来源都过它）。查询侧不再减一遍不是省事：
    两头都减会让「过滤器被删掉」这种破坏扫不到靶——变异自证 D1 打的就是这一口。
    """
    name = Path(token).name
    if "." not in name or Path(name).suffix not in FILE_SUFFIXES or name in GENERIC:
        return None
    return name


def script_targets(script: Path) -> set[str]:
    """该变异脚本会动/会读的文件名集合（含脚本自身，便于 `--files scripts/mutate_x.py` 命中）。"""
    src = script.read_text(encoding="utf-8")
    out = {script.name}
    for token in QUOTED_FILE_RE.findall(src):
        if (name := basename_of(token)):
            out.add(name)
    return out


def discover_scripts() -> dict[str, set[str]]:
    return {p.name: script_targets(p) for p in sorted(SCRIPTS_DIR.glob("mutate_*.py"))}


def gate_witnesses(script: Path) -> list[list[str]]:
    """脚本自己点名的闸 → 可执行的证人命令（pytest / jest）。文件不存在的一律不列。"""
    src = script.read_text(encoding="utf-8")
    cmds: list[list[str]] = []
    for rel in dict.fromkeys(PYTEST_GATE_RE.findall(src)):
        full = rel if rel.startswith("backend/") else f"backend/{rel}"
        if (REPO / full).is_file():
            cmds.append([sys.executable, "-m", "pytest", full, "-q", "--no-header"])
    for rel in dict.fromkeys(JEST_GATE_RE.findall(src)):
        if (FRONT / rel).is_file():
            cmds.append(["npx", "jest", "--silent", rel])
    return cmds


def changed_files(rev: str) -> list[str]:
    proc = subprocess.run(["git", "diff", "--name-only", rev], cwd=REPO,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"git diff --name-only {rev} 失败：{proc.stderr.strip()}")
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def run(cmd: list[str], timeout: int) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=(FRONT if cmd[0] == "npx" else REPO),
                          capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def porcelain() -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=REPO,
                          capture_output=True, text=True).stdout


def parse_summary(out: str) -> tuple[bool, str]:
    hits = FRACTION_RE.findall(out)
    if not hits:
        return False, "脚本没打印可解析的红/绿闸分数（汇总行格式变了，判据要跟着改）"
    bad = [f"{label} {num}/{den}" for label, num, den in hits if int(num) != int(den)]
    if bad:
        return False, "；".join(bad)
    return True, "／".join(f"{label} {num}/{den}" for label, num, den in hits)


def run_sample(script: Path, ids: list[str]) -> bool:
    print(f"[{script.name}]")
    dirty_before = porcelain()

    witnesses = gate_witnesses(script)
    if not witnesses:
        print("  ✗ 该脚本没点名任何可跑的 pytest/jest 闸：缺正向证人，不判绿")
        return False
    for cmd in witnesses:
        code, out = run(cmd, 1800)
        if code != 0:
            print(f"  ✗ 正向证人先红了：{' '.join(cmd[2:])} 基线不绿——此时「变异打红」不算证据")
            print("  " + "｜".join(out.strip().splitlines()[-2:]))
            return False
    print(f"  证人绿 ✓（{len(witnesses)} 个闸文件）")

    cmd = [sys.executable, f"scripts/{script.name}"]
    if ids and '"--only"' in script.read_text(encoding="utf-8"):
        cmd += ["--only", ",".join(ids)]
    elif ids:
        print(f"  ⓘ {script.name} 没有 --only 入口：整脚本跑（抽样 id 只对支持的脚本生效）")
    code, out = run(cmd, 5400)
    if code != 0:
        print(f"  ✗ 脚本退出码 {code}：有变异没打红，或绿闸被误伤")
        print("  " + "｜".join(out.strip().splitlines()[-3:]))
        return False
    ok, note = parse_summary(out)
    if not ok:
        print(f"  ✗ 汇总不齐：{note}")
        return False
    print(f"  打红到位 ✓ {note}")

    if porcelain() != dirty_before:
        print("  ✗ 跑完后工作区与跑前不一致：变异残留在文件里（恢复没做到位）")
        return False
    print("  恢复 ✓ git 工作区与跑前逐字节一致")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--changed-from", default="", help="git diff 基准 rev（PR 用 base sha）")
    ap.add_argument("--files", default="", help="逗号分隔的变更文件（本地手工指定）")
    ap.add_argument("--all", action="store_true", help="全量跑所有 mutate_*.py（夜间用）")
    ap.add_argument("--scripts", default="",
                    help="逗号分隔的脚本名，强制入选（本地复现某一条样本时用，绕开选择映射）")
    ap.add_argument("--only", default="", help="透传给各脚本 --only 的用例 id（逗号分隔）")
    ap.add_argument("--limit", type=int, default=6, help="抽样上限（默认 6 条脚本）")
    ap.add_argument("--list", action="store_true", help="只打印选择映射，不跑")
    ap.add_argument("--select-only", action="store_true",
                    help="只打印本次改动会抽到哪些脚本（不跑证人也不跑变异）")
    args = ap.parse_args()

    registry = discover_scripts()
    if not registry:
        print("scripts/mutate_*.py 一个都没有：无事可做不等于判绿")
        return 1

    if args.list:
        for name, targets in registry.items():
            print(f"{name}：{len(targets)} 个文件名 → {'、'.join(sorted(targets)[:6])}…")
        print(f"共 {len(registry)} 个变异脚本")
        return 0

    ids = [x.strip() for x in args.only.split(",") if x.strip()]
    forced = [x.strip() for x in args.scripts.split(",") if x.strip()]
    unknown = [f for f in forced if f not in registry]
    if unknown:
        print(f"--scripts 点名了不在选择表里的脚本：{'、'.join(unknown)}"
              "（拼错一个脚本名不该被当成「抽到了」，也不该被当成「抽不到」）")
        return 1
    reason: dict[str, str] = {}
    if args.all:
        selected = sorted(registry)
    else:
        raw = [f.strip() for f in args.files.split(",") if f.strip()]
        if args.changed_from:
            raw += changed_files(args.changed_from)
        names = {Path(f).name for f in raw}
        reason = {
            name: "命中 " + "、".join(sorted(targets & names))
            for name, targets in registry.items() if targets & names
        }
        selected = sorted(reason)
        if not raw and not forced:
            print("没有可比对的变更：抽样集为空（本条不判绿也不判红，只说明本轮无事）")
            return 0

    for name in forced:
        reason.setdefault(name, "--scripts 强制入选")
    selected = sorted(set(selected) | set(forced))

    if not selected:
        print("变更文件与任何变异脚本都不相关：抽样集为空。"
              "口径：给新改动立新的 `mutate_*.py`＋闸，而不是指望既有样本覆盖它")
        return 0
    if args.select_only:
        for name in selected:
            print(f"选中 {name}：{reason.get(name, '--all 全量（夜间）')}")
        print(f"抽样集 {len(selected)} 条（--select-only：证人、变异、恢复核验都没跑）")
        return 0
    if len(selected) > args.limit and not args.all:
        skipped = selected[args.limit:]
        selected = selected[: args.limit]
        print(f"⚠️ 抽样上限 {args.limit}：跳过 {len(skipped)} 条 → {'、'.join(skipped)}"
              "（跳过是记账项，夜间 --all 才补得回来；PR 面上别把跳过读成通过）")

    print(f"本轮抽样 {len(selected)} 条变异脚本（串行，禁并发）")
    failed = [name for name in selected if not run_sample(SCRIPTS_DIR / name, ids)]
    print(f"\n变异抽样合计：{len(selected)} 条｜通过 {len(selected) - len(failed)}"
          f"｜失败 {len(failed)}{'' if not failed else '：' + '、'.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

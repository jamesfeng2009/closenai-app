#!/usr/bin/env python3
"""T7-12：在两个干净环境里装同一把锁，逐包比对安装快照。

判据不是「pip 没报错」，而是三件事同时成立：
  1. 每个环境装出来的版本与锁文件逐条相同（多一个、少一个都算不一致）；
  2. 两个环境之间逐包相同；
  3. `pip check` 没有依赖冲突。

用法：
    python3 scripts/verify_backend_lock.py                       # 默认 3.12 + 3.13
    python3 scripts/verify_backend_lock.py --pythons python3.12
    python3 scripts/verify_backend_lock.py --keep                # 保留临时环境便于排查

注意：这条会真的联网下载约 200MB（有 pip 缓存时后续更快）。它不进 CI 的每个 PR，
因为验收要的是「两个干净环境」，本机/发布前各跑一次即可；日常闸是
`backend/tests/test_python_lock.py` 的静态判据。
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOCK = REPO / "backend" / "requirements-dev.lock"

# ensurepip 给每个解释器装的 pip 版本可能不同，它不是锁的一部分。
SNAPSHOT_EXCLUDE = {"pip"}


def parse_lock(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(
            r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^]]*\])?==([^\s;\\]+)", line
        )
        if m:
            pins[m.group(1).lower().replace("_", "-")] = m.group(2)
    return pins


def freeze(python: Path) -> dict[str, str]:
    out = subprocess.run(
        [str(python), "-m", "pip", "freeze", "--all"],
        capture_output=True, text=True, check=True, timeout=300,
    ).stdout
    pins: dict[str, str] = {}
    for line in out.splitlines():
        name, sep, version = line.partition("==")
        if not sep:
            continue
        key = name.split("[", 1)[0].lower().replace("_", "-")
        if key in SNAPSHOT_EXCLUDE:
            continue
        pins[key] = version.strip()
    return pins


def build_env(name: str, base: Path, lock_pins: dict[str, str], interpreter: str) -> dict[str, str]:
    env_dir = base / name
    subprocess.run([interpreter, "-m", "venv", str(env_dir)], check=True, timeout=300)
    python = env_dir / "bin" / "python"
    if not python.exists():
        python = env_dir / "Scripts" / "python.exe"
    subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", "--require-hashes",
         "-r", str(LOCK)],
        check=True, timeout=1800,
    )
    subprocess.run([str(python), "-m", "pip", "check"], check=True, timeout=300)
    got = freeze(python)

    missing = sorted(set(lock_pins) - set(got))
    extra = sorted(set(got) - set(lock_pins))
    wrong = sorted(
        (k, lock_pins[k], got[k]) for k in set(lock_pins) & set(got)
        if lock_pins[k] != got[k]
    )
    if missing or extra or wrong:
        raise SystemExit(
            f"{name} 与锁文件不一致：缺 {missing[:8]}；多 {extra[:8]}；"
            f"版本不同 {wrong[:8]}"
        )
    print(f"  {name}: {len(got)} 个包，全部与锁一致（pip check 通过）")
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pythons", default="python3.12,python3.13",
                    help="逗号分隔的解释器命令（默认 python3.12,python3.13）")
    ap.add_argument("--keep", action="store_true", help="保留临时 venv")
    args = ap.parse_args()

    pythons = [p.strip() for p in args.pythons.split(",") if p.strip()]
    for py in pythons:
        if shutil.which(py) is None:
            print(f"未跑：宿主没有 {py}，不能把它记成绿")
            return 2
    if not LOCK.is_file():
        print(f"锁文件不存在：{LOCK}")
        return 1

    lock_pins = parse_lock(LOCK)
    print(f"锁：{LOCK.relative_to(REPO)}（{len(lock_pins)} 个包）")

    tmp = Path(tempfile.mkdtemp(prefix="t712-lock-"))
    snapshots: dict[str, dict[str, str]] = {}
    try:
        for py in pythons:
            print(f"→ {py}")
            snapshots[py] = build_env(py.replace("/", "_"), tmp, lock_pins, py)
    finally:
        if args.keep:
            print(f"保留临时环境：{tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    names = list(snapshots)
    base = snapshots[names[0]]
    for other in names[1:]:
        if snapshots[other] != base:
            diff = {
                k: (base.get(k), snapshots[other].get(k))
                for k in set(base) | set(snapshots[other])
                if base.get(k) != snapshots[other].get(k)
            }
            print(f"两个干净环境不一致：{diff}")
            return 1

    print(f"\n结论：{len(names)} 个干净环境 × {len(base)} 个包，快照逐条一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""T7-12（Python 依赖可重复安装）的变异自证（2026-10-26）。

判据不是「测试跑过」，而是「每条纪律都能被打红一次，且红在它该红的闸上」。
红靶逐条覆盖（不写条数——条数句和清单漂过一次，见本文件 GIT 历史 2026-11-04 那笔）：
哈希缺失、直接依赖版本漂出声明范围、两份锁共享包版本分叉、开发锁丢掉仅测试用的包、
镜像回退到范围依赖、CI 回退到范围依赖、说明文档丢掉前置、
测试建库用了没声明的驱动（M8，2026-11-03 CI 三条红的形状）、
锁里新增包而文档没登记那一格（M9）。
绿靶证明这些闸不是「见字就红」的假红制造机：注释不改变判据、文档加一句不改形状、
以及 G3——把 M8 那个连接串只写进注释，判据必须仍绿。

口径照抄既有纪律（[[feedback-mutation-gates-no-concurrency]]、
[[feedback-mutation-restore-no-git-checkout]]、[[feedback-definition-of-done-tests]]）：
**串行跑**、copy2 备份写回 ＋ `filecmp` 逐字节复核、**不用 `git checkout`**。

用法：
    ./.venv/bin/python scripts/mutate_t712_gates.py --check
    ./.venv/bin/python scripts/mutate_t712_gates.py
    ./.venv/bin/python scripts/mutate_t712_gates.py --only M1,M7
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
RUNTIME_LOCK = REPO / "backend" / "requirements.lock"
DEV_LOCK = REPO / "backend" / "requirements-dev.lock"
DOCKERFILE = REPO / "deploy" / "Dockerfile.py-api"
CI = REPO / ".github" / "workflows" / "ci.yml"
DOC = REPO / "docs" / "python-dependency-lock.md"
CONFTEST = REPO / "backend" / "tests" / "conftest.py"

TARGET = "backend/tests/test_python_lock.py"
VENV_PY = REPO / ".venv" / "bin" / "python"

# 锚点出现次数不是 1 的用例，在这里显式登记期望次数。
ANCHOR_COUNT = {"M6": 2, "M7": 3}

# 每条：(id, 说明, (文件, 原文, 坏文), target, 名片段)
MUTATIONS = [
    (
        "M1",
        "抽掉 pgvector 的两条哈希：锁文件不再能校验索引端内容，"
        "装到的包可能与取证时不是同一个",
        (RUNTIME_LOCK,
         "pgvector==0.5.0 \\\n"
         "    --hash=sha256:07a9dcf735696879406983afc6eba9a787cef7c0cf6c367ca1a5779f036dee74 \\\n"
         "    --hash=sha256:fedc9800894e6da2be51358d7b7c574bf34f247ca741a5a09513622135f5964f",
         "pgvector==0.5.0"),
        TARGET, "没有 sha256 哈希",
    ),
    (
        "M2",
        "把 fastapi 锁到声明的下界之下（0.100.0 < >=0.110.0）："
        "锁与 requirements.txt 已经漂了，干净环境会装出一个不满足声明的版本",
        (RUNTIME_LOCK, "fastapi==0.141.1", "fastapi==0.100.0"),
        TARGET, "不满足声明的",
    ),
    (
        "M3",
        "开发锁把共享包 pgvector 降到 0.4.0：两份锁各装各的，"
        "「CI 绿 = 镜像里的版本」不再成立",
        (DEV_LOCK, "pgvector==0.5.0", "pgvector==0.4.0"),
        TARGET, "在两份锁里版本不同",
    ),
    (
        "M4",
        "开发锁丢掉 pypdf：CI 上 PDF 用例会在 import 处红，"
        "而镜像不需要它也看不出缺",
        (DEV_LOCK,
         "pypdf==6.19.0 \\\n"
         "    --hash=sha256:7e5d6e730e7dae87d560a2cee218b852f6498c8be61966f3cd02ead971e48d14 \\\n"
         "    --hash=sha256:bbc43aca292369ccc6cbc8a921991ecf2538a3587ab5a116eff06c321d647155\n",
         ""),
        TARGET, "开发锁缺 pypdf",
    ),
    (
        "M5",
        "镜像回退到 requirements.txt：部署面重新开始按 >= 现场解析，"
        "而 CI 仍装锁——两边装的不是同一套",
        (DOCKERFILE,
         "COPY backend/requirements.lock ./\n"
         "RUN pip install --require-hashes -r requirements.lock",
         "COPY backend/requirements.txt ./\n"
         "RUN pip install -r requirements.txt"),
        TARGET, "镜像没有拷贝运行锁",
    ),
    (
        "M6",
        "CI 的安装入口回退到 requirements.txt：流水线不再受哈希约束，"
        "锁文件只成了摆设",
        (CI,
         "run: bash scripts/install_backend_deps.sh --dev",
         "run: pip install -r backend/requirements.txt"),
        TARGET, "仍引用 requirements.txt",
    ),
    (
        "M7",
        "说明文档丢掉 --require-hashes：后来的人照文档装就没有哈希校验，"
        "而文档本身是这套流程唯一的入口说明",
        (DOC, "--require-hashes", "--no-hashes"),
        TARGET, "依赖说明缺",
    ),
    (
        "M8",
        "把测试建库的驱动换成 aiomysql（开发锁里没有它），却不往开发输入里加声明："
        "clean env 一 import backend/database.py 就 ModuleNotFoundError"
        "——这正是 2026-11-03 CI 三条红的形状",
        (CONFTEST,
         'engine = create_async_engine("sqlite+aiosqlite:///:memory:")',
         'engine = create_async_engine("mysql+aiomysql://x/y")'),
        TARGET, "但 `install_backend_deps.sh --dev`",
    ),
    (
        "M9",
        "开发锁新增一个包、说明文档不登记那一格：后来的人照文档装就不知道该用 --dev，"
        "「文档说的」和「锁里的」开始分叉（本批把它做成靶，是因为这一批自己就漏改过一次）",
        (DOC,
         "- `aiosqlite` —— 2026-11-04 补。**这条是 CI 三条红的根因**：",
         "- （这一格被删掉了）："),
        TARGET, "只在开发锁里，但",
    ),
]

GREEN = [
    (
        "G1",
        "在运行锁里加一行注释（不改依赖）：闸判的是条目与哈希，不读注释",
        (RUNTIME_LOCK,
         "# T7-12：Python 依赖锁（完整、带哈希）。不要手改本文件。",
         "# 注释不改变判据。\n# T7-12：Python 依赖锁（完整、带哈希）。不要手改本文件。"),
        TARGET,
    ),
    (
        "G2",
        "在说明文档里加一行 HTML 注释（不改内容）：文档闸找的是关键词，不排除注释",
        (DOC, "## 文件角色", "<!-- 注释不改变判据 -->\n## 文件角色"),
        TARGET,
    ),
    (
        "G3",
        "在 conftest 里加一句**注释**，内容就是 M8 那个没声明的驱动连接串："
        "判据必须仍绿——它只认建引擎的调用点，不认解释性文字（否则就成了见字就红）",
        (CONFTEST,
         'engine = create_async_engine("sqlite+aiosqlite:///:memory:")',
         '# 注释里的 mysql+aiomysql://x/y 只是解释，不是调用点。\n'
         '    engine = create_async_engine("sqlite+aiosqlite:///:memory:")'),
        TARGET,
    ),
    (
        "G4",
        "在说明文档 aiosqlite 那一格里追加一句话（列表项形状不动）："
        "文档闸判的是「有没有这一格」，不是字数",
        (DOC,
         "- `aiosqlite` —— 2026-11-04 补。",
         "- `aiosqlite` —— 2026-11-04 补（绿闸 G4 追加的一句，不改形状）。"),
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
    with tempfile.TemporaryDirectory(prefix="mutate-t712-") as tmp:
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

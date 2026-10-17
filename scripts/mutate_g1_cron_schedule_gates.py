#!/usr/bin/env python3
"""批1 G1（剧本调度）的变异自证（2026-10-17）。

正题是「**代码全对、从调度面不可达**」：`dispatch_journey_events` 的调用链一路完好，
断的是 `vercel.json` 里那一条排程——所以本脚本的每一靶都只动调度面/宣称面，
不动业务代码，验证 `test_cron_schedule_coverage.py` 真的盯得住。

靶子（每条都必须打红）：
- M1 撤掉 journey 那条排程＝回到本批立项时的状态；
- M2 排成每日一次＝端点 docstring 说「每小时」，节律与清单脱钩；
- M3 清单里的 path 改名＝cron 每小时打一个不存在的路径，静默 404；
- M4 health docstring 写回「Vercel Cron 每 5 分钟调用」＝谎话登记；
- M5 周节律端点排成每天（会把周洞察/漂移检测变成日跑）；
- M6 删/改豁免表里的路由＝豁免条目烂掉却没人管。

应保持绿（证明这条闸不是假红制造机）：
- G1 只把每小时那格的分钟偏移（节律没变）；
- G2 给端点 docstring 追加 15 行说明（判据是节律词，不是源码长度）。

恢复一律 **copy2 + filecmp 逐字节校验**，不用 git checkout
（见 [[feedback-mutation-restore-no-git-checkout]]）；**严禁并发跑**
（见 [[feedback-mutation-gates-no-concurrency]]）。

用法：
    ./.venv/bin/python scripts/mutate_g1_cron_schedule_gates.py --check
    ./.venv/bin/python scripts/mutate_g1_cron_schedule_gates.py
    ./.venv/bin/python scripts/mutate_g1_cron_schedule_gates.py --only M1,G1
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
VERCEL = REPO / "vercel.json"
CRON_PY = REPO / "backend" / "routers" / "cron.py"
GATE = "backend/tests/test_cron_schedule_coverage.py"

JOURNEY_BLOCK = '''    {
      "path": "/api/v1/cron/journey",
      "schedule": "0 * * * *"
    },
'''
JOURNEY_SCHEDULE = '"path": "/api/v1/cron/journey",\n      "schedule": "0 * * * *"'
WEEKLY_BLOCK = '"path": "/api/v1/cron/weekly-insight",\n      "schedule": "0 0 * * 0"'
JOURNEY_DOC = '    """第一周剧本调度（PRD §5.19.3 P1-B02 / 架构方案 §二 M2，Vercel Cron 每小时触发）。'
HEALTH_DOC = '    """保热端点（供外部探活/手动调用）。'

# (id, 破坏说明, 文件, 原文, 坏文, 受害测试名片段)
MUTATIONS = [
    (
        "M1",
        "撤掉 /cron/journey 的排程（＝本批立项时那个状态：端点在、调度没有）",
        VERCEL, JOURNEY_BLOCK, "",
        "test_every_get_cron_route_is_scheduled_or_exempt",
    ),
    (
        "M2",
        "把 journey 排成每日一次：docstring 宣称每小时，清单却是 0 3 * * *",
        VERCEL, JOURNEY_SCHEDULE,
        '"path": "/api/v1/cron/journey",\n      "schedule": "0 3 * * *"',
        "test_docstring_cadence_claim_matches_manifest",
    ),
    (
        "M3",
        "清单里的 path 打错一个字母：cron 每小时敲一个不存在的路径，只会静默重试",
        VERCEL, '"path": "/api/v1/cron/mood-reminder",',
        '"path": "/api/v1/cron/mood-remindrr",',
        "test_scheduled_paths_are_live_get_routes",
    ),
    (
        "M4",
        "health 的 docstring 写回「Vercel Cron 每 5 分钟调用」：清单没这条，就是谎话登记",
        CRON_PY, HEALTH_DOC,
        '    """保热端点（Vercel Cron 每 5 分钟调用）。',
        "test_docstring_cadence_claim_matches_manifest",
    ),
    (
        "M5",
        "周洞察排成每天：周日字段丢了，每周该跑一次的生成任务变成日跑",
        VERCEL, WEEKLY_BLOCK,
        '"path": "/api/v1/cron/weekly-insight",\n      "schedule": "0 0 * * *"',
        "test_weekly_endpoints_are_actually_weekly",
    ),
    (
        "M6",
        "把豁免的 health 路由改名：EXEMPT 里那条登记已经指向不存在的路径",
        CRON_PY, '@router.get("/cron/health")',
        '@router.get("/cron/health-keepwarm")',
        "test_exempt_table_is_not_rotten",
    ),
]

# (id, 不该打红的说明, 文件, 原文, 新文)
GREEN = [
    (
        "G1",
        "journey 那格只把分钟从 0 挪到 30：还是每小时，节律判据不该认死字面分钟",
        VERCEL, JOURNEY_SCHEDULE,
        '"path": "/api/v1/cron/journey",\n      "schedule": "30 * * * *"',
    ),
    (
        "G2",
        "往 journey 的 docstring 追加 15 行说明：判据取节律词，文档变长不该变红",
        CRON_PY, JOURNEY_DOC,
        JOURNEY_DOC + "\n" + "".join(f"    登记占位第 {i} 行。" for i in range(15)),
    ),
]


def pytest(node: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(REPO / ".venv/bin/python"), "-m", "pytest", node, "-q", "--no-header"],
        cwd=REPO, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode, proc.stdout + proc.stderr


def patch(path: Path, old: str, new: str) -> None:
    src = path.read_text(encoding="utf-8")
    count = src.count(old)
    assert count == 1, f"{path.name}：锚点命中 {count} 次（要求恰好 1 次）\n{old[:80]!r}"
    path.write_text(src.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()

    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    if args.check:
        bad = 0
        for case in [*MUTATIONS, *GREEN]:
            path, old = case[2], case[3]
            n = path.read_text(encoding="utf-8").count(old)
            if n != 1:
                print(f"坏锚点 {case[0]}：{path.name} 命中 {n} 次")
                bad += 1
        total_cases = len(MUTATIONS) + len(GREEN)
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}（共 {total_cases} 条）")
        return 1 if bad else 0

    tmp = Path(tempfile.mkdtemp(prefix="g1-cron-gate-backup-"))
    originals: dict[Path, Path] = {}
    for case in [*MUTATIONS, *GREEN]:
        p = case[2]
        if p not in originals:
            bak = tmp / p.name
            shutil.copy2(p, bak)
            originals[p] = bak

    def restore() -> None:
        for p, bak in originals.items():
            shutil.copy2(bak, p)
            assert filecmp.cmp(bak, p, shallow=False), f"{p} 恢复后字节不一致"

    red_ok = red_fail = green_ok = green_fail = 0
    failures: list[str] = []
    try:
        for cid, desc, path, old, new, marker in MUTATIONS:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = pytest(GATE)
                hit = marker in out and re.search(r"\d+ (failed|error)", out)
                if code != 0 and hit:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（exit={code}，marker={bool(hit)}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-8:]))
            finally:
                restore()
        for cid, desc, path, old, new in GREEN:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = pytest(GATE)
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 应保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 应保持绿 ✗（exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-12:]))
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
    print("源文件已全部按备份写回并逐字节校验一致。")
    return 0 if red_fail == 0 and green_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

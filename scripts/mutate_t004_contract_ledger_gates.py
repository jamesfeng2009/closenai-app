#!/usr/bin/env python3
"""T0-04（契约台账真实性 G-01）的变异自证（2026-10-18）。

正题是「台账说的接线必须是真的」：`contracts/endpoints.md` 的「前端调用」列
是 Go 切流排序的唯一依据，一次漏扫（跨行模板串）就把已接线端点记成了 `—`。
本脚本的每一靶只动台账/调用点/行号，不动业务代码，
验证 `backend/tests/test_contract_ledger_truth.py` 三条判据真的盯得住。

靶子（每条都必须打红，红的必须是判据本身）：
- M1 注释掉 PaywallScreen 的 getLifeArchivePdf 调用＝「标已接线却无调用点」；
- M2 在 api.ts 顶部插一行＝全部行号漂移 1，台账引用不再指真；
- M3 把台账 #80 行的前端调用改回 `—`＝台账与当前扫描脱钩；
- M4 把台账 #79 行引用改指文件头注释＝引用不落在 export function 内。

应保持绿（证明这条闸不是假红制造机）：
- G1 api.ts 末尾追加注释（不移动任何已登记引用行）；
- G2 PaywallScreen 追加 15 行说明注释（调用点仍在，判据不认文档长度）。

恢复一律 **copy2 + filecmp 逐字节校验**，不用 git checkout
（见 [[feedback-mutation-restore-no-git-checkout]]）；**严禁并发跑**
（见 [[feedback-mutation-gates-no-concurrency]]）。

用法：
    ./.venv/bin/python scripts/mutate_t004_contract_ledger_gates.py --check
    ./.venv/bin/python scripts/mutate_t004_contract_ledger_gates.py
    ./.venv/bin/python scripts/mutate_t004_contract_ledger_gates.py --only M1,G1
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
API_TS = REPO / "frontend" / "src" / "services" / "api.ts"
PAYWALL = REPO / "frontend" / "src" / "screens" / "PaywallScreen.tsx"
MD = REPO / "contracts" / "endpoints.md"
GATE = "backend/tests/test_contract_ledger_truth.py"

API_HEADER = "/**\n * ClosenAI API 服务。\n"
API_TAIL = "export async function getShareLink(): Promise<ShareLink> {"
PAYWALL_CALL = "const bytes = await getLifeArchivePdf(scope);"
MD_WIRED_ROW = "| 80 | GET | `/api/v1/share/life-archive/pdf` | PDF 二进制 | auth | frontend/src/services/api.ts:761 |"
MD_UNWIRED_REF = "| 79 | POST | `/api/v1/share/life-archive` | JSON 信封 | auth | frontend/src/services/api.ts:933（封装未接线）"

MUTATIONS = [
    (
        "M1",
        "PaywallScreen 的 getLifeArchivePdf 调用改名：台账 #80 仍标「已接线」，封装却零调用点",
        PAYWALL, PAYWALL_CALL,
        "const bytes = await getLifeArchivePdfDisabled(scope);",
        "test_wired_refs_have_real_screen_or_hook_callers",
    ),
    (
        "M2",
        "api.ts 顶部插一行：所有真实调用行号 +1，台账里的 api.ts:N 引用集体漂移",
        API_TS, API_HEADER, "// 登记占位行（行号漂移变异）。\n" + API_HEADER,
        "test_ledger_matches_fresh_scan",
    ),
    (
        "M3",
        "把台账 #80 的前端调用改回 `—`：台账与当前扫描脱钩（漏扫失真的镜像）",
        MD, MD_WIRED_ROW,
        "| 80 | GET | `/api/v1/share/life-archive/pdf` | PDF 二进制 | auth | —",
        "test_ledger_matches_fresh_scan",
    ),
    (
        "M4",
        "把台账 #79 的引用改成 api.ts:5（文件头注释区）：引用不落在任何 export function 内",
        MD, MD_UNWIRED_REF,
        MD_UNWIRED_REF.replace("api.ts:933", "api.ts:5"),
        "test_every_api_ts_ref_lands_in_export_function",
    ),
]

GREEN = [
    (
        "G1",
        "在 getShareLink 签名行行尾追加注释：不新增行、不漂移任何登记引用行号，台账仍与扫描同源",
        API_TS, API_TAIL, API_TAIL + " // 行尾注释，不移动行号",
    ),
    (
        "G2",
        "PaywallScreen 追加 15 行说明：调用点仍在，判据不该认文档长度",
        PAYWALL, PAYWALL_CALL,
        "\n".join(f"// 登记占位第 {i} 行。" for i in range(15)) + "\n" + PAYWALL_CALL,
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
    ap.add_argument("--fail-on-green", action="store_true", help="任一靶未如预期即退出码 1")
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

    tmp = Path(tempfile.mkdtemp(prefix="t004-ledger-gate-backup-"))
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
                    print(f"[{cid}] 打红 ✓（{marker}）  {desc}")
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

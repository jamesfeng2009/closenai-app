#!/usr/bin/env python3
"""批1 G2（Life Archive 导出接线）的变异自证（2026-10-17）。

正题是「**付款通、货不可领**」：买断链路（StoreKit → /iap/verify → user_entitlements）
早就全绿，`GET /share/life-archive/pdf` 也早写好，缺的只是 App 里那一下点击。
所以本脚本的每一靶都打「接线的那一半」，撤掉哪半，`pdfExportWiring.test.ts` 就得红。

靶子（每条都必须打红）：
- A1 封装撤调用＝回到立项时的状态（付了钱仍拿不到档案）；
- A2 前端自造落盘名＝第二真源，分享面板与 §七 分叉；
- A3 40304 退回 generic＝「没买断」被说成「稍后再试」，用户会反复重试；
- A4 40304 常量抄成 40302＝把档案买断门当订阅门，文案与埋点全串；
- A5 zh 的 entitlement 填英文原文＝本仓踩过两次的英文兜底根因；
- A6 km 删掉 entitlement 一行＝静默回退成英文；
- A7 封装丢 scope＝三档导出全变成默认档，买 1y 拿到 all。

应保持绿（证明这条闸不是假红制造机）：
- B1 导出函数体插 15 行注释：接线闸钉的是逐字锚点，不是源码行数；
- B2 zh 的 entitlement 换一句中文：判据管「是不是英文」，不管措辞。

恢复一律 **copy2 + filecmp 逐字节校验**，不用 git checkout
（见 [[feedback-mutation-restore-no-git-checkout]]）；**严禁并发跑**
（见 [[feedback-mutation-gates-no-concurrency]]）。

用法：
    ./.venv/bin/python scripts/mutate_g2_life_archive_pdf_gates.py --check
    ./.venv/bin/python scripts/mutate_g2_life_archive_pdf_gates.py
    ./.venv/bin/python scripts/mutate_g2_life_archive_pdf_gates.py --only A1,B1
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
FRONT = REPO / "frontend"
LOCALES = FRONT / "src" / "i18n" / "locales"

API_TS = FRONT / "src" / "services" / "api.ts"
PDF_FILES = FRONT / "src" / "config" / "pdfFiles.ts"
PAYWALL = FRONT / "src" / "screens" / "PaywallScreen.tsx"

WIRING = "__tests__/pdfExportWiring.test.ts"
I18N = "__tests__/i18nLocales.test.ts"

CALL = "      const bytes = await getLifeArchivePdf(scope);\n"
EXPORT = "      await exportPdf(bytes, lifeArchivePdfFile(scope));\n"
NAME_LINE = '  return `closenai_life_archive_${scope}.pdf`;'
KEY_LINE = "  if (code === CODE_ENTITLEMENT_REQUIRED) return 'pdf.failed.entitlement';"
CODE_LINE = "export const CODE_ENTITLEMENT_REQUIRED = 40304; // ENTITLEMENT_REQUIRED：没有档案买断权益"
QUERY = "`/share/life-archive/pdf?scope=${scope}&language=${langParam()}&regenerate=${regenerate}`"
ZH_LINE = '      "entitlement": "这份档案还没有导出权益——可以先买断，或用掉本月的 Pro 免费次数。",'
EN_LINE = '      "entitlement": "You don\'t have export rights for this archive yet — buy it, or use your monthly Pro credit.",'
KM_LINE = '      "entitlement": "មិនទាន់មានសិទ្ធិនាំចេញសម្រាប់ប័ណ្ណសារនេះទេ សូមទិញ ឬប្រើសិទ្ធិប្រចាំខែរបស់ Pro។",'

# (id, 破坏说明, 文件, 原文, 坏文, 测试类型, 受害测试, 失败用例名片段)
MUTATIONS = [
    (
        "A1",
        "撤掉封装调用（＝回到立项时那个状态：权益买得到，档案拿不出）",
        PAYWALL, CALL, "", "jest", WIRING, "四个封装各有且只有一个调用点",
    ),
    (
        "A2",
        "前端自造落盘名 closenai_archive_{scope}.pdf：与 §七 登记值分叉，成了第二真源",
        PDF_FILES, NAME_LINE, "  return `closenai_archive_${scope}.pdf`;",
        "jest", WIRING, "Life Archive 是拼接名",
    ),
    (
        "A3",
        "40304 退回 generic：「没买断」被说成「稍后再试」，用户会反复重试同一扇门",
        PDF_FILES, KEY_LINE, "",
        "jest", WIRING, "三个码各映射到自己的文案键",
    ),
    (
        "A4",
        "40304 常量抄成 40302：档案买断门被当成订阅门，与 backend/core/errors.py 脱钩",
        PDF_FILES, CODE_LINE,
        "export const CODE_ENTITLEMENT_REQUIRED = 40302; // ENTITLEMENT_REQUIRED：没有档案买断权益",
        "jest", WIRING, "三个常量与 Python 侧逐值相等",
    ),
    (
        "A5",
        "zh 的 entitlement 填回英文原文（本仓踩过两次的英文兜底根因：逐字等于 en 才算漏翻）",
        LOCALES / "zh.ts", ZH_LINE, EN_LINE,
        "jest", I18N, "没有任何一个值逐字等于英文",
    ),
    (
        "A6",
        "km 删掉 entitlement 一行：高棉语界面会静默回落成英文整句",
        LOCALES / "km.ts", KM_LINE + "\n", "",
        "jest", WIRING, "pdf.failed.entitlement：15 语言都非空",
    ),
    (
        "A7",
        "封装丢掉 scope 查询参：三档导出全退化成后端默认档 all，买 1y 却拿到 all",
        API_TS, QUERY,
        '`/share/life-archive/pdf?language=${langParam()}&regenerate=${regenerate}`',
        "jest", WIRING, "档案封装带 scope 与 regenerate 查询参",
    ),
]

GREEN = [
    (
        "B1",
        "往导出函数体插 15 行注释：接线闸钉的是逐字锚点，函数体变长不该把它变红",
        PAYWALL, EXPORT,
        EXPORT + "".join(f"      // 增长占位 {i}\n" for i in range(15)),
        "jest", WIRING,
    ),
    (
        "B2",
        "zh 的 entitlement 换一句中文：判据管「是不是英文」，不管措辞",
        LOCALES / "zh.ts", ZH_LINE,
        '      "entitlement": "还没有这份档案的导出权益——请先买断，或使用本月 Pro 免费额度。",',
        "jest", I18N,
    ),
]


def jest(test_file: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["npx", "jest", "--silent", test_file],
        cwd=FRONT, capture_output=True, text=True, timeout=900,
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
    cases = [*MUTATIONS, *GREEN]

    if args.check:
        bad = 0
        for case in cases:
            path, old = case[2], case[3]
            n = path.read_text(encoding="utf-8").count(old)
            if n != 1:
                print(f"坏锚点 {case[0]}：{path.name} 命中 {n} 次")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}（共 {len(cases)} 条）")
        return 1 if bad else 0

    tmp = Path(tempfile.mkdtemp(prefix="g2-archive-gate-backup-"))
    originals: dict[Path, Path] = {}
    for case in cases:
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
        for cid, desc, path, old, new, kind, test, marker in MUTATIONS:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = jest(test)
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
        for cid, desc, path, old, new, kind, test in GREEN:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = jest(test)
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

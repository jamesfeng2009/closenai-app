#!/usr/bin/env python3
"""前端三类闸的变异自证（2026-10-14，jest 基线修复批的 DoD ②）。

被修的三类根因
1. `referral.test.ts` 里三条 ENOENT：站点 28a9fc3 迁 Astro 后真源搬到 `site/public/`，
   测试路径陈旧（产品件没缺）。
2. `i18nLocales.test.ts` 的 17 条英文兜底：317b390 往 15 个 locale 写回
   `explore.reflectionsTitle`/`reflectionsEmpty` 时非英语填的是英文原文。
3. `feedbackStates.test.ts` 的 `setSavingId(null)`：固定 16 行窗被 317b390 插进的
   13 行本地落库挤出窗外（假红）。

闸要能被一次真实破坏打红才算数，所以这里逐条写坏→只跑受害的测试文件→要求输出里
出现那条失败用例的名字→备份写回。**恢复用 copy2 + filecmp，不用 git checkout**
（见 [[feedback-mutation-restore-no-git-checkout]]）。

用法：
    python3 scripts/mutate_frontend_gates.py --check   # 只验锚点，各命中一次
    python3 scripts/mutate_frontend_gates.py           # 15 条红 + 1 条绿
    python3 scripts/mutate_frontend_gates.py --only F1,F9
"""
from __future__ import annotations

import argparse
import filecmp
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FRONT = REPO / "frontend"
SITE = REPO / "site" / "public"
LOCALES = FRONT / "src" / "i18n" / "locales"
REFLECT = FRONT / "src" / "screens" / "ReflectScreen.tsx"

I18N = "__tests__/i18nLocales.test.ts"
REFERRAL = "__tests__/referral.test.ts"
FEEDBACK = "__tests__/feedbackStates.test.ts"

# ---------------------------------------------------------------- 红闸清单
# (id, 破坏说明, 文件, 原文, 坏文, 受害测试, 失败用例名片段)
MUTATIONS: list[tuple[str, str, Path, str, str, str, str]] = [
    (
        "F1",
        "es 的 reflectionsTitle 改回英文原文（正是 317b390 的写法）",
        LOCALES / "es.ts",
        '    "reflectionsTitle": "Mis reflexiones",',
        '    "reflectionsTitle": "My Reflections",',
        I18N,
        "没有任何一个值逐字等于英文",
    ),
    (
        "F2",
        "ja 改成拉丁文但**不与英文逐字相等**：只可能由文字种类闸抓到",
        LOCALES / "ja.ts",
        '    "reflectionsTitle": "私の振り返り",',
        '    "reflectionsTitle": "My Furikaeri",',
        I18N,
        "每个值都含该语言文字",
    ),
    (
        "F3",
        "de 塞进连续 3 个英文虚词：整句英文闸",
        LOCALES / "de.ts",
        '    "reflectionsTitle": "Meine Reflexionen",',
        '    "reflectionsTitle": "das the of your",',
        I18N,
        "没有整句英文的值",
    ),
    (
        "F4",
        "fr 的 hourOption 丢掉 {{hour}} 占位符：运行时直出裸花括号",
        LOCALES / "fr.ts",
        '    "hourOption": "{{hour}}:00",',
        '    "hourOption": ":00",',
        I18N,
        "名称与个数逐键相等",
    ),
    (
        "F5",
        "es 的 home.social 译掉：同形词豁免条目当场变死条目",
        LOCALES / "es.ts",
        '    "social": "Social",',
        '    "social": "Redes sociales",',
        I18N,
        "同形词豁免表不许留死条目",
    ),
    (
        "F6",
        "es 的 auth.version 换行并进一行：换行个数闸",
        LOCALES / "es.ts",
        '"version": "ClosenAI v1.0.0\\nBuild 2026.08.03"',
        '"version": "ClosenAI v1.0.0 Build 2026.08.03"',
        I18N,
        "换行转义个数逐键相等",
    ),
    (
        "F7",
        "ja（CLDR 单形态语言）多出 chatCount_one：死键档",
        LOCALES / "ja.ts",
        '    "chatCount_other": "{{count}}件の会話",',
        '    "chatCount_one": "{{count}}件の会話",\n    "chatCount_other": "{{count}}件の会話",',
        I18N,
        "变体键恰好等于该语言的类别集",
    ),
    (
        "F8",
        "ms 的键名打错一个字母：静默回退成英文",
        LOCALES / "ms.ts",
        '    "reflectionsTitle": "Refleksi Saya",',
        '    "reflectionsTitel": "Refleksi Saya",',
        I18N,
        "键集合与 en 完全一致",
    ),
    (
        "F9",
        "_redirects 的 /r/* 从 200 重写改成 301 跳转",
        SITE / "_redirects",
        "/r/*     /r/index.html  200",
        "/r/*     /r/index.html  301",
        REFERRAL,
        "落地页只重写 /r/*",
    ),
    (
        "F10",
        "AASA 接管路径从 /r/* 放宽成 *（整站变唤起 App）",
        SITE / ".well-known" / "apple-app-site-association",
        '"/": "/r/*",',
        '"/": "*",',
        REFERRAL,
        "落地页只重写 /r/*",
    ),
    (
        "F11",
        "落地页码表少一个字符（与前后端不再逐字相等 → 静默丢归因）",
        SITE / "r" / "index.html",
        "[ABCDEFGHJKMNPQRSTUVWXYZ234567]{8}",
        "[ABCDEFGHJKNPQRSTUVWXYZ234567]{8}",
        REFERRAL,
        "落地页的码字符集与前后端逐字相等",
    ),
    (
        "F12",
        "onSaveDraft 的 finally 里删掉 setSavingId(null)（按钮永久卡「保存中」）",
        REFLECT,
        "    } finally {\n      setSavingId(null);\n    }",
        "    } finally {\n    }",
        FEEDBACK,
        "onSaveDraft 记录 savingId",
    ),
    (
        "F13",
        "删掉连点守卫 if (savingId) return;",
        REFLECT,
        "    if (savingId) return; // 单条进行中：忽略连点，避免同一草稿重复入库\n",
        "",
        FEEDBACK,
        "onSaveDraft 记录 savingId",
    ),
    (
        "F14",
        "删掉按钮的 disabled={savingId === msg.id}",
        REFLECT,
        "                  disabled={savingId === msg.id}\n",
        "",
        FEEDBACK,
        "按钮在保存中 disabled",
    ),
    (
        "F15",
        "keyboardVerticalOffset 从被验证过的 90 改成 100",
        REFLECT,
        "keyboardVerticalOffset={90}",
        "keyboardVerticalOffset={100}",
        FEEDBACK,
        "既有键盘避让参数不动",
    ),
]

# ---------------------------------------------------------------- 绿闸清单
# 破坏「不该打红」的东西＝证明这条守卫不是假红制造机。
GREEN: list[tuple[str, str, Path, str, str, str]] = [
    (
        "G1",
        "往 onSaveDraft 的 try 里插 20 行（正是 317b390 造成假红的那种增长）："
        "函数体切片守卫不该因此变红",
        REFLECT,
        "    setSavingId(msg.id);\n",
        "    setSavingId(msg.id);\n" + "".join(f"    // 增长占位 {i}\n" for i in range(20)),
        FEEDBACK,
    ),
]


def jest(test_file: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["npx", "jest", "--silent", test_file],
        cwd=FRONT,
        capture_output=True,
        text=True,
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
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}")
        return 1 if bad else 0

    tmp = Path(tempfile.mkdtemp(prefix="mut-frontend-"))
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
    try:
        for cid, desc, path, old, new, test, marker in MUTATIONS:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = jest(test)
                hit = (marker in out) and re.search(r"\d+ failed", out)
                if code != 0 and hit:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    print(f"[{cid}] 打红 ✗（exit={code}，marker={bool(hit)}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-8:]))
            finally:
                restore()
        for cid, desc, path, old, new, test in GREEN:
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
    print("源文件已全部按备份写回并逐字节校验一致。")
    return 0 if red_fail == 0 and green_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

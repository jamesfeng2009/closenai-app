#!/usr/bin/env python3
"""U3-a（推送频率 UI）的变异自证（2026-10-16）。

这条缺陷的正题是「**写进得、读不出**」：PATCH 侧从立项之前就一直是好的，
所以任何只测「能不能写进去」的闸都是空转。本脚本据此选靶：

- P1 撤掉 get_profile 的传值 → 只有读路径被打断才会红；
- P3/P4 把读侧档位改成 Optional 或少一档 → 同集闸红（前端会打出键名原文那种坏法）；
- P5 撤掉空串归一 → 老行直接 500，个人页整页打挂；
- F1 把 handler 改成只改本地 state（「装作存上了」）→ 接线闸红；
- F2 把界面读侧硬编码成 'daily' → 正是本批之前那个状态；
- F4/F5 打的是文案面：非英语填英文、15 语言缺一键。

恢复一律 **copy2 + filecmp 逐字节校验**，不用 git checkout
（见 [[feedback-mutation-restore-no-git-checkout]]）；**严禁并发跑**
（见 [[feedback-mutation-gates-no-concurrency]]）。

用法：
    ./.venv/bin/python scripts/mutate_push_freq_gates.py --check
    ./.venv/bin/python scripts/mutate_push_freq_gates.py
    ./.venv/bin/python scripts/mutate_push_freq_gates.py --only P1,F2
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

SCHEMAS = REPO / "backend" / "models" / "schemas.py"
USER_SERVICE = REPO / "backend" / "services" / "user_service.py"
PROFILE_TS = "src/screens/ProfileScreen.tsx"
TYPES_TS = "src/types/index.ts"

BACKEND_GATE = "backend/tests/test_push_frequency_setting.py"
WIRING = "__tests__/pushFrequencyWiring.test.ts"
I18N = "__tests__/i18nLocales.test.ts"

FIELD = '    push_frequency: Literal["daily", "important", "off"] = "daily"'
PASS_LINE = (
    '            push_frequency=user.push_frequency if user.push_frequency '
    'in ("daily", "important", "off") else "daily",\n'
)
PATCH_LINE = "      const updated = await updateUserSettings({ push_frequency: frequency });\n"
ROW_READ = "{profile?.push_frequency || 'daily'}"

# (id, 破坏说明, 文件, 原文, 坏文, 测试命令类型, 受害测试, 失败用例名片段)
MUTATIONS = [
    (
        "P1",
        "get_profile 撤掉 push_frequency 传值（＝回到本批之前的读路径）",
        USER_SERVICE, PASS_LINE, "", "pytest", BACKEND_GATE, "PATCH 回了",
    ),
    (
        "P2",
        "UserProfileResponse 删掉该字段：响应里再没有这一档，前端类型与后端脱钩",
        SCHEMAS, FIELD + "\n", "", "pytest", BACKEND_GATE,
        "test_write_and_read_enums_cannot_drift",
    ),
    (
        "P3",
        "读侧退化成 Optional[Literal]：响应能给 null，界面只能猜默认值",
        SCHEMAS, FIELD,
        '    push_frequency: Optional[Literal["daily", "important", "off"]] = "daily"',
        "pytest", BACKEND_GATE, "读侧退化成了 Optional",
    ),
    (
        "P4",
        "读侧 Literal 少一档（去掉 off）：选了「不发」存得进但显示不出来",
        SCHEMAS, FIELD, '    push_frequency: Literal["daily", "important"] = "daily"',
        "pytest", BACKEND_GATE, "读侧档位集合变了",
    ),
    (
        "P5",
        "撤掉空串归一，直传列值：迁移前留空串的老行会被 Literal 校验打挂",
        USER_SERVICE, PASS_LINE, "            push_frequency=user.push_frequency,\n",
        "pytest", BACKEND_GATE, "test_empty_column_normalises_to_daily",
    ),
    (
        "F1",
        "handler 改成只改本地 state（不发 PATCH）：界面看起来存上了，重进就回默认档",
        FRONT / PROFILE_TS,
        PATCH_LINE,
        "      const updated = { ...profile, push_frequency: frequency } as UserProfile;\n",
        "jest", WIRING, "点档位走 PATCH",
    ),
    (
        "F2",
        "界面读侧硬编码成 daily：正是本批立项时的那个状态",
        FRONT / PROFILE_TS, f"t(`profile.pushFreqOption.${ROW_READ}`)",
        "t('profile.pushFreqOption.daily')",
        "jest", WIRING, "行与弹层都从 profile.push_frequency 取当前档",
    ),
    (
        "F3",
        "前端三档常量删成两档：off 这一档在界面上消失，后端照旧收 off",
        FRONT / PROFILE_TS,
        "const PUSH_FREQUENCIES: PushFrequency[] = ['daily', 'important', 'off'];",
        "const PUSH_FREQUENCIES: PushFrequency[] = ['daily', 'important'];",
        "jest", WIRING, "三档常量与后端同集同序",
    ),
    (
        "F4",
        "zh 的 important 档填回英文原文（i18n 英文兜底那条老根因）",
        LOCALES / "zh.ts", '      "important": "只发重要的",',
        '      "important": "Only what matters",',
        "jest", I18N, "没有任何一个值逐字等于英文",
    ),
    (
        "F5",
        "km 删掉 pushFreqHint.daily 一行：高棉语界面弹层里那档说明是空串",
        LOCALES / "km.ts",
        '      "daily": "ផ្ញើសារមួយរាល់ថ្ងៃ នៅម៉ោងដែលអ្នកជ្រើស",\n', "",
        "jest", WIRING, "km：8 个键都有值",
    ),
    (
        "F6",
        "types 的 UserProfile 撤掉 push_frequency：读路径在类型面上又消失了",
        FRONT / TYPES_TS,
        "  push_frequency?: PushFrequency | null;", "",
        "jest", WIRING, "UserProfile（读）与 UserSettings（写）都带 push_frequency",
    ),
]

# 破坏「不该打红」的东西＝证明这条闸不是假红制造机。
GREEN = [
    (
        "G1",
        "往 onSelectPushFrequency 函数体插 15 行注释：接线闸钉的是逐字锚点，"
        "函数体变长不该把它变红",
        FRONT / PROFILE_TS, PATCH_LINE,
        PATCH_LINE + "".join(f"      // 增长占位 {i}\n" for i in range(15)),
        "jest", WIRING,
    ),
    (
        "G2",
        "往 get_profile 的构造参数前插 15 行注释：行为闸判的是响应体，"
        "源码行数不该成为判据",
        USER_SERVICE, "            push_time=user.push_time,\n",
        "".join(f"            # 增长占位 {i}\n" for i in range(15))
        + "            push_time=user.push_time,\n",
        "pytest", BACKEND_GATE,
    ),
]


def jest(test_file: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["npx", "jest", "--silent", test_file],
        cwd=FRONT, capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def pytest(node: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(REPO / ".venv/bin/python"), "-m", "pytest", node, "-q", "--no-header"],
        cwd=REPO, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run(kind: str, target: str) -> tuple[int, str]:
    return jest(target) if kind == "jest" else pytest(target)


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
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}（共 {len(MUTATIONS) + len(GREEN)} 条）")
        return 1 if bad else 0

    tmp = Path(tempfile.mkdtemp(prefix="u3a-gate-backup-"))
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
        for cid, desc, path, old, new, kind, test, marker in MUTATIONS:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = run(kind, test)
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
                code, out = run(kind, test)
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

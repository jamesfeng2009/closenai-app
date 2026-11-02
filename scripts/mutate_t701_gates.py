#!/usr/bin/env python3
"""T7-01（签到备注 / 本机清理完整性）的变异自证（2026-10-25）。

被验对象与各自的靶：
- M1 历史合并把 intensity 写回默认 0.5 → 本地非默认强度被同 id upsert 抹掉；
- M2 历史合并把 note 写成 local.note ?? null → 本地备注被服务端「没有这列」抹掉；
- M3 SQLite 清理失败被忽略 → 界面会把「没清干净」当成已清空；
- M4 失败汇总不再抛错 → UI 没有可重试的失败信号；
- M5 clearAuth 首个键删失败就跳出 → 后续 SecureStore 键永远不尝试删除；
- M6 HomeScreen 历史同步退回整行覆盖（note: null / intensity: 0.5）；
- M7 ReflectScreen 空输入用 null 覆盖当天既有备注。

G1 是「不该打红」的证人：改注释不动行为时，同一条闸必须仍然绿。

纪律：串行执行、copy2 备份＋filecmp 逐字节写回、不用 git checkout
（[[feedback-mutation-restore-no-git-checkout]]、[[feedback-mutation-gates-no-concurrency]]）；
每条红闸先跑基线，基线不绿记「未跑」，不把环境的红算成闸的红
（[[feedback-mutation-anchor-must-reach-target]]）。

用法：
    python3 scripts/mutate_t701_gates.py --check
    python3 scripts/mutate_t701_gates.py
    python3 scripts/mutate_t701_gates.py --only M1,M3
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
MOOD_REPO = FRONT / "src" / "services" / "db" / "moodCheckinRepository.ts"
SESSION = FRONT / "src" / "services" / "session.ts"
API_TS = FRONT / "src" / "services" / "api.ts"
HOME = FRONT / "src" / "screens" / "HomeScreen.tsx"
REFLECT = FRONT / "src" / "screens" / "ReflectScreen.tsx"

MOOD_TEST = "__tests__/moodCheckinRepository.test.ts"
SESSION_TEST = "__tests__/sessionReset.test.ts"
CLEAR_AUTH_TEST = "__tests__/clearAuthCleanup.test.ts"
WIRING_TEST = "__tests__/moodNoteWiring.test.ts"

MUTATIONS: list[tuple] = [
    (
        "M1",
        "历史合并把 intensity 写回默认 0.5：本地非默认强度丢失",
        [(MOOD_REPO,
          "      intensity: existing?.intensity ?? 0.5,",
          "      intensity: 0.5,")],
        "jest", MOOD_TEST, "同 id 合并不抹掉本地 note 与非默认 intensity",
    ),
    (
        "M2",
        "历史合并把 note 写成 local.note ?? null：本地备注被抹掉",
        [(MOOD_REPO,
          "      note: local.note !== undefined ? local.note : existing?.note ?? null,",
          "      note: local.note ?? null,")],
        "jest", MOOD_TEST, "同 id 合并不抹掉本地 note 与非默认 intensity",
    ),
    (
        "M3",
        "SQLite 清理失败被忽略：没清干净被当成已清空",
        [(SESSION,
          "  if (!clearUserLocalData()) failures.push('sqlite');",
          "  clearUserLocalData();")],
        "jest", SESSION_TEST, "SQLite 清理失败：抛 sqlite 失败、数据保持原样；重试成功后清空",
    ),
    (
        "M4",
        "失败汇总不再抛错：UI 拿不到可重试的失败信号",
        [(SESSION,
          "  notifySessionReset();\n"
          "  if (failures.length > 0) throw new LocalCleanupError(failures);\n"
          "}\n\n"
          "/** 注销账号：",
          "  notifySessionReset();\n"
          "}\n\n"
          "/** 注销账号：")],
        "jest", SESSION_TEST, "SecureStore 没删干净：抛 LocalCleanupError(secure_store)，仍然通知回登录页",
    ),
    (
        "M5",
        "clearAuth 首个键删失败就跳出：后面的键永远不尝试删除",
        [(API_TS,
          "    } catch {\n      failed.push(label);\n    }",
          "    } catch {\n      failed.push(label);\n      break;\n    }")],
        "jest", CLEAR_AUTH_TEST, "中间一个键删失败仍继续删其余键，并报出失败标签",
    ),
    (
        "M6",
        "HomeScreen 历史同步退回整行覆盖（note: null / intensity: 0.5）",
        [(HOME,
          "    await moodCheckinRepo.mergeRemoteHistory(checkins);",
          "    for (const c of checkins) await moodCheckinRepo.save({ id: c.id, "
          "date: c.created_at.slice(0, 10), emoji: c.mood_emoji, intensity: 0.5, "
          "note: null, ai_interpretation: c.ai_interpretation || null, "
          "created_at: c.created_at });")],
        "jest", WIRING_TEST, "HomeScreen 历史同步走合并，不整行覆盖本地 note / intensity（T7-01）",
    ),
    (
        "M7",
        "ReflectScreen 空输入用 null 覆盖当天既有备注",
        [(REFLECT,
          "          ...(note === undefined ? {} : { note }),",
          "          note: note ?? null,")],
        "jest", WIRING_TEST, "本地行写用户刚打的那句；没输入时不拿 null 覆盖当天既有备注",
    ),
]

GREEN: list[tuple] = [
    (
        "G1",
        "只改注释不动行为：T7-01 的闸必须仍然绿",
        [(SESSION,
          "  // 即使本机没清干净也要回到登录页",
          "  // 本机没清干净时也要回到登录页")],
        "jest", SESSION_TEST, "",
    ),
]


def _apply(edits: list) -> None:
    for path, old, new in edits:
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:90]!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _verify_anchor(edits: list) -> str | None:
    for path, old, _new in edits:
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"命中 {n} 次（要 1）"
    return None


def run_jest(test_file: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["npx", "jest", "--silent", test_file],
        cwd=FRONT, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode, proc.stdout + proc.stderr


def is_red(code: int, out: str, marker: str) -> bool:
    if code == 0 or not marker:
        return False
    if not re.search(r"\d+ (failed|error)", out):
        return False
    return marker in out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    cases = [*MUTATIONS, *GREEN]
    if args.check:
        bad = 0
        for case in cases:
            problem = _verify_anchor(case[2])
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}（共 {len(cases)} 条）")
        return 1 if bad else 0

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-t701-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            bak = Path(tmp) / f"{idx:02d}-{path.name}"
            shutil.copy2(path, bak)
            originals[path] = bak

        def restore() -> None:
            for path, bak in originals.items():
                shutil.copy2(bak, path)
                assert filecmp.cmp(bak, path, shallow=False), f"{path} 恢复后字节不一致"

        red_ok = red_fail = green_ok = green_fail = 0
        failures: list[str] = []
        try:
            for cid, desc, edits, kind, target, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = run_jest(target)
                if base_code != 0:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）：不能把环境的红记成闸的红\n  "
                          + "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                _apply(edits)
                try:
                    code, out = run_jest(target)
                finally:
                    restore()
                if is_red(code, out, marker):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓（jest）  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（jest exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))

            for cid, desc, edits, kind, target, _marker in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(edits)
                try:
                    code, out = run_jest(target)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓（jest）  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（jest exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))
        except Exception as exc:  # noqa: BLE001
            restore()
            print(f"异常中止，已恢复：{exc!r}")
            return 2

        code, out = run_jest(MOOD_TEST)
        residue = 0 if code == 0 else 1
        print(("复核 签到合并闸：绿" if code == 0
               else "残留（红！）：\n  " + "\n  ".join(out.splitlines()[-15:])))
        print(f"\nT7-01 变异自证：打红 {red_ok}/{len(MUTATIONS)}，保持绿 {green_ok}/{len(GREEN)}，"
              f"失败 {red_fail + green_fail}，未跑 0，残留 {residue}")
        if failures:
            print(f"失败清单：{failures}")
        return 1 if (red_fail or green_fail or residue) else 0


if __name__ == "__main__":
    sys.exit(main())

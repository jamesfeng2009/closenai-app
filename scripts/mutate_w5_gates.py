#!/usr/bin/env python3
"""W5（T5-01 consents / T5-02 免打扰 / T5-03 幂等 / T5-05 投递档）的变异自证（2026-10-18）。

这批的正题是「**门到底在不在它该在的位置**」，所以选靶全部对着位置而不是对着存在性：

- B1 撤掉 start==end 的退化窗口早退 → 22:00–22:00 被读成全天静默，主动消息永久消失；
- B2/B4 撤掉 cron 与 push 两层静默门 → 用户设了免打扰照样被叫醒；
- B3 撤掉频率档 off 的生成门 → 回到「off 只拦推送、消息每天照样落库还吃配额」；
- B5/B6 把两条部分唯一索引的 partial WHERE 摘掉 → 全表唯一，对话回复直接写不进去；
- B7 撤掉 purpose 白名单 → 任何拼错的用途都能写进合规台账；
- B8 撤掉台账→兼容布尔位的同步 → 双写期两份真源各说一套；
- B9 让回填脚本按 UTC 日出键 → 回填键与 cron 运行键不是同一个键，幂等形同虚设。

F1/F2/F3 打的是前端面：只改本地 state（装作存上了）、类型面读路径消失、15 语言缺一键。

恢复一律 **copy2 + filecmp 逐字节校验**，不用 git checkout
（见 [[feedback-mutation-restore-no-git-checkout]]）；**严禁并发跑**
（见 [[feedback-mutation-gates-no-concurrency]]）。

用法：
    ./.venv/bin/python scripts/mutate_w5_gates.py --check
    ./.venv/bin/python scripts/mutate_w5_gates.py
    ./.venv/bin/python scripts/mutate_w5_gates.py --only B1,F2
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

TIME_UTILS = REPO / "backend" / "core" / "time_utils.py"
CRON = REPO / "backend" / "services" / "cron_service.py"
PUSH = REPO / "backend" / "services" / "push" / "service.py"
CHAT_MODEL = REPO / "backend" / "models" / "chat.py"
JOURNEY_MODEL = REPO / "backend" / "models" / "journey.py"
CONSENT = REPO / "backend" / "services" / "consent_service.py"
SCHEMAS = REPO / "backend" / "models" / "schemas.py"
BACKFILL = REPO / "scripts" / "backfill_idempotency.py"
PROFILE_TS = "src/screens/ProfileScreen.tsx"
TYPES_TS = "src/types/index.ts"

QUIET_GATE = "backend/tests/test_quiet_hours.py"
OFF_GATE = "backend/tests/test_delivery_off_e2e.py"
IDEM_GATE = "backend/tests/test_idempotency_indexes.py"
CONSENT_GATE = "backend/tests/test_consent_purposes.py"
WIRING = "__tests__/quietHoursWiring.test.ts"

QUIET_CRON_GATE = (
    '        if user_in_quiet_hours(user, local_now(user.timezone)):\n'
    '            logger.info("免打扰时段命中，本轮不生成不投递 user=%s", uid)\n'
    '            return "skipped_quiet"\n'
)
OFF_CRON_GATE = (
    '        if user.push_frequency == "off":\n'
    '            logger.info("频率档 off，本轮不生成不投递 user=%s", uid)\n'
    '            return "skipped_off"\n'
)
PUSH_GATE = (
    '        if user is not None and user_in_quiet_hours(user):\n'
    '            logger.info("免打扰时段命中，跳过设备推送 user=%s type=%s",'
    ' user_id, (data or {}).get("type"))\n'
    '            return {"sent": 0, "failed": 0}\n'
)
TOGGLE_CALL = (
    "      const updated = await updateUserSettings(\n"
    "        next\n"
    "          ? { quiet_hours_enabled: true, ...quietWindow() }\n"
    "          : { quiet_hours_enabled: false }\n"
    "      );\n"
)
MOMENT_CALL = (
    "      const updated = await updateUserSettings(\n"
    "        quietWindow(which === 'start' ? { start: time } : { end: time })\n"
    "      );\n"
)

MUTATIONS = [
    (
        "B1",
        "in_quiet_hours 撤掉 start==end 早退：退化窗口被跨零点分支读成全天静默",
        TIME_UTILS,
        "    if start is None or end is None or start == end:\n",
        "    if start is None or end is None:\n",
        "pytest", QUIET_GATE, "test_same_start_and_end_is_not_silently_all_day",
    ),
    (
        "B2",
        "撤掉 cron 的免打扰门（T5-02）：窗口内照样生成、照样落库、照样吃当日配额",
        CRON, QUIET_CRON_GATE, "", "pytest", QUIET_GATE,
        "test_cron_skips_inside_quiet_window_and_keeps_quota",
    ),
    (
        "B3",
        "撤掉频率档 off 的生成门（T5-05）：回到「off 只拦推送」，消息每天照样落库",
        CRON, OFF_CRON_GATE, "", "pytest", OFF_GATE,
        "test_frequency_off_delivers_nothing",
    ),
    (
        "B4",
        "撤掉 send_to_user 的免打扰门：剧本/里程碑等九处投递点凌晨照样叫醒用户",
        PUSH, PUSH_GATE, "", "pytest", QUIET_GATE,
        "test_push_gate_blocks_devices_inside_window",
    ),
    (
        "B5",
        "chat 幂等索引摘掉 partial WHERE：从「只管 proactive」变成全表唯一",
        CHAT_MODEL,
        "            postgresql_where=_PROACTIVE_KEY_PREDICATE,\n"
        "            sqlite_where=_PROACTIVE_KEY_PREDICATE,\n",
        "", "pytest", IDEM_GATE, "test_chat_index_ddl_matches_model",
    ),
    (
        "B6",
        "journey 日序索引摘掉 partial WHERE：承诺回访同日多条被误判成重复",
        JOURNEY_MODEL,
        "            postgresql_where=_USER_KIND_DAY_PREDICATE,\n"
        "            sqlite_where=_USER_KIND_DAY_PREDICATE,\n",
        "", "pytest", IDEM_GATE, "test_journey_index_ddl_matches_model",
    ),
    (
        "B7",
        "consent 白名单整道校验撤掉（未知用途照写）：拼错的 purpose 也能进合规台账",
        CONSENT,
        "        if purpose not in CONSENT_PURPOSES:",
        "        if False:",
        "pytest", CONSENT_GATE, "test_unknown_purpose_is_rejected",
    ),
    (
        "B8",
        "撤掉台账→兼容布尔位的同步：兼容期两份真源各说一套（T5-01 双写口径）",
        CONSENT,
        "        await self._sync_linked_flag(db, user_id, purpose, granted)",
        "        pass",
        "pytest", CONSENT_GATE, "test_ledger_write_syncs_ai_flag",
    ),
    (
        "B9",
        "回填脚本按原始（UTC）日出键，不按用户本地日：回填键与 cron 键不是同一个",
        BACKFILL,
        "    if created_at.tzinfo is None:\n"
        "        created_at = created_at.replace(tzinfo=timezone.utc)\n"
        "    return created_at.astimezone(safe_zoneinfo(tz_name)).date()\n",
        "    return created_at.date()\n",
        "pytest", IDEM_GATE, "test_key_uses_user_local_day_not_utc_day",
    ),
    (
        "B10",
        "UserProfileResponse 撤掉 quiet_start/quiet_end 声明：读路径又在响应面上消失一次",
        SCHEMAS,
        "    quiet_start: Optional[str] = None\n"
        "    quiet_end: Optional[str] = None\n\n\n"
        "class AIConsentUpdate(BaseModel):",
        "class AIConsentUpdate(BaseModel):",
        "jest", WIRING, "后端读侧 UserProfileResponse 回显 quiet_start/quiet_end",
    ),
    (
        "F1",
        "开关 handler 改成只改本地 state（不发 PATCH）：看起来存上了，重进就回默认",
        FRONT / PROFILE_TS, TOGGLE_CALL,
        "      const updated = { ...profile, quiet_start: '22:00', "
        "quiet_end: '08:00' } as UserProfile;\n",
        "jest", WIRING, "开启时 enabled 与起止一起发",
    ),
    (
        "F2",
        "types 的 UserProfile 撤掉 quiet_start：读路径在类型面上又消失了",
        FRONT / TYPES_TS, "  quiet_start?: string | null;\n", "",
        "jest", WIRING, "前端 types 读写两侧都带这三个字段",
    ),
    (
        "F3",
        "km 删掉 profile.quietHours 一行：高棉语界面那行开关标签是空串",
        FRONT / "src" / "i18n" / "locales" / "km.ts",
        '    "quietHours": "ម៉ោងមិនរំខាន",\n', "",
        "jest", WIRING, "km：8 个 quiet* 键都在",
    ),
]

# 破坏「不该打红」的东西＝证明这几条闸不是假红制造机（判据钉行为与名单，不钉行数）。
GREEN = [
    (
        "G1",
        "往 cron 免打扰门前面插 15 行注释：门在配额与写库之前这件事与源码行数无关",
        CRON, QUIET_CRON_GATE,
        "".join(f"        # 增长占位 {i}\n" for i in range(15)) + QUIET_CRON_GATE,
        "pytest", QUIET_GATE,
    ),
    (
        "G2",
        "往 onSelectQuietMoment 的 PATCH 前插 15 行注释：接线闸判的是载荷形状，不是行号",
        FRONT / PROFILE_TS, MOMENT_CALL,
        "".join(f"      // 增长占位 {i}\n" for i in range(15)) + MOMENT_CALL,
        "jest", WIRING,
    ),
]


def jest(test_file: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["npx", "jest", "--silent", test_file],
        cwd=FRONT, capture_output=True, text=True, timeout=900,
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
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}"
              f"（共 {len(MUTATIONS) + len(GREEN)} 条）")
        return 1 if bad else 0

    tmp = Path(tempfile.mkdtemp(prefix="w5-gate-backup-"))
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

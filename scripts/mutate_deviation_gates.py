#!/usr/bin/env python3
"""偏离面闸门的变异自证（2026-10-14，tasks.md 剩余 ⚠️ 偏离收口批的 DoD ②）。

守的是四处「实现与原文档不一致」的落点，每处都只在**代码存在**、没有可复判面：
- M2 偏离 1：D2 剧本走模板直出，零 LLM（三层回退：昨日情绪 → 昨日原话 → 无断言兜底）。
- M5 偏离 1/5/6：纯数据住 `src/config/`、不落打包 JSON、屏内不另造第五类内容。
- B4 偏离 2：记忆卡标题按本地日界判周年，正文永不提周年。
- B4 偏离 3：归因只有深链一条路（无剪贴板、无手工填码），统计口径是下界。

每条都要能被一次真实破坏打红才算数：写坏 → 只跑受害的那份测试 → 要求输出里出现那条
失败用例的名字 → 备份写回。**恢复用 copy2 + filecmp，不用 git checkout**
（见 [[feedback-mutation-restore-no-git-checkout]]）。创建型破坏（新文件）按 unlink 复原。

用法：
    python3 scripts/mutate_deviation_gates.py --check   # 只验锚点，各命中一次
    python3 scripts/mutate_deviation_gates.py           # 全部红 + 绿
    python3 scripts/mutate_deviation_gates.py --only D1,F4
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
FRONT = REPO / "frontend"
SRC = FRONT / "src"
SHARE = REPO / "backend" / "services" / "share_service.py"
JOURNEY = REPO / "backend" / "services" / "journey_service.py"
SCREEN = SRC / "screens" / "DemoArchiveScreen.tsx"
PERSONA = SRC / "config" / "demoPersona.ts"
APP_TSX = FRONT / "App.tsx"
API_TS = SRC / "services" / "api.ts"
REFERRAL_TS = SRC / "services" / "referral.ts"

PY_REFERRAL = "backend/tests/test_referral.py"
PY_JOURNEY = "backend/tests/test_journey.py"
TS_DEMO = "__tests__/demoPersona.test.ts"
TS_REFERRAL = "__tests__/referral.test.ts"

# ------------------------------------------------------------------ 红闸：改写型
# (id, 破坏说明, 文件, 原文, 坏文, 受害测试, 失败用例名片段)
MUTATIONS: list[tuple[str, str, Path, str, str, str, str]] = [
    (
        "D1",
        "周年判定放宽成年差 ≥0：今年写的记忆也被印成「0 年前的今天」",
        SHARE,
        "        years_ago = today_local.year - created_local.year\n        if (\n            years_ago >= 1\n",
        "        years_ago = today_local.year - created_local.year\n        if (\n            years_ago >= 0\n",
        PY_REFERRAL,
        "never_fabricates_an_anniversary",
    ),
    (
        "D2",
        "标题不按用户本地日界判周年（时区铁律失守：上海用户差一天就丢周年）",
        SHARE,
        "        created_local = created.astimezone(zone)\n",
        "        created_local = created.astimezone(timezone.utc)\n",
        PY_REFERRAL,
        "uses_the_user_local_day_boundary",
    ),
    (
        "D3",
        "多年周年的 {n} 不做 format：卡上直出「{n} 年前的今天」",
        SHARE,
        '            return copy.t("share", "memory.anniversary_n", language).format(n=years_ago)\n',
        '            return copy.t("share", "memory.anniversary_n", language)\n',
        PY_REFERRAL,
        "multi_year_anniversary_formats_the_count",
    ),
    (
        "D4",
        "删掉 created_at 缺失的兜底：没有时间的记忆当场抛异常",
        SHARE,
        '        if created is None:\n            return copy.t("share", "memory.title", language)\n',
        "",
        PY_REFERRAL,
        "without_created_at_falls_back_to_template",
    ),
    (
        "D5",
        "把周年词写进正文（正文的口径正是「不写一年前的今天」）",
        SHARE,
        '            copy.t("share", "memory.intro", language),\n',
        '            copy.t("share", "memory.anniversary_one", language),\n',
        PY_REFERRAL,
        "body_never_mentions_the_anniversary",
    ),
    (
        "D6",
        "把 LLM 生成器接回 D2 渲染路径（M2 偏离 1 的反面）",
        JOURNEY,
        '        tpl = self._t(user.language)["day2_first_outreach"]\n',
        "        from backend.services.message_generator import MessageGenerator\n"
        "        tpl = await MessageGenerator().generate_proactive_message()\n",
        PY_JOURNEY,
        "test_day2_is_template_direct_and_never_touches_the_llm",
    ),
    (
        "D7",
        "D2 三层回退退成两层：昨日情绪那条分支哑掉，直接掉到原话",
        JOURNEY,
        "        if yesterday_emoji:\n",
        "        if False and yesterday_emoji:\n",
        PY_JOURNEY,
        "prefers_yesterday_mood_over_quote",
    ),
    (
        "D8",
        "D2 无任何信号时造假断言（兜底问句被换成「昨天你写下过…」）",
        JOURNEY,
        '            key, ctx = "plain", {}\n',
        '            key, ctx = "with_quote", {"q0": "编造的原话"}\n',
        PY_JOURNEY,
        "falls_back_to_plain_question_without_any_signal",
    ),
    (
        "F3",
        "数据层开始指向打包 JSON 资产（偏离 5 的回归第一步）",
        PERSONA,
        "export const DEMO_WINDOW_DAYS = 30;\n",
        "const DEMO_PROFILE_ASSET = './assets/demo_profile.json';\n"
        "export const DEMO_PROFILE_SOURCE = DEMO_PROFILE_ASSET;\n\n"
        "export const DEMO_WINDOW_DAYS = 30;\n",
        TS_DEMO,
        "没有打包 JSON 资产",
    ),
    (
        "F4",
        "往屏里另塞第五类卡片（偏离 6 的口径是不另造 AI 断言示例）",
        SCREEN,
        "          <Text style={styles.cardTitle}>💬 {t('demo.memoryTitle')}</Text>\n",
        "          <Text style={styles.cardTitle}>🤖 {t('demo.badge')}</Text>\n"
        "          <Text style={styles.cardTitle}>💬 {t('demo.memoryTitle')}</Text>\n",
        TS_DEMO,
        "屏内只有概览 / Pattern / 时间线+原话四类卡片",
    ),
    (
        "F5",
        "Pattern 卡去掉「依据」行（断言卡的形态就是断言 + 依据）",
        SCREEN,
        "              <Text style={styles.patternBasis}>{basis(p)}</Text>\n",
        "",
        TS_DEMO,
        "每条 Pattern 都渲染 demo.patternBasis 依据行",
    ),
    (
        "F6",
        "删掉「统计口径是下界」这句口径（B4 偏离 3 的结论）",
        REFERRAL_TS,
        " * 结论：referral 统计口径是**下界**，不能当分享总数看。\n",
        "",
        TS_REFERRAL,
        "下界口径的注释在位",
    ),
    (
        "F7",
        "建号取码多开一条路：从唤起 URL 直接取（绕过品牌域白名单）",
        API_TS,
        "  const code = await peekPendingReferral();\n",
        "  const code = String((globalThis as any).Linking?.getInitialURL?.() ?? '')\n"
        "    || await peekPendingReferral();\n",
        TS_REFERRAL,
        "注册页没有手工填码入口",
    ),
    (
        "F8",
        "唤起时顺手写剪贴板（v1 明确不做剪贴板 handoff，且是审核高风险项）",
        APP_TSX,
        "      if (code) savePendingReferral(code).catch(() => {});\n",
        "      if (code) Clipboard.setString(code);\n"
        "      if (code) savePendingReferral(code).catch(() => {});\n",
        TS_REFERRAL,
        "没有剪贴板：依赖不装、源码不读不写",
    ),
]

# ------------------------------------------------------------------ 红闸：新建文件型
# 偏离 1 与偏离 5 守的是「不存在」，所以破坏方式就是把它造出来。
CREATES: list[tuple[str, str, Path, str, str]] = [
    (
        "F1",
        "把纯数据搬回 §二 M5 原文的 src/data/demoArchive.ts（偏离 1 的文件位）",
        SRC / "data" / "demoArchive.ts",
        "export const DEMO_WINDOW_DAYS = 30;\n",
        "数据层住 src/config/demoPersona.ts",
    ),
    (
        "F2",
        "落地打包件 assets/demo_profile.json（偏离 5：数字必须与曲线同源现算）",
        FRONT / "assets" / "demo_profile.json",
        '{"days": []}\n',
        "没有打包 JSON 资产",
    ),
]

# ------------------------------------------------------------------ 绿闸
# 破坏「不该让守卫变红」的东西＝证明守卫不是固定行窗假红制造机。
GREEN: list[tuple[str, str, Path, str, str, str]] = [
    (
        "G1",
        "往原话卡上方插 12 行 JSX 注释：三段结构与依据行两条闸都不该红",
        SCREEN,
        "          <Text style={styles.cardTitle}>💬 {t('demo.memoryTitle')}</Text>\n",
        "".join("          {/* 增长占位 %d */}\n" % i for i in range(12))
        + "          <Text style={styles.cardTitle}>💬 {t('demo.memoryTitle')}</Text>\n",
        TS_DEMO,
    ),
    (
        "G2",
        "往 _memory_card_lines 里插 12 行注释：标题/正文两侧周年闸都不该红",
        SHARE,
        '        date_label = memory.created_at.strftime("%Y-%m-%d") if memory.created_at else ""\n',
        "".join("        # 增长占位 %d\n" % i for i in range(12))
        + '        date_label = memory.created_at.strftime("%Y-%m-%d") if memory.created_at else ""\n',
        PY_REFERRAL,
    ),
]


def run_test(target: str) -> tuple[int, str]:
    if target.endswith(".py"):
        cmd = [str(REPO / ".venv" / "bin" / "pytest"), target, "-q", "--no-header"]
        proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    else:
        proc = subprocess.run(
            ["npx", "jest", "--silent", target], cwd=FRONT, capture_output=True, text=True
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
        for cid, _, path, _, _ in CREATES:
            if path.exists():
                print(f"坏前置 {cid}：{path} 已存在（这条闸现在就是红的）")
                bad += 1
        print(f"锚点校验：{'全部命中一次，且新建型目标均不存在' if bad == 0 else f'{bad} 条有问题'}")
        return 1 if bad else 0

    def pick(items):
        return [c for c in items if not wanted or c[0].upper() in wanted]

    tmp = Path(tempfile.mkdtemp(prefix="mut-dev-"))
    originals: dict[Path, Path] = {}
    created: list[Path] = []
    created_dirs: list[Path] = []

    for case in [*pick(MUTATIONS), *pick(GREEN), *pick(CREATES)]:
        p = case[2]
        if p not in originals and p.exists():
            bak = tmp / f"{len(originals)}-{p.name}"
            shutil.copy2(p, bak)
            originals[p] = bak

    def restore() -> None:
        for path in created:
            if path.exists():
                path.unlink()
        for path in created_dirs:
            if path.exists() and not any(path.iterdir()):
                path.rmdir()
        created.clear()
        created_dirs.clear()
        for p, bak in originals.items():
            shutil.copy2(bak, p)
            assert filecmp.cmp(bak, p, shallow=False), f"{p} 恢复后字节不一致"

    red_ok = red_fail = green_ok = green_fail = 0
    try:
        for cid, desc, path, old, new, test, marker in pick(MUTATIONS):
            try:
                patch(path, old, new)
                code, out = run_test(test)
                hit = (marker in out) and (" failed" in out or "Failed" in out)
                if code != 0 and hit:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    print(f"[{cid}] 打红 ✗（exit={code}，marker={bool(hit)}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-8:]))
            finally:
                restore()

        for cid, desc, path, content, marker in pick(CREATES):
            try:
                for parent in [path.parent, path.parent.parent]:
                    if not parent.exists():
                        parent.mkdir(parents=True, exist_ok=True)
                        created_dirs.append(parent)
                path.write_text(content, encoding="utf-8")
                created.append(path)
                code, out = run_test(TS_DEMO)
                hit = (marker in out) and (" failed" in out or "Failed" in out)
                if code != 0 and hit:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    print(f"[{cid}] 打红 ✗（exit={code}，marker={bool(hit)}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-8:]))
            finally:
                restore()

        for cid, desc, path, old, new, test in pick(GREEN):
            try:
                patch(path, old, new)
                code, out = run_test(test)
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
    print("源文件已全部按备份写回（新建件按 unlink）并逐字节校验一致。")
    return 0 if red_fail == 0 and green_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

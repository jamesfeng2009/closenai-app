#!/usr/bin/env python3
"""回忆卡（§10.1 ① 第四模板 · 情绪色环）闸门的变异自证（2026-10-14，C4 色环批的 DoD ②）。

守的是这一批里**只会静默画错或静默失效**的落点：
- 换算层（memoryRing.test.ts）：五段几何、值→色相方向、只亮一段、无签到中性降级、
  扇形路径闭合、日期大字只从服务端「今天」倒推、摘录截断、分享正文两段。
- 接线层（memoryCardWiring.test.ts）：memory 走 on-this-day 不撞 Pro 门、入口按钮与
  导航参数、色环组件不吃触摸、清单与实际分享同源、深链只在分享时取、防连点、
  失败走屏内 error 不开新 Alert 站点、跨仓键集对齐。
- 后端（test_on_this_day.py）：当日首个签到、无签到返回 None、表外 emoji 折中 3、
  签到与记忆共用同一条本地日界窗口。

每条都要能被一次真实破坏打红才算数：写坏 → 只跑受害的那份测试 → 要求输出里出现那条
失败用例的名字 → 备份写回。**恢复用 copy2 + filecmp，不用 git checkout**
（见 [[feedback-mutation-restore-no-git-checkout]]）。

G1/G2 是反向绿闸：往被切片守卫的窗口里插注释（正是 317b390 造成假红的那种增长），
守卫不该因此变红——证明这些结构断言不是脆的。

用法：
    python3 scripts/mutate_memory_card_gates.py --check   # 只验锚点，各命中一次
    python3 scripts/mutate_memory_card_gates.py           # 全部红 + 2 条绿
    python3 scripts/mutate_memory_card_gates.py --only R3,B1
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
CFG = SRC / "config" / "memoryRing.ts"
RING = SRC / "components" / "charts" / "MoodRing.tsx"
SHARE_SCREEN = SRC / "screens" / "ShareCardScreen.tsx"
HOME_SCREEN = SRC / "screens" / "HomeScreen.tsx"
APP_TSX = FRONT / "App.tsx"
TYPES = SRC / "types" / "index.ts"
BACKEND = REPO / "backend" / "services" / "share_service.py"

TS_RING = "__tests__/memoryRing.test.ts"
TS_WIRING = "__tests__/memoryCardWiring.test.ts"
PY_ON_THIS_DAY = "backend/tests/test_on_this_day.py"

# ------------------------------------------------------------------ 红闸：换算层
# (id, 破坏说明, 文件, 原文, 坏文, 受害测试, 失败用例名片段)
MUTATIONS: list[tuple[str, str, Path, str, str, str, str]] = [
    (
        "R1",
        "色相步长取负：值越高越红（情绪与颜色反过来，画面上不会报错）",
        CFG,
        "export const HUE_STEP = 30;",
        "export const HUE_STEP = -30;",
        TS_RING,
        "色相随情绪值单调不减",
    ),
    (
        "R2",
        "分段数从 5 改 4：值 5（开心）没有落脚段，满环也不再是 360°",
        CFG,
        "export const RING_SEGMENTS = 5;",
        "export const RING_SEGMENTS = 4;",
        TS_RING,
        "整环等分",
    ),
    (
        "R3",
        "起始角从 −90° 改 0°：第一段跑到 3 点方向（与折线/热力图不同向）",
        CFG,
        "export const START_ANGLE = -90;",
        "export const START_ANGLE = 0;",
        TS_RING,
        "每段的角度是首尾相接",
    ),
    (
        "R4",
        "非高亮段也拉满不透明（五段全亮＝看不出那天心情如何）",
        CFG,
        "      opacity: hue === null ? DIM_OPACITY : active ? 1 : DIM_OPACITY,",
        "      opacity: 1,",
        TS_RING,
        "其余四段降透明",
    ),
    (
        "R5",
        "active 判定放宽成恒真：中心 emoji 与高亮段同时点亮五段",
        CFG,
        "    const active = hue !== null && segValue === value;",
        "    const active = hue !== null;",
        TS_RING,
        "其余四段降透明",
    ),
    (
        "R6",
        "无签到也按段上色（空环被画成有情绪＝拿别处的观感冒充当天的情绪）",
        CFG,
        "      color: hue === null ? NEUTRAL_COLOR : colorForValue(segValue),",
        "      color: colorForValue(segValue),",
        TS_RING,
        "五段全中性",
    ),
    (
        "R7",
        "内弧方向写成顺时针：扇形自交，环上出现缺口或重影",
        CFG,
        "    `A ${rInner} ${rInner} 0 ${large} 0 ${iStart.x.toFixed(3)} ${iStart.y.toFixed(3)}`,",
        "    `A ${rInner} ${rInner} 0 ${large} 1 ${iStart.x.toFixed(3)} ${iStart.y.toFixed(3)}`,",
        TS_RING,
        "内弧逆时针回起点",
    ),
    (
        "R8",
        "去掉退化角保护：0° 段也产路径，环上多一条看不见的占位路径",
        CFG,
        "  const sweep = endAngle - startAngle;\n  if (sweep <= 0) return '';",
        "  const sweep = endAngle - startAngle;",
        TS_RING,
        "退化角",
    ),
    (
        "R9",
        "日期大字把年份差加成未来年份（「一年前」显成明年）",
        CFG,
        "  const year = Number(match[1]) - yearsAgo;",
        "  const year = Number(match[1]) + yearsAgo;",
        TS_RING,
        "今天 2026-11-01",
    ),
    (
        "R10",
        "去掉 years_ago 的合法性校验：0/负数/小数也照样出一个像样的日期",
        CFG,
        "  if (!Number.isInteger(yearsAgo) || yearsAgo < 1) return null;",
        "  // 变异：不再校验年份差",
        TS_RING,
        "非法输入一律 null",
    ),
    (
        "R11",
        "摘录不再截断：三千字原话整段灌进卡片",
        CFG,
        "  if (text.length <= MEMORY_CARD_EXCERPT_MAX) return text;\n"
        "  return `${text.slice(0, MEMORY_CARD_EXCERPT_MAX).trimEnd()}…`;",
        "  return text;",
        TS_RING,
        "超长截到上限",
    ),
    (
        "R12",
        "分享正文去掉空段过滤：原话为空时发出去的是空行 + 链接",
        CFG,
        "  return [content, link].map((s) => (s ?? '').trim()).filter(Boolean).join('\\n\\n');",
        "  return [content, link].map((s) => (s ?? '').trim()).join('\\n\\n');",
        TS_RING,
        "空字段不留空行",
    ),
]

# ------------------------------------------------------------------ 红闸：接线层
MUTATIONS += [
    (
        "W1",
        "类型表去掉 'memory'：第四模板只在运行时存在，编译面不认识它",
        TYPES,
        "export type ShareCardVariant = 'classic' | 'minimal' | 'combo' | 'memory';",
        "export type ShareCardVariant = 'classic' | 'minimal' | 'combo';",
        TS_WIRING,
        "ShareCardVariant 含 'memory'",
    ),
    (
        "W2",
        "导航参数表退回无参：首页带 memory_id 跳进来的一跳被丢掉",
        APP_TSX,
        "  ShareCard: { variant?: ShareCardVariant; memory_id?: string } | undefined;",
        "  ShareCard: undefined;",
        TS_WIRING,
        "导航参数表带 variant 与 memory_id",
    ),
    (
        "W3",
        "首页入口按钮不再传 memory_id（点哪条都出同一张卡）",
        HOME_SCREEN,
        "navigation.navigate('ShareCard', { variant: 'memory', memory_id: c.memory_id });",
        "navigation.navigate('ShareCard');",
        TS_WIRING,
        "首页考古卡→回忆卡入口",
    ),
    (
        "W4",
        "memory 分支改走 getPersonalityCard：免费引流卡撞上 40302 Pro 门",
        SHARE_SCREEN,
        "          const data = await getOnThisDay();",
        "          const data = await getPersonalityCard(variant);",
        TS_WIRING,
        "memory 分支调 getOnThisDay()",
    ),
    (
        "W5",
        "日期大字改吃 created_at（UTC 落库时刻按设备时区格式化＝差一天）",
        SHARE_SCREEN,
        "{memoryCardDate(memoryToday, memory.years_ago) ?? '—'}",
        "{memoryCardDate(memory.created_at ?? '', memory.years_ago) ?? '—'}",
        TS_WIRING,
        "不用 created_at",
    ),
    (
        "W6",
        "「今天」换成设备时钟（跨时区用户与服务端窗口不同日，周年判定漂移）",
        SHARE_SCREEN,
        "            setMemoryToday(data.date);",
        "            setMemoryToday(new Date().toISOString());",
        TS_WIRING,
        "不用 created_at",
    ),
    (
        "W7",
        "清单虚报第三项（多列的那一项并不会被分享出去）",
        SHARE_SCREEN,
        "<ShareManifest items={[t('home.archiveManifestQuote'), t('share.manifestLink')]} />",
        "<ShareManifest items={[t('home.archiveManifestQuote'), t('share.manifestLink'), t('share.memoryMoodNone')]} />",
        TS_WIRING,
        "清单两项＝原话 + 深链",
    ),
    (
        "W8",
        "进屏就预取深链：后端首次调用即惰性生成推荐码，白烧一次",
        SHARE_SCREEN,
        "          if (!stale) {\n            setMemory(picked);",
        "          void getShareLink();\n          if (!stale) {\n            setMemory(picked);",
        TS_WIRING,
        "深链只在真要分享时取",
    ),
    (
        "W9",
        "去掉防连点守卫（并发两次取链＝两个推荐码请求，分享按钮还亮着）",
        SHARE_SCREEN,
        "      if (!memory || sharing) return; // 防连点：并发取链会白烧一个推荐码\n",
        "",
        TS_WIRING,
        "防连点",
    ),
    (
        "W10",
        "分享失败改用 Alert（长文失败提示回到系统弹窗，Alert 站点数失控）",
        SHARE_SCREEN,
        "        setError(e instanceof ApiError ? e.message : t('share.memoryShareFailed'));",
        "        Alert.alert(t('share.memoryShareFailed'));",
        TS_WIRING,
        "失败走屏内 error 而不是开新 Alert 站点",
    ),
    (
        "W11",
        "MoodRing 在组件内自算路径（几何脱离可测纯函数，jest 再也管不到它）",
        RING,
        "              d={annularSectorPath(cx, cy, rOuter, rInner, s.fromAngle, s.toAngle)}",
        "              d={`M ${cx} ${cy} L ${rOuter} ${rInner} Z`}",
        TS_WIRING,
        "MoodRing 组件不自算几何",
    ),
    (
        "W12",
        "中心 emoji 只看有没有传进来（无签到那天也被塞一个表情）",
        RING,
        "  const showEmoji = segments.some((s) => s.active) && !!emoji;",
        "  const showEmoji = !!emoji;",
        TS_WIRING,
        "中心 emoji 只在真有情绪时出现",
    ),
    (
        "W13",
        "覆盖层去掉 pointerEvents=none（emoji 层吃掉整卡点击，分享按钮按不动）",
        RING,
        '        <View style={styles.center} pointerEvents="none">',
        "        <View style={styles.center}>",
        TS_WIRING,
        "中心 emoji 只在真有情绪时出现",
    ),
    (
        "W14",
        "前端类型自造 mood 键名（value 改成 score）：色环永远拿不到值",
        TYPES,
        "  value: number;\n}\n\nexport interface OnThisDayCandidate",
        "  score: number;\n}\n\nexport interface OnThisDayCandidate",
        TS_WIRING,
        "mood 三键 emoji/label/value",
    ),
    (
        "W15",
        "后端候选不再下发 mood 键（接线闸的负向对账：前端类型在册也没数据）",
        BACKEND,
        '                    "mood": mood,\n',
        "",
        TS_WIRING,
        "候选五个键与 OnThisDayCandidate",
    ),
]

# ------------------------------------------------------------------ 红闸：后端取数
MUTATIONS += [
    (
        "B1",
        "色环取当日最后一条签到（那天起点的心情被晚间覆盖）",
        BACKEND,
        "        first = checkins[0]",
        "        first = checkins[-1]",
        PY_ON_THIS_DAY,
        "test_on_this_day_mood_uses_first_checkin_of_day",
    ),
    (
        "B2",
        "无签到也返回一个中性情绪（拿「疲惫」冒充没记过心情的那天）",
        BACKEND,
        "        if not checkins:\n            return None",
        '        if not checkins:\n            return {"emoji": "😴", "label": "疲惫", "value": 3}',
        PY_ON_THIS_DAY,
        "test_on_this_day_ignores_other_day_checkin",
    ),
    (
        "B3",
        "表外 emoji 的分值从折中 3 改成 5（脏数据被画成最开心的一段）",
        BACKEND,
        '            "value": MOOD_MAP.get(first.mood_emoji, (None, 3))[1],',
        '            "value": MOOD_MAP.get(first.mood_emoji, (None, 5))[1],',
        PY_ON_THIS_DAY,
        "test_on_this_day_mood_neutral_for_unknown_emoji",
    ),
    (
        "B4",
        "签到窗口改用 UTC 日界（时区铁律失守：上海用户那天的签到落进前一天）",
        BACKEND,
        "            checkins = await mood_repository.list_between(db, user_id, start, end)",
        "            checkins = await mood_repository.list_between(\n"
        '                db, user_id, local_day_start_utc("UTC", local_day),\n'
        '                local_day_start_utc("UTC", local_day + timedelta(days=1)),\n'
        "            )",
        PY_ON_THIS_DAY,
        "test_on_this_day_mood_local_day_boundary",
    ),
]

# ------------------------------------------------------------------ 绿闸
# 破坏「不该打红」的东西＝证明这些切片守卫不是假红制造机。
GREEN: list[tuple[str, str, Path, str, str, str]] = [
    (
        "G1",
        "往回忆卡 JSX 分支（MoodRing 与水印之间）插 12 行注释："
        "「卡面四要素」「色环接线」两类窗口切片守卫不该因此变红",
        SHARE_SCREEN,
        "              <Text style={styles.memoryMoodNote}>",
        "              {/* 增长占位 */}\n" * 12 + "              <Text style={styles.memoryMoodNote}>",
        TS_WIRING,
    ),
    (
        "G2",
        "往 _day_mood 的 docstring 里插 8 行注释（同类增长）："
        "跨仓键集闸与后端取数都不该因此变红",
        BACKEND,
        "        不另建第二份 emoji→分值映射。\n",
        "        不另建第二份 emoji→分值映射。\n"
        + "".join(f"        增长占位 {i}\n" for i in range(8)),
        PY_ON_THIS_DAY,
    ),
]


def run_test(target: str) -> tuple[int, str]:
    if target.endswith(".py"):
        proc = subprocess.run(
            [str(REPO / ".venv" / "bin" / "pytest"), target, "-q", "--no-header"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
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

    def pick(items):
        return [c for c in items if not wanted or c[0].upper() in wanted]

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

    tmp = Path(tempfile.mkdtemp(prefix="mut-memory-card-"))
    originals: dict[Path, Path] = {}
    for case in [*pick(MUTATIONS), *pick(GREEN)]:
        p = case[2]
        if p not in originals:
            bak = tmp / f"{len(originals)}-{p.name}"
            shutil.copy2(p, bak)
            originals[p] = bak

    def restore() -> None:
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
    print("源文件已全部按备份写回并逐字节校验一致。")
    return 0 if red_fail == 0 and green_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

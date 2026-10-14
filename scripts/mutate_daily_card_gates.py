#!/usr/bin/env python3
"""每日卡与睡前复盘三批新闸的变异自证（C4 主位 / C2 次位 / C1 全屏，2026-10-14）。

这三条 ⚠️ 的前端面全是「静默不生效」型改动：路径写错只让卡不出、i18n 漏键只让界面回退成
英文、危机分支写错会把转介当第 1 步成功推进、RN 组件在 node 环境下渲染不了（结构闸读源码文本）。
所以每条破坏都对应一次真实写法回退，要求在受害的那个测试文件里出现那条失败用例名。

恢复用 copy2 + filecmp 逐字节校验，**不用 git checkout**
（见 [[feedback-mutation-restore-no-git-checkout]]）。

用法：
    python3 scripts/mutate_daily_card_gates.py --check   # 只验锚点，各命中一次
    python3 scripts/mutate_daily_card_gates.py           # 18 条红 + 2 条绿
    python3 scripts/mutate_daily_card_gates.py --only DC3,DC10
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
HOME_SRC = FRONT / "src" / "screens" / "HomeScreen.tsx"
BED_SRC = FRONT / "src" / "screens" / "BedtimeReviewScreen.tsx"
CFG = FRONT / "src" / "config" / "dailyCards.ts"
API = FRONT / "src" / "services" / "api.ts"
APP_TSX = FRONT / "App.tsx"
EN_LOCALE = FRONT / "src" / "i18n" / "locales" / "en.ts"

DCARD = "__tests__/dailyCards.test.ts"
CWIRE = "__tests__/dailyCardWiring.test.ts"
TRUST = "__tests__/trustPresentation.test.ts"

# (id, 破坏说明, 文件, 原文, 坏文, 受害测试, 失败用例名片段)
MUTATIONS: list[tuple[str, str, Path, str, str, str, str]] = [
    (
        "DC1",
        "考古端点改成驼峰路径（后端没有这条路由，卡片只会静默不出）",
        API,
        "  return request<OnThisDayResponse>('/share/on-this-day');",
        "  return request<OnThisDayResponse>('/share/onThisDay');",
        CWIRE,
        "api.ts 里五个函数各打这几条路径",
    ),
    (
        "DC2",
        "周年标题写死 count:1（第 2、3 年的候选全显「A year ago today」）",
        HOME_SRC,
        "                count: visibleCards[0].years_ago,",
        "                count: 1,",
        CWIRE,
        "周年标题用 count 交给 i18next 选 CLDR 档",
    ),
    (
        "DC3",
        "装载时预取一次深链（后端首次调用才惰性生成推荐码，白烧一个码且分享归因分叉）",
        HOME_SRC,
        "      const { link } = await getShareLink();\n",
        "      await getShareLink(); // 预热深链\n      const { link } = await getShareLink();\n",
        CWIRE,
        "分享出口只有深链一条路",
    ),
    (
        "DC4",
        "折叠态也一次铺满 3 条（首页主位卡变成列表页）",
        HOME_SRC,
        "          {(archiveOpen ? visibleCards : visibleCards.slice(0, 1)).map((c) => (",
        "          {visibleCards.map((c) => (",
        CWIRE,
        "折叠态只端首条",
    ),
    (
        "DC5",
        "空候选也渲染卡片（标题下挂一个空白引用块）",
        HOME_SRC,
        "      {visibleCards.length > 0 && (",
        "      {visibleCards.length >= 0 && (",
        CWIRE,
        "且空候选不渲染空卡",
    ),
    (
        "DC6",
        "取主题卡不再排除已读（本周读完的一直被端回首页骚扰）",
        CFG,
        "  const unread = days.filter((d) => d.revealed && !d.read && !!d.id);",
        "  const unread = days.filter((d) => d.revealed && !!d.id);",
        DCARD,
        "已读的不算候选",
    ),
    (
        "DC7",
        "周内序按「周序」而不是「周内位」算（7 格进度条永远错位）",
        CFG,
        "  return ((dayNo % WEEK_SLOTS) + WEEK_SLOTS) % WEEK_SLOTS;",
        "  return Math.floor(dayNo / WEEK_SLOTS) % WEEK_SLOTS;",
        DCARD,
        "weekSlot 用后端同一份 day_no 编码",
    ),
    (
        "DC8",
        "晚间兜底卡起点提前到 21:00（原型规范是 22:00–23:30）",
        CFG,
        "export const BEDTIME_CARD_START_MINUTES = 22 * 60;",
        "export const BEDTIME_CARD_START_MINUTES = 21 * 60;",
        DCARD,
        "窗口起点＝本地 22:00",
    ),
    (
        "DC9",
        "忽略完成态（今晚已复盘还在每晚重复打扰）",
        CFG,
        "  return minutesOfDay >= BEDTIME_CARD_START_MINUTES && !completedToday;",
        "  return minutesOfDay >= BEDTIME_CARD_START_MINUTES;",
        DCARD,
        "今日已完成即整卡消失",
    ),
    (
        "DC10",
        "危机判定条件取反（正常出参被当转介拦下，三步再也推不动）",
        CFG,
        "  return (res as { intercepted?: boolean }).intercepted === true;",
        "  return (res as { intercepted?: boolean }).intercepted !== true;",
        DCARD,
        "提交成功后逐级推进",
    ),
    (
        "DC11",
        "恢复时不看第 1 步已落库（重进本页从头走，重发 step1 覆盖用户上一句原话）",
        CFG,
        "  if (steps.step1) return 'step2';\n",
        "",
        DCARD,
        "半程恢复按已落库的最深一步续接",
    ),
    (
        "DC12",
        "输入框放宽到 5000 字（后端 max_length=1000，用户写完才被 422 打回）",
        BED_SRC,
        "                maxLength={1000}",
        "                maxLength={5000}",
        CWIRE,
        "输入上限与后端 schema 同值",
    ),
    (
        "DC13",
        "第 2 步标签漏掉（三步同屏塌成两步，用户不知道这段是谁写的）",
        BED_SRC,
        "          <Text style={styles.stepLabel}>{t('bedtime.step2Label')}</Text>\n",
        "",
        CWIRE,
        "三步同屏常驻",
    ),
    (
        "DC14",
        "只注册 Stack.Screen 不写参数表类型（导航类型面漏登记）",
        APP_TSX,
        "  BedtimeReview: undefined;\n",
        "",
        CWIRE,
        "屏在导航注册",
    ),
    (
        "DC15",
        "en 表把 step1Label 键名打错（源码照旧调用，界面静默显键名/英文）",
        EN_LOCALE,
        '    "step1Label": "Step 1 · Today in one line (skippable)",',
        '    "step1LabelX": "Step 1 · Today in one line (skippable)",',
        CWIRE,
        "的字面量键在 en 表全部命中",
    ),
    (
        "DC16",
        "考古卡分享清单漏掉「原话」这一项（清单与实际发出去的内容脱钩）",
        HOME_SRC,
        "                    items={[t('home.archiveManifestQuote'), t('share.manifestLink')]}",
        "                    items={[t('share.manifestLink')]}",
        TRUST,
        "首页考古卡：清单两条＝原话 + 链接",
    ),
    (
        "DC17",
        "分享失败另开第三个 Alert 站点（Alert 站点数回升＝长文又进系统弹窗）",
        HOME_SRC,
        "      showError(e instanceof ApiError ? e.message : t('home.archiveShareFailed'));",
        "      Alert.alert(t('common.error'), String(e));",
        TRUST,
        "其余 Alert 保留",
    ),
    (
        "DC18",
        "点卡后未读数不收敛（红点数与真实未读永久分叉）",
        HOME_SRC,
        "              unread_count: Math.max(0, prev.unread_count - 1),",
        "              unread_count: prev.unread_count,",
        CWIRE,
        "点击即记已读并乐观收敛未读数",
    ),
]

# 破坏「不该打红」的东西＝证明文本闸不是靠固定行窗吃饭的假红制造机。
GREEN: list[tuple[str, str, Path, str, str, str]] = [
    (
        "G1",
        "往 onArchiveShare 函数体插 12 行注释：源码文本断言不该因函数体增长变红",
        HOME_SRC,
        "      trackFunnelShare('share_tapped', 'memory');\n",
        "      trackFunnelShare('share_tapped', 'memory');\n"
        + "".join(f"      // 增长占位 {i}\n" for i in range(12)),
        CWIRE,
    ),
    (
        "G2",
        "往 load() 里插 10 行注释：复盘屏的接线断言同样不该变红",
        BED_SRC,
        "      const data = await getBedtimeToday();\n",
        "      const data = await getBedtimeToday();\n"
        + "".join(f"      // 增长占位 {i}\n" for i in range(10)),
        CWIRE,
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

    tmp = Path(tempfile.mkdtemp(prefix="mut-daily-card-"))
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
                    print("  " + "\n  ".join(out.splitlines()[-10:]))
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

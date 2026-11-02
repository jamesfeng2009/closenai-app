#!/usr/bin/env python3
"""KG-19／KG-20（测量面）的变异自证（2026-11-02）。

口径沿用前几批：**每条变异只喂它该打红的那一张闸**，并要求报错原文里出现该用例的名片段，
否则「红」可能只是别的用例连带崩（假红）。GREEN 族反过来证这些闸不是假红制造机。

三族靶点各自的形状，跑之前先读这段：

* **K 族（KG-19 文档闸）**——这张表全部是**口径文字**，没有一行代码会因为它写错而崩。
  所以这一族打的全是「表还在不在、格子还填没填、指没指回真表、行号锚点打不打得中」：
  K4 把引用区间挪到不含真实定义行的地方（这是最容易悄悄发生的一种：代码重构后行号漂，
  文档还写着 `:79-88`，读的人以为有同源判据，其实那一段里根本没有那个 key）。
  K7 打的是**定位串台**：有人把关键词表挪到核心表之前，`test_metrics_export` 的
  「从第一个 `| 机器码 |` 读到第一行非表格」就会被抢走——这条红是 KG-19 与既有闸共存的底线。
* **A 族（KG-20 归因）**——这一族判的是「非品牌域不许贴渠道标签」「PII 不许落本机」
  「首触不许被覆盖」「读回不许直接信落盘值」。全部是静默失败：线上不会报错，
  只在某天真把用户手机号当渠道名导进复盘表那天咬人，所以每条都必须打红自己那条判据。
* **纵深证人（GA2）**——A3 证明 PII 层挡得住字符集管不着的形状；GA2 反过来证 PII 层不是死代码：
  把字符集**放宽到允许 `@`**（模拟哪天有人为了带邮箱的活动名放宽主闸），
  邮箱仍然被 PII 层的正则挡在外面，整套仍绿。这两条合起来才说清「双层闸」不是叠影。

⚠️ 纪律（见 [[feedback-mutation-gates-no-concurrency]]、
[[feedback-mutation-restore-no-git-checkout]]、[[feedback-definition-of-done-tests]]）：
**串行跑**、`copy2` 备份写回 + `filecmp` 逐字节复核、**不用 `git checkout` 恢复**。

用法：
    ./.venv/bin/python scripts/mutate_kg19_kg20_gates.py --check   # 只验锚点，各命中一次
    ./.venv/bin/python scripts/mutate_kg19_kg20_gates.py           # 基线 + 全量变异
    ./.venv/bin/python scripts/mutate_kg19_kg20_gates.py --only K4,A3
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
PY = REPO / ".venv" / "bin" / "python"

DOC = REPO / "docs" / "metrics-dictionary.md"
ATTR = FRONT / "src" / "services" / "attribution.ts"

GATE = "backend/tests/test_keyword_funnel_doc.py"
K = lambda name: f"{GATE}::{name}"  # noqa: E731  简写：KG-19 用例节点 id
JEST = ["__tests__/attribution.test.ts"]

# ---------------------------------------------------------------- 红闸清单
# (id, 破坏说明, 文件, 原文, 坏文, runner, 目标用例, 必须出现在报错里的名片段)
RED: list[tuple[str, str, Path, str, str, str, list[str], list[str]]] = [
    # ── K 族：KG-19 关键词渠道七格 ────────────────────────────────────
    (
        "K1",
        "机器码改名（due_outcome → outcome_back）：七格的码与顺序不再等于口径",
        DOC,
        "| `due_outcome` | 到期结果回访",
        "| `outcome_back` | 到期结果回访",
        "pytest",
        [K("test_seven_rows_in_fixed_order")],
        ["test_seven_rows_in_fixed_order"],
    ),
    (
        "K2",
        "把 first_action 的定义格写成「待补」：口径退化成占位符，读数时没人知道这格数什么",
        DOC,
        "用户从洞察或反思里落下的第一条行动承诺（不是待办清单里的通用条目）",
        "待补",
        "pytest",
        [K("test_no_cell_is_a_placeholder")],
        ["test_no_cell_is_a_placeholder"],
    ),
    (
        "K3",
        "first_action 指回 `personal_goals` 而不是 `personal_commitments`：数的不再是行动",
        DOC,
        "服务端表 `personal_commitments.created_at`",
        "服务端表 `personal_goals.created_at`",
        "pytest",
        [K("test_last_four_cells_point_at_existing_server_tables")],
        ["test_last_four_cells_point_at_existing_server_tables"],
    ),
    (
        "K4",
        "引用的行号区间挪到 `:40-45`（那一段里没有 `key=\"first_action\"`）：同源判据变成假证据",
        DOC,
        "`backend/services/metrics_export.py:79-88` 的 `first_action` 行",
        "`backend/services/metrics_export.py:40-45` 的 `first_action` 行",
        "pytest",
        [K("test_cited_line_ranges_reach_the_real_definition")],
        ["test_cited_line_ranges_reach_the_real_definition"],
    ),
    (
        "K5",
        "把 search_reach 的查询入口写成「仓库内脚本已自动接入」：读的人以为本机有搜索数据",
        DOC,
        "各控制台的效果/查询报告按 `site-content/pages.json` 的 slug 过滤后导出 CSV",
        "仓库内 `scripts/export_metrics.py` 已自动接入搜索数据",
        "pytest",
        [K("test_first_three_cells_admit_manual_console_source")],
        ["test_first_three_cells_admit_manual_console_source"],
    ),
    (
        "K6",
        "first_value 的窗口写成「安装后尽快」：一格没有时间边界，两窗口并列的口径消失",
        DOC,
        "安装后 24h 与 7d 两个窗口都算，报告里并列",
        "安装后尽快，报告里并列",
        "pytest",
        [K("test_no_cell_is_a_placeholder")],
        ["test_no_cell_is_a_placeholder"],
    ),
    (
        "K7",
        "把关键词表头插到核心表之前：`test_metrics_export` 的定位被抢走（两张表串台）",
        DOC,
        "## 核心循环七格\n",
        "## 核心循环七格\n\n| 关键词码 | 中文 | 定义 | 来源 | 窗口 | 分群 | 负责人 | 查询入口 | 数据质量检查 |\n"
        "|---|---|---|---|---|---|---|---|---|\n",
        "pytest",
        [K("test_locator_does_not_cross_tables")],
        ["test_locator_does_not_cross_tables"],
    ),
    (
        "K8",
        "删掉 search_reach 里的 `BLOCKED` 标记：渠道曝光读成「已接入」",
        DOC,
        "接入动作属 KG-21，仍 `BLOCKED`",
        "接入动作属 KG-21，已完成",
        "pytest",
        [K("test_uncovered_cells_declare_zero_traffic_or_missing_integration")],
        ["test_uncovered_cells_declare_zero_traffic_or_missing_integration"],
    ),
    (
        "K9",
        "把零流量的 `no_users` 改成按 0 报：洞察产出率会被读成「转化差」",
        DOC,
        "生产流量为零时按 `no_users` 报",
        "生产流量为零时按 0 报",
        "pytest",
        [K("test_uncovered_cells_declare_zero_traffic_or_missing_integration")],
        ["test_uncovered_cells_declare_zero_traffic_or_missing_integration"],
    ),
    # ── A 族：KG-20 归因 ───────────────────────────────────────────────
    (
        "A1",
        "去掉品牌域一票否决：任何能唤起 App 的链接都能给自己贴一个付费渠道标签",
        ATTR,
        "  if (!url || !isBrandHost(url)) return null;",
        "  if (!url) return null;",
        "jest",
        JEST,
        ["非品牌域整条判 null：别的域贴的渠道标签不作数"],
    ),
    (
        "A2",
        "删掉「连续 7 位以上数字」这条：手机号/完整日期串能当渠道名落进本机",
        ATTR,
        "  /\\d{7,}/,\n",
        "",
        "jest",
        JEST,
        ["连续 7 位以上数字（含完整日期串） 判 null"],
    ),
    (
        "A3",
        "PII 层整体短路（`looksLikePii` 恒返回 false）：字符集管不着的形状全部放行",
        ATTR,
        "  return PII_PATTERNS.some((pattern) => pattern.test(value));",
        "  return false;",
        "jest",
        JEST,
        [
            "连续 7 位以上数字（含完整日期串） 判 null",
            "分段电话 判 null",
            "hex 长串（设备指纹／哈希） 判 null",
        ],
    ),
    (
        "A4",
        "长度闸从截断改成不设限（48 → 4096）：超长串照样落盘",
        ATTR,
        "const SAFE_VALUE_RE = /^[a-z0-9][a-z0-9._+-]{0,47}$/;",
        "const SAFE_VALUE_RE = /^[a-z0-9][a-z0-9._+-]{0,4095}$/;",
        "jest",
        JEST,
        ["超过 48 个字符判 null（长度闸而不是截断）"],
    ),
    (
        "A5",
        "captures 恒写成 1：本机看到过几次唤起这条读数消失",
        ATTR,
        "        captures: existing.captures + 1,",
        "        captures: 1,",
        "jest",
        JEST,
        ["首次触达写入 first 与 last，重复唤起只动 last 与 captures"],
    ),
    (
        "A6",
        "首触也覆盖（first: fresh）：first-touch 口径与 last 合流，只剩 last-touch",
        ATTR,
        "        first: existing.first,",
        "        first: fresh,",
        "jest",
        JEST,
        ["首次触达写入 first 与 last，重复唤起只动 last 与 captures"],
    ),
    (
        "A7",
        "读回不重走清洗（直接信落盘值）：本机被旧版本或手工写脏的邮箱原样读出来",
        ATTR,
        "  const first = revalidate(raw.first);",
        "  const first = (raw.first ?? null) as Attribution | null;",
        "jest",
        JEST,
        ["读回时重走清洗：本机被改写成带邮箱的脏值，读出来必须没有"],
    ),
    (
        "A8",
        "往 allowlist 里塞一个 `utm_kludge_id`：未知参数「先留着以后看」回来了",
        ATTR,
        "  'utm_content',\n] as const;",
        "  'utm_content',\n  'utm_kludge_id',\n] as const;",
        "jest",
        JEST,
        ["未知参数与未知 utm_ 变体一律忽略，不「先留着以后看」"],
    ),
    (
        "A9",
        "从 analytics 引 track 进来（归因准备上送）：决策 D6 的白名单边界被跨过",
        ATTR,
        "import AsyncStorage from '@react-native-async-storage/async-storage';",
        "import AsyncStorage from '@react-native-async-storage/async-storage';\nimport { track } from './analytics';",
        "jest",
        JEST,
        ["这个模块不 import analytics、不调 track、不 enqueue"],
    ),
    (
        "A10",
        "落地路径字符集放进点号：`/R/../../etc` 这类穿越串能被当路径存下",
        ATTR,
        "const SAFE_PATH_RE = /^\\/([a-z0-9][a-z0-9/_-]{0,95})?$/;",
        "const SAFE_PATH_RE = /^\\/([a-z0-9][a-z0-9/_.-]{0,95})?$/;",
        "jest",
        JEST,
        ["落地路径只留规范化路径，查询串与片段一律不带"],
    ),
    (
        "A11",
        "去掉「没有 UTM 也没有 campaign 就不算归因」的早退：裸域名唤起也写盘、也计数",
        ATTR,
        "  if (!Object.keys(utm).length && !Object.keys(campaign).length) return null;\n\n  return {\n    utm,",
        "  return {\n    utm,",
        "jest",
        JEST,
        ["没有归因的唤起不写盘，也不把 captures 加上去"],
    ),
]

# ---------------------------------------------------------------- 应保持绿
# (id, 说明, 文件, 原文, 坏文（只加注释／只放宽主闸）, runner, 目标)
GREEN: list[tuple[str, str, Path, str, str, str, list[str]]] = [
    (
        "GA1",
        "归因模块里加 12 行注释：31 条判据都该照旧绿（闸不是靠注释行数判的）",
        ATTR,
        "export const ATTRIBUTION_STORAGE_KEY = '@closenai/attribution';\n",
        "".join(f"// 增长占位 {i}\n" for i in range(12))
        + "export const ATTRIBUTION_STORAGE_KEY = '@closenai/attribution';\n",
        "jest",
        JEST,
    ),
    (
        "GA2",
        "纵深证人：把字符集**放宽到允许 `@`**（模拟主闸漂移），邮箱仍被 PII 层挡住，整套仍绿",
        ATTR,
        "const SAFE_VALUE_RE = /^[a-z0-9][a-z0-9._+-]{0,47}$/;",
        "const SAFE_VALUE_RE = /^[a-z0-9][a-z0-9._+@-]{0,47}$/;",
        "jest",
        JEST,
    ),
    (
        "GK1",
        "关键词表上方加 12 行引用注释：KG-19 的 8 条判据都该照旧绿",
        DOC,
        "\n| 关键词码 | 中文 | 定义 | 来源 | 窗口 | 分群 | 负责人 | 查询入口 | 数据质量检查 |\n",
        "".join(f"> 复核占位 {i}\n" for i in range(12))
        + "\n| 关键词码 | 中文 | 定义 | 来源 | 窗口 | 分群 | 负责人 | 查询入口 | 数据质量检查 |\n",
        "pytest",
        [GATE],
    ),
]

ALL_CASES = RED + GREEN


def patch(path: Path, old: str, new: str) -> None:
    src = path.read_text(encoding="utf-8")
    count = src.count(old)
    assert count == 1, f"{path.name}：锚点命中 {count} 次（要求恰好 1 次）\n{old[:80]!r}"
    path.write_text(src.replace(old, new, 1), encoding="utf-8")


def run_pytest(targets: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [str(PY), "-m", "pytest", *targets, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run_jest(targets: list[str]) -> tuple[int, str]:
    proc = subprocess.run(["npx", "jest", "--silent", *targets], cwd=FRONT, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def run(runner: str, targets: list[str]) -> tuple[int, str]:
    return run_pytest(targets) if runner == "pytest" else run_jest(targets)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    ap.add_argument("--no-baseline", action="store_true", help="跳过基线（只在排障时用）")
    args = ap.parse_args()

    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    if args.check:
        bad = 0
        for case in ALL_CASES:
            path, old = case[2], case[3]
            n = path.read_text(encoding="utf-8").count(old)
            if n != 1:
                print(f"坏锚点 {case[0]}：{path.name} 命中 {n} 次")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}")
        return 1 if bad else 0

    # ── 基线：先把每张要认领的闸跑一遍，缺证据不许当绿 ──────────────
    if not args.no_baseline:
        print("── 基线 ──")
        for runner, targets, label in [
            ("pytest", [GATE], f"KG-19 {GATE}"),
            ("jest", JEST, "前端 attribution.test.ts"),
        ]:
            code, out = run(runner, targets)
            tail = [ln for ln in out.splitlines() if "passed" in ln or "failed" in ln]
            mark = "✓" if code == 0 else "✗ 基线不干净，先修再跑变异"
            print(f"  {mark}  {label}  {tail[-1] if tail else ''}")
            if code != 0:
                return 1

    tmp = Path(tempfile.mkdtemp(prefix="mut-kg1920-"))
    originals: dict[Path, Path] = {}
    for i, case in enumerate(ALL_CASES):
        p = case[2]
        if p not in originals:
            bak = tmp / f"{i}_{p.parent.name}_{p.name}"
            shutil.copy2(p, bak)
            originals[p] = bak

    def restore() -> None:
        for p, bak in originals.items():
            shutil.copy2(bak, p)
            assert filecmp.cmp(bak, p, shallow=False), f"{p} 恢复后字节不一致"

    red_ok = red_fail = green_ok = green_fail = 0
    try:
        for case in RED:
            cid, desc, path, old, new, runner, targets, markers = case
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = run(runner, targets)
                hit = all(m in out for m in markers) and "failed" in out
                if code != 0 and hit:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    print(f"[{cid}] 打红 ✗（exit={code}，名片段命中={hit}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-8:]))
            finally:
                restore()
        for cid, desc, path, old, new, runner, targets in GREEN:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = run(runner, targets)
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
        for bak in originals.values():
            bak.unlink(missing_ok=True)
        tmp.rmdir()

    print(
        f"\n红闸 {red_ok}/{red_ok + red_fail}"
        f"；绿闸 {green_ok}/{green_ok + green_fail}"
        + (f"；红闸失手 {red_fail} 条" if red_fail else "")
        + (f"；绿闸被打红 {green_fail} 条" if green_fail else "")
    )
    return 1 if (red_fail or green_fail) else 0


if __name__ == "__main__":
    sys.exit(main())

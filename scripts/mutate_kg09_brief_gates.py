#!/usr/bin/env python3
"""KG-09 四份内容简报闸的变异自证（2026-11-02，批次 4）。

规矩与前几批一样，不许打折：
- **先跑基线**，基线不干净直接退出——不许把「本来就红」当成「我把简报打红了」。
- **串行**，绝不并发：并发会把变异态当原件写回。
- 恢复只用 `copy2` 备份写回 + `filecmp` 逐字节复核，**禁用 `git checkout`**（工作区里有未提交改动）。
- 每条红靶除 exit≠0 外还要求**名片段**命中——红的必须是那条判据本身，不是别的用例被牵连崩了。

**这批的破坏面**全部落在四份简报的表格与提纲上：简报是「方案 §8.2 页面表 ↔ 台账 §1 任务 ↔ 提示库簇名 ↔ 仓内真行」
的汇合点，所以每条靶都对着一条**抄录关系**，不是对着措辞喜好。

用法：
    ./.venv/bin/python scripts/mutate_kg09_brief_gates.py --check
    ./.venv/bin/python scripts/mutate_kg09_brief_gates.py
    ./.venv/bin/python scripts/mutate_kg09_brief_gates.py --only N3,N12
"""
from __future__ import annotations

import argparse
import copy
import filecmp
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = REPO / ".venv" / "bin" / "python"

D = REPO / "docs" / "content" / "briefs-2026-11"
B1 = D / "reflection-method.md"
B2 = D / "long-term-self-understanding.md"
B3 = D / "action-outcome-review.md"
B4 = D / "ai-insight-credibility.md"

T_GATE = "backend/tests/test_content_briefs.py"

CASES: list[dict] = [
    # ── 结构与覆盖 ─────────────────────────────────────────────
    dict(id="N1", kind="text", file=B1, targets=[T_GATE],
         desc="把 §3 的节标题改成「标题」：八节的固定标题是闸的解析面，改名＝那节消失",
         old="## 3. H1", new="## 3. 标题",
         markers=["缺节"]),
    dict(id="N2", kind="text", file=B1, targets=[T_GATE],
         desc="往 §2 塞一行方案 §8.2 里没有的页面：简报不许自造落点",
         old="| `overthinking` | 反刍 vs 反思怎么分 | 三步练习＋什么时候该找真人 | 危机资源 | KG-15 |",
         new="| `overthinking` | 反刍 vs 反思怎么分 | 三步练习＋什么时候该找真人 | 危机资源 | KG-15 |\n"
             "| `journal-templates` | 模板 | 模板 | 模板 | KG-12 |",
         markers=["里没有的页面"]),
    dict(id="N3", kind="text", file=B1, targets=[T_GATE],
         desc="把 journal-prompts 的任务号写成 KG-99：任务列必须真在台账 §1",
         old="| `journal-prompts` | 想要现成的提示 | 30 条提示**按功能分簇**且可追溯 | 复用 KG-26 JSON | KG-12 |",
         new="| `journal-prompts` | 想要现成的提示 | 30 条提示**按功能分簇**且可追溯 | 复用 KG-26 JSON | KG-99 |",
         markers=["挂在不存在的任务"]),
    dict(id="N4", kind="text", file=B2, targets=[T_GATE],
         desc="把 §3 的一行删掉：§2 有这页而 §3 没有＝页面没有 H1",
         old="| `personality-test` | `Personality Frameworks, Read From Your Own Entries` | 50 |\n",
         new="",
         markers=["页面集合不同"]),
    # ── H1 三面尺子 ────────────────────────────────────────────
    dict(id="N5", kind="text", file=B2, targets=[T_GATE],
         desc="把 self-discovery 的 H1 换成 self-reflection 那条：同站两页抢同一句必须红",
         old="| `self-discovery` | `Self-Discovery Is a Record, Not a Quiz` | 38 |",
         new="| `self-discovery` | `Self-Reflection That Ends in Evidence` | 38 |",
         markers=["的 H1"]),
    dict(id="N6", kind="text", file=B3, targets=[T_GATE],
         desc="把 mood-tracker 的 H1 拉到 70 字符以上：H1 不是关键词堆叠（G01）",
         old="| `mood-tracker` | `Mood Tracker: Check-Ins, Trends and What Came After` | 51 |",
         new="| `mood-tracker` | `Mood Tracker App: Daily Check-Ins, 7 and 30 Day Trends, Heatmap and What Came After` | 51 |",
         markers=["字符（上限 70）"]),
    dict(id="N7", kind="text", file=B4, targets=[T_GATE],
         desc="把 journal-app 的 H1 写进 `best`：禁语清单是共享尺子，不是商店面专用",
         old="| `journal-app` | `Choosing a Journal App That Doesn't Just Talk Back` | 50 |",
         new="| `journal-app` | `Choosing the Best Journal App That Talks Back` | 50 |",
         markers=["命中禁语 best"]),
    dict(id="N8", kind="text", file=B4, targets=[T_GATE],
         desc="把 ai-journal 的 H1 塞进竞品词 rosebud：品牌词只许出现在 /compare/ 面（R3）",
         old="| `ai-journal` | `What an AI Journal Should Show You` | 34 |",
         new="| `ai-journal` | `What a Rosebud Alternative Should Show You` | 37 |",
         markers=["不是对比页却含竞品品牌词"]),
    dict(id="N9", kind="text", file=B1, targets=[T_GATE],
         desc="把 journal-prompts 的 H1 字符数从 52 手改成 51：那列必须现算",
         old="| `journal-prompts` | `30 Journal Prompts Built From What You Already Wrote` | 52 |",
         new="| `journal-prompts` | `30 Journal Prompts Built From What You Already Wrote` | 51 |",
         markers=["手填的数字漂了"]),
    # ── 证据面 ─────────────────────────────────────────────────
    dict(id="N10", kind="text", file=B3, targets=[T_GATE],
         desc="把 Outcome 的行号从 506 改成 999999：证据必须打到真行",
         old="`backend/models/canonical.py:506`（`result … default=\"unknown\"`）",
         new="`backend/models/canonical.py:999999`（`result … default=\"unknown\"`）",
         markers=["该文件只有"]),
    dict(id="N11", kind="text", file=B3, targets=[T_GATE],
         desc="把 Outcome 那行的来源换成一句形容词：没有可核验来源的主张不进页面",
         old="`backend/models/canonical.py:506`（`result … default=\"unknown\"`）",
         new="数据结构里就没有自动判定成败（口说无凭）",
         markers=["没有可核验来源"]),
    dict(id="N12", kind="text", file=B1, targets=[T_GATE],
         desc="把第 5 条证据的日期写成 2026-11：G03 要精确到日",
         old="`docs/keyword-claims-audit-2026-11.md:100`（KG-01 汇总与判定分布） | 2026-11-02 |",
         new="`docs/keyword-claims-audit-2026-11.md:100`（KG-01 汇总与判定分布） | 2026-11 |",
         markers=["日期不是 YYYY-MM-DD"]),
    # ── 法务边界与提纲 ─────────────────────────────────────────
    dict(id="N13", kind="text", file=B2, targets=[T_GATE],
         desc="把 §6 的商标那条整句换成不含「商标」的说法：人格簇不许丢商标边界",
         old="- **商标**：`MBTI`、`Enneagram`、`16 types` 的词面使用方式（是否需加「不属于我们的商标」脚注）属 KG-02 未决项，\n  本簇**不自行决定**；草稿阶段一律用「类型框架／人格框架」这类中文中性描述。",
         new="- **命名**：`MBTI`、`Enneagram`、`16 types` 的词面使用方式属 KG-02 未决项，\n  本簇**不自行决定**；草稿阶段一律用「类型框架／人格框架」这类中文中性描述。",
         markers=["没写商标边界"]),
    dict(id="N14", kind="text", file=B4, targets=[T_GATE],
         desc="把提纲里的差异化段落换成自造说法：方案 §8.2 那一行的原词不许被同义替换",
         old="（差异化段落＝**记录→情绪→反思→行动结果**，与方案 §8.2 该行同词）",
         new="（差异化段落＝**记录到结果的闭环**，与方案 §8.2 该行同词）",
         markers=["差异化段落"]),
    dict(id="N15", kind="text", file=B1, targets=[T_GATE],
         desc="把提纲的目标页改成本簇 §2 里没有的 mood-tracker：提纲不许挂错簇",
         old="**本篇给 `self-reflection`**",
         new="**本篇给 `mood-tracker`**",
         markers=["但 §2 里没有这页"]),
    # ── 「不做」声明与簇同源 ───────────────────────────────────
    dict(id="N16", kind="text", file=B4, targets=[T_GATE],
         desc="把 §8 的「不部署」改成「不上线」以外的一句话：读者会把简报当成已发布页面",
         old="1. 不生成 HTML／不写 `web/`／不改 `web/sitemap.xml`／不部署（KG-02、T7-05 未解）。\n2. 不写对比页正文与提纲",
         new="1. 不生成 HTML／不写 `web/`／不改 `web/sitemap.xml`（KG-02、T7-05 未解）。\n2. 不写对比页正文与提纲",
         markers=["没写「不部署」"]),
    dict(id="N17", kind="text", file=B1, targets=[T_GATE],
         desc="把头部写明簇名改成 reflection_methods：文档与闸的映射必须同词（正向证人）",
         old="`cluster: reflection_method`（8 条提示）",
         new="`cluster: reflection_methods`（8 条提示）",
         markers=["没写明它对应提示库的哪个簇"]),
    # ── 绿闸：正常收尾动作不许被判成破坏 ───────────────────────
    dict(id="GN1", kind="green_text", file=B3, targets=[T_GATE],
         desc="在 §8 之后追加一节「## 9. 变更记录」：八节判据照旧绿",
         old="4. 不动 `docs/content/drafts/journal-prompts.*.md`（那是 KG-26 提示库的导出物，不是面向搜索的页面）。",
         new="4. 不动 `docs/content/drafts/journal-prompts.*.md`（那是 KG-26 提示库的导出物，不是面向搜索的页面）。\n\n"
             "---\n\n## 9. 变更记录\n\n- 2026-11-02：四份简报首次落盘（KG-09 批次）。本条是绿闸 GN1，用来证明"
             "「在文末追加一节说明」不会被判成破坏——闸判的是页面清单、H1 尺子、证据真行与簇名是否与上游同源，不是文档长度。",
         markers=[]),
    dict(id="GN2", kind="green_text", file=B2, targets=[T_GATE],
         desc="在 §6 末尾追加一条含「商标」的新说明：法务边界照旧绿",
         old="- **加密与导出**：本簇页面不提加密、不提保留期——这两处口径存在冲突（`docs/keyword-claims-audit-2026-11.md:129` 起），\n  等 KG-02 隐私对照给结论。",
         new="- **加密与导出**：本簇页面不提加密、不提保留期——这两处口径存在冲突（`docs/keyword-claims-audit-2026-11.md:129` 起），\n  等 KG-02 隐私对照给结论。\n"
             "- **绿闸说明**：本条是 GN2 追加的，用来证明 §6 的判据是「点到该点的边界」而不是「数行数」；商标一词仍在册。",
         markers=[]),
    dict(id="GN3", kind="green_text", file=B4, targets=[T_GATE],
         desc="在提纲末尾加一条 H2 与一句解释：≥6 段的下限照旧满足",
         old="- H2 8｜CTA：等待名单＋一句「为什么现在没有商店按钮」。",
         new="- H2 8｜CTA：等待名单＋一句「为什么现在没有商店按钮」。\n"
             "- H2 9｜（GN3 绿闸追加）一句与判据无关的补充说明：本簇四页的 H2 数量下限是 6，多一段不会红。",
         markers=[]),
]


def patch_text(path: Path, old: str, new: str) -> None:
    src = path.read_text(encoding="utf-8")
    n = src.count(old)
    assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:80]!r}"
    path.write_text(src.replace(old, new, 1), encoding="utf-8")


def run_pytest(targets: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [str(PY), "-m", "pytest", *targets, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def apply(case: dict) -> None:
    assert case["kind"] in ("text", "green_text"), f"未知靶型 {case['kind']}"
    patch_text(case["file"], case["old"], case["new"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    ap.add_argument("--no-baseline", action="store_true", help="跳过基线（只在排障时用）")
    args = ap.parse_args()

    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}
    cases = [c for c in CASES if not wanted or c["id"] in wanted]

    if args.check:
        bad = 0
        for case in cases:
            n = case["file"].read_text(encoding="utf-8").count(case["old"])
            if n != 1:
                print(f"坏锚点 {case['id']}：{case['file'].name} 命中 {n} 次 ⇒ {case['old'][:60]!r}")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}")
        return 1 if bad else 0

    if not args.no_baseline:
        print("── 基线 ──")
        code, out = run_pytest([T_GATE])
        tail = [ln for ln in out.splitlines() if "passed" in ln or "failed" in ln]
        print(f"  {'✓' if code == 0 else '✗ 基线不干净，先修再跑变异'}  KG-09 简报闸  {tail[-1] if tail else out[-300:]}")
        if code != 0:
            return 1

    tmp = Path(tempfile.mkdtemp(prefix="mut-kg09-"))
    originals: dict[Path, Path] = {}
    for i, case in enumerate(cases):
        p = case["file"]
        if p not in originals:
            bak = tmp / f"{i}_{p.name}"
            shutil.copy2(p, bak)
            originals[p] = bak

    def restore() -> None:
        for p, bak in originals.items():
            shutil.copy2(bak, p)
            assert filecmp.cmp(bak, p, shallow=False), f"{p} 恢复后字节不一致"

    red_ok = red_fail = green_ok = green_fail = 0
    try:
        for case in cases:
            green = case["kind"] == "green_text"
            snapshot = copy.copy(case)
            try:
                apply(snapshot)
                code, out = run_pytest(snapshot["targets"])
                hit = all(m in out for m in snapshot["markers"])
                if green:
                    if code == 0:
                        green_ok += 1
                        print(f"[{snapshot['id']}] 应保持绿 ✓  {snapshot['desc']}")
                    else:
                        green_fail += 1
                        print(f"[{snapshot['id']}] 应保持绿 ✗（exit={code}）{snapshot['desc']}")
                        print("  " + "\n  ".join(out.splitlines()[-12:]))
                else:
                    if code != 0 and hit:
                        red_ok += 1
                        print(f"[{snapshot['id']}] 打红 ✓  {snapshot['desc']}")
                    else:
                        red_fail += 1
                        why = "exit=0（没打红）" if code == 0 else f"exit={code} 但名片段没命中：{snapshot['markers']}"
                        print(f"[{snapshot['id']}] 打红 ✗  {why}  {snapshot['desc']}")
                        print("  " + "\n  ".join(out.splitlines()[-10:]))
            finally:
                restore()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n红闸 {red_ok}/{red_ok + red_fail}；绿闸 {green_ok}/{green_ok + green_fail}；红闸失手 {red_fail} 条")
    return 0 if red_fail == 0 and green_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

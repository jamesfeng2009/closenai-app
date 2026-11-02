#!/usr/bin/env python3
"""KG-10～KG-14 页面源闸＋生成器的变异自证（2026-11-03，批次 7）。

规矩与前几批一样，不许打折：
- **先跑基线**，基线不干净直接退出——不许把「本来就红」当成「我把页面打红了」。
- **串行**，绝不并发：并发会把变异态当原件写回。
- 恢复只用 `copy2` 备份写回 + `filecmp` 逐字节复核，**禁用 `git checkout`**（工作区里有未提交改动）。
- 每条红靶除 exit≠0 外还要求**名片段**命中——红的必须是那条判据本身。

**这批的破坏面**分四层：内容源 `site-content/pages.json`、正文 `docs/content/pages/*.en.md`、
抽检表 `docs/keyword-content-gate.md` §3、下游抄台账的行号（`docs/aso/` 两份）。靶型全部对着
**抄录关系**（三面同词、related↔真内链、清单项↔抽检行、发布闸↔输出目录、行号引用↔被抄原文），不对着措辞喜好。

用法：
    ./.venv/bin/python scripts/mutate_kg10_kg14_page_gates.py --check
    ./.venv/bin/python scripts/mutate_kg10_kg14_page_gates.py
    ./.venv/bin/python scripts/mutate_kg10_kg14_page_gates.py --only P4,B7
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

PJ = REPO / "site-content" / "pages.json"
GATE = REPO / "docs" / "keyword-content-gate.md"
D = REPO / "docs" / "content" / "pages"
AIJ, JAP, MOOD = D / "ai-journal.en.md", D / "journal-app.en.md", D / "mood-tracker.en.md"
SR, JP, SI, GT = D / "self-reflection.en.md", D / "journal-prompts.en.md", D / "self-improvement.en.md", D / "goal-tracker.en.md"

T_GATE = "backend/tests/test_seo_page_source.py"
T_KG18 = "backend/tests/test_keyword_content_gate.py"
T_BOARD = "backend/tests/test_keyword_task_board.py"
BOTH = [T_GATE, T_KG18]

ASO_PACK = REPO / "docs" / "aso" / "en-US-2026-11.md"
TRACKER = REPO / "docs" / "aso" / "keyword-tracker-2026-11.md"

#: P20 的靶要把一条提示 id 塞进正文。这里拼出来而不是直写字面量，是因为
#: `test_prompt_ids_live_only_in_the_library_and_its_consumers` 会扫 `scripts/` 下的 .py，
#: 直写会让那条闸在**没变异的时候**就红——本批的靶只许打别人的闸，不许先弄坏自己的闸。
PROMPT_ID = "rp" + "-0" + "01"

CASES: list[dict] = [
    # ── 内容源 ↔ 正文 ↔ 简报 的三面同词 ─────────────────────────
    dict(id="P1", kind="text", file=PJ, targets=[T_GATE],
         desc="把 mood-tracker 的 H1 改一个词：正文与内容源立刻不同词，简报 §3 也对不上",
         old='"h1": "Mood Tracker: Check-Ins, Trends and What Came After",',
         new='"h1": "Mood Tracker: Check-Ins, Trends and What Followed",',
         markers=["H1 与内容源不同词"]),
    dict(id="P2", kind="text", file=AIJ, targets=[T_GATE],
         desc="只改正文的 H1（内容源不动）：另一侧漂了同样要红",
         old="# What an AI Journal Should Show You",
         new="# What an AI Journal Should Show You Today",
         markers=["H1 与内容源不同词"]),
    dict(id="P3", kind="text", file=PJ, targets=[T_GATE],
         desc="把 journal-app 的 description 抄成 ai-journal 那句：两页互相蚕食（G01）",
         old='"description": "A chatbot answers and forgets the thread. A journal app should keep entries you can revisit, compare and check. This is the difference we build around.",',
         new='"description": "An AI journal should link every insight back to your own words. Here is the record, check-in, reflection and outcome loop ClosenAI keeps.",',
         markers=["description 与"]),
    dict(id="P4", kind="text", file=PJ, targets=[T_GATE],
         desc="把 journal-app 的 H1 写成 ai-journal 那句：同站两页抢同一句（G01/G02）",
         old='"h1": "Choosing a Journal App That Doesn\'t Just Talk Back",',
         new='"h1": "What an AI Journal Should Show You",',
         markers=["的 H1 与"]),
    dict(id="P5", kind="text", file=PJ, targets=[T_GATE],
         desc="把 self-reflection 的 template 抄成 goal-tracker 那句：两页交付物同文（G02）",
         old='"template": "The weekly five-liner: what happened, what I read into it, what I did, what came of it, one change next week.",',
         new='"template": "One goal per week, written as a prediction you can be wrong about.",',
         markers=["的模板同文"]),
    # ── 证据面（G03）──────────────────────────────────────────
    dict(id="P6", kind="text", file=PJ, targets=[T_GATE],
         desc="把 ai-journal 的证据行号从 :357 挪到 :999999：出处打不开就是假出处",
         old='"ref": "backend/models/canonical.py:357"',
         new='"ref": "backend/models/canonical.py:999999"',
         markers=["行号越界"]),
    dict(id="P7", kind="text", file=PJ, targets=[T_GATE],
         desc="把提示库那条证据的日期写成 Nov 2026：日期必须成形可排序",
         old='{"claim": "The prompt library holds 30 entries.", "ref": "backend/content/reflection_prompts.json:37", "date": "2026-11-01"}',
         new='{"claim": "The prompt library holds 30 entries.", "ref": "backend/content/reflection_prompts.json:37", "date": "Nov 2026"}',
         markers=["日期不成形"]),
    # ── 禁语／非医疗／品牌词（G05＋G13，共享尺子）───────────────
    dict(id="P8", kind="text", file=MOOD, targets=[T_GATE],
         desc="删掉 mood-tracker 的非医疗声明那半句（G05 的机械命中面）",
         old="This is a description of a self-recording tool. It is not medical advice, not a screening result,",
         new="This is a description of a self-recording tool. It is a wellbeing summary, not a screening result,",
         markers=["缺非医疗声明句"]),
    dict(id="P9", kind="text", file=JAP, targets=[T_GATE],
         desc="在 journal-app 正文提一次友站名：品牌词只许出现在 /compare/ 面（KG-05）",
         old="ClosenAI is a diary app by category but a record by structure.",
         new="ClosenAI is a diary app by category — unlike Rosebud, it is a record by structure.",
         markers=["含竞品品牌词"]),
    dict(id="P10", kind="text", file=PJ, targets=[T_GATE],
         desc="把 ASO 尺子里的 `best` 塞进 description：禁语清单是共享的，不是商店面专用",
         old='"title": "Mood Tracker: Check-Ins and Trends",',
         new='"title": "Best Mood Tracker: Check-Ins and Trends",',
         markers=["命中禁语"]),
    # ── 堆词（G07）────────────────────────────────────────────
    dict(id="P11", kind="text", file=PJ, targets=[T_GATE],
         desc="给 ai-journal 的变体加到 6 条：超过 5 个就是批量同义词",
         old='"keyword_variants": ["ai journal", "ai journaling", "journal with ai"],',
         new='"keyword_variants": ["ai journal", "ai journaling", "journal with ai", "ai diary", "ai journal app free", "journal ai online"],',
         markers=["变体数越界"]),
    dict(id="P12", kind="text", file=JAP, targets=[T_GATE],
         desc="把唯一的 `journaling app` 改成 `journaling tool`：变体声明了却没真出现在叙述行",
         old="What a record-keeping journaling app has instead:",
         new="What a record-keeping journaling tool has instead:",
         markers=["不在正文叙述行"]),
    dict(id="P13", kind="text", file=MOOD, targets=[T_GATE],
         desc="把三个变体并列写进同一行：同句堆三个同义词（G07）",
         old="## What a check-in is, and what it is not",
         new="## What a check-in is, and what it is not\n\nA mood tracker, a mood journal and a mood tracking app are the same tool.",
         markers=["同一行并列 3 个变体"]),
    # ── 内链与 CTA（G08/G09）──────────────────────────────────
    dict(id="P14", kind="text", file=PJ, targets=[T_GATE],
         desc="把 mood-tracker 的 related 砍到一条：每页 ≥2 条内链（G08）",
         old='"related": ["goal-tracker", "self-reflection"],\n      "keyword_variants": ["mood tracker", "mood journal", "mood tracking app"],',
         new='"related": ["goal-tracker"],\n      "keyword_variants": ["mood tracker", "mood journal", "mood tracking app"],',
         markers=["related 少于两条"]),
    dict(id="P15", kind="text", file=SI, targets=[T_GATE],
         desc="把 self-improvement 指向 goal-tracker 的内链写错 slug：断链＝红",
         old="[the goal tracker](/en/goal-tracker/) is the page about that",
         new="[the goal tracker](/en/goal-tracking/) is the page about that",
         markers=["链到不存在的页面"]),
    dict(id="P16", kind="text", file=MOOD, targets=[T_GATE],
         desc="related 里留着 goal-tracker，正文却把那条链接删成纯文本：两侧不同源",
         old="see [the goal tracker](/en/goal-tracker/), where the follow-up is the feature.",
         new="see the goal tracker, where the follow-up is the feature.",
         markers=["正文缺一条指向 /en/goal-tracker/ 的内链"]),
    dict(id="P17", kind="text", file=PJ, targets=[T_GATE],
         desc="把等待名单的按钮文案换成 Download now：没有商店链接就是假按钮（G09）",
         old='"label": "Join the ClosenAI closed-test list",',
         new='"label": "Download now",',
         markers=["假按钮"]),
    dict(id="P18", kind="text", file=GT, targets=[T_GATE],
         desc="在 goal-tracker 正文写一句 Download now 并附商店域名：假按钮面在正文也要拦",
         old="## Getting it\n\nThere is no App Store or Play Store link yet",
         new="## Getting it\n\nDownload now at https://apps.apple.com/app/id0000000 —\nthere is no App Store or Play Store link yet",
         markers=["正文出现商店域名/短链", "download now"]),
    # ── 提示页：生成物不许手抄（KG-12）─────────────────────────
    dict(id="P19", kind="text", file=JP, targets=[T_GATE],
         desc="删掉提示库插入点：正文一旦不含插入点，30 条就是人抄的",
         old="\n<!--prompt-library-->\n",
         new="\n",
         markers=["恰好一个插入点"]),
    dict(id="P20", kind="text", file=JP, targets=[T_GATE],
         desc="把一条提示的 id 手抄进正文：库里现算的面被破坏",
         old="## The library\n",
         new="## The library\n\n" + PROMPT_ID + " — What the camera would have caught\n",
         markers=["正文里出现了提示 id"]),
    # ── 发布闸与语言面（G04/G11/G14＋方案 §8.3）────────────────
    dict(id="P21", kind="text", file=PJ, targets=[T_GATE],
         desc="把输出目录改成 web：发布闸（KG-02／T7-05）未结案，生成物不许落现网目录",
         old='"output_dir": "docs/content/build",',
         new='"output_dir": "web",',
         markers=["生成物不许直接落现网目录"]),
    dict(id="P22", kind="text", file=PJ, targets=[T_GATE],
         desc="清空 publish_blocked_by：草稿不再标明「发布前置未结案」，就会被当对待发布",
         old='"publish_blocked_by": ["KG-02", "T7-05"],',
         new='"publish_blocked_by": [],',
         markers=["发布闸没登记"]),
    dict(id="P23", kind="text", file=GT, targets=[T_GATE],
         desc="在英文支柱页正文混进中文词：本批只出 en（简报 §8.4 的语言决策）",
         old="## The follow-up mechanic, described plainly",
         new="## 回访 mechanic, described plainly",
         markers=["正文混入中文"]),
    # ── 抽检表（KG-18 §3：空表不算交付，回填了也要判得动）────────
    dict(id="P24", kind="text", file=GATE, targets=BOTH,
         desc="把 journal-prompts 那行的勾少列一项（去掉 G12）：抽检不许自称覆盖全清单",
         old="| `journal-prompts` | 2026-11-03 | Qoder 自查 ＋ 提示库现算对拉 | 逐项打勾 G01/G02/G03/G04/G05/G06/G07/G08/G09/G10/G11/G12/G13/G14 |",
         new="| `journal-prompts` | 2026-11-03 | Qoder 自查 ＋ 提示库现算对拉 | 逐项打勾 G01/G02/G03/G04/G05/G06/G07/G08/G09/G10/G11/G13/G14 |",
         markers=["抽检缺清单项"]),
    dict(id="P25", kind="text", file=GATE, targets=[T_GATE],
         desc="把抽检行写成内容源里根本没有的页面名：抽检对象必须真在页面集合里",
         old="| `ai-journal` | 2026-11-03 | Qoder 自查 ＋ `scripts/build_seo_pages.py` 复算 |",
         new="| `mood-journal` | 2026-11-03 | Qoder 自查 ＋ `scripts/build_seo_pages.py` 复算 |",
         markers=["不在内容源页面集合里"]),
    # ── 下游抄台账的行号（本批插入两行说明，正是这条的空档被踩出来的地方）──
    dict(id="P26", kind="text", file=ASO_PACK, targets=[T_BOARD],
         desc="把 ASO 文档引的台账副标题 A 行号从 232 漂回 230（同一 KG-04 小节的相邻真行）：只靠节号证人抓不到，必须被内容锚判红",
         old="`docs/keyword-growth-task-board-2026-11-01.md:232` 那条 `副标题 A`",
         new="`docs/keyword-growth-task-board-2026-11-01.md:230` 那条 `副标题 A`",
         markers=["同小节内行号漂了"]),
    dict(id="P27", kind="text", file=TRACKER, targets=[T_BOARD],
         desc="把词表引的 §4 映射表行号从 813 漂到 811（同节、非空、不含表头锚）：内容锚是唯一能拦住它的判据",
         old="`docs/keyword-growth-task-board-2026-11-01.md:813` 起的映射表",
         new="`docs/keyword-growth-task-board-2026-11-01.md:811` 起的映射表",
         markers=["同小节内行号漂了"]),
    # ── 绿闸：正常收尾动作不许被判成破坏 ───────────────────────
    dict(id="GN1", kind="green_text", file=GT, targets=[T_GATE],
         desc="在正文末尾追加一节「A note on dates」：判据看的是同源与在场，不是文档长度",
         old="first written prediction reaches its date. Or email support@closenai.app.",
         new="first written prediction reaches its date. Or email support@closenai.app.\n\n"
             "## A note on dates\n\nEverything above was checked against this repository on 2026-11-03. The evidence rows\ncarry their own dates, so you can re-check each one instead of trusting this sentence.",
         markers=[]),
    dict(id="GN2", kind="green_text", file=PJ, targets=[T_GATE],
         desc="把 generated_on 往前挪一天：sitemap 的 lastmod 跟着走，判据不数日期",
         old='"generated_on": "2026-11-03",',
         new='"generated_on": "2026-11-04",',
         markers=[]),
    dict(id="GN3", kind="green_text", file=PJ, targets=[T_GATE],
         desc="把等待名单的说明写得更长（仍不是下载按钮）：G09 判的是动作真假，不是字数",
         old='"note": "There is no App Store or Play Store link yet, so this is deliberately not a download button. The list gets one invitation and one follow-up when a goal you wrote reaches its date."',
         new='"note": "There is no App Store or Play Store link yet, so this is deliberately not a download button. The list gets one invitation and one follow-up when a goal you wrote reaches its date, and nothing else — no content push."',
         markers=[]),
    dict(id="GN4", kind="green_text", file=GATE, targets=BOTH,
         desc="给抽检表再加一行（第三页，逐项覆盖 14 项）：≥2 的下限照旧满足",
         old="**回填说明（2026-11-03）**：内容源 `site-content/pages.json` 落地 7 页后，",
         new="| `self-reflection` | 2026-11-03 | Qoder 自查 ＋ 三面同词复算 | 逐项打勾 G01/G02/G03/G04/G05/G06/G07/G08/G09/G10/G11/G12/G13/G14 | 与上面两行同判：G04 运营主体待 T7-05、G11 只出 en、G12 未提交 GSC ⇒ 只到草稿面 |\n\n"
             "**回填说明（2026-11-03）**：内容源 `site-content/pages.json` 落地 7 页后，",
         markers=[]),
    dict(id="GN5", kind="green_text", file=ASO_PACK, targets=[T_BOARD],
         desc="在 ASO 文档里再加一处「带逐字内容锚」的台账行号引用：新判据不许把正确引用也判红",
         old="而不是替法务拍板。",
         new="而不是替法务拍板。\n- **绿闸 GN5（2026-11-03）**：台账 §KG-04 那条 `副标题 A` 逐字在 "
             "`docs/keyword-growth-task-board-2026-11-01.md:232`，本处引用带同一行的内容锚，用来证明"
             "「逐字内容锚」这条判据放行正确引用，不是只认原来那三处。",
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
    cases = [c for c in CASES if not wanted or c["id"].upper() in wanted]

    if args.check:
        bad = 0
        for case in cases:
            n = case["file"].read_text(encoding="utf-8").count(case["old"])
            if n != 1:
                print(f"坏锚点 {case['id']}：{case['file'].name} 命中 {n} 次 ⇒ {case['old'][:60]!r}")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}（共 {len(cases)} 条）")
        return 1 if bad else 0

    if not args.no_baseline:
        print("── 基线 ──")
        for targets, label in (([T_GATE], "页面源闸＋生成器"), ([T_KG18], "KG-18 清单闸"),
                               (["backend/tests/test_content_briefs.py"], "KG-09 简报闸"),
                               ([T_BOARD], "台账一致性闸")):
            code, out = run_pytest(targets)
            tail = [ln for ln in out.splitlines() if "passed" in ln or "failed" in ln]
            mark = "✓" if code == 0 else "✗ 基线不干净，先修再跑变异"
            print(f"  {mark}  {label}  {tail[-1] if tail else out[-300:]}")
            if code != 0:
                return 1

    tmp = Path(tempfile.mkdtemp(prefix="mut-kg10-"))
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

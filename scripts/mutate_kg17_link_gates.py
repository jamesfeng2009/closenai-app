#!/usr/bin/env python3
"""KG-17 内链/CTA/面包屑/在册表闸的变异自证（2026-11-03，批次 8）。

规矩与前七批一样，不许打折：
- **先跑基线**，基线不干净直接退出——不许把「本来就红」当成「我把内链图打红了」。
- **串行**，绝不并发：并发会把变异态当原件写回。
- 恢复只用 `copy2` 备份写回 + `filecmp(shallow=False)` 逐字节复核，**禁用 `git checkout`**
  （工作区里有未提交改动，而且 `web/` 整目录从未进版本控制，git 根本看不见它）。
- 每条红靶除 exit≠0 外还要求**名片段**命中——红的必须是那条判据本身，不是旁边顺手也红的那条。

**这批的破坏面**分五层，一层层对着判据打：
1. 正文 `docs/content/pages/*.en.md`（图的一条边）；
2. 内容源 `site-content/pages.json`（related／CTA 定义）；
3. 生成器 `scripts/build_seo_pages.py`（面包屑那一行）；
4. 结案文档 `docs/seo/internal-links-2026-11.md`（§2 图面表、§5 现网面在册表）；
5. 判据自己（`scripts/check_site_links.py` 的 `INBOUND_MIN`）与现网面 `web/`（sitemap／robots／index）。

**与第七批的靶型差别**：那批打的是「三面同词」的抄录关系，这批打的是**图关系**——
一条边的消失，同时会让孤儿判据、可达性判据与 §2 表三条都动。凡此都在 desc 里写明，
名片段只取该靶要证的那条；多红的不是失败，但也不许冒充「只有我要的那条红了」。

用法：
    ./.venv/bin/python scripts/mutate_kg17_link_gates.py --check
    ./.venv/bin/python scripts/mutate_kg17_link_gates.py
    ./.venv/bin/python scripts/mutate_kg17_link_gates.py --only L1,GN1
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
D = REPO / "docs" / "content" / "pages"
AIJ, JAP, MOOD = D / "ai-journal.en.md", D / "journal-app.en.md", D / "mood-tracker.en.md"
SI = D / "self-improvement.en.md"
DOC = REPO / "docs" / "seo" / "internal-links-2026-11.md"
GATE = REPO / "scripts" / "check_site_links.py"
BUILDER = REPO / "scripts" / "build_seo_pages.py"
WEB = REPO / "web"
INDEX, SITEMAP, ROBOTS = WEB / "index.html", WEB / "sitemap.xml", WEB / "robots.txt"

T_LINK = "backend/tests/test_site_links.py"
T_GATE = "backend/tests/test_seo_page_source.py"

CRUMB_OLD = '<nav aria-label="Breadcrumb"><a href="{domain}/">Home</a> / {escape(page["title"])}</nav>'
CRUMB_NEW = '<nav aria-label="Breadcrumb"><a href="{domain}/en/">Home</a> / {escape(page["title"])}</nav>'

CTA_HREF = '      "href": "https://closenai.app/#cta",'
CTA_LABEL = '      "label": "Join the ClosenAI closed-test list",'
CTA_NOTE = ('      "note": "There is no App Store or Play Store link yet, so this is deliberately '
            'not a download button.')

# ── 正文锚点（每段都是文件里的逐字原文）────────────────────────
AIJ_JP_LINK = """The questions a follow-up is drawn from are not invented mid-conversation; they are a fixed set of
thirty rows, and [the journal prompts library](/en/journal-prompts/) lists them with the job each
one does — including the seven that exist only to turn a reflection into a dated prediction."""
AIJ_JP_LINK_UNLINKED = """The questions a follow-up is drawn from are not invented mid-conversation; they are a fixed set of
thirty rows, listed with the job each one does — including the seven that exist only to turn a
reflection into a dated prediction."""

MOOD_TAIL = """For the writing side, [self reflection that ends in evidence](/en/self-reflection/) covers what to
do with a low day once you have one."""
MOOD_GOAL_LINK = "see [the goal tracker](/en/goal-tracker/), where the follow-up is the feature."

SI_GT_LINK = """for the follow-up side — how the return date is scheduled and what it asks —
[the goal tracker](/en/goal-tracker/) is the page about that, and it deliberately does not repeat
this template."""
SI_GT_UNLINKED = """for the follow-up side — how the return date is scheduled and what it asks —
the follow-up screen is the thing that page describes, and it deliberately does not repeat
this template."""

JAP_TAIL = "in [the mood tracker page](/en/mood-tracker/) — trend, not verdict."

# ── 文档表锚点 ───────────────────────────────────────────────
ROW_JA = "| `journal-app` | 2 | 2 | 1 |"
ROW_SR = "| `self-reflection` | 2 | 4 | 1 |"
LEDGER_ROW = "| `web/index.html` | `#` | 5 |"

CASES: list[dict] = [
    # ── 图面：一条边的消失 ────────────────────────────────────
    dict(id="L1", targets=[T_LINK],
         desc="摘掉 ai-journal→journal-prompts 这条边（正文＋related 同摘）：入链从 2 掉到 1，孤儿判据必须红",
         patches=[(AIJ, AIJ_JP_LINK, AIJ_JP_LINK_UNLINKED),
                  (PJ, '      "related": ["journal-app", "self-reflection", "journal-prompts"],',
                       '      "related": ["journal-app", "self-reflection"],')],
         markers=["孤儿页（入链 < 2）"]),
    dict(id="L2", targets=[T_LINK],
         desc="把 mood-tracker 的一条内链指到不存在的 /en/journal-club/（related 同步加上，别让集合判据抢戏）：断链判据必须红",
         patches=[(MOOD, MOOD_TAIL, MOOD_TAIL +
                   "\nFor the wording of the check-in itself, see [the journal club](/en/journal-club/)."),
                  (PJ, '      "related": ["goal-tracker", "self-reflection"],',
                       '      "related": ["goal-tracker", "self-reflection", "journal-club"],')],
         markers=["正文内链出现断链或自链"]),
    dict(id="L15", targets=[T_LINK],
         desc="正文里加一条指向自己页面的内链：入链不该由自链凑出来（§2 表会跟着漂，名片段取自链那条）",
         patches=[(MOOD, MOOD_TAIL, MOOD_TAIL +
                   "\nA second read of [this same page](/en/mood-tracker/) adds nothing.")],
         markers=["正文自链"]),
    dict(id="L3", targets=[T_LINK],
         desc="把 goal-tracker 的两条入边全改指别页（正文＋related 四处同改）：3 跳内不可达，可达性判据必须红",
         patches=[(MOOD, MOOD_GOAL_LINK,
                   "see [the 30-prompt library](/en/journal-prompts/), where the question comes from."),
                  (PJ, '      "related": ["goal-tracker", "self-reflection"],',
                       '      "related": ["self-reflection", "journal-prompts"],'),
                  (SI, SI_GT_LINK, SI_GT_UNLINKED),
                  (PJ, '      "related": ["goal-tracker", "self-reflection", "ai-journal"],',
                       '      "related": ["self-reflection", "ai-journal"],')],
         markers=["不可达："]),

    # ── CTA 面 ───────────────────────────────────────────────
    dict(id="L4", targets=[T_LINK],
         desc="把 CTA 文案改成「Download Now on the App Store」：没有商店链接时这就是假按钮（G09 同判）",
         patches=[(PJ, CTA_LABEL,
                   '      "label": "Download Now on the App Store",')],
         markers=["假按钮"]),
    dict(id="L5", targets=[T_LINK],
         desc="把 CTA 的 href 换成 apps.apple.com：台账验收明写「不指向占位商店链接」",
         patches=[(PJ, CTA_HREF,
                   '      "href": "https://apps.apple.com/app/closenai/id0000000000",')],
         markers=["指向商店域"]),
    dict(id="L6", targets=[T_LINK],
         desc="把 CTA 的锚点换成 #waitlist-cta（web/index.html 里没有这个 id）：站内路径在但锚点悬空",
         patches=[(PJ, CTA_HREF, '      "href": "https://closenai.app/#waitlist-cta",')],
         markers=["锚点 #waitlist-cta 在 index.html 里不存在"]),

    # ── 生成器面 ─────────────────────────────────────────────
    dict(id="L7", targets=[T_LINK],
         desc="把面包屑的 Home 改回 {domain}/en/（第七批首跑真实抓到的那个缺陷）：/en/ 既不是站点根也不是页面",
         patches=[(BUILDER, CRUMB_OLD, CRUMB_NEW)],
         markers=["不指向站点根"]),

    # ── 文档 ↔ 现算 的抄录面 ─────────────────────────────────
    dict(id="L8", targets=[T_LINK],
         desc="把 §2 表里 self-reflection 的入链从 4 抄成 5：人写的表漂一格也必须红（图没动）",
         patches=[(DOC, ROW_SR, "| `self-reflection` | 2 | 5 | 1 |")],
         markers=["入链：文档 5 / 现算 4"]),
    dict(id="L9", targets=[T_LINK],
         desc="把 §5 在册条数从 5 抄成 4：条数也是抄录关系，少一条＝修好了不注销",
         patches=[(DOC, LEDGER_ROW, "| `web/index.html` | `#` | 4 |")],
         markers=["条数：现算 5 / 在册 4"]),
    dict(id="L10", targets=[T_LINK],
         desc="删掉 §5 那一行（在册表变空）：归零闸缺正向证人——「0 条新坏」这句话从此谁都拦不住",
         patches=[(DOC, LEDGER_ROW + """ `:463`、`:708` 两处 logo；`:731` 更新日志；`:732` 博客；`:733` X / Twitter | PLAT（占位导航未接线） | 裸 `href="#"`：点了不回任何东西 |

""", "")],
         markers=["归零闸缺证人"]),

    # ── 判据自身 ─────────────────────────────────────────────
    dict(id="L11", targets=[T_LINK],
         desc="把爬虫的 INBOUND_MIN 从 2 放宽到 0：另一侧从台账 §KG-17 现读，常量单独放宽必须红",
         patches=[(GATE, "INBOUND_MIN = 2", "INBOUND_MIN = 0")],
         markers=["有人为了让闸绿改了常量"]),
    dict(id="L12", targets=[T_LINK],
         desc="只往 goal-tracker 的 related 里加一个真页面（正文不加）：Related 段说三页、正文只链两页就是假同源",
         patches=[(PJ, '      "related": ["self-improvement", "mood-tracker"],',
                       '      "related": ["self-improvement", "mood-tracker", "journal-prompts"],')],
         markers=["只在 JSON"]),
    dict(id="L13", targets=[T_LINK],
         desc="把未发布的 /en/ai-journal/ 塞进现网 web/sitemap.xml：等于替 KG-02 提前提交索引",
         patches=[(SITEMAP, "</urlset>",
                   '  <url>\n    <loc>https://closenai.app/en/ai-journal/</loc>\n  </url>\n</urlset>')],
         markers=["未发布的支柱页出现在现网 sitemap"]),
    dict(id="L14", targets=[T_LINK],
         desc="往 web/robots.txt 加一条 Disallow: /en/：内容源七页全在 /en/ 下，发布那天整簇被挡",
         patches=[(ROBOTS, "Disallow: /api/", "Disallow: /api/\nDisallow: /en/")],
         markers=["把 /en/ 挡了"]),

    # ── 绿闸：正确的改动必须放行 ─────────────────────────────
    dict(id="GN1", kind="green", targets=[T_LINK],
         desc="journal-app 加一条真入链（正文＋related＋§2 表两处同改）：判据拦的是「漂」，不是「改」",
         patches=[(JAP, JAP_TAIL, JAP_TAIL +
                   "\nFor what a written entry is supposed to end in, [self reflection that ends in "
                   "evidence](/en/self-reflection/) states the standard once."),
                  (PJ, '      "related": ["ai-journal", "mood-tracker"],',
                       '      "related": ["ai-journal", "mood-tracker", "self-reflection"],'),
                  (DOC, ROW_JA, "| `journal-app` | 3 | 2 | 1 |"),
                  (DOC, ROW_SR, "| `self-reflection` | 2 | 5 | 1 |")],
         markers=[]),
    dict(id="GN2", kind="green", targets=[T_LINK],
         desc="在 web/index.html 再加一个指向 #cta 的合法按钮：锚点解析得过的链接不许被算成 offender",
         patches=[(INDEX, '        <a href="#cta" class="btn btn-cta">加入内测名单</a>',
                   '        <a href="#cta" class="btn btn-cta">加入内测名单</a>\n'
                   '        <a href="#cta" class="btn btn-cta">加入内测名单</a>')],
         markers=[]),
    dict(id="GN3", kind="green", targets=[T_LINK, T_GATE],
         desc="把 CTA 的说明写得更长（仍不是下载按钮）：G09 判的是动作真假，不是字数",
         patches=[(PJ, CTA_NOTE + ' The list gets one invitation and one follow-up when a goal you wrote reaches its date."',
                   CTA_NOTE + ' The list gets one invitation and one follow-up when a goal you wrote '
                   'reaches its date, and nothing else — no content push."')],
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
    for path, old, new in case["patches"]:
        patch_text(path, old, new)


def main() -> int:
    ap = argparse.ArgumentParser(description="KG-17 内链/CTA/在册表闸的变异自证")
    ap.add_argument("--check", action="store_true", help="只验锚点命中数")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    ap.add_argument("--no-baseline", action="store_true", help="跳过基线（只在排障时用）")
    args = ap.parse_args()

    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}
    cases = [c for c in CASES if not wanted or c["id"].upper() in wanted]

    if args.check:
        bad = 0
        for case in cases:
            for path, old, _ in case["patches"]:
                n = path.read_text(encoding="utf-8").count(old)
                if n != 1:
                    print(f"坏锚点 {case['id']}：{path.name} 命中 {n} 次 ⇒ {old[:60]!r}")
                    bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 处有问题'}（共 {len(cases)} 条用例）")
        return 1 if bad else 0

    if not args.no_baseline:
        print("── 基线 ──")
        for targets, label in (([T_LINK], "KG-17 内链/CTA/在册表闸"), ([T_GATE], "页面源闸＋生成器")):
            code, out = run_pytest(targets)
            tail = [ln for ln in out.splitlines() if "passed" in ln or "failed" in ln]
            mark = "✓" if code == 0 else "✗ 基线不干净，先修再跑变异"
            print(f"  {mark}  {label}  {tail[-1] if tail else out[-300:]}")
            if code != 0:
                return 1

    tmp = Path(tempfile.mkdtemp(prefix="mut-kg17-"))
    originals: dict[Path, Path] = {}
    for i, case in enumerate(cases):
        for p, _, _ in case["patches"]:
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
            green = case.get("kind") == "green"
            snapshot = copy.deepcopy(case)
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
                        print("  " + "\n  ".join(out.splitlines()[-14:]))
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

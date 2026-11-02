"""KG-17 的内链/CTA/孤儿页爬虫闸（2026-11-03，批次 8）。

**这张闸判什么**：台账 §KG-17（`docs/keyword-growth-task-board-2026-11-01.md` 的
`#### KG-17 · 内链与 CTA 规范` 小节）验收那四句
——「爬虫报告：0 断链、0 孤儿页；每个支柱页至少 2 条入链」
「所有 CTA 指向真实下载/等待名单，不指向占位商店链接」「建立面包屑、导航、页脚」
「每季度跑一次断链与孤儿页检查」——全部改成脚本判，不留人眼。
**小节按标题现读**（`:170` 那条判据就是这么找的 `INBOUND_MIN`），所以台账插行不会让这里失准。

**判据分两面**（`scripts/check_site_links.py` 的模块 docstring 是同一条口径的第二处表述）：
支柱面硬红；现网面**只与 `docs/seo/internal-links-2026-11.md` §5 的在册表逐条对拉**，
不替 KG-23/KG-02 的文案面遮丑，也不许悄悄多坏一条。

**为什么断言两侧不许同源**：`:189` 这条不叫爬虫复述自己的返回值，
而是直接读 `web/index.html` 的正则结果——爬虫若把锚点判据写错，这里就红。
`:137` 同理：把现算的图与人写的 §2 表对拉，表漂了红、图漂了红，两边都改才绿。
`:205` 反过来给归零闸配正向证人：在册表**必须非空**，
否则「现网面 0 条新坏」这句话是爬虫换了口径也照样绿的那种空话；`:226` 把「这条闸还能被打红」钉进 CI。
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
#: `check_site_links.py` 在函数里 `import build_seo_pages`（独立跑时 `scripts/` 天然在 sys.path[0]）。
#: 从 pytest 里加载它就没有这一条，所以先把 `scripts/` 挂上——否则 crawl_links() 直接 ImportError，
#: 而那条 ImportError 会被读成「闸没判出缺陷」以外的东西，很难查。
sys.path.insert(0, str(REPO / "scripts"))
PAGES_JSON = REPO / "site-content" / "pages.json"
LEDGER_MD = REPO / "docs" / "seo" / "internal-links-2026-11.md"
BOARD_MD = REPO / "docs" / "keyword-growth-task-board-2026-11-01.md"
GATE_PY = REPO / "scripts" / "check_site_links.py"
WEB = REPO / "web"


def _load_gate():
    spec = importlib.util.spec_from_file_location("check_site_links", GATE_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


L = _load_gate()
BUILDER = REPO / "scripts" / "build_seo_pages.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_seo_pages", BUILDER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


B = _load_builder()
PAYLOAD = json.loads(PAGES_JSON.read_text(encoding="utf-8"))
PAGES = {p["slug"]: p for p in PAYLOAD["pages"]}
SLUGS = sorted(PAGES)
GRAPH = L.crawl_links(PAYLOAD)
LEDGER_DOC = LEDGER_MD.read_text(encoding="utf-8") if LEDGER_MD.is_file() else ""


def _md_links(page: dict) -> set[str]:
    body = (REPO / page["body_md"]).read_text(encoding="utf-8")
    return {m.group(1) for m in
            re.finditer(r"\]\(/en/([a-z0-9-]+)/\)", body)} - {page["slug"]}


# ---------- 支柱面：台账验收的四句，逐句一条 ----------

def test_zero_broken_links_in_the_pillar_graph() -> None:
    """§KG-17 验收「0 断链」：正文内链的目标必须都在内容源页面集合里，且不许自链。"""
    assert GRAPH["broken"] == [], "正文内链出现断链或自链：" + "；".join(GRAPH["broken"])
    assert set(GRAPH["links"]) == set(SLUGS), "爬虫看到的页面集合与内容源不一致"


def test_every_pillar_page_has_the_boarded_minimum_of_inbound_links() -> None:
    """§KG-17 验收「每个支柱页至少 2 条入链」——入链只数别的页面**正文**里的内链。

    这条是本闸存在的理由：出链一眼看得见，孤儿只有算入链才看得出来。
    判据本身能被 `--self-test` 打红（`:226` 把它钉进 CI）。
    """
    low = {s: n for s, n in GRAPH["inbound"].items() if n < L.INBOUND_MIN}
    assert not low, f"孤儿页（入链 < {L.INBOUND_MIN}）：{low}"
    assert sum(GRAPH["inbound"].values()) == GRAPH["edges"], (
        "入链总和与出边总数不等 ⇒ 有边的目标不在页面集合里，孤儿判据在漏算")


def test_every_page_is_reachable_from_the_hub_within_three_hops() -> None:
    """§KG-17 验收「面包屑与导航可达」：从 hub 走 `HOPS_MAX` 跳必须覆盖全部页面。"""
    assert GRAPH["far"] == [], (
        f"从 `{L.HUB_SLUG}` 走 {L.HOPS_MAX} 跳到不了：{GRAPH['far']}")
    assert GRAPH["hops"].get(L.HUB_SLUG) == 0, "hub 自己不是 0 跳，BFS 起点写错了"


def test_cta_points_at_a_real_action_and_no_fake_store_button() -> None:
    """§KG-17 验收「CTA 指向真实下载/等待名单，不指向占位商店链接」。

    现网面**没有**商店链接，所以合法动作只有等待名单一个；出现第二个动作名就是假按钮。
    """
    assert set(PAYLOAD["cta_definitions"]) == {"waitlist"}, (
        f"CTA 定义面该只有等待名单：{sorted(PAYLOAD['cta_definitions'])}")
    assert GRAPH["cta"] == [], "生成物 CTA 面不合格：" + "；".join(GRAPH["cta"])
    for slug, page in PAGES.items():
        href = PAYLOAD["cta_definitions"][page["cta"]]["href"]
        assert href.startswith(PAYLOAD["domain"]), f"{slug} 的 CTA 跳出品牌域：{href}"


def test_breadcrumb_home_resolves_and_no_page_links_to_the_unbuilt_language_root() -> None:
    """§KG-17「建立面包屑」不只是有没有 `<nav>`：首项必须解析得到，且不许指向 `/en/`。

    `/en/` 既不是站点根也不是任何 slug ⇒ 7 页各一条断链（首跑实测抓到，生成器改在
    `scripts/build_seo_pages.py:530`）。这里重新解析生成物，不信 `crawl_links()` 的判定。
    """
    domain = PAYLOAD["domain"].rstrip("/")
    for slug in SLUGS:
        html = B.page_html(PAGES[slug], PAYLOAD)
        crumb = re.search(r'aria-label="Breadcrumb"><a href="([^"]+)"', html)
        assert crumb, f"{slug} 的生成物没有可点的面包屑首项"
        assert crumb.group(1) == f"{domain}/", (
            f"{slug} 的面包屑首项 {crumb.group(1)!r} 不指向站点根——现网只有 web/index.html 是根")
        assert f'href="{domain}/en/"' not in html and 'href="/en/"' not in html, (
            f"{slug} 链到 /en/ 这个不存在的语言根（hreflang 只有 en，没有 /en/ 索引页）")
        for href in re.findall(r'href="(?:https?://closenai\.app)?/en/([^/"]*)/"', html):
            assert href in PAGES, f"{slug} 的面包屑/Related 段链到未知页面：/en/{href}/"


# ---------- 两侧不同源：爬虫 vs 人写的文档 ----------

def test_the_documented_graph_table_equals_the_live_counts() -> None:
    """把 §KG-17 结案文档 §2 那张表与爬虫现算的出/入链、跳数逐格对拉。

    表是人抄的，图是算的——任一侧漂了就红。这是「随页面落地数字会漂」唯一的解法。
    """
    rows = re.findall(r"^\| `([a-z0-9-]+)` \| (\d+) \| (\d+) \| (\d+) \|$",
                      LEDGER_DOC, re.M)
    assert len(rows) == len(SLUGS), (
        f"§2 表解析到 {len(rows)} 行，内容源有 {len(SLUGS)} 页（行数不符＝表没跟着页面走）")
    for slug, out_n, in_n, hop_n in rows:
        assert slug in PAGES, f"§2 表里有内容源不存在的页面：{slug}"
        assert int(out_n) == len(GRAPH["links"][slug]), (
            f"{slug} 出链：文档 {out_n} / 现算 {len(GRAPH['links'][slug])}")
        assert int(in_n) == GRAPH["inbound"][slug], (
            f"{slug} 入链：文档 {in_n} / 现算 {GRAPH['inbound'][slug]}")
        assert int(hop_n) == GRAPH["hops"][slug], (
            f"{slug} 距 hub：文档 {hop_n} / 现算 {GRAPH['hops'][slug]}")


def test_the_ledger_table_and_the_crawler_agree_entry_by_entry() -> None:
    """现网面：爬虫现算的 offender 与文档 §5 的在册表**必须同键同条数**。

    多了＝新坏没登记就上线；少了＝修好了不注销，读者以为还在坏。
    这一条同时是 §5「修完删一行」那句的执行机制。
    """
    offenders = L.web_offenders()
    ledger = L.ledger_entries()
    assert set(offenders) == set(ledger), (
        f"新坏未登记 {sorted(set(offenders) - set(ledger))} / 已不坏却没注销 {sorted(set(ledger) - set(offenders))}")
    for key, n in offenders.items():
        assert ledger[key] == n, f"{key} 条数：现算 {n} / 在册 {ledger[key]}"


def test_the_inbound_gate_is_numbered_from_the_board_not_from_itself() -> None:
    """`INBOUND_MIN` 与 `HOPS_MAX` 必须等于台账那句话里的数字——不许靠调常量变绿。

    另一侧从 `docs/keyword-growth-task-board-2026-11-01.md` §KG-17 现读，不是这里的字面量。
    """
    board = BOARD_MD.read_text(encoding="utf-8")
    section = board.split("#### KG-17 · 内链与 CTA 规范", 1)[1].split("#### KG-18", 1)[0]
    promised = re.search(r"每个支柱页至少 (\d+) 条入链", section)
    assert promised, "台账 §KG-17 验收里那句「每个支柱页至少 N 条入链」不见了"
    assert int(promised.group(1)) == L.INBOUND_MIN, (
        f"台账说 {promised.group(1)} 条，常量写 {L.INBOUND_MIN} ⇒ 有人为了让闸绿改了常量")
    assert re.search(r"每页的 2[–\-]3 条相关内链", section), "台账「2–3 条相关内链」的口径漂了"
    for slug, page in PAGES.items():
        assert 2 <= len(page["related"]) <= 3, f"{slug} 的 related 条数跳出 2–3：{page['related']}"
        assert set(page["related"]) == _md_links(page), (
            f"{slug} 的 related（JSON）与正文内链（MD）不是同一组："
            f"只在 JSON {set(page['related']) - _md_links(page)} / 只在正文 {_md_links(page) - set(page['related'])}")


def test_the_cta_anchor_exists_in_the_live_file_read_here_not_by_the_crawler() -> None:
    """CTA 的 `#cta` 必须真的在 `web/index.html` 里——本条自己正则读，不走爬虫。

    「爬虫说锚点存在」和「锚点存在」不是一回事（断言两侧不许同源）。
    """
    href = PAYLOAD["cta_definitions"]["waitlist"]["href"]
    frag = href.split("#", 1)[1] if "#" in href else ""
    assert frag, f"CTA 没有锚点，等待名单那一段靠什么定位：{href}"
    html = (WEB / "index.html").read_text(encoding="utf-8")
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    assert frag in ids, f"web/index.html 里没有 id=\"{frag}\" ⇒ CTA 是悬空锚点"
    assert "final-cta" in html, "等待名单区块的 class 名改了，锚点判据要跟着改"


# ---------- 归零闸的正向证人 ----------

def test_the_web_face_ledger_has_a_witness_and_the_gate_reads_no_ignored_dir() -> None:
    """§5 在册表**必须非空**，且爬虫判的 offender 也必须非空——两边都得有东西。

    为什么需要这条：「现网面 0 条新坏」这句话，在爬虫口径被改掉、或 §5 被清空时同样成立。
    正向证人＝此刻真的数得出 5 条裸 `href="#"`（`web/index.html:463`、`:708`、`:731`、`:732`、`:733`）。
    顺带钉住第二条实现约束：闸不许读被 `.gitignore` 排除的 `docs/content/build/`，
    否则判据变成「本机跑过一次才有」的假绿。
    """
    offenders = L.web_offenders()
    ledger = L.ledger_entries()
    assert offenders, "现网面一条 offender 都扫不出来 ⇒ 爬虫口径失效或 web/ 不在了"
    assert ledger, "§5 在册表是空的——归零闸缺证人"
    assert sum(offenders.values()) == sum(ledger.values()) == 5, (
        f"裸锚总数：现算 {sum(offenders.values())} / 在册 {sum(ledger.values())}（证人要求恰好 5）")
    src = GATE_PY.read_text(encoding="utf-8")
    code = src.split('"""', 2)[2]          # 模块 docstring 里那句「不读它」是说明，不是代码
    body = "\n".join(l for l in code.splitlines() if not l.strip().startswith("#"))
    assert "content/build" not in body, "闸不许读 gitignore 掉的草稿目录，生成物须在内存里现生成"
    assert "import build_seo_pages" in body, "闸不再复用生成器 ⇒ 判的和部署的可能不是同一份 HTML"


def test_the_orphan_gate_can_still_be_turned_red() -> None:
    """把 `--self-test` 的两条证人钉进 CI：清零入链必判孤儿、清空 §5 必判失效。

    没有这条，「0 孤儿页」永远只是此刻的真话；有了它，判据失效（有人把下限改成 0、
    或把 §5 标题改掉）本身就是红。
    """
    assert L.main(["--self-test"]) == 0, (
        "--self-test 非 0：要么基线已不干净，要么孤儿/在册两条判据打不红了")


def test_the_live_sitemap_and_robots_do_not_preempt_the_publish_gate() -> None:
    """§KG-17 验收「与 `T7-08` 的技术 SEO 底座不冲突」：草稿面不许先从现网面漏出去。

    两侧不同源：一侧是内容源的页面集合，另一侧是**现网**`web/sitemap.xml` 与 `web/robots.txt`。
    未发布的支柱页一旦进了现网 sitemap，等于替 `KG-02`（法务）提前提交索引；
    robots 若 Disallow `/en/`，则发布那天整簇页面一起被挡在抓取之外。
    """
    assert GRAPH["sitemap"] == [], "sitemap 面不合格：" + "；".join(GRAPH["sitemap"])
    assert GRAPH["robots"] == [], "robots 面不合格：" + "；".join(GRAPH["robots"])
    live = (WEB / "sitemap.xml").read_text(encoding="utf-8")
    for slug in SLUGS:
        assert f"/en/{slug}/" not in live, f"现网 sitemap 泄漏了未发布页面：{slug}"


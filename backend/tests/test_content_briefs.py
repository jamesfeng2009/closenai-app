"""KG-09 四份内容简报的闸（2026-11-02，批次 4）。

**为什么要有这张闸**：简报是「写什么、凭什么写」的唯一中间层——它上游接方案 §8.2 的页面—意图表，
下游接页面草稿与 `site-content/pages.json`。这类文档最常见的失败不是写得少，而是**抄漂**：
页面清单与方案不一致、H1 的字符数是手填的、证据指的行号根本不存在、提纲里的差异化段落换了个说法。
本文件把这些全部改成现算对拉（两侧不同源）：简报文本 ↔ 方案表 ↔ 台账 §1 ↔ ASO 禁语与竞品清单 ↔ 仓内真行。

**判据面**：
①四份文件在册且八节齐全按序（`:113`）；②目标 URL 必须是方案 §8.2 的 12 页且不重不漏（`:123`、`:131`）；
③任务号必须真在台账 §1（`:150`）；④H1 与 §2 同集合（`:164`）、唯一、≤70 字符、不含禁语与竞品词（`:171`）、
字符数现算（`:191`）；⑤每条证据的来源打得到真行且非空，外部来源必须 https＋日期（`:202`）；
⑥法务边界按簇内页面点名（`:231`）；⑦提纲唯一、目标页在本簇内、含方案那行的差异化段落原词、≥6 个 H2（`:248`）；
⑧「本轮不做」明写不部署与不动 sitemap（`:264`）；⑨四簇名与条数＝KG-26 提示库的 `cluster` 现算（`:284`）。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BRIEF_DIR = REPO / "docs" / "content" / "briefs-2026-11"
PLAN = REPO / "docs" / "keyword-growth-implementation-plan-2026-11-01.md"
BOARD = REPO / "docs" / "keyword-growth-task-board-2026-11-01.md"
ASO_JSON = REPO / "docs" / "aso" / "en-US-2026-11.json"

BRIEFS = [
    "reflection-method.md",
    "long-term-self-understanding.md",
    "action-outcome-review.md",
    "ai-insight-credibility.md",
]

SECTIONS = [
    "## 1. 搜索意图",
    "## 2. 目标 URL",
    "## 3. H1",
    "## 4. 证据",
    "## 5. 内链与 CTA",
    "## 6. 法务边界",
    "## 7. 支柱页提纲（先写 1 篇）",
    "## 8. 本轮不做",
]

#: H1 的长度上限：比 Google 建议的标题长度再宽一档，但禁止把 H1 写成关键词堆叠（G01）
H1_MAX = 70

FULL_CITE = re.compile(
    r"(?P<path>(?:docs|backend|scripts|frontend|site|web|\.github)/[^`\s:（）、]+):(?P<line>\d+)"
)


def _spec(name: str, rel: str):
    path = REPO / rel
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


aso = _spec("aso_copy_for_briefs", "scripts/check_aso_copy.py")

import json  # noqa: E402

BRANDS = [b.lower() for b in json.loads(ASO_JSON.read_text(encoding="utf-8"))["competitor_brands"]]
BANNED = aso.BANNED_PATTERNS

BOARD_IDS = set(re.findall(r"^\| (KG-\d{2}) \|", BOARD.read_text(encoding="utf-8"), re.M))


def _plan_pages() -> dict[str, dict[str, str]]:
    """方案 §8.2 的页面—意图表：slug → {意图, 必须回答, 差异化段落}。"""
    out: dict[str, dict[str, str]] = {}
    for line in PLAN.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| `/en/([^`]+)` \| (.+?) \| (.+?) \| (.+?) \|$", line.strip())
        if m:
            out[m.group(1)] = {"intent": m.group(2), "must": m.group(3), "diff": m.group(4)}
    assert len(out) == 12, f"方案 §8.2 解析到 {len(out)} 页，应当是 12 页 ⇒ 表结构漂了"
    return out


PLAN_PAGES = _plan_pages()


def _text(name: str) -> str:
    path = BRIEF_DIR / name
    assert path.is_file(), f"简报缺失：{path}"
    return path.read_text(encoding="utf-8")


def _section(text: str, start: str, stop: str) -> str:
    return text.split(start, 1)[1].split(stop, 1)[0]


def _table_rows(section: str) -> list[list[str]]:
    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("| `"):
            continue
        rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


IDS = pytest.mark.parametrize("name", BRIEFS)


@IDS
def test_the_four_briefs_have_all_eight_sections(name: str) -> None:
    """四份在册＋八节齐全且按序：少一节或改标题即红（正向证人：文件数恰好 4）。"""
    assert len(BRIEFS) == 4, f"简报应当是 4 份（台账 §KG-09 的验收原文），当前清单 {len(BRIEFS)} 份"
    text = _text(name)
    pos = [text.find(sec) for sec in SECTIONS]
    assert -1 not in pos, f"{name} 缺节：{[s for s, p in zip(SECTIONS, pos) if p < 0]}"
    assert pos == sorted(pos), f"{name} 的八节顺序漂了：{pos}"


@IDS
def test_target_urls_match_the_plan_exactly(name: str) -> None:
    """§2 的 slug 必须是方案 §8.2 表里真有的页（不新造页面，也不把别簇的页抢过来）。"""
    rows = _table_rows(_section(_text(name), SECTIONS[1], SECTIONS[2]))
    assert rows, f"{name} 的 §2 没解析到目标 URL 行"
    unknown = [c[0].strip("`") for c in rows if c[0].strip("`") not in PLAN_PAGES]
    assert not unknown, f"{name} 写了方案 §8.2 里没有的页面：{unknown}"


def test_the_four_briefs_partition_the_twelve_plan_pages() -> None:
    """四份简报的目标 URL 并集＝方案 12 页，且互不重复（一簇抢两页＝分工漂了）。"""
    seen: dict[str, str] = {}
    for name in BRIEFS:
        for cells in _brief_url_rows(name):
            slug = cells[0].strip("`")
            assert slug not in seen, f"{slug} 同时挂在 {seen.get(slug)} 与 {name} 两簇"
            seen[slug] = name
    assert set(seen) == set(PLAN_PAGES), (
        f"简报覆盖与方案 §8.2 不符：多 {sorted(set(seen) - set(PLAN_PAGES))} "
        f"缺 {sorted(set(PLAN_PAGES) - set(seen))}"
    )


def _brief_url_rows(name: str) -> list[list[str]]:
    return _table_rows(_section(_text(name), SECTIONS[1], SECTIONS[2]))


@IDS
def test_each_row_names_a_real_board_task(name: str) -> None:
    """§2 的「任务」列必须是台账 §1 真存在的 KG 号——简报不许指向不存在的任务。"""
    for cells in _brief_url_rows(name):
        assert len(cells) == 5, f"{name} 的 §2 行是 {len(cells)} 列，契约是 5 列：{cells}"
        task = cells[4]
        assert task in BOARD_IDS, f"{name} 的 {cells[0]} 挂在不存在的任务 {task!r}（台账 §1 没有）"


def _h1_rows(name: str) -> list[tuple[str, str, str]]:
    return [(c[0].strip("`"), c[1].strip("`"), c[2])
            for c in _table_rows(_section(_text(name), SECTIONS[2], SECTIONS[3]))]


@IDS
def test_h1_rows_are_unique_within_the_brief(name: str) -> None:
    """§3 的页集合必须与 §2 完全相同：漏一页＝那页没有 H1，多一页＝H1 挂在不存在的页上。"""
    urls = {c[0].strip("`") for c in _brief_url_rows(name)}
    h1s = {slug for slug, _t, _n in _h1_rows(name)}
    assert urls == h1s, f"{name} 的 §2 与 §3 页面集合不同：§2 独有 {sorted(urls - h1s)}／§3 独有 {sorted(h1s - urls)}"


def test_every_h1_is_short_unique_and_free_of_banned_words() -> None:
    """跨四份合看：H1 不许重复（同站两页抢一句）、不许超长、不许含禁语或竞品词（除对比页）。"""
    all_h1: dict[str, str] = {}
    total = 0
    for name in BRIEFS:
        for slug, text, _n in _h1_rows(name):
            total += 1
            assert len(text) <= H1_MAX, f"{slug} 的 H1 有 {len(text)} 字符（上限 {H1_MAX}）"
            low = text.lower()
            for label, pattern in BANNED:
                assert not pattern.search(text), f"{slug} 的 H1 命中禁语 {label}：{text!r}"
            if not slug.startswith("compare/"):
                hit = [b for b in BRANDS if b in low]
                assert not hit, f"{slug} 不是对比页却含竞品品牌词 {hit}（R3）"
            assert text not in all_h1, f"{text!r} 同时是 {all_h1.get(text)} 与 {slug} 的 H1"
            all_h1[text] = slug
    assert total == 12, f"四份简报合起来应有 12 条 H1（方案 §8.2），现算 {total}"


@IDS
def test_h1_char_counts_are_recomputed_not_hand_written(name: str) -> None:
    """§3 的「字符数」列由 H1 文本现算再比——手填错一位就红。"""
    for slug, text, declared in _h1_rows(name):
        m = re.match(r"(\d+)", declared)
        assert m, f"{name} 的 {slug} 字符数列不是数字：{declared!r}"
        assert int(m.group(1)) == len(text), (
            f"{name} 的 {slug}：§3 写 {m.group(1)}，按 H1 文本现算 {len(text)} ⇒ 手填的数字漂了"
        )


@IDS
def test_every_evidence_source_reaches_a_real_line_or_a_dated_url(name: str) -> None:
    """§4 每条证据：仓内 `路径:行` 必须打到真文件的非空真行；外部来源必须 https＋带日期。"""
    section = _section(_text(name), SECTIONS[3], SECTIONS[4])
    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("| "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == 4 and re.fullmatch(r"\d+", cells[0]):
            rows.append(cells)
    assert len(rows) >= 3, f"{name} 的 §4 只解析到 {len(rows)} 条证据，验收要求每条主张都有来源"
    for idx, _claim, source, date in rows:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date), f"{name} §4 第 {idx} 条的日期不是 YYYY-MM-DD：{date!r}"
        cites = FULL_CITE.findall(source)
        urls = re.findall(r"https?://\S+", source)
        assert cites or urls, f"{name} §4 第 {idx} 条没有可核验来源：{source!r}"
        for rel, line_s in cites:
            path = REPO / rel
            assert path.is_file(), f"{name} §4 第 {idx} 条引用 {rel}，文件不存在"
            lines = path.read_text(encoding="utf-8").splitlines()
            n = int(line_s)
            assert 1 <= n <= len(lines), f"{name} §4 第 {idx} 条引用 {rel}:{n}，该文件只有 {len(lines)} 行"
            assert lines[n - 1].strip(), f"{name} §4 第 {idx} 条引用 {rel}:{n}，那一行是空的 ⇒ 行号漂了"
        for url in urls:
            assert url.startswith("https://"), f"{name} §4 第 {idx} 条的外部来源不是 https：{url}"


@IDS
def test_legal_boundaries_are_named_per_cluster(name: str) -> None:
    """§6 不是套话：簇里有什么页，就必须点到什么边界（提纲与正文之后没人替法务复核）。"""
    legal = _section(_text(name), SECTIONS[5], SECTIONS[6])
    slugs = {c[0].strip("`") for c in _brief_url_rows(name)}
    assert "check_aso_copy" in legal or "禁语" in legal, f"{name} 的 §6 没指回禁语清单"
    if {"mood-tracker", "overthinking"} & slugs:
        assert "医疗" in legal or "诊断" in legal, f"{name} 含情绪／反刍页却没写非医疗边界（G05）"
    if "personality-test" in slugs:
        assert "商标" in legal, f"{name} 含人格框架页却没写商标边界（KG-02 未决项）"
    if any(s.startswith("compare/") for s in slugs):
        assert "可核验" in legal or "核验" in legal, f"{name} 含对比页却没写「只写可核验差异」"
        assert "KG-16" in _text(name), f"{name} 的对比页没挂 KG-16"
    if {"self-discovery", "self-reflection", "mood-tracker", "ai-journal"} & slugs:
        assert "危机" in legal, f"{name} 含会触及自伤表述的页面，却没写危机段落的前置规则"


@IDS
def test_the_outline_targets_a_page_in_this_cluster(name: str) -> None:
    """§7 每份简报只有一个提纲；提纲目标页必须在本簇 §2 里；且必须出现方案那一行的「差异化段落」原词。"""
    outline = _section(_text(name), SECTIONS[6], SECTIONS[7])
    m = re.findall(r"\*\*本篇给 `([^`]+)`\*\*", outline)
    assert len(m) == 1, f"{name} 的 §7 应当只有 1 篇提纲，找到 {len(m)} 篇：{m}"
    slug = m[0]
    urls = {c[0].strip("`") for c in _brief_url_rows(name)}
    assert slug in urls, f"{name} 的提纲给了 {slug}，但 §2 里没有这页"
    diff = PLAN_PAGES[slug]["diff"]
    assert diff in outline, f"{name} 的提纲缺方案 §8.2 要求 {slug} 写的差异化段落「{diff}」"
    h2 = re.findall(r"^- H2 \d+｜", outline, re.M)
    assert len(h2) >= 6, f"{name} 的提纲只有 {len(h2)} 个 H2，支柱页提纲至少 6 段"
    assert "等待名单" in outline or "CTA" in outline, f"{name} 的提纲没写 CTA 落点（G09）"


@IDS
def test_what_we_do_not_do_says_no_deploy(name: str) -> None:
    """§8 必须明写「不生成/不部署」，否则读者会把简报当成已上线页面（发布闸仍 BLOCKED）。"""
    tail = _text(name).split(SECTIONS[7], 1)[1]
    assert "不部署" in tail, f"{name} 的 §8 没写「不部署」：{tail[:80]!r}"
    assert "sitemap" in tail, f"{name} 的 §8 没说明不动 sitemap"
    text = _text(name)
    assert "KG-02" in text and "T7-05" in text, f"{name} 没点明发布闸是 KG-02 与 T7-05"


#: 简报的四簇**不是新造的**：KG-26 的提示库已经按这四个簇分好了（`cluster` 字段），
#: 内容簇与产品簇一旦漂开，页面就会承诺一种产品里没有的组织方式。
PROMPTS_JSON = REPO / "backend" / "content" / "reflection_prompts.json"
CLUSTER_OF_BRIEF = {
    "reflection-method.md": "reflection_method",
    "long-term-self-understanding.md": "long_term_understanding",
    "action-outcome-review.md": "action_outcome",
    "ai-insight-credibility.md": "ai_credibility",
}


def test_the_four_clusters_are_the_prompt_library_ones() -> None:
    """四份简报＝提示库的四个簇，且每簇条数与库一致（两侧不同源：文档 ↔ JSON 现算）。"""
    raw = PROMPTS_JSON.read_text(encoding="utf-8")
    counts: dict[str, int] = {}
    for cluster in re.findall(r'"cluster": "([a-z_]+)"', raw):
        counts[cluster] = counts.get(cluster, 0) + 1
    assert len(counts) == 4, f"提示库解析到 {len(counts)} 个簇，简报是四簇制：{sorted(counts)}"
    assert sum(counts.values()) == 30, f"提示库总数现算 {sum(counts.values())}，验收口径是 30 条"
    expect = {CLUSTER_OF_BRIEF[b] for b in BRIEFS}
    assert expect == set(counts), f"简报簇名与库里的 `cluster` 不同源：简报 {sorted(expect)}／库 {sorted(counts)}"
    assert sorted(counts.values()) == [7, 7, 8, 8], f"提示库的簇分布现算 {sorted(counts.values())}，简报按 8-8-7-7 写"
    for brief, cluster in CLUSTER_OF_BRIEF.items():
        assert f"`cluster: {cluster}`" in _text(brief), (
            f"{brief} 的头部没写明它对应提示库的哪个簇 ⇒ 文档与闸的映射两面上，改文档不会红"
        )


def test_the_docstring_cites_point_at_this_files_own_tests() -> None:
    """本文件头部列的判据行号必须打到本文件真存在的 `def test_` 行（引用即自证，含闸自己）。"""
    lines = p_text().splitlines()
    cites = [int(n) for n in re.findall(r"`:(\d+)`", (__doc__ or ""))]
    assert len(cites) >= 8, f"docstring 只列了 {len(cites)} 条判据行号，覆盖面漂了"
    for n in cites:
        assert 1 <= n <= len(lines), f"docstring 引用 :{n}，本文件只有 {len(lines)} 行"
        assert lines[n - 1].startswith("def test_"), (
            f"docstring 引用 :{n}，那一行是 {lines[n - 1][:40]!r}，不是判据本体 ⇒ 行号漂了"
        )


def p_text() -> str:
    return Path(__file__).resolve().read_text(encoding="utf-8")

"""KG-10～KG-14 的页面源闸＋生成器端到端（2026-11-03，批次 7）。

**这张闸判什么**：`docs/keyword-content-gate.md` 的 G01–G14 里机械可判的那几项，
在**内容源**（`site-content/pages.json`）、**正文**（`docs/content/pages/*.en.md`）与
**生成物**（`scripts/build_seo_pages.py` 写到临时目录的 HTML）三面之间对拉。
任一面单独改都会红：正文换了 H1、JSON 没换 ⇒ 红；JSON 加了 related、正文没链 ⇒ 红；
生成器把 canonical 写错 ⇒ 红的不是它自己的返回值，是本文件重新解析 HTML 的那段。

**为什么不直接信脚本的返回值**（断言两侧不许同源）：
`:251` 那条判据只把 `violations()` 的返回再数一遍，等于让脚本自己给自己发合格证。
所以生成那一组判据一律**读写出来的 HTML 文本**重新算，与脚本的内部判定是两条独立路径。

**发布闸在场**：`:236` 钉的是「内容源登记了 `publish_blocked_by`、生成物带 `noindex`、
脚本拒绝写 `web/`」三件事同时成立。方案 §8.3 把 KG-02／T7-05 列为发布前置，
本批全部页面因此只到草稿面——不许被引用成「已上线／两页 200」。
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from html import unescape
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PAGES_JSON = REPO / "site-content" / "pages.json"
PLAN_MD = REPO / "docs" / "keyword-growth-implementation-plan-2026-11-01.md"
BOARD_MD = REPO / "docs" / "keyword-growth-task-board-2026-11-01.md"
BRIEF_DIR = REPO / "docs" / "content" / "briefs-2026-11"
GATE_MD = REPO / "docs" / "keyword-content-gate.md"
AUDIT_MD = REPO / "docs" / "keyword-claims-audit-2026-11.md"
BUILDER = REPO / "scripts" / "build_seo_pages.py"

PROMPT_MARKER = "<!--prompt-library-->"
DOMAIN = "https://closenai.app"


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


def _body(page: dict) -> str:
    return (REPO / page["body_md"]).read_text(encoding="utf-8")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _prose(body: str) -> list[str]:
    return [
        line.strip()
        for line in body.splitlines()
        if line.strip() and not re.match(r"^([#|>]|\d+\. |[-*] )", line.strip())
    ]


def _plan_82_slugs() -> set[str]:
    section = PLAN_MD.read_text(encoding="utf-8").split("### 8.2 页面与意图", 1)[1]
    table = section.split("### 8.3", 1)[0]
    out = set()
    for line in table.splitlines():
        for cell in re.findall(r"`(/en/[^`]+)`", line):
            out.add(cell[len("/en/"):].strip("/"))
    return out


def _board_row_for(task: str) -> list[str]:
    for line in BOARD_MD.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {task} |"):
            return [c.strip() for c in line.strip("|").split("|")]
    raise AssertionError(f"台账 §1 找不到 {task} 行")


def test_the_content_source_is_the_single_one_declared_by_the_plan() -> None:
    """方案 §8.1：`site/` 与 `web/` 都不许各留一份页面源——这里正向数一遍。"""
    assert PAGES, "唯一内容源里一个页面都没有，那 §8.1 的『只有一个』是空话"
    assert PAYLOAD["domain"].rstrip("/") == DOMAIN
    assert PAYLOAD["output_dir"] == "docs/content/build", "生成物不许直接落现网目录"
    assert SLUGS.count("journal-prompts") == 1
    assert not (REPO / "web" / "en").exists(), "现网目录里冒出了 /en/ 页面＝第二份内容源"


def test_page_set_is_a_subset_of_the_plan_and_names_the_task_on_the_board() -> None:
    """页面集合与方案 §8.2 对拉；每页 `task` 与台账 §1 该行的「做什么」列真含这个 slug。"""
    plan = _plan_82_slugs()
    assert plan, "方案 §8.2 的表解析不出来，判据就成空集"
    unknown = [s for s in SLUGS if s not in plan]
    assert not unknown, f"内容源里有方案 §8.2 没登记的页面：{unknown}"
    for slug in SLUGS:
        task = PAGES[slug]["task"]
        row = _board_row_for(task)
        assert re.fullmatch(r"KG-\d{2}", task), f"{slug} 的 task 不是 KG-NN：{task}"
        assert row[3] in ("GROWTH", "GROWTH+LEGAL"), f"{task} 行负责人列不是 §1 契约：{row[3]!r}"
        assert f"/{slug}" in row[2], f"台账 {task} 行的『做什么』没含 /{slug}：{row[2][:40]}"


def test_h1_is_the_same_sentence_in_brief_content_source_and_body() -> None:
    """H1 三面同词：简报 §3 的表、内容源的 `h1`、正文唯一那个 `#`。"""
    brief_lines = []
    for md in sorted(BRIEF_DIR.glob("*.md")):
        brief_lines += md.read_text(encoding="utf-8").splitlines()
    for slug in SLUGS:
        page = PAGES[slug]
        h1s = re.findall(r"^# (.+)$", _body(page), re.M)
        assert len(h1s) == 1, f"{slug} 正文的 H1 必须恰好一个，当前 {len(h1s)} 个"
        assert h1s[0] == page["h1"], f"{slug} 正文 H1 与内容源不同词：{h1s[0]!r} ≠ {page['h1']!r}"
        row = [l for l in brief_lines if l.startswith(f"| `{slug}` | `{page['h1']}` |")]
        assert row, f"简报 §3 里找不到 {slug} 的 H1 行（三面漂了）：{page['h1']!r}"
        count = int(row[0].rstrip("|").split("|")[-1].strip())
        assert count == len(page["h1"]), f"{slug} 简报 §3 写 {count} 字符，现算 {len(page['h1'])}"


def test_templates_are_unique_and_each_appears_in_its_own_body() -> None:
    """G02：模板非空、站内不重文、且那句话真在正文里出现（不是一张空表）。"""
    seen: dict[str, str] = {}
    for slug in SLUGS:
        tpl = _norm(PAGES[slug]["template"])
        assert len(tpl) >= 20, f"{slug} 的 template 太短，判不出原创性：{tpl!r}"
        assert tpl not in seen, f"{slug} 与 {seen[tpl]} 的模板同文"
        seen[tpl] = slug
        assert tpl in _norm(_body(PAGES[slug])), f"{slug} 的正文里没有自己那一句话模板"


def test_evidence_rows_open_real_lines_and_carry_dates() -> None:
    """G03：每条主张的出处要么打到仓内真文件的真行（且该行非空），要么是 https；日期成形。"""
    checked = 0
    for slug in SLUGS:
        rows = PAGES[slug]["evidence"]
        assert rows, f"{slug} 一条证据都没有"
        for ev in rows:
            assert re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", ev["date"]), f"{slug} 日期不成形：{ev}"
            problem = B._evidence_problem(ev["ref"])
            assert problem is None, f"{slug}：{problem}"
            if not ev["ref"].startswith("http"):
                path, _, num = ev["ref"].rpartition(":")
                target = REPO / path
                line = target.read_text(encoding="utf-8").splitlines()[int(num) - 1]
                assert line.strip(), f"{slug} 的证据打到空行：{ev['ref']}"
                checked += 1
    assert checked >= 15, f"正向证人不足：只核到 {checked} 条仓内出处（要 ≥15）"


def test_no_page_reuses_a_sentence_the_claims_audit_already_judged() -> None:
    """G13：KG-01 审计表判「改写／删除」的原文不许在新页复活（两侧对拉，不是抄一份禁句表）。"""
    rows = []
    for line in AUDIT_MD.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == 6 and cells[3] in ("改写", "删除"):
            quote = cells[1].strip("`")
            if len(quote) >= 12 and re.search(r"[a-zA-Z]{3}", quote):
                rows.append(quote)
    assert len(rows) >= 20, f"审计表可判句太少（{len(rows)}），这条闸会扫空集"
    for slug in SLUGS:
        body = _norm(_body(PAGES[slug]))
        for quote in rows:
            assert _norm(quote) not in body, f"{slug} 复用了审计表判过的原文：{quote[:50]!r}"


def test_banned_ruler_is_shared_and_non_medical_line_is_present() -> None:
    """G05：禁语取自 ASO 尺子（第二份清单＝漂移源），且每页正文有可机械命中的非医疗句。"""
    banned, brands = B._shared_ruler()
    assert len(banned) == 8, f"共享尺子的禁语条数变了（{len(banned)}），本批判据要重新核"
    for slug in SLUGS:
        page = PAGES[slug]
        body = _body(page)
        for label, text in (("title", page["title"]), ("description", page["description"]), ("h1", page["h1"]), ("body", body)):
            for term, pat in banned:
                assert not pat.search(text), f"{slug} 的 {label} 命中禁语 {term!r} ⇒ {pat.search(text).group(0)!r}"
        assert "not medical" in _norm(body) or "不是医疗" in body, f"{slug} 缺非医疗声明句"
        if not slug.startswith("compare/"):
            for brand in brands:
                assert not re.search(rf"\b{brand}\b", body, re.I), f"{slug} 含竞品品牌词 {brand}（KG-05：只许对比页）"


def test_keyword_variants_are_in_the_prose_not_only_in_headings() -> None:
    """G07：≤5 个变体、每个都出现在叙述行、同一行不并列三个（只在标题堆词＝红）。"""
    for slug in SLUGS:
        page = PAGES[slug]
        variants = page["keyword_variants"]
        assert 1 <= len(variants) <= 5, f"{slug} 的变体数越界：{len(variants)}"
        prose = _prose(_body(page))
        for v in variants:
            assert any(_norm(v) in _norm(line) for line in prose), f"{slug} 的 {v!r} 不在正文叙述行"
        for line in prose:
            hit = [v for v in variants if _norm(v) in _norm(line)]
            assert len(hit) < 3, f"{slug} 同一行并列 {len(hit)} 个变体：{hit}"


def test_related_links_exist_in_the_page_set_and_in_the_body() -> None:
    """G08：related ≥2、目标都在页面集合，且正文里真的有那条 `/en/<slug>/` 链接（断链＝红）。"""
    for slug in SLUGS:
        page = PAGES[slug]
        assert len(page["related"]) >= 2, f"{slug} 的 related 少于两条"
        assert slug not in page["related"], f"{slug} 把自己列成内链"
        body = _body(page)
        hrefs = set(re.findall(r"\]\(/en/([^/)]+)/?\)", body))
        for target in page["related"]:
            assert target in PAGES, f"{slug} → {target}：目标不在内容源里"
            assert target in hrefs, f"{slug} 正文缺一条指向 /en/{target}/ 的内链"
        for href in hrefs:
            assert href in PAGES, f"{slug} 链到不存在的页面：/en/{href}/"


def test_cta_is_a_real_action_and_no_fake_download_button() -> None:
    """G09：没有商店链接时 CTA 只能是等待名单，且按钮文案与正文都不许写「Download now」。"""
    defs = PAYLOAD["cta_definitions"]
    assert set(defs) == {"waitlist"}, f"本批只该有等待名单这一个真动作：{sorted(defs)}"
    for key, d in defs.items():
        assert d["href"].startswith(DOMAIN), f"cta[{key}] 的 href 不在品牌域：{d['href']}"
        assert not B.CTA_LABEL_BANNED.search(d["label"]), f"cta[{key}] 文案是假按钮：{d['label']}"
        assert len(d["note"]) > 30, f"cta[{key}] 没解释为什么现在不是下载按钮"
    for slug in SLUGS:
        body = _body(PAGES[slug])
        assert PAGES[slug]["cta"] in defs, f"{slug} 的 cta {PAGES[slug]['cta']!r} 没有定义"
        assert not B.STORE_URL.search(body), f"{slug} 正文出现商店域名/短链"
        assert not B.CTA_LABEL_BANNED.search(body), f"{slug} 正文写了 download now"


def test_the_publish_gate_is_written_into_the_source_and_the_output() -> None:
    """G04/G14＋方案 §8.3：发布前置登记在内容源里，生成物一律带 noindex，脚本拒绝写 web/。"""
    assert PAYLOAD["publish_blocked_by"] == ["KG-02", "T7-05"], "发布闸没登记，草稿随时可能被当成待发布"
    for slug in SLUGS:
        deps = _board_row_for(PAGES[slug]["task"])[6]
        for gate in PAYLOAD["publish_blocked_by"]:
            assert f"{gate}（发布闸）" in deps, f"台账 {PAGES[slug]['task']} 行的依赖列没把 {gate} 列为发布闸：{deps}"
    with pytest.raises(SystemExit) as boom:
        B.write_all(PAYLOAD, Path("web"))
    assert "web" in str(boom.value), f"拒绝写 web/ 的理由没提目录：{boom.value}"
    html = B.page_html(PAGES["ai-journal"], PAYLOAD)
    assert 'name="robots" content="noindex' in html, "草稿生成物没带 noindex，被手工传上去就会被索引"
    assert "build_seo_pages.py" in html, "生成物没写『别手改，源在这里』"


def test_builder_output_is_validated_by_reparsing_not_by_its_own_return_value() -> None:
    """端到端：真跑一次生成到临时目录，再**重新解析 HTML** 判 canonical/hreflang/x-default/sitemap。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "build"
        written = B.write_all(PAYLOAD, out)
        assert len(written) == len(PAGES) + 1, "生成文件数应＝页面数＋1 张 sitemap"
        assert not (REPO / "web" / "en").exists(), "生成器把东西写进现网目录了"
        for slug in SLUGS:
            page = PAGES[slug]
            html = (out / slug / "index.html").read_text(encoding="utf-8")
            url = f"{DOMAIN}/en/{slug}/"
            assert html.count(f'<link rel="canonical" href="{url}" />') == 1, f"{slug} canonical 不是自指恰好一次"
            assert f'<link rel="alternate" hreflang="en" href="{url}" />' in html, f"{slug} 缺 en hreflang"
            assert f'<link rel="alternate" hreflang="x-default" href="{url}" />' in html, f"{slug} 缺 x-default"
            assert re.findall(r"<h1>", html) and html.count("<h1>") == 1, f"{slug} 生成物 H1 不唯一"
            assert f"<title>{page['title']}</title>" in html, f"{slug} 的 title 没进生成物"
            assert f'name="description" content="{page["description"]}"' in html, f"{slug} 的 description 没进生成物"
        sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
        locs = re.findall(r"<loc>([^<]+)</loc>", sitemap)
        assert sorted(locs) == sorted(f"{DOMAIN}/en/{s}/" for s in SLUGS), "sitemap 行与页面集合不同源"
        assert len(locs) == len(set(locs)), "sitemap 有重复行（G12：2 次＝重复提交）"


def test_the_prompt_page_is_generated_from_the_library_not_transcribed() -> None:
    """KG-12：提示页正文只留插入点，30 条由 `backend/content/reflection_prompts.json` 现算展开。"""
    page = PAGES["journal-prompts"]
    source = REPO / page["prompt_source"]
    body = _body(page)
    assert body.count(PROMPT_MARKER) == 1, "提示页必须恰好一个插入点（手抄 30 条＝红）"
    assert not re.search(r"rp-0\d\d", body), "正文里出现了提示 id——那是手抄库"
    prompts = json.loads(source.read_text(encoding="utf-8"))["prompts"]
    assert len(prompts) == 30, f"提示库不是 30 条：{len(prompts)}"
    html = _norm(unescape(B.render_prompts(source)))
    assert B.render_prompts(source).count('<article class="prompt">') == 30, "生成物没展开到 30 条"
    for p in prompts:
        assert _norm(p["question"]["en"]) in html, f"{p['id']} 的问题没进生成物"
        assert _norm(p["why"]["en"]) in html, f"{p['id']} 缺『目的』"
        assert _norm(p["follow_up"]["en"]) in html, f"{p['id']} 缺『追问』"
        assert p["id"] in html, f"{p['id']} 没留回溯号"
    assert "library-reflect" in html and "library-goal" in html and "library-mood" in html, "缺按功能分的目录锚"


def test_the_sampled_pages_backfill_the_gate_doc_checklist() -> None:
    """KG-18 §3 的空表不算交付：内容源有页，抽检表就要有真行且覆盖 G01–G14。"""
    text = GATE_MD.read_text(encoding="utf-8")
    section = text.split("## 3. 抽检记录", 1)[1].split("\n---", 1)[0]
    rows = []
    for line in section.splitlines():
        s = line.strip()
        if s.startswith("|") and not s.startswith("| 页面") and not set(s) <= set("|-: "):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) == 5 and cells[0] != "（待 KG-10～KG-14 草稿落地后回填，至少 2 页）":
                rows.append(cells)
    assert len(rows) >= min(2, len(PAGES)), f"内容源 {len(PAGES)} 页，抽检只 {len(rows)} 行"
    ids = set(re.findall(r"^\| (G\d{2}) \|", text, re.M))
    for page_name, _d, _who, covered, _issue in rows:
        slug = page_name.strip("`")
        assert slug in PAGES, f"抽检表里的 {page_name} 不在内容源页面集合里"
        hit = set(re.findall(r"G\d{2}", covered))
        assert hit >= ids, f"{slug} 的抽检缺清单项：{sorted(ids - hit)}"


def test_the_cli_check_mode_is_the_ci_entry_point() -> None:
    """`--check` 必须真跑一次校验并全绿（CI 用的就是这条；退出码非 0 说明页面源坏了）。"""
    proc = subprocess.run([sys.executable, str(BUILDER), "--check"], capture_output=True, text=True, cwd=REPO)
    assert proc.returncode == 0, f"--check 红了：\n{proc.stdout}\n{proc.stderr}"
    assert f"{len(PAGES)} 页" in proc.stdout, f"--check 没报页面数证人：{proc.stdout!r}"


def test_the_validator_rejects_a_fake_button_when_mutated_in_memory() -> None:
    """正向证人：判据在场——把 CTA 换成「Download now」必须立刻红（不是只跑一次全绿）。"""
    assert B.violations(PAYLOAD) == [], f"基线不该有不合格项：{B.violations(PAYLOAD)}"
    bad = B.mutated_for_self_test(json.loads(json.dumps(PAYLOAD)))
    hits = B.violations(bad)
    assert hits, "内存变异后仍全绿 ⇒ 判据不在场"
    assert any(h.startswith("G09") and "假按钮" in h for h in hits), f"红的不是 G09 那条：{hits[:3]}"


def test_no_page_body_leaks_chinese_into_the_english_pillars() -> None:
    """本批只出英文页（简报 §8.4）：正文混进汉字＝写坏了，不是风格问题。"""
    for slug in SLUGS:
        assert PAGES[slug]["lang"] == "en", f"{slug} 的 lang 不是 en"
        body = _body(PAGES[slug])
        cjk = re.findall(r"[\u4e00-\u9fff]+", body)
        assert not cjk, f"{slug} 正文混入中文：{cjk[:3]}"
        assert body.count(PROMPT_MARKER) <= 1


def test_the_docstring_cites_point_at_this_files_own_tests() -> None:
    """引用即自证：本文件 docstring 里的每个 `:NNN` 必须打到本文件一个真 test 行。"""
    text = Path(__file__).read_text(encoding="utf-8").splitlines()
    doc = re.match(r'""".*?"""', Path(__file__).read_text(encoding="utf-8"), re.S)
    assert doc, "模块 docstring 不见了"
    cites = re.findall(r"`:(\d+)`", doc.group(0))
    assert cites, "docstring 没带任何 `:NNN` 引用——那条自证判据本身没人判"
    tests = {n: l for n, l in enumerate(text, 1) if l.startswith("def test_")}
    for c in cites:
        n = int(c)
        assert n in tests, f"docstring 引的 :{c} 不是本文件的 test 行（{tests.get(n, '空')!r}）"

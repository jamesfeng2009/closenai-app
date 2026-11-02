#!/usr/bin/env python3
"""KG-10～KG-14 页面生成器与 KG-18 内容质量闸的**执行侧**（2026-11-03，批次 7）。

**为什么是脚本而不是人**：`docs/keyword-content-gate.md` 的 G01–G14 里有一大半是机械的
（长度、唯一、内链目标存不存在、禁语、变体是否在正文出现、canonical 自指、sitemap 出现几次）。
留在人眼里就会漂，而且页面一旦上线就是对外承诺，比代码更没「回头再改」的余地。

**三侧对拉**（断言两侧不许同源）：
① 正文 `docs/content/pages/<slug>.en.md`  ↔  ② 内容源 `site-content/pages.json`  ↔  ③ 判据本身。
H1 两侧都要出现且同词、`related` 与正文真内链互指、`template` 那句话必须真在正文里、
`evidence.ref` 必须打到仓内真文件的真行——任何一侧单独改都会红。

**发布闸是硬编码的，不是提醒**：`KG-02`（法务）与 `T7-05`（运营主体）在台账 §1 仍是 `BLOCKED`，
方案 §8.3 把这两条列成发布前置。所以本脚本**拒绝**把生成物写进 `web/`：
`publish_blocked_by` 非空时输出目录落在 `web/` 直接红退出，产物只落 `docs/content/build/`，
并且每页带 `<meta name="robots" content="noindex">`——防止有人手工把草稿传上去被索引。

用法：
    python scripts/build_seo_pages.py --check          # 只校验不写（CI 用）
    python scripts/build_seo_pages.py                  # 校验通过后写 docs/content/build/
    python scripts/build_seo_pages.py --self-test      # 证明这条闸会红（假按钮页必须被拒）

`--self-test` 与「反向归零闸要配正向证人」同规矩：只跑一次全绿证明不了判据在场。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from html import escape
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAGES_JSON = REPO / "site-content" / "pages.json"
AUDIT_MD = REPO / "docs" / "keyword-claims-audit-2026-11.md"

#: 长度上限写在本脚本里（另一侧是内容源），与 `check_aso_copy.py` 同口径。
TITLE_MAX = 60
DESCRIPTION_MAX = 160
QUESTION_MIN = 12
VARIANTS_MAX = 5

#: G09 允许的 CTA 动作与「假按钮」写法。等待名单是真动作，Download now 不是。
CTA_LABEL_BANNED = re.compile(r"download\s+now|get\s+it\s+now|立即下载|免费下载", re.I)
STORE_URL = re.compile(r"(apps\.apple\.com|play\.google\.com|//bit\.ly/)", re.I)

#: G06 的故事句式（编造人物＋数字的「我从 X 到 Y」体）。
STORY_PATTERN = re.compile(r"\bi\s+went\s+from\b.{0,80}\bto\b", re.I)

#: 提示库正文的插入点：页面正文写这行，生成时由 JSON 现算展开，作者不许手抄 30 条。
PROMPT_MARKER = "<!--prompt-library-->"

REQUIRED_FIELDS = [
    "slug", "lang", "cluster", "task", "question", "title", "description",
    "h1", "body_md", "cta", "related", "keyword_variants", "template",
    "author", "testimonials", "evidence",
]

FULL_CITE = re.compile(r"(?P<path>(?:docs|backend|scripts|frontend|web|site|site-content)/[^`\s:]+):(?P<line>\d+)")


def _shared_ruler() -> tuple[list[tuple[str, re.Pattern[str]]], list[str]]:
    """禁语与竞品词**从既有资产取**，不在这里抄第二份清单。

    `scripts/check_aso_copy.py` 的 `BANNED_PATTERNS` 是 KG-01/KG-04 定的词边界正则，
    `docs/aso/en-US-2026-11.json` 的 `competitor_brands` 是 KG-05 定的名单；
    本脚本 import/读它们，所以文案包改禁语，页面侧同刻跟着变——不会各留一份。
    """
    spec = importlib.util.spec_from_file_location("aso_ruler", REPO / "scripts" / "check_aso_copy.py")
    assert spec and spec.loader, "取不到 ASO 尺子：scripts/check_aso_copy.py"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    brands = json.loads((REPO / "docs" / "aso" / "en-US-2026-11.json").read_text(encoding="utf-8"))
    return list(module.BANNED_PATTERNS), list(brands.get("competitor_brands", []))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _prose_lines(body: str) -> list[str]:
    """正文行，去掉标题、表头分隔、引用与链接行——G07 不许「只在标题里堆词」。"""
    out = []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("|") or s.startswith(">"):
            continue
        out.append(s)
    return out


def _audit_phrases() -> list[tuple[str, str]]:
    """KG-01 审计表里判「改写／删除」的**原文**，逐条当禁句用（G13 的机械形态）。

    只取英文原文（≥12 字符），因为本批页面是英文页；中文判句留在同一张表里，
    等中文页那批再判——判据面写清楚，不假装全覆盖。
    """
    rows: list[tuple[str, str]] = []
    for line in AUDIT_MD.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 6 or cells[3] not in ("改写", "删除"):
            continue
        quote = cells[1].strip("`")
        if re.search(r"[a-zA-Z]{3}", quote) and len(quote) >= 12:
            rows.append((quote, cells[3]))
    return rows


def _resolve(path_text: str) -> Path | None:
    p = (REPO / path_text)
    return p if p.is_file() else None


def _evidence_problem(ref: str) -> str | None:
    """G03：每条出处要么是可点开的 `路径:行`，要么是 https 外链；都要带日期（调用方判）。"""
    if ref.startswith("http://") or ref.startswith("https://"):
        return None if ".app" in ref or "." in ref.split("/")[2] else f"外链域名可疑：{ref}"
    m = FULL_CITE.fullmatch(ref)
    if not m:
        return f"出处不是 `路径:行` 也不是 https：{ref}"
    target = _resolve(m.group("path"))
    if target is None:
        return f"出处指到不存在的文件：{ref}"
    lines = target.read_text(encoding="utf-8").splitlines()
    n = int(m.group("line"))
    if not (1 <= n <= len(lines)):
        return f"出处行号越界（该文件共 {len(lines)} 行）：{ref}"
    if not lines[n - 1].strip():
        return f"出处打到空行：{ref}"
    return None


def load_pages(payload: dict) -> tuple[dict[str, dict], dict[str, str]]:
    """返回 (按 slug 索引的页, slug → 生成 URL)。"""
    domain = payload["domain"].rstrip("/")
    pages: dict[str, dict] = {}
    urls: dict[str, str] = {}
    for page in payload["pages"]:
        slug = page["slug"]
        pages[slug] = page
        urls[slug] = f"{domain}/en/{slug}/"
    return pages, urls


def violations(payload: dict) -> list[str]:
    """全部不合格项；空列表＝这七页可以进 `docs/content/build/`（发布是另一回事，见模块开头）。"""
    out: list[str] = []
    banned, brands = _shared_ruler()
    pages, urls = load_pages(payload)
    domain = payload["domain"]

    # ── 全局：域名、输出目录、发布闸 ─────────────────────────────
    if urlparse_host(domain) != "closenai.app":
        out.append(f"G10 canonical 的域名不是品牌域 closenai.app：{domain}")
    if urlparse_scheme(domain) != "https":
        out.append(f"G10 内容源 domain 必须是 https：{domain}")
    out_dir = payload.get("output_dir", "")
    if out_dir in ("", "web", "web/") or out_dir.startswith("web/"):
        out.append(f"G14 输出目录落在 web/：生成物只许写 {out_dir!r}，发布闸未过 ⇒ 红")
    if not payload.get("publish_blocked_by"):
        out.append("G04/G14 内容源没登记 `publish_blocked_by`：谁保证这些页不会被手工传上线？")

    seen_title: dict[str, str] = {}
    seen_desc: dict[str, str] = {}
    seen_h1: dict[str, str] = {}
    seen_template: dict[str, str] = {}
    seen_paragraph: dict[str, str] = {}
    phrase_rows = _audit_phrases()

    for slug, page in pages.items():
        missing = [f for f in REQUIRED_FIELDS if f not in page]
        if missing:
            out.append(f"G01 页 {slug} 缺字段：{missing}")
            continue

        # G01 一个具体问题（问号结尾、够长），H1 不堆关键词
        q = page["question"].strip()
        if len(q) < QUESTION_MIN or not q.endswith("?"):
            out.append(f"G01 {slug} 的 question 不是一个够长的问题句：{q!r}")
        if page["h1"].count(",") + page["h1"].count("、") > 1:
            out.append(f"G01 {slug} 的 H1 像关键词堆叠（逗号/顿号 >1）：{page['h1']!r}")

        # 长度唯一性（title/description/H1 各自不许撞车，也不许空）
        for field, cap, label in (("title", TITLE_MAX, "title"), ("description", DESCRIPTION_MAX, "description")):
            text = page[field]
            if not text.strip():
                out.append(f"G01 {slug} 的 {label} 为空")
            if len(text) > cap:
                out.append(f"G01 {slug} 的 {label} 超 {cap} 字符：{len(text)}")
            bucket = seen_title if field == "title" else seen_desc
            key = _norm(text)
            if key in bucket:
                out.append(f"G01 {slug} 的 {label} 与 {bucket[key]} 重复（两页互相蚕食）")
            bucket[key] = slug
        if _norm(page["h1"]) in seen_h1:
            out.append(f"G01 {slug} 的 H1 与 {seen_h1[_norm(page['h1'])]} 重复")
        seen_h1[_norm(page["h1"])] = slug

        # G02 原创模板：非空、站内不重文；段落级查重防「同一段抄三页」
        tpl = page["template"].strip()
        if len(tpl) < 20:
            out.append(f"G02 {slug} 的 template 空洞：{tpl!r}")
        if _norm(tpl) in seen_template:
            out.append(f"G02 {slug} 的 template 与 {seen_template[_norm(tpl)]} 同文（两页抢同一个交付物）")
        seen_template[_norm(tpl)] = slug

        body_path = REPO / page["body_md"]
        if not body_path.is_file():
            out.append(f"G14 {slug} 的正文源不存在：{page['body_md']}")
            continue
        body = body_path.read_text(encoding="utf-8")
        if not str(page["body_md"]).startswith("docs/content/pages/"):
            out.append(f"G14 {slug} 的正文源不在 docs/content/pages/：{page['body_md']}")

        h1s = re.findall(r"^# (.+)$", body, re.M)
        if len(h1s) != 1:
            out.append(f"G01 {slug} 正文的 H1 必须恰好一个，当前 {len(h1s)} 个：{h1s}")
        elif h1s[0].strip() != page["h1"]:
            out.append(f"G01 {slug} 正文 H1 与内容源不同词：{h1s[0].strip()!r} ≠ {page['h1']!r}")

        for para in re.split(r"\n\s*\n", body):
            key = _norm(para)
            if len(key) < 60:
                continue
            if key in seen_paragraph:
                out.append(f"G02 {slug} 的整段正文与 {seen_paragraph[key]} 同文（同站互抄）")
            seen_paragraph[key] = slug

        if _norm(tpl) not in _norm(body):
            out.append(f"G02 {slug} 的 template 那句话在正文里找不到：{tpl[:40]!r}…")

        # G03 来源与日期
        if not page["evidence"]:
            out.append(f"G03 {slug} 没有一条 evidence：页面主张全是形容词")
        for ev in page["evidence"]:
            for fld in ("claim", "ref", "date"):
                if not str(ev.get(fld, "")).strip():
                    out.append(f"G03 {slug} 的 evidence 缺 {fld}：{ev}")
            problem = _evidence_problem(str(ev.get("ref", "")))
            if problem:
                out.append(f"G03 {slug}：{problem}")
            if not re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", str(ev.get("date", ""))):
                out.append(f"G03 {slug} 的 evidence.date 不是 YYYY-MM(-DD)：{ev.get('date')!r}")

        # G04 作者非占位符；运营主体未确认 ⇒ 只能留草稿（由 publish_blocked_by 兜）
        author = str(page["author"]).strip()
        if len(author) < 3 or re.search(r"待补|待定|tbd|todo|xxx", author, re.I):
            out.append(f"G04 {slug} 的 author 是占位符：{author!r}")

        # G05 非医疗句在场，且禁语一律不出现（禁语清单来自 ASO 尺子，不另立一份）
        if "not medical" not in _norm(body) and "不是医疗" not in body:
            out.append(f"G05 {slug} 正文缺非医疗声明句（英文命中 not medical）")
        scan = [(f"{fld}", page[fld]) for fld in ("title", "description", "h1")] + [("body", body)]
        for label, text in scan:
            for term, pat in banned:
                if pat.search(text):
                    out.append(f"G05/G13 {slug} 的 {label} 命中禁用词 {term!r} ⇒ {pat.search(text).group(0)!r}")

        # G06 无虚假案例
        for t in page["testimonials"]:
            if not t.get("verified"):
                out.append(f"G06 {slug} 有一条没 verified 的证言：{t}")
        if STORY_PATTERN.search(body):
            out.append(f"G06 {slug} 正文有「I went from … to …」式编造故事句")

        # G07 变体：数量、真出现在正文、同句不并列三个
        variants = page["keyword_variants"]
        if len(variants) > VARIANTS_MAX:
            out.append(f"G07 {slug} 的 keyword_variants {len(variants)} 条 > {VARIANTS_MAX}")
        prose = _prose_lines(body)
        for v in variants:
            if not any(_norm(v) in _norm(line) for line in prose):
                out.append(f"G07 {slug} 的变体 {v!r} 没出现在正文叙述行（只在标题/表格里堆）")
        for line in prose:
            hit = [v for v in variants if _norm(v) in _norm(line)]
            if len(hit) >= 3:
                out.append(f"G07 {slug} 同一行并列 {len(hit)} 个变体：{hit}")

        # G08 内链：≥2、目标都在页面集合、且正文里真的有这些链接
        related = page["related"]
        if len(related) < 2:
            out.append(f"G08 {slug} 的 related 只有 {len(related)} 条（要 ≥2）")
        for target in related:
            if target not in pages:
                out.append(f"G08 {slug} 内链目标不在页面集合里（断链）：{target}")
        hrefs = set(re.findall(r"\]\(/en/([^/)]+)/?\)", body)) | set(re.findall(r'href="/en/([^/"]+)/?"', body))
        for href in sorted(hrefs):
            if href not in pages:
                out.append(f"G08 {slug} 正文链到不存在的页面：/en/{href}/")
        for target in related:
            if target not in hrefs:
                out.append(f"G08 {slug} 的 related 写了 {target}，正文里却没有这条内链")

        # G09 CTA
        cta = page["cta"]
        defs = payload.get("cta_definitions", {})
        if cta not in defs:
            out.append(f"G09 {slug} 的 cta {cta!r} 不在 cta_definitions 里（动作不可达）")
        else:
            d = defs[cta]
            if CTA_LABEL_BANNED.search(str(d.get("label", ""))):
                out.append(f"G09 cta[{cta}] 的按钮文案是假按钮：{d.get('label')!r}")
            if not str(d.get("note", "")).strip():
                out.append(f"G09 cta[{cta}] 没有说明为什么现在不是下载按钮")
        if STORE_URL.search(body):
            out.append(f"G09 {slug} 正文出现商店短链/商店域名（当前无商店链接＝假按钮）")
        if CTA_LABEL_BANNED.search(body):
            out.append(f"G09 {slug} 正文写了 download now／立即下载")

        # G13 承诺口径：审计表判「改写／删除」的原文不得在新页复活
        for quote, verdict in phrase_rows:
            if _norm(quote) in _norm(body):
                out.append(f"G13 {slug} 正文复用了审计表判「{verdict}」的原文：{quote[:40]!r}…")

        # 品牌词只在 /compare/ 面（KG-05 规则；本批无对比页 ⇒ 一律不许出现）
        if not slug.startswith("compare/"):
            for brand in brands:
                if re.search(rf"\b{re.escape(brand)}\b", body, re.I):
                    out.append(f"G13 {slug} 含竞品品牌词 {brand!r} ⇒ 按 KG-05 只能放对比页")

        # G11 语言集合：本批只做 en，但 x-default 必须在
        langs = payload.get("hreflang", {}).get("langs", ["en"])
        if "en" not in langs:
            out.append(f"G11 内容源的 hreflang.langs 少了 en：{langs}")
        if "x-default" not in payload.get("hreflang", {}).get("variants", ["en", "x-default"]):
            out.append("G11 没有 x-default ⇒ 搜索侧默认落到哪一页没人知道")

    # G12 sitemap：每个 URL 恰好一次（由 sitemap_entries 现算，不数文件）
    entries = sitemap_urls(payload)
    dup = [u for u in set(entries) if entries.count(u) > 1]
    for u in dup:
        out.append(f"G12 sitemap 里 {u} 出现 {entries.count(u)} 次（2 次＝重复提交）")
    for slug, url in urls.items():
        if url not in entries:
            out.append(f"G12 {slug} 不在 sitemap 行里（0 次＝没提交）")
    return out


def urlparse_host(url: str) -> str:
    return re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]


def urlparse_scheme(url: str) -> str:
    return "https" if url.startswith("https://") else ("http" if url.startswith("http://") else "")


def sitemap_urls(payload: dict) -> list[str]:
    domain = payload["domain"].rstrip("/")
    return [f"{domain}/en/{p['slug']}/" for p in payload["pages"]]


def sitemap_xml(payload: dict) -> str:
    today = payload.get("generated_on", "2026-11-03")
    rows = "\n".join(
        f"  <url>\n    <loc>{escape(u)}</loc>\n    <lastmod>{today}</lastmod>\n  </url>"
        for u in sitemap_urls(payload)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{rows}\n</urlset>\n"
    )


def _inline(text: str) -> str:
    html = escape(text)
    html = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', html)
    html = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"(?<!`)`([^`]+)`(?!`)", r"<code>\1</code>", html)
    return html


def render_prompts(source: Path) -> str:
    """把 KG-26 提示库现算成 HTML——正文里**不许手抄**，只留 `PROMPT_MARKER`。

    展开后每条给三个字段（问题／为什么／追问）与 `id` 回溯，凑齐台账 §KG-12 的
    「每条给目的、示例、追问 + 逐条可追溯」；分节按 `function`，数量对不上就交给闸去红。
    """
    data = json.loads(source.read_text(encoding="utf-8"))
    order = ["reflect", "goal", "mood"]
    labels = {"reflect": "Reflect", "goal": "Set a goal", "mood": "Check in on a feeling"}
    parts: list[str] = []
    for fn in order:
        items = [p for p in data["prompts"] if p["function"] == fn]
        parts.append(f'<h3 id="library-{fn}">{escape(labels[fn])} — {len(items)} prompts</h3>')
        for p in items:
            parts.append(
                '<article class="prompt">'
                f'<h4>{escape(p["title"]["en"])}</h4>'
                f'<p class="ask">{escape(p["question"]["en"])}</p>'
                f'<p class="why"><strong>Why:</strong> {escape(p["why"]["en"])}</p>'
                f'<p class="follow"><strong>Follow-up:</strong> {escape(p["follow_up"]["en"])}</p>'
                f'<p class="trace">Source: <code>backend/content/reflection_prompts.json</code> '
                f'#{escape(p["id"])} · function <code>{escape(p["function"])}</code> '
                f'· cluster <code>{escape(p["cluster"])}</code></p>'
                "</article>"
            )
    return "\n".join(parts)


def _blocks(md: str) -> list[str]:
    out: list[str] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s or s == "---":
            i += 1
            continue
        if s == PROMPT_MARKER:
            out.append(s)
            i += 1
            continue
        if s.startswith("####"):
            out.append(f"<h4>{_inline(s[4:].strip())}</h4>")
        elif s.startswith("###"):
            out.append(f"<h3>{_inline(s[3:].strip())}</h3>")
        elif s.startswith("##"):
            out.append(f"<h2>{_inline(s[2:].strip())}</h2>")
        elif s.startswith("#"):
            out.append(f"<h1>{_inline(s[1:].strip())}</h1>")
        elif s.startswith("> "):
            buf = []
            while i < len(lines) and lines[i].strip().startswith("> "):
                buf.append(lines[i].strip()[2:])
                i += 1
            out.append(f"<blockquote><p>{_inline(' '.join(buf))}</p></blockquote>")
            continue
        elif s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not set("".join(cells)) <= set("-: "):
                    rows.append(cells)
                i += 1
            head, body_rows = rows[0], rows[1:]
            out.append(
                "<table><thead><tr>"
                + "".join(f"<th>{_inline(c)}</th>" for c in head)
                + "</tr></thead><tbody>"
                + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body_rows)
                + "</tbody></table>"
            )
            continue
        elif re.match(r"^[-*] ", s):
            items = []
            while i < len(lines) and re.match(r"^[-*] ", lines[i].strip()):
                items.append(lines[i].strip()[2:])
                i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(it)}</li>" for it in items) + "</ul>")
            continue
        elif re.match(r"^\d+\. ", s):
            items = []
            while i < len(lines) and re.match(r"^\d+\. ", lines[i].strip()):
                items.append(re.sub(r"^\d+\. ", "", lines[i].strip()))
                i += 1
            out.append("<ol>" + "".join(f"<li>{_inline(it)}</li>" for it in items) + "</ol>")
            continue
        elif s.startswith("<a ") or s.startswith("<div"):
            out.append(s)
        else:
            buf = []
            while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#|\||>|[-*] |\d+\. |<a )", lines[i].strip()
            ) and lines[i].strip() != PROMPT_MARKER:
                buf.append(lines[i].strip())
                i += 1
            out.append(f"<p>{_inline(' '.join(buf))}</p>")
            continue
        i += 1
    return out


def page_html(page: dict, payload: dict) -> str:
    domain = payload["domain"].rstrip("/")
    slug = page["slug"]
    url = f"{domain}/en/{slug}/"
    langs = payload.get("hreflang", {}).get("langs", ["en"])
    links = [f'<link rel="alternate" hreflang="{lang}" href="{url}" />' for lang in langs]
    links.append(f'<link rel="alternate" hreflang="x-default" href="{url}" />')
    body_md = (REPO / page["body_md"]).read_text(encoding="utf-8")
    chunks = []
    for block in _blocks(body_md):
        if block == PROMPT_MARKER:
            chunks.append(render_prompts(REPO / page["prompt_source"]))
        else:
            chunks.append(block)
    nav = " · ".join(
        f'<a href="/en/{t}/">{escape(pages_title(t, payload))}</a>' for t in page["related"]
    )
    cta = payload["cta_definitions"][page["cta"]]
    evidence = "".join(
        f"<li>{escape(ev['claim'])} <code>{escape(ev['ref'])}</code> <time>{escape(ev['date'])}</time></li>"
        for ev in page["evidence"]
    )
    ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": page["h1"],
        "description": page["description"],
        "author": {"@type": "Organization", "name": page["author"]},
        "dateModified": payload.get("generated_on", "2026-11-03"),
        "mainEntityOfPage": url,
        "isPartOf": domain,
    }
    related = [t for t in page["related"]]
    return f"""<!doctype html>
<!-- GENERATED BY scripts/build_seo_pages.py — 不要手改；源：site-content/pages.json + {page["body_md"]} -->
<!-- 草稿产物：发布闸 {'/'.join(payload.get("publish_blocked_by", []))} 未结案，本目录不部署 -->
<html lang="{page["lang"]}">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{escape(page["title"])}</title>
<meta name="description" content="{escape(page["description"])}" />
<meta name="robots" content="noindex, nofollow" />
<link rel="canonical" href="{url}" />
{chr(10).join(links)}
<meta property="og:title" content="{escape(page["title"])}" />
<meta property="og:description" content="{escape(page["description"])}" />
<meta property="og:url" content="{url}" />
<meta property="og:type" content="article" />
<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>
</head>
<body>
<nav aria-label="Breadcrumb"><a href="{domain}/">Home</a> / {escape(page["title"])}</nav>
{chr(10).join(chunks)}
<h2>Where this is checkable</h2>
<p>Every claim on this page points at a file in the repository, dated. Open it, read the line.</p>
<ul>{evidence}</ul>
<h2>Related</h2>
<p>{nav}</p>
<section class="cta">
<h2>{escape(cta["heading"])}</h2>
<p><a class="btn" href="{escape(cta["href"])}">{escape(cta["label"])}</a></p>
<p class="note">{escape(cta["note"])}</p>
</section>
<p class="meta">Author: {escape(page["author"])} · Cluster: {escape(page["cluster"])} · Task: {escape(page["task"])} · Related: {escape(", ".join(related))}</p>
</body>
</html>
"""


def pages_title(slug: str, payload: dict) -> str:
    for p in payload["pages"]:
        if p["slug"] == slug:
            return p["title"]
    return slug


def write_all(payload: dict, out_dir: Path) -> list[Path]:
    root = (REPO / out_dir) if not out_dir.is_absolute() else out_dir
    if urlparse_host(payload["domain"]) != "closenai.app":
        raise SystemExit("拒绝写出：domain 不是品牌域")
    if str(out_dir).startswith("web") or str(root).endswith("/web"):
        raise SystemExit(
            "拒绝写出：生成物不许落 web/。发布闸 "
            + "/".join(payload.get("publish_blocked_by", ["KG-02", "T7-05"]))
            + " 在台账 §1 仍是 BLOCKED，方案 §8.3 把它列为发布前置。"
        )
    written: list[Path] = []
    for page in payload["pages"]:
        target = root / page["slug"] / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page_html(page, payload), encoding="utf-8")
        written.append(target)
    sitemap = root / "sitemap.xml"
    sitemap.parent.mkdir(parents=True, exist_ok=True)
    sitemap.write_text(sitemap_xml(payload), encoding="utf-8")
    written.append(sitemap)
    return written


def mutated_for_self_test(payload: dict) -> dict:
    """造一条**必然不合格**的输入：把等待名单换成「Download now」＋占位商店链接。

    选它是因为 G09 是对外承诺的硬边界（现在根本没有商店链接），
    而且它只碰 CTA 一处，红得干净——正文、字数、内链都不受影响。
    """
    import copy

    bad = copy.deepcopy(payload)
    bad["cta_definitions"]["download_now"] = {
        "heading": "Get the app",
        "label": "Download now",
        "href": "https://apps.apple.com/app/id0000000",
        "note": "占位商店链接",
    }
    bad["pages"][0]["cta"] = "download_now"
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KG-10～KG-14 页面生成与 KG-18 内容质量闸执行侧")
    ap.add_argument("--pages", type=Path, default=PAGES_JSON)
    ap.add_argument("--out", type=str, default=None, help="覆盖输出目录（只许仓内路径）")
    ap.add_argument("--check", action="store_true", help="只校验不写文件（CI 用）")
    ap.add_argument("--self-test", action="store_true", help="用假按钮页证明这条闸会红")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    payload = json.loads(args.pages.read_text(encoding="utf-8"))
    hits = violations(payload)

    if args.self_test:
        bad = mutated_for_self_test(payload)
        bad_hits = violations(bad)
        proof = [h for h in bad_hits if h.startswith("G09") and "假按钮" in h]
        ok = bool(proof)
        if args.json:
            print(json.dumps({"self_test": ok, "violations": bad_hits}, ensure_ascii=False))
        else:
            print(f"[self-test] Download now 的 CTA 被拒：{'✓ 会红' if ok else '✗ 闸失效'}")
            for h in bad_hits[:6]:
                print(f"  - {h}")
        return 0 if ok else 1

    if args.json:
        print(json.dumps({"ok": not hits, "pages": len(payload["pages"]), "violations": hits}, ensure_ascii=False))
    if hits:
        print("不合格：", file=sys.stderr)
        for h in hits:
            print(f"  - {h}", file=sys.stderr)
        return 1

    n_pages = len(payload["pages"])
    if args.check:
        print(f"页面源判据全绿：{n_pages} 页／{len(sitemap_urls(payload))} 条 sitemap 行（--check 未写文件）")
        return 0
    written = write_all(payload, Path(args.out or payload["output_dir"]))
    print(f"写出 {len(written)} 个文件（{n_pages} 页 + sitemap.xml）到 {args.out or payload['output_dir']}；未部署、未写 web/")
    for w in written:
        print(f"  {w.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

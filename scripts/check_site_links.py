#!/usr/bin/env python3
"""KG-17 的爬虫闸：内链、CTA、面包屑、孤儿页与断链一律改成脚本判（2026-11-03）。

**为什么要有脚本**：台账 §KG-17 的验收原文写的是「0 断链、0 孤儿页；每个支柱页至少 2 条入链」
「所有 CTA 指向真实下载/等待名单，不指向占位商店链接」。这四条全是图上能算的，
留在人眼里就会漂——尤其是**入链**：只看某页自己链出去几条，永远看不出它是孤儿。

**判据分两面，不许混**：
- **支柱面**（`site-content/pages.json` ＋ `docs/content/pages/*.en.md` ＋ 生成器产出的 HTML）：
  硬红。断链、孤儿、CTA 假按钮、面包屑缺失、从 hub 不可达，任意一条即红。
- **现网面**（`web/*.html`）：本轮**不修**（属 KG-23/KG-02 的法务与文案面），
  但把发现的 offender 与 `docs/seo/internal-links-2026-11.md` §5 的**在册清单逐条对拉**——
  多了＝新缺陷没登记就上线；少了＝修好了却不注销登记，读者以为还在坏。这就是「两侧不同源」：
  一侧是爬虫现算，另一侧是人写的文档表格。

**为什么不读仓内 `docs/content/build/`**：`.gitignore` 的 `build/` 让这个目录不入库，
判据若读它就会变成「本机跑过一次才有」的假绿。生成物一律**在内存里现生成再解析**
（`import build_seo_pages`），跑过就消失，不留状态。

用法：
    ./.venv/bin/python scripts/check_site_links.py                # 人读报告
    ./.venv/bin/python scripts/check_site_links.py --check        # CI 入口（只判不合格，不打数字）
    ./.venv/bin/python scripts/check_site_links.py --json         # 机器可读，给文档对拉数字
    ./.venv/bin/python scripts/check_site_links.py --self-test    # 证明这条闸会红（造一个孤儿页）
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
PAGES_JSON = REPO / "site-content" / "pages.json"
WEB = REPO / "web"
LEDGER_DOC = REPO / "docs" / "seo" / "internal-links-2026-11.md"

#: 台账 §KG-17 验收点名的下限：「每个支柱页至少 2 条入链」。
#: 入链只数**别的页面正文里的内链**，sitemap 与页脚不算——否则每页白捡一条，孤儿也看不出来。
INBOUND_MIN = 2
#: 从 hub 出发几跳内必须可达。3 跳是「首页级导航＋簇内互链」的现实上限，
#: 再多就意味着真实站点里要点四次才到，等发布后用户就找不到了。
HOPS_MAX = 3
HUB_SLUG = "ai-journal"

STORE_HOSTS = {"apps.apple.com", "apple.co", "play.google.com", "bit.ly", "goo.gl"}
FAKE_CTA_WORDS = re.compile(r"download now|get the app|free download", re.I)
ALLOWED_MAILTO = "support@closenai.app"

#: 现网面的路径 → 真文件。无扩展名路由是 Pages 的内置行为（见 `web/_redirects` 的头两行注释），
#: 所以闸必须自己认这张映射表，而不是把 `/privacy` 判成断链。
WEB_ROUTES = {
    "/": "index.html",
    "/privacy": "privacy.html",
    "/terms": "terms.html",
    "/404": "404.html",
}

MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
HTML_LINK = re.compile(r'href="([^"]*)"')
HTML_ID = re.compile(r'\bid="([^"]+)"')


def _web_ids(name: str) -> set[str]:
    return set(HTML_ID.findall((WEB / name).read_text(encoding="utf-8")))


def _web_exists(name: str) -> bool:
    return (WEB / name).is_file()


def load_payload() -> dict:
    return json.loads(PAGES_JSON.read_text(encoding="utf-8"))


def body_links(payload: dict) -> dict[str, list[str]]:
    """每页正文里的内链目标（slug 列表）。"""
    out: dict[str, list[str]] = {}
    for page in payload["pages"]:
        md = (REPO / page["body_md"]).read_text(encoding="utf-8")
        targets = []
        for href in MD_LINK.findall(md):
            m = re.match(r"^/en/([a-z0-9-]+)/$", href)
            if m:
                targets.append(m.group(1))
        out[page["slug"]] = targets
    return out


def crawl_links(payload: dict) -> dict:
    """现算图。返回的每一项都是判据的输入，不在这里下结论。"""
    slugs = [p["slug"] for p in payload["pages"]]
    known = set(slugs)
    links = body_links(payload)
    domain = payload["domain"].rstrip("/")

    broken: list[str] = []
    inbound = {s: 0 for s in slugs}
    for src, targets in links.items():
        for t in targets:
            if t == src:
                broken.append(f"{src} 正文自链（自我引用不算入链也不算出链）")
                continue
            if t not in known:
                broken.append(f"{src} 的正文内链 `/en/{t}/` 不在内容源页面集合里 ⇒ 断链")
                continue
            inbound[t] += 1

    # 生成物面：CTA、面包屑与 noindex 都在 HTML 里，不在 JSON 里
    import build_seo_pages as B

    cta_problems: list[str] = []
    crumb_problems: list[str] = []
    reach_edges: dict[str, set[str]] = {s: set(links[s]) for s in slugs}
    for page in payload["pages"]:
        slug = page["slug"]
        html = B.page_html(page, payload)
        hrefs = HTML_LINK.findall(html)
        ids = set(HTML_ID.findall(html))
        bare = sum(1 for h in hrefs if h == "#")
        if bare:
            cta_problems.append(f"{slug} 的生成物有 {bare} 个裸 href=\"#\" ⇒ 点了不回任何东西（G09 同判）")
        dangling = sorted({h for h in hrefs if h.startswith("#") and h != "#" and h[1:] not in ids})
        if dangling:
            broken.append(f"{slug} 的生成物锚点 {dangling} 在本文档里没有对应的 id ⇒ 断链")

        cta_def = payload["cta_definitions"].get(page["cta"])
        if cta_def is None:
            cta_problems.append(f"{slug} 的 cta `{page['cta']}` 不在 cta_definitions 里")
        else:
            if FAKE_CTA_WORDS.search(cta_def["label"]):
                cta_problems.append(f"{slug} 的 CTA 文案是假按钮：{cta_def['label']!r}")
            host = urlparse(cta_def["href"]).netloc.lower()
            if host in STORE_HOSTS or host.endswith(".appstore.com"):
                cta_problems.append(f"{slug} 的 CTA 指向商店域 {host} ⇒ 现在没有商店链接")
            if not cta_def["href"].startswith(domain):
                cta_problems.append(f"{slug} 的 CTA 不是站内动作：{cta_def['href']!r}")
            else:
                path = urlparse(cta_def["href"]).path
                frag = urlparse(cta_def["href"]).fragment
                route = WEB_ROUTES.get(path) or WEB_ROUTES.get(path.rstrip("/") + "/")
                if route is None and not path.startswith("/en/"):
                    cta_problems.append(f"{slug} 的 CTA 指到站内不存在的路径 {path!r}")
                elif route and frag and frag not in _web_ids(route):
                    cta_problems.append(f"{slug} 的 CTA 锚点 #{frag} 在 {route} 里不存在")

        if 'aria-label="Breadcrumb"' not in html:
            crumb_problems.append(f"{slug} 的生成物没有面包屑导航")
        else:
            crumb_href = re.search(r'aria-label="Breadcrumb"[^>]*><a href="([^"]+)"', html)
            if not crumb_href:
                crumb_problems.append(f"{slug} 的面包屑首项不是链接")
            else:
                u = urlparse(crumb_href.group(1))
                if u.netloc and f"{u.scheme}://{u.netloc}" != domain:
                    crumb_problems.append(f"{slug} 的面包屑指向站外 {u.netloc}")
                elif u.path in ("/", ""):
                    if not _web_exists("index.html"):
                        crumb_problems.append(f"{slug} 的面包屑指回首页，而 web/index.html 不存在")
                elif not re.match(r"^/en/[a-z0-9-]+/$", u.path):
                    crumb_problems.append(
                        f"{slug} 的面包屑首项 {u.path!r} 既不是站点根，也不是内容源里的页面 ⇒ 断链"
                    )

    # BFS：从 hub 沿正文内链走
    seen = {HUB_SLUG: 0}
    q = deque([HUB_SLUG])
    while q:
        cur = q.popleft()
        if seen[cur] >= HOPS_MAX:
            continue
        for nxt in reach_edges.get(cur, ()):
            if nxt in known and nxt not in seen:
                seen[nxt] = seen[cur] + 1
                q.append(nxt)
    far = sorted(s for s in slugs if s not in seen)

    sitemap = set(B.sitemap_urls(payload))
    want = {f"{domain}/en/{s}/" for s in slugs}
    sm_problems = []
    if sitemap != want:
        sm_problems.append(f"sitemap 与页面集合不同源：多 {sorted(sitemap - want)} 少 {sorted(want - sitemap)}")
    live_sitemap = (WEB / "sitemap.xml").read_text(encoding="utf-8") if _web_exists("sitemap.xml") else ""
    leaked = sorted(s for s in slugs if f"/en/{s}/" in live_sitemap)
    if leaked:
        sm_problems.append(f"未发布的支柱页出现在现网 sitemap：{leaked} ⇒ 等于替 KG-02 提前提交")

    robots = (WEB / "robots.txt").read_text(encoding="utf-8") if _web_exists("robots.txt") else ""
    robots_problems = []
    if re.search(r"^Disallow:\s*/en/?\s*$", robots, re.M):
        robots_problems.append("web/robots.txt 把 /en/ 挡了，但内容源的页面全在 /en/ 下")

    return {
        "slugs": slugs,
        "links": links,
        "inbound": inbound,
        "hops": seen,
        "broken": broken,
        "cta": cta_problems,
        "crumb": crumb_problems,
        "far": far,
        "sitemap": sm_problems,
        "robots": robots_problems,
        "edges": sum(len(v) for v in links.values()),
    }


def web_offenders() -> dict[tuple[str, str], int]:
    """现网面的 offender 计数：裸 `#`、指向不存在文件的链接、指向未发布支柱页的链接。

    这里**只报不改**（台账 §KG-17 的处置属于文案面）。结果交给 `--check` 与
    `docs/seo/internal-links-2026-11.md` §5 的在册表逐条对拉。
    带**条数**而不带去重集合，是因为「同一个坏链接出现 5 次」和「出现 1 次」修法不同，
    去重会把 4 条悄悄坏掉的日子藏起来。
    """
    out: dict[tuple[str, str], int] = {}

    def hit(name: str, href: str) -> None:
        out[(name, href)] = out.get((name, href), 0) + 1

    for f in sorted(WEB.glob("*.html")):
        text = f.read_text(encoding="utf-8")
        ids = set(HTML_ID.findall(text))
        for h in HTML_LINK.findall(text):
            if h.startswith(("mailto:", "tel:")):
                if h.split(":", 1)[1] != ALLOWED_MAILTO:
                    hit(f.name, h)
                continue
            if h == "#":
                hit(f.name, h)
                continue
            if h.startswith("#"):
                if h[1:] not in ids:
                    hit(f.name, h)
                continue
            ext = h.startswith(("http://", "https://"))
            if ext and not urlparse(h).netloc.lower().endswith("closenai.app"):
                continue                        # 站外链接不在本闸判据内
            path = urlparse(h).path if ext else h.split("#", 1)[0]
            frag = h.split("#", 1)[1] if "#" in h else ""
            route = WEB_ROUTES.get(path if path.startswith("/") else "/" + path)
            if route:
                if frag and frag not in _web_ids(route):
                    hit(f.name, h)
            elif frag and path.endswith(".html") and _web_exists(path):
                if frag not in _web_ids(path):
                    hit(f.name, h)
            elif not _web_exists(path.lstrip("/")):
                hit(f.name, h)                  # 含未发布的 /en/<slug>/ 与真的缺文件
    return out


LEDGER_ROW = re.compile(r"^\|\s*`(web/[a-z0-9._-]+)`\s*\|\s*`([^`]*)`\s*\|\s*(\d+)\s*\|")


def ledger_entries() -> dict[tuple[str, str], int]:
    """从文档 §5 的在册表读出「人承认坏了」的那几条（含条数）。"""
    if not LEDGER_DOC.is_file():
        return {}
    section = LEDGER_DOC.read_text(encoding="utf-8").split("## 5. 现网面在册缺陷", 1)
    if len(section) < 2:
        return {}
    rows: dict[tuple[str, str], int] = {}
    for line in section[1].splitlines():
        m = LEDGER_ROW.match(line.strip())
        if m:
            rows[(m.group(1).replace("web/", ""), m.group(2))] = int(m.group(3))
    return rows


def violations(g: dict, off: dict[tuple[str, str], int], ledger: dict[tuple[str, str], int]) -> list[str]:
    out: list[str] = []
    out += [f"断链：{b}" for b in g["broken"]]
    for slug, n in sorted(g["inbound"].items()):
        if n < INBOUND_MIN:
            out.append(f"孤儿页：`{slug}` 只有 {n} 条入链，台账 §KG-17 的下限是 {INBOUND_MIN}")
    for slug in g["far"]:
        out.append(f"不可达：从 `{HUB_SLUG}` 走 {HOPS_MAX} 跳到不了 `{slug}`")
    out += [f"G09/CTA：{c}" for c in g["cta"]]
    out += [f"面包屑：{c}" for c in g["crumb"]]
    out += [f"sitemap：{s}" for s in g["sitemap"]]
    out += [f"robots：{r}" for r in g["robots"]]
    if not ledger:
        out.append("现网面在册表是空的——要么 §5 没写，要么爬虫换了口径（归零闸要配在册证人）")
    else:
        missing = sorted(set(off) - set(ledger))
        stale = sorted(set(ledger) - set(off))
        if missing:
            out.append(f"现网面新坏 {len(missing)} 条未登记：{missing}")
        if stale:
            out.append(f"在册表有 {len(stale)} 条已不坏了却不注销（修完要回头删 §5）：{stale}")
        drift = sorted((k, off[k], ledger[k]) for k in set(off) & set(ledger) if off[k] != ledger[k])
        if drift:
            out.append(f"在册条数与现算不符（文件, 现算, 在册）：{drift}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KG-17 内链/CTA/孤儿页爬虫闸")
    ap.add_argument("--check", action="store_true", help="只输出不合格项，exit≠0 即红")
    ap.add_argument("--json", action="store_true", help="机器可读：计数＋不合格项")
    ap.add_argument("--self-test", action="store_true", help="造一个孤儿页＋清空在册表，证明这两条闸会红")
    args = ap.parse_args(argv)

    payload = load_payload()
    g = crawl_links(payload)
    off = web_offenders()
    ledger = ledger_entries()
    hits = violations(g, off, ledger)

    if args.self_test:
        if hits:
            print(f"[self-test] 基线不干净，先修再自证：{hits[:3]}")
            return 1
        victim = sorted(g["slugs"])[-1]
        g2 = copy.deepcopy(g)
        g2["inbound"] = {**g["inbound"], victim: 0}
        orphan = [v for v in violations(g2, off, ledger) if v.startswith(f"孤儿页：`{victim}`")]
        no_ledger = [v for v in violations(g, off, {}) if v.startswith("现网面在册表是空的")]
        ok = bool(orphan) and bool(no_ledger)
        print(f"[self-test] 把 `{victim}` 的入链清零 → 判成孤儿：{'✓ 会红' if orphan else '✗ 闸失效'}")
        print(f"[self-test] 清空在册表（§5 被删或换了标题）→ 判成失效：{'✓ 会红' if no_ledger else '✗ 闸失效'}")
        print(f"[self-test] 基线本身是绿的：✓（否则自证无意义）")
        return 0 if ok else 1

    if args.json:
        print(json.dumps({
            "ok": not hits,
            "pages": len(g["slugs"]),
            "edges": g["edges"],
            "inbound": g["inbound"],
            "hops": g["hops"],
            "web_offenders": {f"web/{f}::{h}": n for (f, h), n in sorted(off.items())},
            "ledger_entries": {f"web/{f}::{h}": n for (f, h), n in sorted(ledger.items())},
            "inbound_min": INBOUND_MIN,
            "hops_max": HOPS_MAX,
            "violations": hits,
        }, ensure_ascii=False))
        return 0 if not hits else 1

    if args.check:
        if hits:
            print("KG-17 爬虫闸不合格：", file=sys.stderr)
            for h in hits:
                print(f"  - {h}", file=sys.stderr)
            return 1
        print(f"内链/CTA/孤儿页判据全绿：{len(g['slugs'])} 页／{g['edges']} 条出链／"
              f"入链下限 {INBOUND_MIN}／{HOPS_MAX} 跳内从 {HUB_SLUG} 全可达")
        return 0

    print(f"页面 {len(g['slugs'])} 个，正文内链 {g['edges']} 条")
    for slug in g["slugs"]:
        print(f"  {slug:<28} 出链 {len(g['links'][slug])}  入链 {g['inbound'][slug]}  "
              f"距 hub {g['hops'].get(slug, '—')} 跳")
    print(f"现网面 offender {sum(off.values())} 条（{len(off)} 种），在册 {sum(ledger.values())} 条（{len(ledger)} 种）")
    for (f, h), n in sorted(off.items()):
        print(f"  web/{f}  {h}  ×{n}")
    if hits:
        print("不合格：", file=sys.stderr)
        for h in hits:
            print(f"  - {h}", file=sys.stderr)
        return 1
    print("判据全绿")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

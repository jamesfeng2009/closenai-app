"""G3 · 隐私政策/条款两端断链的闸门（站点侧没有测试运行器，闸按 test_k8s_manifests.py 的先例放这里）。

这批要防的不是「页面写得不好看」，而是两类会重新把链接断开的回归：

1. **App 指过去了、站点没有那一页**（这次的原始故障）：App 内两条常驻法务入口
   （frontend/src/config/links.ts:7-8）写的是 `closenai.app/privacy` 与 `/terms`，
   而站点当时只有落地页——两条都是 404。所以闸的正题是「App 常量指的路径有没有
   对应的页面源文件」，不是「页面里有没有 privacy 这个词」。
2. **两种语言说的不是同一件事**：中英两份正文按同序同数组织，少一段就红。
   这类页面最坏的情况是一种语言承诺了另一种语言没承诺的东西。

外加三条本来就会漏的：占位商店链不许回到会被线上访问的源（与
test_share_landing_fallback.py 同一口径，那条只管 Python/Go，本文件补站点与 App 侧）、
`href="#"` 这类「看起来能点、点了没反应」的死链、以及安全那一节必须保持
「不自称端到端加密」那句实话（写得越漂亮越容易顺手改掉它）。
"""
import re
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SITE = ROOT / "site"
SITE_SRC = SITE / "src"
SITE_PUBLIC = SITE / "public"

LEGAL_TS = (SITE_SRC / "legal.ts").read_text(encoding="utf-8")
I18N_TS = (SITE_SRC / "i18n.ts").read_text(encoding="utf-8")
SITE_LINKS = (SITE_SRC / "config" / "links.ts").read_text(encoding="utf-8")
APP_LINKS = (ROOT / "frontend" / "src" / "config" / "links.ts").read_text(encoding="utf-8")
LANDING = (SITE_SRC / "components" / "Landing.astro").read_text(encoding="utf-8")
LEGAL_ASTRO = (SITE_SRC / "components" / "Legal.astro").read_text(encoding="utf-8")
BASE_ASTRO = (SITE_SRC / "layouts" / "Base.astro").read_text(encoding="utf-8")
R_LANDING = (SITE_PUBLIC / "r" / "index.html").read_text(encoding="utf-8")
REDIRECTS = (SITE_PUBLIC / "_redirects").read_text(encoding="utf-8")

# App 常量里的路径 ↔ 站点页面源文件（闸的第 1 条判据就落在这张表上）
LEGAL_PATHS = {
    "/privacy": "pages/privacy.astro",
    "/terms": "pages/terms.astro",
    "/zh/privacy": "pages/zh/privacy.astro",
    "/zh/terms": "pages/zh/terms.astro",
}

# 一条正文 = 换行 + 10 个空格 + 单引号。条数比对建立在「每条独占一行」之上，
# 所以这个排版前提本身也要有闸（见 test_legal_body_is_canonical），
# 否则「把两条并成一行」就能让中英条数假性相等。
_BODY_ITEM = "\n" + " " * 10 + "'"


def _doc(locale: str, kind: str) -> str:
    """取 legal.ts 里某个语言某个文档的对象体（按花括号配对切，正文里没有花括号）。"""
    head = f"const {locale}: Record<LegalKind, LegalDoc> = {{"
    start = LEGAL_TS.index(head)
    key = f"\n  {kind}: {{"
    open_at = LEGAL_TS.index(key, start) + len(key) - 1
    depth = 0
    for i in range(open_at, len(LEGAL_TS)):
        ch = LEGAL_TS[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return LEGAL_TS[open_at + 1 : i]
    raise AssertionError(f"legal.ts 的 {locale}.{kind} 没闭合")


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/|<!--.*?-->", re.S)


def _code_lines(text: str):
    """只返回代码行的 (行号, 内容)：注释里可以讲这段历史，不许讲成一个真的值。

    与 test_share_landing_fallback.py 同一口径（那里按 `#`/`//`/`*` 前缀滤），本文件
    多了 .astro/.html，所以整块注释（`/* */`、`<!-- -->`）要按块剥掉——块内续行不
    带前缀，逐行前缀滤会把注释正文当成代码。
    """
    out = []
    for no, line in enumerate(_BLOCK_COMMENT.sub("", text).splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("#"):
            continue
        out.append((no, line))
    return out


# ── 1. 断链本身：App 指的每一条路径都得有页面源文件，且真的渲染出正文 ──

@pytest.mark.parametrize("path,rel", sorted(LEGAL_PATHS.items()))
def test_app_legal_url_has_a_site_page(path, rel):
    page = SITE_SRC / rel
    assert page.exists(), f"App 常量指向 {path}，但站点没有 {page.relative_to(ROOT)}"
    src = page.read_text(encoding="utf-8")
    kind = "privacy" if "privacy" in rel else "terms"
    lang = "zh" if rel.startswith("pages/zh/") else "en"
    assert f'path="{path}"' in src, f"{rel} 没把 canonical 路径写成 {path}（canonical 与实链不一致）"
    assert f'<Legal lang="{lang}" kind="{kind}" />' in src, f"{rel} 没渲染 {lang}/{kind}"


def test_app_constants_are_what_the_pages_claim():
    """App 侧两条 URL 的**主机**必须与站点声明的站点根一致，路径必须落在上面那张表里。"""
    hosts = re.findall(r"= '(https://[^/']+)(/[^']*)';", APP_LINKS)
    pairs = {p: h for h, p in hosts}
    assert pairs["/privacy"].endswith("closenai.app"), pairs
    assert pairs["/terms"].endswith("closenai.app"), pairs
    assert set(pairs) <= set(LEGAL_PATHS), f"App 指了站点上不存在的法务路径: {set(pairs) - set(LEGAL_PATHS)}"


def test_support_email_is_the_same_value_on_both_ends():
    """两端联系邮箱逐字相同：不一致的表现是用户在 App 里看到的地址发不到站点那侧的收件箱。"""
    site_email = re.search(r"export const SUPPORT_EMAIL = '([^']+)'", SITE_LINKS).group(1)
    app_email = re.search(r"SUPPORT_EMAIL = '([^']+)'", APP_LINKS).group(1)
    assert site_email == app_email == "support@closenai.app"
    assert "support@closenai.app" in _doc("EN", "privacy") and "support@closenai.app" in _doc("ZH", "privacy")


def test_extensionless_legal_paths_are_rewritten_to_the_pages():
    """App 写的是无扩展名的 /privacy，Astro 目录式构建产出的是 privacy/index.html。

    这一跳不赌 Pages 的 pretty-URL 推断：写在 _redirects 里才是确定的，
    而赌输的表现是提审时点开隐私政策是 404。
    """
    lines = [ln.strip() for ln in REDIRECTS.splitlines() if ln.strip() and not ln.startswith("#")]
    rules = {ln.split()[0]: ln.split()[1:] for ln in lines}
    for path in LEGAL_PATHS:
        assert path in rules, f"_redirects 没有 {path} 的重写"
        assert rules[path] == [f"{path}/index.html", "200"], f"{path} 的重写不是 200 → index: {rules[path]}"


# ── 2. 死链：会被线上访问的源里不许留 href="#" ──

@pytest.mark.parametrize("name,src", [
    ("site/src/components/Landing.astro", LANDING),
    ("site/src/components/Legal.astro", LEGAL_ASTRO),
    ("site/public/r/index.html", R_LANDING),
])
def test_no_dead_hash_links(name, src):
    hits = [f"{no}: {ln.strip()}" for no, ln in _code_lines(src) if 'href="#"' in ln]
    assert not hits, f"{name} 里还有点开没反应的 href=\"#\":\n" + "\n".join(hits)


def test_download_area_uses_the_shared_constant_and_has_an_unreleased_state():
    """下载区与站点常量同源，且未配置商店链时渲染说明而不是按钮。"""
    assert "import { APP_STORE_URL, homePath, MAILTO, privacyPath, termsPath } from '../config/links'" in LANDING
    assert "const storeReady = APP_STORE_URL !== '';" in LANDING
    assert "href={APP_STORE_URL}" in LANDING
    assert "{t.fin.unreleased}" in LANDING
    assert "href={privacyPath(lang)}" in LANDING and "href={termsPath(lang)}" in LANDING
    assert "href={MAILTO}" in LANDING


def test_privacy_section_links_to_the_full_policy():
    """摘要卡片说的是「能导出/能删」，全文必须一跳可达（页脚之外再多一条入口）。"""
    assert '<p class="priv-more reveal"><a href={privacyPath(lang)}>{t.pri.policyLink}</a></p>' in LANDING
    assert I18N_TS.count("policyLink:") == 2, "policyLink 只许中英各一份（多了说明有重复键）"
    assert I18N_TS.count("unreleased:") == 2, "unreleased 只许中英各一份"


# ── 3. 中英同构：同序同数，且没有一边是空的 ──

@pytest.mark.parametrize("kind", ["privacy", "terms"])
def test_en_and_zh_have_the_same_section_shape(kind):
    en, zh = _doc("EN", kind), _doc("ZH", kind)
    heads = {"en": re.findall(r"h: '(.*)'", en), "zh": re.findall(r"h: '(.*)'", zh)}
    assert len(heads["en"]) == len(heads["zh"]), (
        f"{kind} 中英小节数不一致（en {len(heads['en'])} / zh {len(heads['zh'])}）：两种语言说的不是同一件事")
    # 段落与列表条数逐文档比对：整节对得上、少一段照样红。
    # 「条数」按每条正文独占一行来数，这条排版前提由下面的 test_legal_body_is_canonical 守住。
    assert en.count("ps: [") == zh.count("ps: ["), f"{kind} 的 ps 块数不一致"
    assert en.count("ul: [") == zh.count("ul: ["), f"{kind} 的 ul 块数不一致"
    n_en, n_zh = en.count(_BODY_ITEM), zh.count(_BODY_ITEM)
    assert n_en == n_zh, f"{kind} 正文条数不一致（en {n_en} / zh {n_zh}）：两种语言说的不是同一件事"


def test_legal_body_is_canonical():
    """正文数组必须一条一行、每条以单引号开头：`ps: [` / `ul: [` 后直接跟换行。

    这条不是排版洁癖。上面那组同构闸数的是「换行+缩进+引号」的条数，两种改法都能
    让计数凭空少一条而中英各少一条还能对上：把两条并到一行，或把某条换成反引号
    （为了写 an apostrophe 而换引号是最顺手的一种）。于是「少一段承诺」这种最该红
    的形态反而绿了。先守住格式，条数比对才是一条真的判据。
    """
    inline = [
        f"{no}: {line.strip()[:70]}"
        for no, line in enumerate(LEGAL_TS.splitlines(), 1)
        if re.search(r"(ps|ul): \[.+\]", line)
    ]
    assert not inline, "正文写成了单行数组，条数闸会漏数:\n" + "\n".join(inline)
    odd_quote = [
        f"{no}: {line.strip()[:70]}"
        for no, line in enumerate(LEGAL_TS.splitlines(), 1)
        if re.match(r'^\s{6,}["`]', line)
    ]
    assert not odd_quote, "正文用了反引号/双引号开头，条数闸会漏数:\n" + "\n".join(odd_quote)


@pytest.mark.parametrize("kind", ["privacy", "terms"])
def test_every_section_has_body(kind):
    """只有标题、没有正文的小节＝写作中途被截断，而产物看起来是一篇完整的政策。"""
    for locale in ("EN", "ZH"):
        text = _doc(locale, kind)
        blocks = re.split(r"\n      \{\n", text)
        for b in blocks[1:]:
            assert "ps: [" in b or "ul: [" in b, f"{locale}.{kind} 有一节没有正文: {b[:60]!r}"


@pytest.mark.parametrize("kind", ["privacy", "terms"])
def test_zh_is_not_an_english_copy(kind):
    """中文行不许逐字节等于英文行（真正的专有名词与邮箱按整行相等才豁免）。"""
    allow = re.compile(r"^(PostHog|Sentry|Supabase|Upstash|Resend|Apple|Google|Cloudflare|ClosenAI|iOS|HTTPS|Argon2|argon2)")
    for locale, other in (("ZH", "EN"), ("EN", "ZH")):
        zh_lines = set(re.findall(r"\n          '([^']{6,})'", _doc(locale, kind)))
        en_lines = set(re.findall(r"\n          '([^']{6,})'", _doc(other, kind)))
        same = {s for s in zh_lines & en_lines if not allow.match(s)}
        assert not same, f"{locale}.{kind} 有 {len(same)} 行与另一语言逐字节相同: {list(same)[:2]}"


# ── 4. 采集事实清单：代码里有的处理方与开关，页面上必须说 ──

# 每一项都能在 backend/config.py / backend/models/ 里指回代码（见 site/src/legal.ts 头注释的出处表）
MANDATED = [
    ("PostHog", "PostHog"),
    ("Sentry", "Sentry"),
    ("Supabase", "Supabase"),
    ("QStash/Upstash", "QStash"),
    ("Redis", "Redis"),
    ("Resend", "Resend"),
    ("APNs", "APNs"),
    ("FCM", "FCM"),
    ("DashScope（通义）", "DashScope"),
    ("大模型服务商", "DashScope"),
    ("Cloudflare", "Cloudflare"),
    ("App Store 收款", "App Store"),
    ("援助热线", "热线"),
    ("AI 同意开关", "AI 数据共享同意"),
    ("分析同意开关", "分析"),
    ("iOS 钥匙串", "钥匙串"),
    ("argon2", "argon2"),
    ("刷新令牌", "刷新令牌"),
    ("验证码哈希", "哈希"),
    ("注销物理删除", "物理删除"),
    ("分析侧抹除", "抹除"),
    ("危机摘录", "摘录"),
]

MANDATED_EN = [
    ("PostHog", "PostHog"),
    ("Sentry", "Sentry"),
    ("Supabase", "Supabase"),
    ("QStash", "QStash"),
    ("Redis", "Redis"),
    ("Resend", "Resend"),
    ("APNs", "APNs"),
    ("FCM", "FCM"),
    ("DashScope", "DashScope"),
    ("model provider", "Model providers"),
    ("Cloudflare", "Cloudflare"),
    ("App Store", "App Store"),
    ("helpline", "helpline"),
    ("AI consent", "AI data-sharing consent"),
    ("analytics consent", "analytics"),
    ("iOS Keychain", "Keychain"),
    ("argon2", "argon2"),
    ("refresh tokens", "refresh tokens"),
    ("hashed", "hashed"),
    ("physical delete", "physical delete"),
    ("erase", "erase"),
    ("excerpt", "excerpt"),
]


def test_privacy_page_names_every_processor_and_switch():
    """漏一个处理方是一类错误：代码里那一家在收数据，页面上却没有它。"""
    doc = _doc("ZH", "privacy")
    missing = [label for label, needle in MANDATED if needle not in doc]
    assert not missing, f"中文隐私政策缺这些采集事实: {missing}"
    doc_en = _doc("EN", "privacy")
    missing_en = [label for label, needle in MANDATED_EN if needle not in doc_en]
    assert not missing_en, f"英文隐私政策缺这些采集事实: {missing_en}"


def test_security_section_does_not_claim_end_to_end_encryption():
    """这一句是刻意写的实话：内容在库里是明文行列、只有访问控制。

    把它「润色」成「加密存储」是本文件最贵的一类回归——它不是措辞问题，
    而是一条提审与用户都会当成事实核对的承诺。
    """
    assert "not end-to-end encrypted" in _doc("EN", "privacy")
    assert "不是端到端加密" in _doc("ZH", "privacy")
    for doc in (_doc("EN", "privacy"), _doc("ZH", "privacy")):
        assert "AES-256" not in doc and "SSL" not in doc


def test_terms_disclaims_that_it_is_not_therapy():
    en, zh = _doc("EN", "terms"), _doc("ZH", "terms")
    assert "not therapy" in en and "emergency" in en
    assert "不是心理咨询" in zh, "中文条款没写明它不是心理咨询"
    assert "紧急" in zh or "急救" in zh, "中文条款没把「有直接危险时找急救」说出去"


def test_updated_date_is_a_date_and_is_rendered():
    date = re.search(r"export const LEGAL_UPDATED = '([^']+)'", LEGAL_TS).group(1)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date), f"LEGAL_UPDATED 不是日期: {date}"
    assert "{LEGAL_UPDATED}" in LEGAL_ASTRO, "Legal.astro 没把日期渲染出来"


def test_pages_pass_their_own_title_and_description():
    """四条页面各自的 <title>：沿用落地页标题会让四种页面在搜索结果里同名。"""
    for rel in sorted(LEGAL_PATHS.values()):
        src = (SITE_SRC / rel).read_text(encoding="utf-8")
        assert "title={LEGAL." in src and "description={LEGAL." in src, f"{rel} 没传自己的标题/描述"
    assert "const PAGE_TITLE = Astro.props.title ?? t.meta.title;" in BASE_ASTRO
    assert "<title>{PAGE_TITLE}</title>" in BASE_ASTRO


def test_faq_structured_data_stays_on_the_landing_only():
    """FAQPage 抄到法务页＝每个页面都在回答同一批问题；这里把它钉在落地页条件里。"""
    assert "if (IS_LANDING) {" in BASE_ASTRO
    assert "'@type': 'WebPage'" in BASE_ASTRO


# ── 5. 占位值不许留在会被线上访问的源里（与 U3-b 同一口径，补站点与 App 侧）──

PLACEHOLDER_ID = "id6740000000"
PLACEHOLDER_TEAM = "TEAMID."


def _served_web_sources():
    for base in (SITE_SRC, SITE_PUBLIC, ROOT / "frontend" / "src"):
        for p in base.rglob("*"):
            if p.is_file() and p.suffix in {".ts", ".tsx", ".astro", ".html", ".mjs", ".json"}:
                yield p


def test_app_store_placeholder_is_not_in_any_served_web_source():
    """占位商店链一旦出现在会被访问的源里，就是发一条 404 给用户（见 U3-b 的根因）。

    注释里允许写这段历史（「曾经有这个占位」），值里不许有——与
    test_share_landing_fallback.py::test_placeholder_id_is_not_in_any_production_source
    同一判据，只是把扫描面从 Python/Go 换到站点与 App 的源文件。
    """
    offenders = []
    for p in _served_web_sources():
        text = p.read_text(encoding="utf-8")
        for no, line in _code_lines(text):
            if PLACEHOLDER_ID in line or PLACEHOLDER_TEAM in line:
                offenders.append(f"{p.relative_to(ROOT)}:{no}: {line.strip()[:90]}")
    assert not offenders, "线上源里还有占位值:\n" + "\n".join(offenders)


def test_share_landing_falls_back_to_the_site_not_a_fake_store_link():
    """分享落地页的 CTA 在无真商店链时必须落到站点（不 404），且带得上法务两页。"""
    assert "var SITE_HOME = 'https://closenai.app/';" in R_LANDING
    assert 'href="https://closenai.app/"' in R_LANDING
    assert "/privacy" in R_LANDING and "/terms" in R_LANDING
    assert "apps.apple.com" not in R_LANDING, "落地页里又出现了商店直链——未上架时它只会是占位"


def test_site_has_no_hardcoded_app_store_url():
    """站点侧唯一允许的商店链来源是 PUBLIC_APP_STORE_URL（env-only，默认空）。"""
    assert "readEnv('PUBLIC_APP_STORE_URL')" in SITE_LINKS
    offenders = [
        f"{p.relative_to(ROOT)}:{no}"
        for p in _served_web_sources()
        for no, line in _code_lines(p.read_text(encoding="utf-8"))
        if "apps.apple.com" in line
    ]
    assert not offenders, f"站点/App 源里写死了商店链:\n" + "\n".join(offenders)


def test_aasa_placeholder_still_has_its_guard():
    """AASA 的占位 Team ID 由构建期生成器覆盖；守卫被删掉时这条红。

    占位串的表现是 Universal Links **静默不唤起**（两端都不报错），
    所以它只能以「构建期一定会被替换 + --check 一定非零退出」的形态存在。
    """
    pkg = (SITE / "package.json").read_text(encoding="utf-8")
    assert '"aasa:check"' in pkg and '"aasa"' in pkg
    aasa = (SITE_PUBLIC / ".well-known" / "apple-app-site-association").read_text(encoding="utf-8")
    assert PLACEHOLDER_TEAM in aasa or re.search(r"\"appIDs\": \[\s*\"[A-Z0-9]{10}\.", aasa), \
        "AASA 既不是占位也不是合法 Team ID 形态——生成器的判据要跟着改"

"""N8 批 4（onboarding 域）的跨语言逐字闸——每域四步的第④步。

被验对象：`go-core/internal/domain/{first_read,first_read_gate}.go`（生成层＋措辞闸）、
`internal/httpapi/{onboarding_submit,onboarding_read,onboarding_body,batch4}.go`（对外形状）、
`internal/store/legacy_batch4.go`（三处写＋推荐码）、`internal/config/config.go` 与
`cmd/go-api/main.go`（灰度键）、`deploy/k8s/{10-config,50-ingress}.yaml`（放量面）、
`contracts/openapi.json` 里这两条的 deprecated，以及夹具 onboarding 组（28 条）＋
template_corpus（106）／persona_upper_corpus（130）／referral_corpus（11）三块语料。

为什么这一批的闸比批 1–3 更像「比字面量」而不是「比行为」：Go 包不能被 pytest 调用，
所以两侧只能在**同一份事实**上会师。这里用的是三段式，缺一段就有空转的格子：
1. Python 侧现算（`safety_service.filter_template`、`str.upper()`、`referral_link`）——本文件；
2. 夹具存着 Python 当时的结果（`go-core/testdata/n8_contract.json`）——由
   `scripts/gen_n8_contract_goldens.py` 从真 FastAPI＋真 PG 录出；
3. Go 侧就地重算并与夹具比——`internal/httpapi/n8_template_corpus_test.go` 等。
⇒ 本文件证「夹具 == Python」，Go 测证「Go == 夹具」，两段的**输入是同一份文件**，
   所以合起来就是「Go == Python」。本文件因此还额外钉住 Python 侧与 Go 侧的
   **源码字面量**（词表、键序、上限、默认值、掩码正则）：一旦有人在任一侧改了定义而
   语料碰不到那一格，行号级的文本比对会先红，不会等放量时才在现网发现。

口径来源（逐条点名到行）：
- BANNED_TERMS / ATTRIBUTION_MARKERS  ← safety_service.py:224-268 ↔ first_read_gate.go:22-53
- _lang_key vs locale_for             ← safety_service.py:26-28 / first_read_service.py:45-47
                                          ↔ first_read_gate.go::SafetyLangKey / first_read.go::LocaleFor
- 九键 card_payload                   ← first_read_service.py:154-164 ↔ onboarding_submit.go::firstReadCardPayload
- 外层四键                            ← first_read_service.py:165-170 ↔ onboarding_submit.go::firstReadOuter
- restore 六键就地覆盖                ← first_read_service.py:211-220 ↔ onboarding_read.go::handleFirstReadRestore
- 三处写的三个条件                    ← first_read_service.py:182-195 ↔ legacy_batch4.go::SubmitOnboarding:160-198
- max_length=10/30/40/10、[A-Z]{4}    ← models/schemas.py:496-516     ↔ onboarding_body.go:34-44
- REFERRAL_ALPHABET / rstrip('/')     ← core/referral.py:13-14,37      ↔ legacy_batch4.go:34 / batch4.go:49-51
- 四条掩码正则                        ← gen_n8_contract_goldens.py:48-65 ↔ n8_replay_test.go:40-82
- CLOSEN_CUTOVER_ONBOARDING=0         ← config/config.go:77 ↔ 10-config.yaml ↔ main.go:72 ↔ 50-ingress.yaml:62
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from backend.config import settings
from backend.core.referral import REFERRAL_ALPHABET, REFERRAL_CODE_LEN, referral_link
from backend.main import app
from backend.models.schemas import FirstReadAnswer, FirstReadRequest
from backend.services.first_read_service import (
    DATA_DIR,
    MAX_ASSERTIONS,
    MIN_ASSERTIONS,
    _LOCALES,
    first_read_service,
    locale_for,
)
from backend.services.safety_service import SafetyService, _lang_key

ROOT = Path(__file__).resolve().parents[2]
GO = ROOT / "go-core"
FIXTURE = GO / "testdata" / "n8_contract.json"
MANIFESTS = ROOT / "deploy" / "k8s"

BATCH4_DOMAIN = "onboarding"
BATCH4_PATH = "/api/v1/onboarding/first-read"

G_GATE = "internal/domain/first_read_gate.go"
G_GEN = "internal/domain/first_read.go"
G_SUBMIT = "internal/httpapi/onboarding_submit.go"
G_READ = "internal/httpapi/onboarding_read.go"
G_BODY = "internal/httpapi/onboarding_body.go"
G_BATCH4 = "internal/httpapi/batch4.go"
G_STORE = "internal/store/legacy_batch4.go"
G_REPLAY = "internal/httpapi/n8_replay_test.go"
G_TEMPLATES = GO / "internal" / "domain" / "templates"

# 语料计数：改了语料就是改了判据面，必须两侧同时改、同时登记。
TEMPLATE_CORPUS_TOTAL = 106
TEMPLATE_CLOSURE = 92
TEMPLATE_PROBES = 14
TEMPLATE_REACHABLE = 56
PERSONA_CORPUS_TOTAL = 130
PERSONA_LEGAL = 34
REFERRAL_CORPUS_TOTAL = 11
ONBOARDING_CASES = 28


def _go(rel: str) -> str:
    return (GO / rel).read_text(encoding="utf-8")


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _code(text: str) -> str:
    """去掉整行注释——Go 的注释里带反引号与引号，混进 token 流会造出假条目。"""
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("//")
    )


_GO_TOKEN = re.compile(r'"((?:[^"\\\n]|\\.)*)"|`([^`\n]*)`')


def _go_tokens(text: str) -> list[str]:
    """按 Go 的字面量写法取字符串：双引号串可转义，反引号串是原始串。"""
    out: list[str] = []
    for match in _GO_TOKEN.finditer(_code(text)):
        quoted, raw = match.group(1), match.group(2)
        if quoted is None:
            out.append(raw)
        else:
            assert "\\" not in quoted, f"这批表里不该出现转义串：{quoted!r}"
            out.append(quoted)
    return out


def _block(text: str, head: str, indent: str = "") -> str:
    """取 `head` 之后到**同缩进**闭合括号之间的那一段（这些表都按 gofmt 排的）。"""
    start = text.index(head) + len(head)
    close = f"\n{indent}}}"
    end = text.index(close, start)
    return text[start:end]


def _go_banned_terms() -> dict[str, list[str]]:
    src = _code(_go(G_GATE))
    block = _block(src, "var bannedTerms = map[string][]string{")
    out: dict[str, list[str]] = {}
    for locale, body in re.findall(r'"(zh|en)": \{\n(.*?)\n\t\},', block, re.S):
        out[locale] = _go_tokens(body)
    assert len(out) == 2, sorted(out)
    return out


def _go_attribution_markers() -> dict[str, dict[str, list[str]]]:
    src = _code(_go(G_GATE))
    block = _block(src, "var attributionMarkers = map[string]map[string][]string{")
    out: dict[str, dict[str, list[str]]] = {}
    for locale, body in re.findall(r'"(zh|en)": \{\n(.*?)\n\t\},', block, re.S):
        groups: dict[str, list[str]] = {}
        for name, inner in re.findall(r'"(third|label|causal)": \{(.*?)\},', body, re.S):
            groups[name] = _go_tokens(inner)
            assert len(groups[name]) > 0, f"{locale}/{name} 解析成空表——写法变了，闸要跟着改"
        assert set(groups) == {"third", "label", "causal"}, (locale, sorted(groups))
        out[locale] = groups
    assert len(out) == 2, sorted(out)
    return out


def _go_span(rel: str, marker: str) -> str:
    """从 `marker`（必须是**声明**处，带 func 前缀）起，到该顶层函数/块闭合为止。"""
    src = _code(_go(rel))
    start = src.index(marker)
    return src[start : start + src[start:].index("\n}")]


def _go_calls(rel: str, marker: str, pattern: str) -> list[str]:
    """按**出现序**取函数体里匹配 pattern 的捕获组——键序就是出现序。"""
    return re.findall(pattern, _go_span(rel, marker))


def _dedup(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def _py_card_payload() -> tuple[list[str], list[str]]:
    """build_first_read 的九键与外层四键，按字面量书写序。"""
    src = (ROOT / "backend" / "services" / "first_read_service.py").read_text(
        encoding="utf-8"
    )
    block = re.search(
        r"card_payload = \{(.*?)\n        \}\n        return \{(.*?)\n        \}",
        src,
        re.S,
    )
    assert block, "build_first_read 的两段字面量写法变了，闸要跟着改"
    keys = lambda text: re.findall(r'^\s+"([a-z_]+)":', text, re.M)  # noqa: E731
    nine, four = keys(block.group(1)), keys(block.group(2))
    assert len(nine) == 9 and len(four) == 4, (len(nine), len(four))
    return nine, four


# ═══ 一、模板本体：两份 JSON 必须还是同一份文件 ═══


def test_both_embedded_templates_are_byte_identical_to_python_data():
    """Go 用 `go:embed` 自带一份模板副本（first_read.go:29-37 说明为什么不能跨 module 读
    backend/data），所以**分叉是静默的**：文案改了一侧、另一侧照旧，生成的卡片就不同，
    而两侧各自都「合法」。这里比 sha256，不比结构——结构相同而文案不同也是分叉。
    """
    for locale in _LOCALES:
        py = (DATA_DIR / f"{locale}.json").read_bytes()
        got = (G_TEMPLATES / f"first_read_{locale}.json").read_bytes()
        assert hashlib.sha256(py).hexdigest() == hashlib.sha256(got).hexdigest(), (
            f"{locale} 模板两侧分叉：Python {len(py)}B vs Go {len(got)}B"
        )


def test_templates_locales_are_exactly_two_and_default_en():
    """模板只有 zh/en 两份、其余语言一律回退 en（first_read_service.py:38-39 ↔ first_read.go
    的 firstReadTemplates 两格）。多出一格就是往闸里塞了一份没过措辞审校的文案。
    """
    assert _LOCALES == ("zh", "en")
    src = _go(G_GEN)
    assert re.search(r'var firstReadTemplates = map\[string\]\*FirstReadTemplate\{\s*\n'
                    r'\t"zh": .*?,\n\t"en": .*?,\n\}', _code(src), re.S), "Go 侧模板表不是恰好两格"


# ═══ 二、措辞闸：词表逐字＋顺序＋判据形状 ═══


def test_banned_terms_match_python_table_entry_by_entry_in_order():
    """黑名单两份表逐条同序（safety_service.py:224-242 ↔ first_read_gate.go:22-33）。

    比的是**序**而不只是集合：Go 侧注释（:21）写明「条目顺序也是 Python 的书写顺序：
    命中哪一条会进日志」。日志里报哪一条是运维面，但顺序一变本条就红，逼两侧同时改。
    """
    go = _go_banned_terms()
    assert set(go) == set(SafetyService.BANNED_TERMS) == {"zh", "en"}
    for locale in ("zh", "en"):
        assert go[locale] == list(SafetyService.BANNED_TERMS[locale]), (
            locale,
            [t for t in go[locale] if t not in SafetyService.BANNED_TERMS[locale]],
            [t for t in SafetyService.BANNED_TERMS[locale] if t not in go[locale]],
        )


def test_attribution_markers_match_python_table_entry_by_entry_in_order():
    """越界句式三组指纹逐字同序（safety_service.py:244-268 ↔ first_read_gate.go:37-53）。

    含 `(?<![其这那每])他` 这一条**原样**比过去：RE2 不支持后视，Go 在 :56-82 手写等价实现，
    所以字面量一旦被人「顺手改成 RE2 能编译的样子」，判据就悄悄变了——本条按字面钉死。
    """
    go = _go_attribution_markers()
    assert set(go) == set(SafetyService.ATTRIBUTION_MARKERS) == {"zh", "en"}
    for locale in ("zh", "en"):
        for name in ("third", "label", "causal"):
            assert go[locale][name] == list(
                SafetyService.ATTRIBUTION_MARKERS[locale][name]
            ), (locale, name)


def test_gate_semantics_are_the_replicated_ones_not_a_rewrite():
    """判据形状四处各钉一条字面量——它们都在语料覆盖面里，但形状改了就等价不了：

    - zh 走**纯子串**（`in`）：Go 必须是 strings.Contains，不是词边界（:165-172）；
    - en 只有**前**边界：`(?i)\\b` + QuoteMeta(term)，补上后边界会把 "heal" 从
      "healing" 里放出来，那是改判据（:92-107，注释自己写明了这点）；
    - 越界＝(third∧label)∨(third∧causal)，**third 必需**：先判 third、不中即 return false
      （:193-200 ↔ Python :337-354 的注释口径）；
    - 空白判定走 PyTrimSpace 而不是 strings.TrimSpace（Python 的 str.strip() 认 001C-001F）。
    """
    src = _go(G_GATE)
    assert "if strings.Contains(text, term) {" in src, "zh 支不再是纯子串"
    assert 'out = append(out, regexp.MustCompile(`(?i)\\b`+regexp.QuoteMeta(term)))' in src
    assert 'QuoteMeta(term) + `\\b`' not in src, "en 支被补上后边界＝改判据"
    assert "if !third {\n\t\treturn false\n\t}" in src, "third 不再是必需项"
    assert 'return groupHit(groups["label"], text) || groupHit(groups["causal"], text)' in src
    assert "func FilterTemplate(text, language string) string {" in src
    assert "if PyTrimSpace(text) == \"\" {" in src, "空白判定被换成 strings.TrimSpace"
    # 后视只此一条数据，加上 splitLookbehind 里那个 prefix 常量共两处：
    # 出现第三处说明有人新加了 RE2 编不出、却会被静默当成普通模式的后视形状
    assert _code(src).count("(?<!") == 2, _code(src).count("(?<!")
    assert 'const prefix = `(?<!`' in src, "后视的识别形状被改走"


def test_corpus_probes_are_two_sided_not_closure_only():
    """探针必须**两个方向都有**：hit-* 被吞、safe-* 放行。

    只有闭包面（92 条全放行）时，把 Go 的整条判据删掉也照样绿——那份闸就是空转的。
    这里先在 Python 侧现算复核方向（本文件的职责），再核对两侧共用的 kind 词表没被单侧加长。
    """
    corpus = _fixture()["template_corpus"]
    probes = [e for e in corpus if e["kind"].startswith(("hit-", "safe-"))]
    assert len(probes) == TEMPLATE_PROBES
    for entry in probes:
        got = first_read_service.gate(entry["raw"], entry["locale"])
        if entry["kind"].startswith("hit-"):
            assert got == "", f'{entry["kind"]} 探针在 Python 侧没被吞：{entry["raw"]!r}'
        else:
            assert got == entry["raw"], f'{entry["kind"]} 探针在 Python 侧被误吞'
    kinds = {e["kind"] for e in probes}
    assert kinds == {
        "hit-banned", "hit-label-third", "hit-causal-third", "safe-no-third",
        "safe-broken-term", "hit-third-at-start", "hit-third-after-verb",
        "safe-third-lookbehind", "safe-boundary-pre", "hit-boundary-post",
    }, sorted(kinds)
    # 四个关键形状各有一格：前边界、后边界、后视、无 third
    assert {"safe-boundary-pre", "hit-boundary-post", "safe-third-lookbehind",
            "safe-no-third"} <= kinds


def test_template_corpus_is_still_python_answered():
    """夹具的 106 条逐条用**真 Python 闸**重算（含 out 字段）。

    这一条是本文件与 Go 侧 `TestN8TemplateCorpus` 的接缝：Go 比的是「Go == 夹具」，
    这里比的是「Python == 夹具」，同一份 JSON ⇒ 两侧等价。夹具过期（改了 Python 闸没重录）
    也在这里红，而不是等回放出现代差。
    """
    corpus = _fixture()["template_corpus"]
    assert len(corpus) == TEMPLATE_CORPUS_TOTAL
    closure = [e for e in corpus if not e["kind"].startswith(("hit-", "safe-"))]
    assert len(closure) == TEMPLATE_CLOSURE
    assert sum(1 for e in corpus if e["reachable"]) == TEMPLATE_REACHABLE
    assert all(first_read_service.gate(e["raw"], e["locale"]) == e["out"] for e in corpus)
    # 闭包面「一条都不该被闸掉」＝两份模板对措辞规范是干净的，这条也是文案侧的回归哨兵
    assert [e["raw"] for e in closure if e["out"] == ""] == []
    assert {e["locale"] for e in corpus} == set(_LOCALES)


# ═══ 三、locale 两支函数不许合并 ═══


def test_locale_normalization_stays_two_functions_with_two_answers():
    """`locale_for` 先 lower 再比前缀，`_lang_key` **不 lower**（:45-47 vs :26-28）。

    后果是可观测的：`"ZH"` 在前者落 zh、在后者落 en。Go 复刻时最容易「顺手合并成一支」，
    那会同时改掉另一条调用路（safety 的其它 scope）的语言判定。所以这里
    **两侧同时验**：Python 现算的行为差＋Go 两支函数各自的源码形状。
    """
    assert locale_for("ZH") == "zh" and _lang_key("ZH") == "en"
    assert locale_for("zh-CN") == _lang_key("zh-CN") == "zh"
    assert locale_for(None) == _lang_key("") == "en"

    gen, gate = _go(G_GEN), _go(G_GATE)
    assert re.search(r"func LocaleFor\(language string\) string \{\n"
                     r"\tif strings\.HasPrefix\(strings\.ToLower\(language\), \"zh\"\)", gen)
    assert re.search(r"func SafetyLangKey\(language string\) string \{\n"
                     r"\tif strings\.HasPrefix\(language, \"zh\"\)", gate)
    assert "ToLower" not in _go_span(G_GATE, "func SafetyLangKey("), \
        "不 lower 那一支被并进了 lower 的实现"
    assert 'if strings.HasPrefix(language, "zh") {' in _go_span(G_GATE, "func SafetyLangKey(")


def test_assertion_count_bounds_shared_with_go():
    """断言条数区间 2–3（PRD §5.19.2 B01）：MIN 补 filler、MAX 截断（:32-33 ↔ first_read.go:25-26）。"""
    assert (MIN_ASSERTIONS, MAX_ASSERTIONS) == (2, 3)
    gen = _go(G_GEN)
    assert re.search(r"FirstReadMinAssertions = (\d+)", gen).group(1) == str(MIN_ASSERTIONS)
    assert re.search(r"FirstReadMaxAssertions = (\d+)", gen).group(1) == str(MAX_ASSERTIONS)


# ═══ 四、CPython 的 str.upper 与深链拼装 ═══


def test_persona_upper_corpus_is_a_python_closure():
    """persona_type 的 `.upper()` 闭包：把 BMP 里所有「upper 会变样」的码位重扫一遍，
    要求与夹具**逐条相同**（raw / upper / ascii4 三键）。

    为什么不能手挑：校验器判据是 `^[A-Z]{4}$`，于是「ßßßß⇒SSSSSSSS、ﬁﬁﬁ⇒FIII」这类
    **多码位展开**能不能拼出合法 persona 是响应面问题。Go 的 strings.ToUpper 这一类是恒等，
    只有整表逐条比才说得上「复刻的是 CPython 的 upper」。本条证表还闭合（Python 侧重算 == 夹具），
    Go 侧另有 `TestN8PersonaUpperCorpusIsClosed` 反扫。
    """
    corpus = _fixture()["persona_upper_corpus"]
    assert len(corpus) == PERSONA_CORPUS_TOTAL
    assert sum(1 for e in corpus if e["ascii4"]) == PERSONA_LEGAL

    recomputed = []
    for cp in range(0x20, 0x10000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        char = chr(cp)
        upper = char.upper()
        if upper == char:
            continue
        if not (upper.isascii() or len(upper) != 1):
            continue
        four = next(
            (char * k for k in (1, 2, 3, 4)
             if re.fullmatch(r"[A-Z]{4}", (char * k).upper())),
            char * 4,
        )
        recomputed.append(
            {"raw": four, "upper": four.upper(),
             "ascii4": bool(re.fullmatch(r"[A-Z]{4}", four.upper()))}
        )
    assert recomputed == corpus, "夹具的 upper 闭包过期或 Python 语义变了：重录，别改断言"


def test_referral_link_and_alphabet_shared_by_both_sides():
    """深链 11 格逐条用 Python 现算复核，并钉住两处形状：

    - 码表 29 字符 × 8 位（referral.py:13-14 ↔ legacy_batch4.go:34-35）：去掉易混的
      0/1/8/9 与 I/L/O，两侧任一改动都会让「手抄的码」在另一侧判非法；
    - `site_url.rstrip('/')` 剥的是**一串**斜杠（:37 ↔ batch4.go:49-51 用 TrimRight）：
      写成 TrimSuffix 时 "https://a//" 会留双斜杠，而那条链接是要落进 first_read 列的字节。
    """
    corpus = _fixture()["referral_corpus"]
    assert len(corpus) == REFERRAL_CORPUS_TOTAL
    for entry in corpus:
        assert referral_link(entry["site_url"], "QQ22WW33") == entry["link"], entry["site_url"]
    assert corpus[0]["link"].endswith("/r/QQ22WW33")

    assert len(REFERRAL_ALPHABET) == 29 and REFERRAL_CODE_LEN == 8
    assert set("IL0O18 9") & set(REFERRAL_ALPHABET) == set()
    store, batch4 = _go(G_STORE), _go(G_BATCH4)
    assert f'referralAlphabet = "{REFERRAL_ALPHABET}"' in store
    assert re.search(r"referralCodeLen\s*=\s*(\d+)", store).group(1) == "8"
    assert re.search(r"referralRetry\s*=\s*(\d+)", store).group(1) == "5"
    assert 'return strings.TrimRight(siteURL, "/") + "/r/" + code' in batch4
    assert "TrimSuffix" not in _code(batch4), "剥单个斜杠＝改深链形状"


# ═══ 五、键序：JSON 的键序就是契约（两侧写的是同一份 dict 字面量） ═══


def test_card_payload_key_order_is_the_literal_order_on_both_sides():
    """九键 card_payload 的书写序＝响应键序（first_read_service.py:154-164 ↔
    onboarding_submit.go::firstReadCardPayload）。

    POST 直接把这个 dict 当 data 回，所以键序逐字进响应体；同一份内容还以
    `json.dumps(ensure_ascii=False)` 的**默认分隔符**落进 first_read 列（GET 按列复现）。
    顺序变了字节就变了，而 pydantic/dict 都不报错——只有逐字比抓得到。
    """
    nine, four = _py_card_payload()
    go_nine = _go_calls(G_SUBMIT, "func firstReadCardPayload(", r'v\.set\("([a-z_]+)"')
    assert go_nine == nine == [
        "card_type", "language", "persona_type", "assertions", "summary",
        "closer", "answer_count", "share_url", "generated_at",
    ]
    go_four = _go_calls(G_SUBMIT, "func firstReadOuter(", r'v\.set\("([a-z_]+)"')
    assert go_four == four == ["assertions", "summary", "closer", "card_payload"]
    assert _go(G_SUBMIT).count("func firstReadOuter") == 1
    assert 'v.set("card_type", pyStringValue("first_read"))' in _go(G_SUBMIT)


def test_restore_payload_keys_overwrite_in_place_and_append_the_missing():
    """GET 的 `payload.update({...})`：已有键**就地换值、位置不动**，缺的键**追加在末尾**
    （first_read_service.py:211-220 ↔ onboarding_read.go）。

    于是出参形状取决于列里那份 card_payload 有哪些键——这条是整域唯一会改 JSON 形状的地方，
    所以六键的字面量顺序与「id 追加」两件事都要钉。
    """
    src = (ROOT / "backend" / "services" / "first_read_service.py").read_text(encoding="utf-8")
    block = re.search(r"payload\.update\(\s*\{(.*?)\n            \}", src, re.S)
    assert block, "restore 的 update 字面量写法变了，闸要跟着改"
    py_order = re.findall(r'^\s+"([a-z_]+)":', block.group(1), re.M)
    assert py_order == ["id", "assertions", "summary", "closer", "language", "created_at"]

    # language / created_at 各出现两次（有值与 NULL 两支），**键只有一格**——按首次出现去重
    go_order = _dedup(_go_calls(
        G_READ, "func (s *Server) handleFirstReadRestore(", r'payload\.set\("([a-z_]+)"'
    ))
    assert go_order == py_order, go_order

    # card_payload 取假值⇒空 dict（:210 `dict(card.get("card_payload") or {})`），
    # 且 id 是**追加**在九键之后（POST 出十键、restore 出十一键），不是插在中间
    assert 'payload, ok := firstReadRestorePayload(card)' in _go(G_READ)
    assert "out.set(member.key, member.val)" in _go(G_READ), "浅拷贝被改成共享容器"
    nine, _ = _py_card_payload()
    assert nine.index("card_type") == 0 and "id" not in nine


def test_answers_column_two_keys_in_declaration_order():
    """answers 列每项只有两键、按模型声明序 q/option（schemas.py:496-497 ↔
    onboarding_submit.go::firstReadAnswersValue）。请求里多余的键已经被
    `extra="ignore"` 丢掉，落库文本因此是**重建**的，不是回显——顺序也是契约的一部分。
    """
    assert list(FirstReadAnswer.model_fields) == ["q", "option"]
    go_answers = _go_calls(
        G_SUBMIT, "func firstReadAnswersValue(", r'item\.set\("([a-z_]+)"'
    )
    assert go_answers == ["q", "option"], go_answers
    assert "model_config" not in _go(G_SUBMIT)

# ═══ 六、三处写与两个时间格式 ═══


def test_three_writes_keep_three_different_conditions():
    """一次 POST 写三处，三处的**条件不同源**，一格都不许合并（first_read_service.py:182-195
    ↔ legacy_batch4.go:155-198）：推荐码 `if user is not None` 且列为空才生成；
    profile **无条件** upsert；剧本 `if user is not None`。

    最容易「顺手统一」的正是这里：账号已注销/不存在时 Python 照样写 profile（Go 也一样），
    把它改成「都不写」更合理，但那是改契约——灰度期同一账号两侧行数不同。
    """
    py = (ROOT / "backend" / "services" / "first_read_service.py").read_text(encoding="utf-8")
    submit = re.search(r"async def submit\((.*?)\n    async def ", py, re.S).group(1)
    guards = [m.start() for m in re.finditer(r"if user is not None:", submit)]
    assert len(guards) == 2, f"两处条件写被合并或新增：{len(guards)} 处"
    upsert_line = submit.index("profile = await onboarding_repository.upsert(")
    assert guards[0] < upsert_line < guards[1], "upsert 被卷进了某个 if 块＝改了写入条件"

    store = _code(_go(G_STORE))
    assert "if user.Found && code == \"\" {" in store, "推荐码的生成条件不再是「有账号且列为空」"
    assert store.index("profileID, err := upsertOnboardingProfile(ctx, tx, userID, write)") > \
        store.index("if user.Found && code == \"\" {")
    assert "if user.Found {\n\t\tif err := prefillJourneyTx" in store
    assert store.count("p.pool.Begin(ctx)") == 1 and store.count("tx.Commit(ctx)") == 1, \
        "三处写必须落在同一个事务里（Python 是同一个 session＋响应前一次提交）"


def test_generated_at_is_isoformat_with_offset_and_id_is_appended():
    """`generated_at` 不是过 pydantic 的 datetime，而是 service 自己折好的**字符串**
    （first_read_service.py:163 的 `utc_now().isoformat()`）⇒ 带 `+00:00` 而不是 `Z`；
    而 `id` 是 submit 返回值追加的第十键（:196-197），不是插在九键中间。

    Go 用 pyISOOffset 复刻这一支（批 3 同判据：`pyISO` 的 Z 形是给 datetime 用的）。
    """
    assert datetime(2026, 10, 15, 1, 2, 3, tzinfo=timezone.utc).isoformat().endswith("+00:00")
    submit = _go(G_SUBMIT)
    assert 'v.set("generated_at", pyStringValue(pyISOOffset(now)))' in submit
    assert 'out.set("id", pyStringValue(profileID))' in submit, "id 不再是追加的第十键"
    assert _go(G_READ).count('payload.set("id", pyStringValue(profile.ID))') == 1


# ═══ 七、请求校验层与掩码层 ═══


def test_body_limits_come_from_the_same_field_params():
    """四道上限与 persona 正则逐字同源（schemas.py:496-516 ↔ onboarding_body.go:34-44）。

    上限不同＝同一份请求在一侧 422、另一侧 200；persona 的 `[A-Z]{4}` 与 Go 的
    `^[A-Z]{4}$` 同形（RE2 无 (?m) 时 $ 就是文本末尾），报错文案也逐字比。
    """
    answers = FirstReadRequest.model_fields["answers"]
    max_len = [m.max_length for m in answers.metadata if hasattr(m, "max_length")]
    assert max_len == [10]
    assert [
        [m.max_length for m in FirstReadRequest.model_fields[k].metadata
         if hasattr(m, "max_length")]
        for k in ("language",)
    ] == [[10]]
    assert [
        [m.max_length for m in FirstReadAnswer.model_fields[k].metadata
         if hasattr(m, "max_length")]
        for k in ("q", "option")
    ] == [[30], [40]]

    body = _go(G_BODY)
    for name, want in (("maxFirstReadAnswers", 10), ("maxFirstReadQ", 30),
                       ("maxFirstReadOption", 40), ("maxFirstReadLang", 10)):
        assert re.search(rf"{name}\s*=\s*(\d+)", body).group(1) == str(want), name
    assert "regexp.MustCompile(`^[A-Z]{4}$`)" in body
    py_src = (ROOT / "backend" / "models" / "schemas.py").read_text(encoding="utf-8")
    assert 're.fullmatch(r"[A-Z]{4}", upper)' in py_src
    assert '"非法的性格类型: " + value.str' in body, "422 的文案不是逐字复刻"


def test_four_masks_are_the_same_four_regexes_on_both_sides():
    """四条掩码两侧同串（gen_n8_contract_goldens.py:48-65 ↔ n8_replay_test.go:40-82）。

    记 0 的语义是「这个值是确定值，刻意留原文逐字比」，不是「不许有匹配」——所以正则
    各写各的就会造出两种坏结果：Go 少掩一处常红，多掩一处把真发散掩成绿。
    `masked_fields` 的四格名称也是同一份契约的目录。
    """
    fixture = _fixture()
    assert fixture["masked_fields"] == ["generated_at", "journey_event_id",
                                       "referral_link", "created_at"]
    recorder = (ROOT / "scripts" / "gen_n8_contract_goldens.py").read_text(encoding="utf-8")
    replay = _go(G_REPLAY)
    pairs = {
        "GENERATED_AT_RE": ("n8GeneratedAtRe", '"generated_at":"<generated_at>"', "MASKED"),
        "EVENT_ID_RE": ("n8EventIDRe", '"id":"<event_id>"', "MASKED_EVENT_ID"),
        "REFERRAL_LINK_RE": ("n8ReferralLinkRe", "/r/<code>", "MASKED_REFERRAL_LINK"),
        "CREATED_AT_RE": ("n8CreatedAtRe", '"created_at":"<created_at>"', "MASKED_CREATED_AT"),
    }
    for py_name, (go_name, go_mask, _) in pairs.items():
        # 录制侧四条的引号风格不统一（三条 r'…'、一条 r"…"），所以按反向引用取任何一种
        py_match = re.search(py_name + r" = re\.compile\(r(['\"])(.*?)\1\)", recorder)
        assert py_match, f"录制脚本里 {py_name} 的写法变了，闸要跟着改"
        go_match = re.search(
            rf"var {go_name} = regexp\.MustCompile\(\s*`([^`]*)`", _code(replay)
        )
        assert go_match, f"Go 侧 {go_name} 的写法变了，闸要跟着改"
        assert py_match.group(2) == go_match.group(1), (
            py_name, py_match.group(2), go_match.group(1)
        )
        assert go_mask in replay, f"占位符 {go_mask} 换了＝夹具对不上"
    # 推荐码字符集是 [A-Z2-7]{8} 的超集（宁多认不漏认），多掩由计数暴露成红
    assert "/r/[A-Z2-7]{8}" in replay


# ═══ 八、放量面：四面必须同进同退 ═══


def test_cutover_wiring_agrees_on_all_four_planes():
    """0% 的可证形态＝四格同时成立，缺任一格都会「看着是灰度、其实是换后端」：

    Ingress 把 /api/v1/onboarding 指到 go-api（一刀，不做子路径）＋ ConfigMap 该域键为 0
    ＋ Go 的 gate map 注册了这个域名 ＋ py-api 地址非空（Delegate 才有转交对象）。
    再加 Go 侧 env 默认值与 Python settings 逐字——share_url 会**落进列**，restore 按列
    复现不回查设置，两侧默认值不同＝同一账号切流前后拿到不同深链。
    """
    config_yaml = yaml.safe_load((MANIFESTS / "10-config.yaml").read_text(encoding="utf-8"))
    data = config_yaml["data"]
    assert data["CLOSEN_CUTOVER_ONBOARDING"] == "0"
    assert data["CLOSEN_PY_API_URL"].startswith("http://")

    ingress = yaml.safe_load((MANIFESTS / "50-ingress.yaml").read_text(encoding="utf-8"))
    rules = ingress["spec"]["rules"][0]["http"]["paths"]
    hit = [p for p in rules if p["path"] == "/api/v1/onboarding"]
    assert len(hit) == 1 and hit[0]["pathType"] == "Prefix"
    assert hit[0]["backend"]["service"]["name"] == "go-api"

    conf, main = _go("internal/config/config.go"), _go("cmd/go-api/main.go")
    assert 'envInt("CLOSEN_CUTOVER_ONBOARDING", 0)' in conf
    assert '"onboarding": cfg.CutoverOnboarding' in main

    fixture = _fixture()
    assert fixture["share_url_inputs"]["site_url"] == settings.site_url
    assert fixture["share_url_inputs"]["app_store_url"] == settings.app_store_url
    assert f'envString("SITE_URL", "{settings.site_url}")' in conf
    assert f'envString("APP_STORE_URL", "{settings.app_store_url}")' in conf


def test_fixture_onboarding_group_covers_both_paths_exactly_once_each_writing_side():
    """夹具 onboarding 组 28 条：只许出现这一域的两条 path，且 POST/GET 两侧都有写面观测点。

    「切流前后契约 diff 为空」的判据面就是这一组；域名字符串打错（Delegate 里写成
    "onboarding" 之外）不会编译失败，只会让这一域永远 0%。
    """
    cases = [c for c in _fixture()["cases"] if c["group"] == BATCH4_DOMAIN]
    assert len(cases) == ONBOARDING_CASES
    assert len({c["name"] for c in cases}) == ONBOARDING_CASES, "夹具里有重名条目＝一条被覆盖"
    assert all(c["path"].startswith(BATCH4_PATH) for c in cases), sorted(
        {c["path"] for c in cases if not c["path"].startswith(BATCH4_PATH)}
    )
    by_method = {m: sum(1 for c in cases if c["method"] == m) for m in ("GET", "POST")}
    assert by_method == {"GET": 17, "POST": 11}, by_method
    # 三条「按计数应用」的掩码在**每条**上都有格子（记 0＝这个值是确定值，刻意逐字比）；
    # 缺键会让 Go 侧读到 nil 而静默少掩，diff 就不是「为空」而是「没比」
    for c in cases:
        for field in ("id_masks", "code_masks", "created_at_masks"):
            assert isinstance(c.get(field), int), (c["name"], field)
        assert isinstance(c["status"], int) and c["response_text"]
    # 写面观测点必须两侧都有：POST 现算推荐码/落三处，GET 按列复现
    assert sum(1 for c in cases if c["method"] == "POST" and c["code_masks"] == 1) > 0
    assert sum(1 for c in cases if c["method"] == "GET" and c["created_at_masks"] == 1) > 0


def test_this_domain_has_no_ai_consent_check_on_either_side():
    """初读卡是**静态直出、零 LLM**（routers/onboarding.py:8 与 first_read.go:12-13 同口径），
    所以两侧都不该在这一路上检查 ai_consent。有人加一道 consent 闸＝给 Go 复刻凭空添一条
    Python 没有的 403 支，而夹具里没有那一格，回放照绿、现网才红。

    「不检查 ai_consent」这句话本身写在文档注释里，所以比的是**代码面**：
    Python 侧剔掉 docstring、Go 侧剔掉整行注释，剩下的字节里不许出现这一列名。
    """
    def _py_code(text: str) -> str:
        text = re.sub(r'""".*?"""', "", text, flags=re.S)
        return re.sub(r"'''.*?'''", "", text, flags=re.S)

    files = ["backend/routers/onboarding.py", "backend/services/first_read_service.py",
             "go-core/internal/domain/first_read.go", "go-core/internal/domain/first_read_gate.go",
             "go-core/internal/httpapi/onboarding_submit.go",
             "go-core/internal/httpapi/onboarding_read.go",
             "go-core/internal/httpapi/onboarding_body.go",
             "go-core/internal/store/legacy_batch4.go"]
    for rel in files:
        text = (ROOT / rel).read_text(encoding="utf-8")
        body = _code(text) if rel.startswith("go-core") else _py_code(text)
        assert "consent" not in body, rel


# ═══ 九、第④步本体：只标废弃，不删路由 ═══


def test_both_onboarding_routes_deprecated_yet_still_routable():
    """deprecated 只描述**对外契约**的归属，路由本体留着：灰度回滚＝把
    CLOSEN_CUTOVER_ONBOARDING 改回 0，前提是 Python 还答得出这两条。

    `route_class=CommitBeforeResponseRoute` 一并守：POST 的三处写靠「响应前一次提交」，
    换成默认 APIRoute 就变成响应后再提交，同一账号下一次请求两侧读到的行数可能不同。
    初读卡的**内容**还有两个不走 HTTP 的消费者（share_service.py:245 直接 await restore、
    journey_service.py:574 读同一列），所以服务本体同样不能删——这一条由本文件的
    模板/键序/闸测共同背书（它们调的就是这个 service）。
    """
    src = (ROOT / "backend" / "routers" / "onboarding.py").read_text(encoding="utf-8")
    assert 'router = APIRouter(route_class=CommitBeforeResponseRoute)' in src
    for deco in (
        '@router.post("/onboarding/first-read", deprecated=True)',
        '@router.get("/onboarding/first-read", deprecated=True)',
    ):
        assert deco in src, f"路由装饰器少了 deprecated：{deco}"

    paths = app.openapi()["paths"]
    assert BATCH4_PATH in paths
    for op in ("get", "post"):
        assert paths[BATCH4_PATH][op].get("deprecated"), f"{BATCH4_PATH} 的 {op} 没标废弃"


def test_batch4_registered_in_the_domain_roll_call():
    """已切流域的全量登记表（test_n8_batch2_contract.py 的 CUT_DOMAINS/CUT_PATHS）
    必须带上 onboarding——三方对齐闸（Ingress 域集 ≡ ConfigMap 键集 ≡ openapi deprecated 域集）
    少一格就永远判不出「清单漏了一刀」。
    """
    src = (ROOT / "backend" / "tests" / "test_n8_batch2_contract.py").read_text(encoding="utf-8")
    domains = re.search(r"CUT_DOMAINS = \((.*?)\)", src, re.S).group(1)
    assert '"onboarding"' in domains
    assert BATCH4_PATH in src

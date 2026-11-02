"""KG-04 的 ASO 文案包闸：把 `scripts/check_aso_copy.py` 的判据钉进 CI，并证明它会红。

**为什么脚本之外还要一张 pytest**：脚本靠人想起来跑；台账 §KG-04 的验收（字符数不靠手数、
关键词不重复已索引词、不用竞品词、无禁用词）必须每次 CI 都跑，否则商店文案会漂。

**断言的两侧不同源**（这条是本仓的老规矩）：ASC 的上限 30/30/100 写在脚本的字面量里，
JSON 的 `limits` 只是给作者看的注记；本文件再拿**第三方**——md 文档 §1 表里声明的「29/30」这类分数——
跟脚本现算的长度对一遍。所以「把 JSON 上限改成 200」或「把 md 的字符数手改漂亮」都会红。

**红/绿都要证人**：`test_pack_passes_the_validator` 是绿闸（真包必须全绿），
其余参数化用例逐条制造**不合格输入**证明判据在场；
`--self-test` 的等价物是 `test_script_self_test_rejects_overlong_title`。
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_aso_copy.py"
PACK_JSON = ROOT / "docs" / "aso" / "en-US-2026-11.json"
PACK_MD = ROOT / "docs" / "aso" / "en-US-2026-11.md"

_spec = importlib.util.spec_from_file_location("check_aso_copy", SCRIPT)
assert _spec and _spec.loader
aso = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aso)

PACK = json.loads(PACK_JSON.read_text(encoding="utf-8"))
MD = PACK_MD.read_text(encoding="utf-8")

CITE = re.compile(r"`(?P<path>docs/[^`:\s]+):(?P<line>\d+)`")

#: 文案包里必须逐字出现在 md 里的字段文本（防「md 与 JSON 各漂一份」）。
def _field_texts(payload: dict) -> list[tuple[str, str]]:
    f = payload["fields"]
    out = [("title", f["title"])]
    out += [(f"subtitle[{v['id']}", v["text"]) for v in f["subtitle_variants"]]
    out += [(f"keywords[{v['id']}", v["text"]) for v in f["keyword_variants"]]
    out += [("play_short_description", payload["play_store_short_description"]["text"])]
    return out


def test_the_real_pack_is_clean() -> None:
    """绿闸：仓内文案包必须过校验器；红了就是商店文案本身不合格。"""
    hits = aso.violations(PACK)
    assert not hits, "文案包有 %d 条违规：\n" % len(hits) + "\n".join(f"  - {v}" for v in hits)


@pytest.mark.parametrize("label,text", _field_texts(PACK), ids=lambda v: v if isinstance(v, str) else "")
def test_every_field_is_pasted_verbatim_into_the_doc(label: str, text: str) -> None:
    assert text in MD, f"{label} 在 md 里找不到逐字原文 ⇒ 文档与可粘贴源漂了"


#: md §1 表的「字段 / 版本」两列 → 文案包里该格的真文本（表是手抄，包是源，两边不同源）。
def _pack_text_for_row(field: str, version: str) -> str | None:
    f = PACK["fields"]
    if (field, version) == ("标题", "—"):
        return f["title"]
    if (field, version) == ("Play 短描述", "—"):
        return PACK["play_store_short_description"]["text"]
    for key, items in (("副标题", f["subtitle_variants"]), ("关键词", f["keyword_variants"])):
        if field == key:
            for v in items:
                if v["id"] == version:
                    return v["text"]
    return None


def test_doc_table_text_column_is_the_pack_text() -> None:
    """§1 表的文本列本身也要对得上文案包——只查 §2 粘贴块会漏掉「表里改一个字」。

    这条判据存在的理由：评审的人先看 §1 那张带字符数的表，表与源漂了等于给了个假的「已复核」，
    而 §2 的粘贴块仍是旧的、复制走的是另一份文案。
    """
    rows = re.findall(r"^\| (.+?) \| (.+?) \| `(.+?)` \| (\d+)/(\d+) \|", MD, re.M)
    assert len(rows) == 6, f"§1 表解析到 {len(rows)} 行，验收口径是 6 行（标题/副标题 A、B/关键词两版/Play）"
    for field, version, text, _declared, _cap in rows:
        source = _pack_text_for_row(field, version)
        assert source is not None, f"§1 表出现文案包里没有的行：{field} / {version}"
        assert text == source, f"§1 表 {field}/{version} 的文本与文案包漂了：表里 {text!r}，包里 {source!r}"


def test_doc_declared_counts_match_computed_lengths() -> None:
    """md §1 表里的「29/30」这类分数由脚本现算再比——手填错一位即红。

    这一条专门防「文案改了、文档里的字符数没改」：ASC 会按真实长度拦，
    而评审的人只看文档，数字一漂就等于给了个假的「已复核」。
    上限一侧取脚本字面量（ASC 真值），另一侧是 md 里手写的分数，两边不同源。
    """
    rows = re.findall(r"^\| (.+?) \| .+? \| `(.+?)` \| (\d+)/(\d+) \|", MD, re.M)
    assert len(rows) >= 6, f"md §1 表只解析到 {len(rows)} 行，判据覆盖面漂了"
    assert {r[0] for r in rows} >= {"标题", "副标题", "关键词", "Play 短描述"}, sorted({r[0] for r in rows})
    caps = {"标题": aso.ASC_LIMITS["title"], "副标题": aso.ASC_LIMITS["subtitle"],
            "关键词": aso.ASC_LIMITS["keywords"], "Play 短描述": 80}
    for field, text, declared, cap in rows:
        assert int(declared) == len(text), f"md 里 {field} 写 {declared}，实测 {len(text)} ⇒ {text!r}"
        assert int(cap) == caps[field], f"md 里 {field} 的上限写 {cap}，判据侧是 {caps[field]}"


def test_limits_side_is_not_self_referential() -> None:
    """把 JSON 的 limits 改大**不许**让文案包变合法（上限的字面量只能在脚本里）。"""
    tampered = copy.deepcopy(PACK)
    tampered["limits"] = {"title": 200, "subtitle": 200, "keywords": 200}
    hits = aso.violations(tampered)
    assert any("limits." in h for h in hits), f"改 JSON 上限没被判出：{hits}"
    long_title = copy.deepcopy(PACK)
    long_title["limits"] = {"title": 40, "subtitle": 30, "keywords": 100}
    long_title["fields"]["title"] = "ClosenAI: AI Journal & Growth Extra Words"  # 39 字符
    hits = aso.violations(long_title)
    assert any("limits.title" in h for h in hits), "JSON 与脚本上限不符必须红"


def test_self_test_rejects_the_overlong_title() -> None:
    """`--self-test` 的等价物：31 字符标题必须被拒，否则这条闸等于没装。"""
    bad = aso.mutated_for_self_test(PACK)
    hits = [h for h in aso.violations(bad) if h.startswith("title 超长度")]
    assert len(hits) == 1, f"超长标题没打出唯一长度红：{hits}"


@pytest.mark.parametrize(
    ("mutate", "expect"),
    [
        (lambda p: p["fields"].__setitem__("title", "ClosenAI: AI Journal and Growth Tracking"), "超长度"),
        (lambda p: p["fields"]["keyword_variants"][0].__setitem__(
            "text", "mood tracker,growth,journal,self discovery,personality test,mbti,enneagram,reflection,goals,diary"
        ), "重复了标题/副标题已收录的词"),
        (lambda p: p["fields"]["keyword_variants"][0].__setitem__(
            "text", "mood tracker,rosebud app,self discovery,personality test,mbti,enneagram,reflection,goals,diary"
        ), "竞品品牌词"),
        (lambda p: p["fields"]["subtitle_variants"][1].__setitem__(
            "text", "It asks how therapy actually went"
        ), "命中禁用词"),
        (lambda p: p["fields"]["keyword_variants"][1].__setitem__(
            "text", "mood tracker, self improvement,self discovery,personality test,16 types,reflection,goals,diary,"
        ), "前后有空格"),
        (lambda p: p["fields"]["keyword_variants"][0].__setitem__(
            "text", "mood tracker,mood tracker,self discovery,personality test,mbti,enneagram,reflection,goals,diary"
        ), "内部重复用词"),
        (lambda p: p["fields"]["subtitle_variants"][0].__setitem__("kill_metric", "待补"), "不是可判定的内容"),
        (lambda p: p["fields"]["subtitle_variants"][0].__setitem__("kill_metric", "看情况再说，不行就换回去。"), "不是可判定的内容"),
        (lambda p: p["fields"].__setitem__("subtitle_variants", p["fields"]["subtitle_variants"][:1]), "副标题要有 A/B 两版"),
        (lambda p: p["fields"].__setitem__("keyword_variants", p["fields"]["keyword_variants"][:1]), "关键词字段要商标版＋保守版"),
        (lambda p: p.__setitem__("banned_terms", ["therapy", "treat"]), "脚本判了但文案包没声明"),
        (lambda p: p["banned_terms"].append("cure"), "声明了但脚本没判"),
    ],
    ids=["长标题", "关键词重复已索引词", "竞品词", "医疗词", "逗号后空格＋空项", "关键词内部重复",
         "判停占位符", "判停是感想", "副标题只一版", "关键词只一版", "判据有但清单没写", "清单有但判据没装"],
)
def test_each_rule_fires_on_its_own_violation(mutate, expect: str) -> None:
    bad = copy.deepcopy(PACK)
    mutate(bad)
    hits = aso.violations(bad)
    assert any(expect in h for h in hits), f"判据「{expect}」没生效：{hits}"


def test_banned_terms_are_the_documented_eight() -> None:
    """台账 §KG-04 点名的 8 条禁语必须在文案包里，且校验器逐条有对应正则。"""
    declared = [t.lower() for t in PACK["banned_terms"]]
    for term in ["therapy", "treat", "diagnose", "remembers everything", "ai companion", "ai friend", "best", "#1"]:
        assert term in declared, f"禁语 {term} 不在包里 ⇒ 清单与台账脱节"
    enforced = [name.lower() for name, _ in aso.BANNED_PATTERNS]
    assert sorted(declared) == sorted(enforced), "banned_terms 与 BANNED_PATTERNS 不一一对应"


def test_banned_regexes_are_word_bounded() -> None:
    """词边界的反证：`best` 不许打 `better`、`treat` 不许打 `retreat`，但要打 `treatment`。

    没有这一条，禁语闸会因为误伤合法词而被作者绕过（绕过一次就再没人信这条闸）。
    """
    patterns = dict(aso.BANNED_PATTERNS)
    assert not patterns["best"].search("a better journal"), "best 误伤了 better"
    assert not patterns["treat"].search("a quiet retreat"), "treat 误伤了 retreat"
    assert patterns["treat"].search("medical treatment"), "treat 没拦住 treatment"
    assert patterns["diagnose"].search("get a diagnosis"), "diagnose 没拦住 diagnosis"
    assert patterns["remembers everything"].search("It remembers every word"), "绝对化变体没拦住"


def test_citations_in_the_aso_doc_reach_a_real_line() -> None:
    """md 里所有 `docs/...:NN` 引用必须打到真实行的**相邻范围**（文件不存在或行号越界即红）。"""
    for m in CITE.finditer(MD):
        path, line = ROOT / m.group("path"), int(m.group("line"))
        assert path.is_file(), f"引用了不存在的文件：{path}"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert 1 <= line <= len(lines), f"{m.group(0)} 行号越界（该文件 {len(lines)} 行）"


REQUIRED_DOC_NEEDLES = [
    ("docs/aso/en-US-2026-11.md", "scripts/check_aso_copy.py"),
    ("docs/aso/en-US-2026-11.md", "--self-test"),
    ("docs/aso/en-US-2026-11.md", "KG-06 `BLOCKED`"),
    ("docs/aso/en-US-2026-11.md", "第二来源"),
    ("docs/aso/en-US-2026-11.md", "16 types"),
]


@pytest.mark.parametrize("rel,needle", REQUIRED_DOC_NEEDLES)
def test_doc_keeps_the_named_facts(rel: str, needle: str) -> None:
    """正向证人：法务前置、未提交状态、单源限制、保守版用词——少一条就红（防「文档被删薄」）。"""
    assert needle in (ROOT / rel).read_text(encoding="utf-8"), f"{rel} 里找不到 {needle!r}"

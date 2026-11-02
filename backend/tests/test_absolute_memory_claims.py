"""KG-23 的回归守卫：绝对化记忆承诺不许在**任何可发布文案面**复活。

**为什么闸门放在 pytest 而不是 jest**：这一条要扫的面跨了三个构建目录
（`frontend/src/i18n/locales/*.ts`＝App 内文案、`backend/data/copy/*.json`＝分享卡/推送水印、
`site/src/i18n.ts`＋`web/*.html`＝落地站），而 jest 的根目录是 `frontend/`，
它物理上读不到后两个面——2026-11-01 的第一轮就是这种情况：
`en.ts:432` 改完了，`backend/data/copy/zh.json:419` 的水印「它记得你说过的每一句」还留在导出 PDF 上。

**为什么是「归零闸＋正向证人」两条一起**：只断言「禁语 0 命中」的闸会因两件事假绿——
① 扫描面写错（少一个目录就自动通过），② 文案被改名（键没了，禁语自然也没了）。
所以本文件同时钉：命中数为 0、被扫文件数为下界、以及**替换后的那三条键在 15 个语言里各存在一次**。
键消失＝证人红，而不是闸绿。

**白名单口径**：只扫代码面，不扫 `docs/`、`*.md`、`closenai-prd/`、`CLOSENAI_V3_*`——
那些是品牌历史与工作单，台账 §KG-23 的验收原文写的就是「品牌历史文档除外」。
`site/dist/` 是构建产物且已在 `site/.gitignore`，不进仓也不进闸。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

LOCALE_DIR = ROOT / "frontend" / "src" / "i18n" / "locales"
COPY_DIR = ROOT / "backend" / "data" / "copy"
SITE_I18N = ROOT / "site" / "src" / "i18n.ts"
WEB_DIR = ROOT / "web"

LANGS = [
    "de", "en", "es", "fr", "id", "it", "ja", "km", "ko", "ms", "pt", "ru", "th", "vi", "zh",
]

#: 每种语言里「记得＋全部」这一族搭配。刻意写成**搭配**而不是单词表：
#: 「Export or delete everything」是合法表述，禁的是把它挂到「记住」上。
ABSOLUTE_MEMORY: dict[str, re.Pattern[str]] = {
    "en": re.compile(r"remembers (everything|all |every conversation)", re.I),
    "zh": re.compile(r'记得[^"”]{0,14}(每一句|每句话|所有|一切)|你说的[^"”]{0,6}都记得'),
    "ja": re.compile(r"ぜんぶ覚えて|全て覚えて"),
    "ko": re.compile(r"모든 것을 기억|모든 걸 기억|모든 이야기를 기억"),
    "de": re.compile(r"erinnert sich an alles", re.I),
    "fr": re.compile(r"(se souvient|se rappelle) de tout", re.I),
    "es": re.compile(r"recuerda todo", re.I),
    "it": re.compile(r"ricorda tutto", re.I),
    "pt": re.compile(r"lembra (de )?tudo", re.I),
    "ru": re.compile(r"помнит всё", re.I),
    "id": re.compile(r"mengingat semua", re.I),
    "ms": re.compile(r"mengingati semua", re.I),
    "th": re.compile(r"จดจำทุกสิ่ง|จดจำทุกเรื่อง"),
    "vi": re.compile(r"ghi nhớ mọi", re.I),
    "km": re.compile(r"ចងចាំអ្វីៗទាំងអស់"),
}

#: 「产品＝AI 陪伴」这一族：定位已改判为有证据的成长日记（KG-01），
#: 台账 §KG-23 点名把落地站的 `AI companion` 换成 `AI journal / growth journal`。
COMPANION = re.compile(r"\bAI companion\b", re.I)


def _scannable(base: Path, suffixes: set[str]) -> list[Path]:
    if not base.exists():
        return []
    out = []
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.suffix not in suffixes:
            continue
        parts = set(p.parts)
        if parts & {"node_modules", "dist", "__pycache__"}:
            continue
        out.append(p)
    return out


def _locale_files() -> list[Path]:
    """15 个语言文件本身——`index.ts` 是聚合器，不算一个语言。"""
    return [p for p in _scannable(LOCALE_DIR, {".ts"}) if p.stem in LANGS]


def _surfaces() -> list[Path]:
    files: list[Path] = []
    files += _scannable(LOCALE_DIR, {".ts"})
    files += _scannable(COPY_DIR, {".json"})
    if SITE_I18N.is_file():
        files.append(SITE_I18N)
    files += _scannable(WEB_DIR, {".html"})
    return files


def test_scan_surface_is_what_the_gate_thinks_it_is() -> None:
    """证人一：扫描面本身不许悄悄变空——四个面各自至少有一文件在册。

    这一条存在的理由：`_surfaces()` 里任何一次目录改名都会让禁语闸扫 0 个文件而假绿。
    """
    files = _surfaces()
    by_top = {p.relative_to(ROOT).parts[0] for p in files}
    assert by_top == {"frontend", "backend", "site", "web"}, sorted(by_top)
    assert len(files) >= 18, f"扫描面只有 {len(files)} 个文件，比预期的 15＋15＋1＋落地站少 ⇒ 目录漂了"
    assert len(_locale_files()) == 15
    assert len(_scannable(COPY_DIR, {".json"})) == 15


@pytest.mark.parametrize("path", _surfaces(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_absolute_memory_claim_in_publishable_copy(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    hits = [(lang, m.group(0)) for lang, pat in ABSOLUTE_MEMORY.items() for m in pat.finditer(text)]
    assert not hits, (
        f"{path.relative_to(ROOT)} 里仍有绝对化记忆承诺：{hits} ⇒ "
        "台账 §KG-23 的口径是「你选择分享的内容」，落点必须与真实记忆/删除能力一致"
    )


def test_landing_site_does_not_call_itself_an_ai_companion() -> None:
    """台账点名 `site/src/i18n.ts:12/:14/:91` 的 `AI companion` → `AI journal / growth journal`。

    只钉落地站这一个面：`backend/ai/llm.py` 与 `backend/data/copy/en.json` 里那句
    「your AI companion」是**模型的系统提示与对话开场白**，属 KG-01 定位改判的面，
    2026-11-01 已登记为「发现未修」，不在本条范围内。
    """
    src = SITE_I18N.read_text(encoding="utf-8")
    assert not COMPANION.search(src), f"落地站仍自称 AI companion：{COMPANION.findall(src)}"
    assert "growth journal" in src and "成长日记" in src, "新的定位表述（AI growth journal／AI 成长日记）要两侧都在"


def test_replacement_copy_is_present_in_all_fifteen_languages() -> None:
    """证人二（正向）：替换后的三条键在 15 个语言里各出现一次，且不与禁语同形。

    没有这一条，「把 `step1Tagline` 整键删掉」也能让上面的归零闸绿。
    """
    for lng in LANGS:
        text = (LOCALE_DIR / f"{lng}.ts").read_text(encoding="utf-8")
        for key in ("step1Tagline", "step1Feature1Title", "step1Feature1Desc"):
            found = re.findall(rf'^\s*"{key}": "(.+)",\s*$', text, re.M)
            assert len(found) == 1, f"{lng}.ts 的 {key} 命中 {len(found)} 次（要恰好 1 次）"
            assert found[0].strip(), f"{lng}.ts 的 {key} 是空串"
    # 英文那一条是台账给的落点原文，逐字钉住（其余 14 语按语言匹配，不逐字）
    en = (LOCALE_DIR / "en.ts").read_text(encoding="utf-8")
    assert '"step1Tagline": "It remembers what you choose to share"' in en


def test_share_card_watermark_is_not_absolute_in_any_language() -> None:
    """水印是**印在用户分享出去的图上**的那一句，也是商店截图会取景的位置。

    这一条单独存在是因为 `backend/data/copy/*.json` 与 i18n 目录不同源：
    第一轮只改了 App 文案，`zh/de/id/ms/vi/km/ko/th` 的水印还写着「记得你说的每一句」。
    """
    texts = {}
    for lng in LANGS:
        data = json.loads((COPY_DIR / f"{lng}.json").read_text(encoding="utf-8"))
        texts[lng] = data["share"]["brand"]["watermark"]
    assert len({v for v in texts.values()}) > 1, "15 语水印全同＝复制没翻译，这条测试就没有覆盖 14 个面"
    for lng, value in texts.items():
        for lang, pat in ABSOLUTE_MEMORY.items():
            assert not pat.search(value), f"{lng} 的水印仍是绝对化承诺：{value!r}"

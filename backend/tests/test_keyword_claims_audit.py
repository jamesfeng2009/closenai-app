"""KG-01 承诺审计表的完整性闸（2026-11-02）。

**这张闸守的不是文案，是「证据」**：`docs/keyword-claims-audit-2026-11.md` 的每条都写着
`位置 = 文件:行号` 与 `原文 = 那一行的字`。本文件把每条引用**读回源文件逐行核对**——
行号漂了、原文抄错了、文件改名了、位置凭空造了，一律红。

为什么值得为一张表写闸（而不是一次性人工核对）：
1. 台账 §KG-01 的验收原文就是「每条有文件位置、原文、判定、改法」，而方案文档自己就在
   `en.ts:432` 这种**已漂 24 行**的陈旧锚点上栽过（见审计文档 §4 第 1 条）。行号是会在别人
   一次 PR 里悄悄失效的东西。
2. 决策 D12「文案闸要有正向证人」：只断言「禁语 0 命中」的闸会因**把句子整条删掉**而假绿。
   所以 `test_kept_rows_are_the_positive_witness` 钉住「保留」判定的条数下界，以及
   三条否定式免责句必须以 `保留` 在册——删掉它们让禁语消失，本条就红。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "keyword-claims-audit-2026-11.md"

COLUMNS = ["位置", "原文", "风险类型", "判定", "改法", "负责人"]
RISK_TYPES = ["绝对化记忆", "陪伴平台语言", "医疗边界", "无证据预测"]
VERDICTS = ["保留", "改写", "删除", "待法务"]
OWNER_ROLES = {"FE", "BE", "PLAT", "ARCH", "GROWTH", "CONTENT", "LEGAL"}
PLACEHOLDERS = {"", "-", "—", "–", "?", "？", "tbd", "todo", "n/a", "na", "待补", "待定", "暂无", "略"}
#: 只用于「出现在改法任意位置都算不合规」的内部检查。2026-11-02 第一版把「占位」和单字「略」
#: 也放进来，立刻误红了 `en.ts:43` 那条正当写法「输入框占位符」——中文 UI 里 placeholder 就叫占位符，
#: 而 `-`/`—`/单字在任何句子里都会出现。判据要能打坏例子，先把误伤面收掉。
INTERIOR_PLACEHOLDERS = {"待补", "待定", "暂无", "tbd", "todo"}

CITE = re.compile(r"^`(?P<path>[^`:]+):(?P<start>\d+)(?:-(?P<end>\d+))?`$")


def _table_rows() -> list[list[str]]:
    """只取「## 1. 审计表」那一张表，别的表（汇总、分工）不在这个解析面里。"""
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## 1. 审计表", 1)[1].split("## 2. 汇总", 1)[0]
    rows: list[list[str]] = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 6 or cells[0] == "位置" or set(cells[0]) <= set("-: "):
            continue
        rows.append(cells)
    return rows


ROWS = _table_rows()


def _cited_lines(pos: str) -> tuple[Path, list[str]]:
    """把 `path:行` 解析成源文件里那几行的真实文本。文件不存在＝引用是编的。"""
    m = CITE.match(pos)
    assert m, f"位置列必须是 `path:行` 或 `path:起-止`：{pos!r}"
    path = REPO / m.group("path")
    assert path.is_file(), f"位置指向的文件不存在：{pos}"
    lines = path.read_text(encoding="utf-8").splitlines()
    start, end = int(m.group("start")), int(m.group("end") or m.group("start"))
    assert 1 <= start <= end <= len(lines), f"{pos} 越界（该文件只有 {len(lines)} 行）"
    return path, lines[start - 1 : end]


def test_table_shape_is_the_six_documented_columns() -> None:
    header = DOC.read_text(encoding="utf-8").split("## 1. 审计表", 1)[1]
    first_row = next(ln for ln in header.splitlines() if ln.strip().startswith("|"))
    got = [c.strip() for c in first_row.strip("|").split("|")]
    assert got == COLUMNS, f"表头必须恰好是方案 §4.1 的六列，实际 {got}"
    assert len(ROWS) >= 30, f"台账 §KG-01 要求至少 30 条，现在 {len(ROWS)} 条"


def test_every_citation_reaches_the_real_line() -> None:
    """本文件的核心：每条「原文」必须真在那一行里。行号漂＝红，不许靠人记得。"""
    bad = []
    for pos, quoted, _risk, _verdict, _fix, _owner in ROWS:
        try:
            _path, cited = _cited_lines(pos)
        except AssertionError as exc:
            bad.append(str(exc))
            continue
        if quoted not in "\n".join(cited):
            bad.append(f"{pos} 这一行里没有该原文：{quoted[:60]!r}")
    assert not bad, "引用与源文件不一致：\n" + "\n".join(bad)


def test_risk_types_are_the_four_fixed_and_every_one_is_covered() -> None:
    got = {r for _p, _o, r, _v, _f, _w in ROWS}
    assert got == set(RISK_TYPES), f"风险类型只许方案 §4.1 那四类，出现 {sorted(got)}"
    counts = Counter(r for _p, _o, r, _v, _f, _w in ROWS)
    for risk in RISK_TYPES:
        assert counts[risk] >= 3, f"{risk} 只有 {counts[risk]} 条，覆盖不到台账点名的面"


def test_verdicts_are_actionable_and_from_the_fixed_set() -> None:
    counts = Counter(v for _p, _o, _r, v, _f, _w in ROWS)
    assert set(counts) <= set(VERDICTS), f"判定出现口径外的值：{sorted(set(counts) - set(VERDICTS))}"
    for verdict in ("改写", "保留"):
        assert counts[verdict] >= 1, f"审计必须产出可执行结论，`{verdict}` 一条都没有"


def test_fix_column_is_never_a_placeholder() -> None:
    """台账 §KG-01 的验收是「逐条给出保留/改写/删除，并指定负责人」——空改法＝没交付。

    2026-11-02 的变异 C5 打红失手过一次：当时只判「整格等于占位符」，
    于是「…不同口径。待补」这种**尾巴塞占位符**的写法能绕过。现在两条一起：
    ① 占位符不许出现在改法任何位置；② 判「改写/删除」的行必须真给出替换文案
    （`改成/换成/删掉/统一成/补齐/重写` 至少一个），不许只写「建议关注」。
    """
    action = re.compile(r"改成|改为|换成|换：|一起换|删掉|删除|统一成|补齐|重写|重做")
    bad = []
    for pos, _quoted, _risk, verdict, fix, owner in ROWS:
        stripped = fix.strip()
        if stripped in PLACEHOLDERS or stripped.lower() in PLACEHOLDERS:
            bad.append(f"{pos} 的改法是占位符")
        hit = sorted(w for w in INTERIOR_PLACEHOLDERS if w.lower() in stripped.lower())
        if hit:
            bad.append(f"{pos} 的改法里塞着占位符 {hit}：{stripped[:40]!r}")
        if verdict in {"改写", "删除"}:
            if len(stripped) < 12:
                bad.append(f"{pos} 判「{verdict}」但改法只有 {len(stripped)} 字，不可执行")
            if not action.search(stripped):
                bad.append(f"{pos} 判「{verdict}」却没给替换文案（要写『改成…』这种可直接抄的话）：{stripped[:40]!r}")
        roles = {x.strip() for x in owner.split("+")}
        if not roles or not roles <= OWNER_ROLES:
            bad.append(f"{pos} 的负责人 {owner!r} 不在角色表 {sorted(OWNER_ROLES)} 里")
    assert not bad, "改法/负责人不合格：\n" + "\n".join(bad)


#: 台账 §KG-01 与方案 §4.1 点名的锚。用**needle 现算行号**而不是写死行号：
#: 写死就会像方案文档那样漂 24 行还自称证据（2026-11-02 的 §4 勘误第 1 条）。
REQUIRED_ANCHORS = [
    ("frontend/src/i18n/locales/en.ts", '"step1Tagline":', "英文 onboarding 的 step1Tagline"),
    ("frontend/src/i18n/locales/en.ts", '"step1Feature2Desc":', "onboarding 的预测语 step1Feature2Desc"),
    ("web/index.html", "成长陪伴", "现网落地站的「成长陪伴」"),
    ("web/index.html", "性格画像不限", "现网 Pro+ 卡的权益虚标"),
    ("site/src/i18n.ts", "h1Html:", "站点 hero H1"),
    ("site/src/i18n.ts", "foot: { tag:", "站点页脚 tagline"),
    ("site/src/i18n.ts", "The journal that remembers you", "站点 <title>"),
    ("site/public/r/index.html", "It remembers every word you said.", "分享落地页的绝对化记忆句"),
]


@pytest.mark.parametrize("rel,needle,label", REQUIRED_ANCHORS, ids=[a[2] for a in REQUIRED_ANCHORS])
def test_named_anchor_from_the_ledger_is_in_the_list(rel: str, needle: str, label: str) -> None:
    path = REPO / rel
    assert path.is_file(), f"锚点所在文件不存在：{rel}"
    hit_lines = {n for n, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if needle in ln}
    assert hit_lines, f"{rel} 里找不到锚 needle {needle!r}（源文件被改名或整段删了？）"
    cited = {(pos.strip("`").rsplit(":", 1)[0]) for pos, *_rest in ROWS}
    assert rel in cited, f"台账点名的「{label}」必须进审计表（{rel} 一条都没有）"
    # 引用行要真落在 needle 上，不接受「引用了同文件另一行」蒙过去
    for pos, quoted, *_ in ROWS:
        if not pos.strip("`").startswith(f"{rel}:"):
            continue
        _p, lines = _cited_lines(pos)
        if any(needle in ln for ln in lines):
            assert needle in quoted or quoted in "\n".join(lines), (
                f"{pos} 声称覆盖「{label}」，但原文列 {quoted[:40]!r} 与 needle 不同源"
            )
            return
    raise AssertionError(f"「{label}」在 {rel} 的第 {sorted(hit_lines)} 行，审计表没有引用这些行")


def test_kept_rows_are_the_positive_witness() -> None:
    """D12：不许「把风险句整条删掉」来过禁语闸——`保留` 行与三条否定式免责句必须在册。"""
    kept = [(p, o) for p, o, _r, v, _f, _w in ROWS if v == "保留"]
    assert len(kept) >= 20, f"69 条里只判了 {len(kept)} 条保留，审计退化成一刀切删案，就不是审计了"
    for needle in (
        "it predicts nothing",
        "not a prediction, just your own words",
        "no predictions, no promises",
    ):
        assert any(needle in quoted for _p, quoted in kept), (
            f"否定式免责句 {needle!r} 必须以 `保留` 在册——它被删掉后禁语闸会假绿（KG-23 的第一轮就是这么红的）"
        )


def test_summary_counts_match_the_table() -> None:
    """§2 的三组数字由表里现算：手填错一位就红（本批初稿手写错过一次，见 §2 注）。"""
    text = DOC.read_text(encoding="utf-8")
    summary = text.split("## 2. 汇总", 1)[1].split("\n---", 1)[0]
    per_risk = Counter(r for _p, _o, r, _v, _f, _w in ROWS)
    changeable: dict[str, int] = defaultdict(int)
    for _p, _o, risk, verdict, _f, _w in ROWS:
        if verdict in {"改写", "删除"}:
            changeable[risk] += 1
    for risk in RISK_TYPES:
        expect = f"| {risk} | {per_risk[risk]} | {changeable[risk]} |"
        assert expect in summary, f"§2 汇总缺/错该行，应为 {expect!r}"
    expect_total = f"| **合计** | **{len(ROWS)}** | **{sum(changeable.values())}** |"
    assert expect_total in summary, f"§2 合计行应为 {expect_total!r}"
    verdict_counts = Counter(v for _p, _o, _r, v, _f, _w in ROWS)
    expect_line = "／".join(f"`{v}` {verdict_counts[v]}" for v in VERDICTS)
    assert expect_line in summary, f"§2 判定分布应为 {expect_line!r}"


#: 下游抄本表行号/行数的两处文档。2026-11-02 往本表加 KG-04 那一行后，
#: §2 之后的节号整体下移，而这两处仍写 `:23` 表头／`:97` 汇总／69 行／绝对化记忆 31——
#: 机器一声不响。这条判据把「下游引用」也拉进现算面：上游加一行，下游不改就红。
DOWNSTREAM_DOCS = [
    REPO / "docs" / "growth" / "kg01-kg03-kg18-closure-2026-11-02.md",
    REPO / "docs" / "keyword-growth-task-board-2026-11-01.md",
]


def _audit_real_lines() -> dict[str, int]:
    """本表当前真实行号：各节标题＋§1 表头行。"""
    out: dict[str, int] = {}
    for i, line in enumerate(DOC.read_text(encoding="utf-8").splitlines(), 1):
        m = re.match(r"## (\d)\. ", line)
        if m:
            out[f"§{m.group(1)}"] = i
        if line.startswith("| 位置 | 原文 |"):
            out["表头"] = i
    return out


def test_downstream_docs_quote_the_live_line_numbers_and_counts() -> None:
    real = _audit_real_lines()
    verdict_counts = Counter(v for _p, _o, _r, v, _f, _w in ROWS)
    cites = 0
    for path in DOWNSTREAM_DOCS:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "keyword-claims-audit-2026-11.md" not in line:
                continue
            for sec, n in re.findall(r"(§\d)[^`\n]{0,14}`:(\d+)`", line):
                cites += 1
                assert int(n) == real[sec], f"{path.name} 把 {sec} 写成 :{n}，本表现算 {real[sec]} ⇒ 节号漂了"
            for n, sec in re.findall(r"`:(\d+)` (§\d)", line):
                cites += 1
                assert int(n) == real[sec], f"{path.name} 把 {sec} 写成 :{n}，本表现算 {real[sec]} ⇒ 节号漂了"
            for n in re.findall(r":(\d+)` 表头", line):
                cites += 1
                assert int(n) == real["表头"], f"{path.name} 的「表头」引用写 :{n}，本表表头在 {real['表头']}"
        d = re.search(r"保留 (\d+)／改写 (\d+)／删除 (\d+)／待法务 (\d+)", text)
        assert d, f"{path.name} 没抄判定分布，判据没靶"
        assert tuple(map(int, d.groups())) == tuple(verdict_counts[v] for v in VERDICTS), (
            f"{path.name} 的判定分布 {d.groups()} ≠ 本表现算 "
            f"{tuple(verdict_counts[v] for v in VERDICTS)}"
        )
        r = re.search(r"(?:六列审计表|六列表)[\s*]+(\d+) 行", text)
        assert r, f"{path.name} 没抄审计表行数"
        assert int(r.group(1)) == len(ROWS), (
            f"{path.name} 写 {r.group(1)} 行，本表现算 {len(ROWS)} 行 ⇒ 上游加行、下游没回头同步"
        )
    assert cites >= 6, f"下游节号/表头引用只解析到 {cites} 处，判据覆盖面漂了"


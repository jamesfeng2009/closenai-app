"""KG-03 ADR 的完整性闸（2026-11-02）。

ADR 最怕两件事：**引用漂了没人知道**、**「不做」只写在纸上**。
本文件把两者都钉住：

- 文档里每一处 `` `path:line` `` 都要真指到一个存在且非空的行；四个上位判据
  （Baseline 的「不是什么」与「不做清单」）还要真打到那句原文上——写 `:91` 却漂到别的段落，红。
- 决定 3（不做陪伴角色）与决定 4（不做社区）各配一条**代码面证人**：
  陪伴词的禁语闸仍在、仓库里社区面命中为 0 且被检索目录非空。
  这样「不做」不是叙述，是每天会被跑一遍的事实。

禁止裸行号引用（`` `:164` ``）：方案文档自己就栽过一次——`docs/keyword-growth-implementation-plan-2026-11-01.md`
§4.1 写的 `en.ts:432` 到 2026-11-02 实测已漂到 `:456`（见 `docs/keyword-claims-audit-2026-11.md` §4 第 1 条）。
不带文件名的行号在几个月后就是假证据。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ADR = REPO / "docs" / "adr" / "2026-11-keyword-feature-scope.md"
BASELINE = "CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md"

COLUMNS = ["功能", "决定", "重开条件"]
FEATURES = ["每日情绪记录", "三层次情绪分析", "温柔陪伴 AI 角色", "匿名树洞 / 社区", "金句卡"]
PLACEHOLDERS = {"", "-", "—", "–", "?", "？", "tbd", "todo", "n/a", "待补", "待定", "暂无", "略"}

CITE = re.compile(r"`([A-Za-z0-9_.\-/]+\.(?:py|md|ts|tsx|json|html)):(\d+)(?:-(\d+))?`")
BARE_CITE = re.compile(r"`:\d+(?:-\d+)?`")


def _text() -> str:
    return ADR.read_text(encoding="utf-8")


def _decision_rows() -> list[list[str]]:
    section = _text().split("## 1. 五个决定", 1)[1].split("\n---", 1)[0]
    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 3 or cells[0] in ("功能",) or set(cells[0]) <= set("-: "):
            continue
        rows.append(cells)
    return rows


ROWS = _decision_rows()


def test_the_five_decisions_are_all_present_once() -> None:
    header_line = next(ln for ln in _text().splitlines() if ln.strip().startswith("| 功能"))
    got = [c.strip() for c in header_line.strip().strip("|").split("|")]
    assert got == COLUMNS, f"决定表表头必须是方案 §4.3 的三列，实际 {got}"
    assert len(ROWS) == 5, f"方案 §4.3 定的是五个决定，表里有 {len(ROWS)} 行：{[r[0] for r in ROWS]}"
    assert [r[0] for r in ROWS] == FEATURES, "五个决定的名称与顺序必须与方案 §4.3 一致"
    for feature, decision, restart in ROWS:
        assert decision not in PLACEHOLDERS, f"{feature} 没有真决定"
        assert decision != "待定", f"{feature} 还挂着「待定」——ADR 不许把板留给以后"


def test_no_bare_line_citation_anywhere() -> None:
    bare = BARE_CITE.findall(_text())
    assert not bare, f"禁止不带文件名的行号引用（{bare}）：文件会漂，见本文件 docstring 里那条 2026-11-02 勘误"


def test_every_file_line_citation_reaches_a_real_line() -> None:
    bad = []
    for path_rel, start, end in CITE.findall(_text()):
        path = REPO / path_rel
        if not path.is_file():
            bad.append(f"`{path_rel}:{start}` 指向不存在的文件")
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        lo, hi = int(start), int(end or start)
        if hi > len(lines):
            bad.append(f"`{path_rel}:{start}` 越界（该文件 {len(lines)} 行）")
            continue
        if not any(ln.strip() for ln in lines[lo - 1 : hi]):
            bad.append(f"`{path_rel}:{start}` 落在空行上——引用打不到内容＝假证据")
    assert not bad, "ADR 引用不合格：\n" + "\n".join(bad)
    assert len(CITE.findall(_text())) >= 10, "五个决定至少要有 10 处可核引用，否则只是自我叙述"


#: 承重锚点：这几处引用错了，整篇 ADR 的定位依据就断了。needle 必须出现在被引用的那一行。
LOAD_BEARING = [
    (BASELINE, 90, "不是占星、算命或对未来做确定性预测的产品"),
    (BASELINE, 91, "不是以角色库、浪漫幻想或虚拟关系等级为主引擎的陪伴平台"),
    (BASELINE, 164, "没有稳定证据支持的预测性洞察"),
    (BASELINE, 168, "仅为了增加功能数量而建设复杂的社区、社交关系或公开分享体系"),
    ("backend/config.py", 216, "mood_analysis_depths_enabled: bool = False"),
    ("backend/routers/mood.py", 83, "settings.mood_analysis_depths_enabled"),
    ("backend/routers/share.py", 115, "/share/quote-card"),
    ("backend/tests/test_absolute_memory_claims.py", 59, "AI companion"),
]


@pytest.mark.parametrize("rel,line,needle", LOAD_BEARING, ids=[f"{a}:{b}" for a, b, _ in LOAD_BEARING])
def test_load_bearing_anchor_says_what_the_adr_claims(rel: str, line: int, needle: str) -> None:
    src = (REPO / rel).read_text(encoding="utf-8").splitlines()
    assert needle in src[line - 1], (
        f"{rel}:{line} 这一行不再写着 {needle!r} ⇒ ADR 的定位/开关/契约依据需要重写，不是把 needle 改掉就算"
    )
    assert f"`{rel}:{line}`" in _text(), f"ADR 没有引用承重锚 {rel}:{line}"


def test_decision_two_names_the_kill_switch_and_its_default() -> None:
    """实验类决定必须连着写「怎么关」——否则重开以后没人能回滚。"""
    text = _text()
    assert "mood_analysis_depths_enabled" in text, "三层次那条要写出实验开关的键名"
    assert "40402" in text, "开关关时的行为（不暴露功能存在）要写进依据，不然「默认关」没有意义"
    assert "回滚" in text, "每个实验类决定都要给回滚口径"


def test_no_community_surface_exists_and_search_was_not_empty() -> None:
    """决定 4 的代码面证人：社区面命中为 0，且被检索目录真的在（空集不算证人事）。"""
    needle = re.compile(r"community|tree_hole|tree hole|树洞", re.I)
    hits: list[str] = []
    files = 0
    for base in ("backend/routers", "frontend/src/screens"):
        root = REPO / base
        assert root.is_dir(), f"检索目录 {base} 不在了——证人失效，本条要人工复核"
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix not in {".py", ".ts", ".tsx"}:
                continue
            files += 1
            if needle.search(p.read_text(encoding="utf-8", errors="replace")):
                hits.append(str(p.relative_to(REPO)))
    assert files >= 30, f"被检索文件只有 {files} 个，目录结构漂了，这条证人不成立"
    assert not hits, f"仓库里出现了社区/树洞面（{hits}）⇒ 决定 4 已被推翻，须先改 ADR 再改代码"


def test_companion_ban_is_still_enforced_in_landing_copy() -> None:
    """决定 3 的禁语证人：落地站文案不许自称 AI companion（蹭词的最短路径就是改站点）。"""
    pattern = re.compile(r"\bAI companion\b", re.I)
    src = (REPO / "site" / "src" / "i18n.ts").read_text(encoding="utf-8")
    assert not pattern.search(src), f"落地站重新自称 AI companion：{pattern.findall(src)} ⇒ 违反决定 3"
    assert "growth journal" in src, "定位句必须仍是 AI growth journal"


def test_restart_conditions_are_writable_checks_not_mood() -> None:
    for feature, decision, restart in ROWS:
        if restart.strip() in PLACEHOLDERS:
            assert decision.startswith("保留"), f"{feature} 判「{decision}」却不给重开条件"
            continue
        assert len(restart) >= 10, f"{feature} 的重开条件太短，不可执行：{restart!r}"
        if decision.startswith("不做"):
            assert re.search(r"评审|访谈|证明|不重开", restart), (
                f"{feature} 标了「不做」，重开条件必须写清楚**谁评审、拿什么证明**：{restart!r}"
            )


def test_adr_does_not_claim_new_endpoints_or_tables() -> None:
    """对齐决策 D1（只加 3 个端点）与 D2（不新建表）：本 ADR 是边界文件，不是立项文件。

    判据用「否定式必须写在场」而不是「禁词不许出现」——`不新增端点` 本身就含 `新增端点`，
    按禁词扫会把合规句子判红（2026-11-02 本条第一版就是这么误红的）。
    """
    text = _text()
    assert "不新增端点" in text and "不新增表" in text, "ADR 要明说自己不动契约"
    assert "D1" in text and "D2" in text, "要指回方案的全局决策，不能只写一句自我声明"
    assert "CREATE TABLE" not in text and "ALTER TABLE" not in text, "ADR 不该出现建表/加列语句"
    for positive in ("新增一个端点", "新建一张表", "再加一个端点"):
        assert positive not in text, f"ADR 里出现了立项口吻的「{positive}」——那属于另立 ADR 的范围"

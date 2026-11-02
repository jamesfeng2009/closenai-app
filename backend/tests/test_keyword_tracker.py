"""KG-05 的关键词表闸：词表不许凭印象，量不许改写，状态改了汇总必须跟着改。

**为什么要跟台账 §4 对拉**（两侧不同源）：本表声称「词量全部取自台账 §4 的抓取集合」。
如果只在本表内部自检，作者把某个词的量从 1.9K 写成 19K、或偷偷加一个没抓过的词，闸照样绿。
所以这里把台账 `docs/keyword-growth-task-board-2026-11-01.md` §4 的映射表**再解析一遍**，
两边比：词集合、数值、`outdated`（`*`）标记。词表与台账任一漂 ⇒ 红。

**为什么把「本轮没验证过的规则」也钉住**：R1（<500 门槛）本轮零命中、R2（8 周读数）本轮未跑
（KG-21 `BLOCKED`）。这种「规则写了但没人验过」的状态最容易被下游引用成「淘汰机制已在跑」，
所以判据要求文档里必须明写零命中/未跑，缺字即红。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "aso" / "keyword-tracker-2026-11.md"
BOARD = ROOT / "docs" / "keyword-growth-task-board-2026-11-01.md"
ASO_JSON = ROOT / "docs" / "aso" / "en-US-2026-11.json"

TEXT = DOC.read_text(encoding="utf-8")

#: 表头顺序即判据顺序；改名要同步改本表（刻意做成显式契约）。
COLUMNS = ["关键词", "月搜索量", "来源", "抓取日期", "搜索意图", "落点（词对的那块板）", "状态", "理由与下次动作"]
STATUSES = {"保留·主投", "保留", "保留·长尾", "保留·待法务", "保留·仅限对比页", "降级·观察", "淘汰"}
PLACEHOLDERS = {"", "—", "-", "待补", "待定", "暂无", "TBD", "tbd", "TODO", "todo"}


def _rows(text: str) -> list[list[str]]:
    out = []
    for line in text.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == len(COLUMNS):
            out.append(cells)
    return out


def _board_words() -> dict[str, tuple[str, bool]]:
    """台账 §4 映射表里的 {词: (量, 是否被工具标 outdated)}。"""
    section = BOARD.read_text(encoding="utf-8").split("## 4. 关键词 → 落点映射", 1)[1].split("\n---", 1)[0]
    found: dict[str, tuple[str, bool]] = {}
    for line in section.splitlines():
        if not line.startswith("| ") or "`" not in line or line.startswith("| 关键词簇"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        for m in re.finditer(r"`([^`]+)`\s*([\d.,]+[KM]?)(\*?)", cells[1]):
            found[m.group(1)] = (m.group(2), bool(m.group(3)))
    return found


ROWS = _rows(TEXT)
BOARD_WORDS = _board_words()
ASO_PACK = json.loads(ASO_JSON.read_text(encoding="utf-8"))


def test_scan_surface_is_what_the_gate_thinks_it_is() -> None:
    """证人：两侧解析都不许悄悄变空——台账 §4 与本表都要真的解析出词。"""
    assert len(BOARD_WORDS) >= 40, f"台账 §4 只解析到 {len(BOARD_WORDS)} 词 ⇒ 映射表结构漂了"
    assert len(ROWS) >= 40, f"本表只解析到 {len(ROWS)} 行，验收要求 40+"


def test_header_is_the_documented_columns() -> None:
    header = f"| {' | '.join(COLUMNS)} |"
    assert header in TEXT, "§2 表头与本闸的列契约不一致（改列名要同步说明为什么）"


def test_every_cell_is_filled_and_free_of_placeholders() -> None:
    for cells in ROWS:
        for col, value in zip(COLUMNS, cells):
            if value in PLACEHOLDERS:
                pytest.fail(f"{cells[0]} 的「{col}」是空/占位：{value!r}")


def test_word_set_matches_the_ledger_exactly() -> None:
    """词集合与台账 §4 完全相同：本表不许新增没抓过的词，也不许漏登已抓的词。"""
    mine = {c[0].strip("`") for c in ROWS}
    assert mine == set(BOARD_WORDS), (
        f"本表多出 {sorted(mine - set(BOARD_WORDS))} / 缺 {sorted(set(BOARD_WORDS) - mine)}"
    )


def test_volumes_and_outdated_markers_match_the_ledger() -> None:
    """量不许改写；`outdated`（台账里的 `*`）不许丢也不许凭空多。"""
    for cells in ROWS:
        word = cells[0].strip("`")
        vol, outdated = BOARD_WORDS[word]
        assert cells[1].startswith(vol), f"{word} 台账写 {vol}，本表写 {cells[1]!r} ⇒ 数字被改写了"
        marked = "outdated" in cells[1]
        assert marked == outdated, f"{word} 的 outdated 标记与台账不符（本表 {cells[1]!r}，台账星号 {outdated}）"
    assert sum(1 for c in ROWS if "outdated" in c[1]) == sum(1 for v in BOARD_WORDS.values() if v[1]), (
        "outdated 词数与台账不等"
    )


def test_source_and_date_are_the_documented_single_source() -> None:
    """来源必须写「单源」＋日期一致：不许把 Ubersuggest 单源粉饰成交叉核对过的量。"""
    for cells in ROWS:
        assert "单源" in cells[2], f"{cells[0]} 的来源没标单源：{cells[2]!r}"
        assert "Ubersuggest" in cells[2], f"{cells[0]} 来源不是 Ubersuggest：{cells[2]!r}"
        assert cells[3] == "2026-10-27", f"{cells[0]} 抓取日期漂了：{cells[3]!r}"


def test_status_vocabulary_is_closed() -> None:
    for cells in ROWS:
        assert cells[6] in STATUSES, f"{cells[0]} 的状态 {cells[6]!r} 不在口径集合里"


def test_every_kept_word_has_a_landable_target() -> None:
    """R4：保留/降级的词必须有落点，且落点要么是真存在的面，要么挂在台账真有的 KG 任务上。"""
    kg_ids = set(re.findall(r"^\| (KG-\d\d) \|", BOARD.read_text(encoding="utf-8"), re.M))
    assert len(kg_ids) >= 30, f"台账任务号只解析到 {len(kg_ids)} 个"
    for cells in ROWS:
        if cells[6] == "淘汰":
            assert cells[5].startswith("无"), f"{cells[0]} 已淘汰却还挂着落点：{cells[5]!r}"
            continue
        refs_kg = re.search(r"KG-\d\d", cells[5]) is not None and any(
            t in cells[5] for t in kg_ids
        )
        path = re.search(r"`([^`]+)`", cells[5])
        exists = bool(path) and (ROOT / path.group(1)).exists()
        assert refs_kg or exists, (
            f"{cells[0]} 的落点 {cells[5]!r} 既不指向台账真有的 KG 任务，也不是仓内已存在的面 ⇒ 词不对板"
        )


def test_competitor_words_stay_out_of_the_aso_keyword_field() -> None:
    """R3 的两侧对拉：表里说竞品词只进对比页，就去 ASO 包里真验一遍不含它。"""
    brands = {b.lower() for b in ASO_PACK["competitor_brands"]}
    for cells in ROWS:
        word = cells[0].strip("`").lower()
        hit = {b for b in brands if b in word}
        if hit:
            assert cells[6] == "保留·仅限对比页", f"{cells[0]} 含竞品品牌 {sorted(hit)}，状态却是 {cells[6]}"
            assert "/compare/" in cells[5], f"{cells[0]} 的落点必须是对比页：{cells[5]!r}"
    keyword_blob = " ".join(v["text"].lower() for v in ASO_PACK["fields"]["keyword_variants"])
    for b in brands:
        assert b not in keyword_blob, f"ASO 关键词字段里出现竞品品牌词 {b!r}（R3）"


def test_companion_words_are_dropped_and_the_doc_says_why() -> None:
    """ADR 决定 3（不做陪伴角色）在本表的落点：`ai companion`／`ai friend` 族必须全部淘汰。"""
    family = [c for c in ROWS if re.search(r"companion|ai friend", c[0])]
    assert len(family) >= 4, f"陪伴词族只解析到 {len(family)} 条，台账 §4 有 4 条"
    for cells in family:
        assert cells[6] == "淘汰", f"{cells[0]} 未淘汰，与 ADR 决定 3 冲突"
        assert "ADR" in cells[7] or "禁语" in cells[7], f"{cells[0]} 的淘汰理由没指回 ADR/禁语清单"


def _rule_rows(text: str) -> dict[str, list[str]]:
    """§1 规则表：{R1: [#, 规则, 机械判据, 本轮命中]}。"""
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.startswith("| R"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4:
            out[cells[0]] = cells
    return out


def _volume(raw: str) -> float:
    """`1.9K` → 1900、`720` → 720；解析不出数字即红（不许用「约」这类词占量列）。"""
    m = re.match(r"^\s*([\d.,]+)\s*([KM]?)", raw)
    assert m, f"量列 {raw!r} 不是数字"
    n = float(m.group(1).replace(",", ""))
    return n * (1_000 if m.group(2) == "K" else 1_000_000 if m.group(2) == "M" else 1)


def test_untested_rules_are_declared_untested() -> None:
    """R1 本轮零命中、R2 本轮未跑——**写在各自那一行**，缺字即红。

    为什么按行判：全文 `in` 会让「R3 那行顺带提了一句零命中」替 R1 过关，
    于是把没验证过的规则读成已在跑（变异自证 G10 第一次就是这么漏的）。
    """
    rules = _rule_rows(TEXT)
    assert {"R1", "R2", "R3", "R4"} <= set(rules), f"§1 规则表只解析到 {sorted(rules)}，四条判据不许少"
    r1 = rules["R1"][3]
    assert "零命中" in r1, "R1（<500）本轮零命中必须写在 R1 那一行，否则读者会以为门槛在筛东西"
    assert "没被验证过" in r1, "R1 那一行必须明写「本轮没被验证过」，不许写成已生效"
    r2 = rules["R2"][3]
    assert "未跑" in r2, "R2（8 周读数）本轮未跑必须写在 R2 那一行"
    assert "BLOCKED" in r2, "必须在 R2 那一行点明 KG-21 是未跑的原因"

    smallest = min(_volume(c[1]) for c in ROWS)
    stated = re.search(r"最小\s*([\d.,]+[KM]?)", r1)
    assert stated, f"R1 那一行没写本轮最小值，无法复核：{r1!r}"
    assert smallest == _volume(stated.group(1)), (
        f"表里现算最小 {_volume(stated.group(1)):g}（写成 {stated.group(1)}），实测 {smallest:g} ⇒ 量与结论脱钩"
    )
    assert smallest < 500 or "500" in rules["R1"][2], "R1 的门槛 500 必须写在机械判据列"


def test_rule_r3_hit_count_is_recomputed_from_the_table() -> None:
    """R3 声明「命中 4 条」——那一格的数字由 §2 表现算再比，手填错一位即红。"""
    rules = _rule_rows(TEXT)
    brands = {b.lower() for b in ASO_PACK["competitor_brands"]}
    hits = [c[0] for c in ROWS if any(b in c[0].strip("`").lower() for b in brands)]
    declared = re.search(r"命中\s*(\d+)\s*条", rules["R3"][3])
    assert declared, f"R3 那一行没写命中条数：{rules['R3'][3]!r}"
    assert len(hits) == int(declared.group(1)), (
        f"R3 声明命中 {declared.group(1)} 条，表里现算 {len(hits)} 条：{hits}"
    )


def test_summary_distribution_is_recomputed_from_the_table() -> None:
    """§3 的四格由表里现算再比——手填错一位就红（KG-01 那批就是这么纠出来的）。"""
    bucket = Counter()
    for cells in ROWS:
        s = cells[6]
        if s == "保留·主投":
            bucket["主投"] += 1
        elif s.startswith("保留"):
            bucket["保留其余"] += 1
        elif s == "降级·观察":
            bucket["降级"] += 1
        else:
            bucket["淘汰"] += 1
    summary = TEXT.split("## 3. 分布", 1)[1].split("\n## ", 1)[0]
    for label, value in (
        ("保留·主投", bucket["主投"]),
        ("保留（含长尾/待法务/仅限对比页）", bucket["保留其余"]),
        ("降级·观察", bucket["降级"]),
        ("淘汰", bucket["淘汰"]),
    ):
        expect = f"| {label} | {value} |"
        assert expect in summary, f"§3 汇总缺/错该行，应为 {expect!r}（表里现算）"
    assert sum(bucket.values()) == len(ROWS)


def test_planned_url_count_in_the_defects_section_is_live() -> None:
    """§4 第 4 条说「N 个规划 URL 目前不存在」——那个 N 由落点列现算，不许写死。"""
    planned = set()
    for cells in ROWS:
        for url in re.findall(r"`(/[a-z0-9/\-]+)`", cells[5]):
            if url.startswith("/en/"):
                planned.add(url)
    live = {u for u in planned if (ROOT / "web" / (u[len("/en/") :].lstrip("/") + ".html")).exists()}
    missing = planned - live
    assert f"{len(missing)} 个 URL 目前不存在" in TEXT, (
        f"§4 的规划 URL 数与表里现算的 {len(missing)} 不符：{sorted(missing)}"
    )

"""KG-22 月度复盘模板闸（2026-11-03，批次 9）。

**这张闸判什么**：`docs/growth/monthly-review-2026-12.md` 交付的是**模板与阈值**，不是结论——
所以它最容易造假的地方恰恰是「看起来已经有结论了」。本文件把三件事钉住：
① 题面基线的每一个数字／簇／落点都能回指台账 §4 与内容源（不是凭空想的问题）；
② 四条停/改/扩阈值挂在指标字典的既有键名上（不另造一套指标）；
③ 结论表当前 0 行填写，且这个「0」带着正向证人（表头四格与两条列名必须在场）。

**两侧不同源**：题数区间（20–30）、记录字段、四条触发条件、任务状态——全部**从台账现读**再比。
台账改文案，本闸跟着改判；本文件不硬抄台账的句子。
`test_the_engine_set_is_an_implementation_choice_not_a_promise` 是这个风格的反面证人：
台账 §KG-22 里**没有**任何引擎名，所以文档必须自己声明那是实现选择，而不是暗示那是要求。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "growth" / "monthly-review-2026-12.md"
BOARD = REPO / "docs" / "keyword-growth-task-board-2026-11-01.md"
DICT = REPO / "docs" / "metrics-dictionary.md"
PAGES_JSON = REPO / "site-content" / "pages.json"

TEXT = DOC.read_text(encoding="utf-8")
BOARD_TEXT = BOARD.read_text(encoding="utf-8")
DICT_TEXT = DICT.read_text(encoding="utf-8")
SLUGS = {p["slug"] for p in json.loads(PAGES_JSON.read_text(encoding="utf-8"))["pages"]}


def _section(md: str, head: str) -> str:
    """取 `## <head>` 到下一个 `## ` 之间的正文。"""
    start = md.index(head)
    nxt = md.find("\n## ", start + 1)
    return md[start:] if nxt < 0 else md[start:nxt]


def _task_detail(kg: str) -> str:
    """台账 §3 里某个任务的小节（从 `#### KG-NN ·` 到下一个 `#### ` 或 `---`）。"""
    start = BOARD_TEXT.index(f"#### {kg} ·")
    nxt = re.search(r"\n(#### |---\n)", BOARD_TEXT[start + 10:])
    return BOARD_TEXT[start:start + 10 + (nxt.start() if nxt else len(BOARD_TEXT))]


def _rows(section_text: str) -> list[list[str]]:
    """表格数据行（跳过分隔行与表头）。"""
    out = []
    for line in section_text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(set(c) <= {"-", ":", " "} and c for c in cells):
            continue
        out.append(cells)
    return out


# §1 的题面基线：从 Q-01 到 Q-24 那一段
Q_HEAD = "## 1. 问题基线"
Q_ROWS = [r for r in _rows(_section(TEXT, Q_HEAD)) if r and r[0].startswith("Q-")]
# 台账 §4 映射表：第一列＝簇名，整行原文用来验「逐字命中」
BOARD_S4 = _section(BOARD_TEXT, "## 4. 关键词 → 落点映射")
BOARD_CLUSTERS = [r[0] for r in _rows(BOARD_S4) if r and r[0] not in {"关键词簇", ""} and set(r[0]) != {"-"}]
KG22_DETAIL = _task_detail("KG-22")
KG22_ROW = next(r for r in _rows(_section(BOARD_TEXT, "## 1. ")) if r and r[0] == "KG-22")


def test_question_baseline_sits_in_the_boarded_range() -> None:
    """题数不许漂：区间是台账 §KG-22 现读的，不是本文件抄的。"""
    m = re.search(r"固定一组 (\d+)(?:–|-)?(\d*) 个真实问题", KG22_DETAIL)
    assert m, "台账 §KG-22 的『固定一组 N–M 个真实问题』这句话改了形状，本闸要跟着重写"
    lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
    assert lo <= len(Q_ROWS) <= hi, f"基线 {len(Q_ROWS)} 条，台账要求 {lo}–{hi} 条"
    assert len(Q_ROWS) >= 20, f"只有 {len(Q_ROWS)} 条题面 ⇒ 趋势样本太小"


def test_every_cluster_cell_exists_in_the_boards_mapping_table() -> None:
    """簇必须逐格落在台账 §4 的第一列——凭空造的簇就是凭空造的靶。"""
    unknown = sorted({r[2] for r in Q_ROWS} - set(BOARD_CLUSTERS))
    assert not unknown, f"这些簇不在台账 §4 的映射表里：{unknown}"
    banned = [c for c in BOARD_CLUSTERS if "不投" in c or "不主投" in c]
    used = sorted({r[2] for r in Q_ROWS} & set(banned))
    assert not used, f"台账标成不投的簇被拉进基线了：{used}"


def test_rows_claiming_the_literal_keyword_are_actually_the_keyword() -> None:
    """标 `§4 原词` 的行必须**逐字**出现在 §4 里；原词行数还得够撑住「取自抓取集合」这句话。"""
    claims = [r for r in Q_ROWS if r[3] == "§4 原词"]
    assert len(claims) >= 12, f"`§4 原词` 只有 {len(claims)} 条 ⇒『词全部取自 §4』这句成了空话"
    fake = [r[1] for r in claims if r[1].strip("`") not in BOARD_S4]
    assert not fake, f"这些题面标了 §4 原词，但 §4 里没有逐字命中：{fake}"
    other = [r[3] for r in Q_ROWS if r[3] not in {"§4 原词", "同簇问法"}]
    assert not other, f"题面来源只许两种取值，出现第三种：{other}"


def test_the_count_sentence_matches_the_table_above_it() -> None:
    """表下那句「共 24 条：§4 原词 18 条、同簇问法 6 条」必须等于现数表格。

    2026-11-03 首版这句写的是 16／8，表格实际 18／6——**读者抄走的是句子，不是表格**，
    所以这句话是本闸的靶：它的两侧（句子与表格）在同一份文档里，但表格那一侧本身逐字对拉台账 §4，
    链条仍指向外部源。
    """
    m = re.search(r"共 (\d+) 条：`§4 原词` (\d+) 条、`同簇问法` (\d+) 条", _section(TEXT, Q_HEAD))
    assert m, "§1 表下的计数句改了形状 ⇒ 本闸跟着重写，不许悄悄删掉这条判据"
    said_total, said_literal, said_paraphrase = (int(g) for g in m.groups())
    literal = sum(1 for r in Q_ROWS if r[3] == "§4 原词")
    paraphrase = sum(1 for r in Q_ROWS if r[3] == "同簇问法")
    assert said_total == len(Q_ROWS), f"句子说 {said_total} 条，表格有 {len(Q_ROWS)} 行"
    assert said_literal == literal, f"句子说 `§4 原词` {said_literal} 条，表格标了 {literal} 条"
    assert said_paraphrase == paraphrase, f"句子说 `同簇问法` {said_paraphrase} 条，表格标了 {paraphrase} 条"
    assert literal + paraphrase == len(Q_ROWS), "两种合法来源没覆盖全部行 ⇒ 有行的『题面来源』不是这两种"


def test_landed_targets_are_real_pages_and_unlanded_ones_carry_a_task() -> None:
    """落点两面判：`已落地` 必须是内容源里的真 slug，`未落地` 必须带任务号。

    「已落地但页面不存在」是把复盘做成幻觉的入口；反向也要防——页面已经有了却标成未落地，
    等于给 KG-13/15/16 记了一条不存在的欠账。
    """
    bad_landed = [(r[0], r[4]) for r in Q_ROWS if r[5] == "已落地" and r[4].strip("`") not in SLUGS]
    assert not bad_landed, f"这些行标了已落地，但 pages.json 里没有该页：{bad_landed}"
    bad_pending = [
        (r[0], r[5]) for r in Q_ROWS
        if r[5].startswith("未落地") and not re.search(r"KG-1[356]", r[5])
    ]
    assert not bad_pending, f"未落地行必须点名 KG-13／KG-15／KG-16：{bad_pending}"
    contradiction = [r[0] for r in Q_ROWS if r[5].startswith("未落地") and r[4].strip("`") in SLUGS]
    assert not contradiction, f"标未落地但页面真存在：{contradiction}"
    pending_task_ids = {m.group(0) for r in Q_ROWS for m in re.finditer(r"KG-1[356]", r[5])}
    for tid in pending_task_ids:
        row = next(r for r in _rows(_section(BOARD_TEXT, "## 1. ")) if r and r[0] == tid)
        assert row[5] == "`TODO`", f"{tid} 若已到 `REVIEW`，基线里那一行的『未落地』就过期了（本闸不许长期放行假欠账）"


def test_the_record_fields_are_the_boards_own_list() -> None:
    """台账点名的记录字段（是否提及／引用 URL／答案摘要／语言／地区／引擎／日期）一个都不许少。"""
    m = re.search(r"按月记录：(.+)", KG22_DETAIL)
    assert m, "台账 §KG-22 的『按月记录：…』字段清单换了写法，本闸要跟着重写"
    fields: list[str] = []
    for part in m.group(1).rstrip("。").split("、"):
        fields.extend(x.strip() for x in part.split("/") if x.strip())
    assert len(fields) >= 6, f"从台账抠出的字段只有 {fields}"
    s2 = _section(TEXT, "## 2.")
    missing = [f for f in fields if f not in s2]
    assert not missing, f"记录字段表缺这些台账点名的字段：{missing}"


def test_the_four_thresholds_hang_on_the_metric_dictionary() -> None:
    """阈值只能引用指标字典里已有的键：另造指标名＝另造一套无法对账的读数。"""
    s3 = _section(TEXT, "## 3.")
    keys = sorted({k for r in _rows(s3) if len(r) > 1 for k in re.findall(r"`([a-z_]+)`", r[1])})
    assert keys, "阈值表的『指标键』列一个键名都没写 ⇒ 阈值没法对账"
    ghost = [k for k in keys if k not in DICT_TEXT]
    assert not ghost, f"这些键不在 `docs/metrics-dictionary.md` 里：{ghost}"


def test_the_stop_rules_cover_the_boards_four_triggers() -> None:
    """台账写了四条停/改/扩触发条件，本文件就得有四条，且动作/负责人非形容词。"""
    bullets = re.findall(r"^  - (.+)$", KG22_DETAIL, re.M)
    triggers = [b for b in bullets if ("：" in b) and any(k in b for k in ("无曝光", "无激活", "无二次循环", "CAC"))]
    assert len(triggers) == 4, f"台账 §KG-22 的触发条件现算 {len(triggers)} 条，判据按 4 条钉"
    rows = [r for r in _rows(_section(TEXT, "## 3.")) if len(r) > 3 and not r[0].startswith("触发条件")]
    assert len(rows) == len(triggers), f"阈值表 {len(rows)} 行 ≠ 台账 {len(triggers)} 条触发条件"
    for keyword in ("无曝光", "无激活", "无二次循环", "CAC"):
        assert any(keyword in r[0] for r in rows), f"阈值表少了挂在『{keyword}』上的那一行"
        owner = next(r for r in rows if keyword in r[0])[3]
        assert owner in {"GROWTH", "ARCH", "PLAT"} or "+" in owner, f"『{keyword}』那行的负责人写成了形容词：{owner!r}"


def test_no_conclusion_has_been_drawn_yet() -> None:
    """反向归零（结论表 0 行填写）＋正向证人（表头与四格决定必须在场），否则扫空集永远绿。"""
    s4 = _section(TEXT, "## 4.")
    rows = [r for r in _rows(s4) if len(r) > 4 and r[0] in {"保留", "改写", "下线", "加投"}]
    assert len(rows) == 4, f"结论模板的决定格应当是 4 个（保留/改写/下线/加投），现算 {len(rows)}"
    # 负责人一格现在就是 GROWTH（那是归属不是结论），其余四格必须仍是待填
    filled = [(r[0], r[1]) for r in rows if any(c != "待填" for c in (r[1], r[2], r[3], r[5]))]
    assert not filled, f"KG-21 还 `BLOCKED`，结论表却出现了真数据行：{filled}"
    assert "首份月度报告＝未产" in TEXT.replace(" ", ""), "文档没写明『首份报告未产』⇒ 读者会把它当成已复盘"
    assert "数据依据" in s4 and "负责人" in s4, "结论模板丢了『数据依据／负责人』两列，验收句『每个决定有数据依据与负责人』就没载体"


def test_status_agrees_with_the_board_row() -> None:
    """文档自称的状态必须与台账行一致：不许文档 `TODO`、台账 `REVIEW`（或反过来）。"""
    declared = re.search(r"状态：\*\*(?:仍|已|保持)? ?`([A-Z]+)`", TEXT)
    assert declared, "文档开头没有『状态：**`XXX`**』这一行 ⇒ 状态这个载体本身没了"
    assert declared.group(1) == KG22_ROW[5].strip("`"), (
        f"文档说自己是 {declared.group(1)}，台账 KG-22 行是 {KG22_ROW[5]}")
    assert KG22_ROW[5] == "`TODO`", (
        "台账 KG-22 若已升到 `REVIEW`/`DONE`，本闸的『结论未产生』判据就该整体重写，别长期放行旧口径")


def test_the_engine_set_is_an_implementation_choice_not_a_promise() -> None:
    """台账没点任何引擎名 ⇒ 文档必须自己声明四个引擎是**本批选定**，不能写成要求。"""
    engines = ("chatgpt", "perplexity", "gemini", "copilot")
    s2 = _section(TEXT, "## 2.")
    named = [e for e in engines if e in s2.lower()]
    assert len(named) == len(engines), f"观测面的引擎少了：{named}"
    assert not any(e in KG22_DETAIL.lower() for e in engines), (
        "台账 §KG-22 现在点名了引擎 ⇒ 这条『实现选择』判据过期，本行要重写而不是偷偷放行")
    assert "实现选择" in s2, "引擎面没标注成实现选择，读者会以为那是验收要求"


def test_the_paid_cac_row_cannot_fire_while_the_data_task_is_blocked() -> None:
    """第四条（CAC）在 KG-21 解除前必须标成不可触发：写成跑得到的规则就是造假。"""
    kg21 = next(r for r in _rows(_section(BOARD_TEXT, "## 1. ")) if r and r[0] == "KG-21")
    s3 = "\n".join("\t".join(r) for r in _rows(_section(TEXT, "## 3.")))
    if kg21[5] == "`BLOCKED`":
        assert "未启用" in s3 and "不可触发" in s3, (
            "KG-21 仍 BLOCKED，CAC 那行却写得像跑得起来 ⇒ 首月就会拿一条没源的规则做决定")
    else:
        assert "不可触发" not in s3, f"KG-21 已变 {kg21[5]}，CAC 那行的『不可触发』要撤掉"


def test_t7_10_merge_clause_is_written_and_named_by_the_board() -> None:
    """验收句「与 T7-10 的月度复盘合并，不产生第二份互相矛盾的结论」要有载体。"""
    assert "T7-10" in KG22_DETAIL, "台账 §KG-22 验收不再提 T7-10 ⇒ 本闸与文档 §5 一起重写"
    s5 = _section(TEXT, "## 5.")
    assert "T7-10" in s5, "文档没有与 T7-10 的合并口径"
    assert "不许同时" in s5 or "只写一次" in s5, (
        "合并口径没写成可判的禁令（结论只写一次／两份不许同时各有一张决定表）")

"""关键词台账自身的一致性闸（2026-11-02，批次 3 附带）。

**为什么要这张闸**：每批结案都要手改 §1 的状态行、§2 的快照数字与「按工作流」那六行。
批次 1–2 全靠人算，`TODO 14／REVIEW 12` 这类数字一旦少算一个，读者就会把「还差 17 件」读成「还差 14 件」。
本文件把这些数字全部改成**从 §1 现算再比**：上游加一行任务、下游不回头同步即红（两侧不同源）。

**判据面**：
①§1 的 ID/状态/列数（`:65`、`:72`）；②§2 快照每个状态的条数与任务清单（`:81`）；
③「按工作流」四列计数（`:110`）；④§3 详情小节的任务号集合与 §1 完全相同（`:132`）；
⑤`REVIEW`/`DONE` 行的证据列必须指到一个仓内真存在的文件（`:144`）；
⑥全文所有 `路径:行` 完整引用必须打到真行（`:160`，引用即自证）；
⑦**下游**抄台账的行号（ASO 两份文档＋承诺审计）必须打到真行、与同句的任务号／节号同源，
且同句要带一个**逐字内容锚**落在那一行（`:199`，2026-11-03 第七批补的锚，见该条判据文档串）。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BOARD = REPO / "docs" / "keyword-growth-task-board-2026-11-01.md"
TEXT = BOARD.read_text(encoding="utf-8")

STATUSES = ["TODO", "DOING", "REVIEW", "BLOCKED", "DONE", "CANCELLED"]
#: §1 的列契约：ID/工作流/任务/Owner/优先级/状态/依赖/验收摘要（证据）/目标
COLUMN_COUNT = 9
WORKFLOW_COLS = ["TODO", "REVIEW", "BLOCKED", "DONE"]
FULL_CITE = re.compile(r"(?P<path>(?:docs|backend|scripts|frontend|site|web|\.github)/[^`\s:]+):(?P<line>\d+)")
TICK = chr(96)


def _status(row: list[str]) -> str:
    return row[5].strip(TICK)



def _section(start: str, stop: str) -> str:
    return TEXT.split(start, 1)[1].split(stop, 1)[0]


def _rows() -> list[list[str]]:
    out: list[list[str]] = []
    for line in _section("## 1. 任务总表", "## 2. 进度快照").splitlines():
        line = line.strip()
        if not line.startswith("| KG-"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        assert len(cells) == COLUMN_COUNT, f"{cells[0]} 行是 {len(cells)} 列，§1 的列契约是 {COLUMN_COUNT} 列"
        out.append(cells)
    return out


ROWS = _rows()
TABLE_IDS = [r[0] for r in ROWS]


def test_the_table_parsed_what_the_gate_thinks_it_parsed() -> None:
    """证人：解析面本身不许悄悄变空（归零闸要配正向证人，这是老规矩）。"""
    assert len(ROWS) >= 30, f"§1 只解析到 {len(ROWS)} 行，台账现有 31 个任务 ⇒ 表结构漂了"


def test_ids_are_unique_two_digit() -> None:
    for tid in TABLE_IDS:
        assert re.fullmatch(r"KG-\d{2}", tid), f"任务 ID 必须是 KG-01 这种两位编号：{tid!r}"
    dup = [t for t, n in Counter(TABLE_IDS).items() if n > 1]
    assert not dup, f"任务 ID 重复：{dup}"


def test_one_status_per_row_from_the_closed_set() -> None:
    """§0 的规矩：一行一个任务、一个状态；禁止 `REVIEW/DONE` 并列，禁止用表情代替状态。"""
    for r in ROWS:
        raw = r[5]
        inner = raw.strip("`")
        assert raw == f"`{inner}`", f"{r[0]} 的状态列不是单个反引号包着的词：{raw!r}"
        assert inner in STATUSES, f"{r[0]} 的状态 {inner!r} 不在六态集合里"


def test_snapshot_counts_match_the_table() -> None:
    """§2 的「数量」列与「任务」列都由 §1 现算——手算少一个就红。"""
    by_status: dict[str, list[str]] = defaultdict(list)
    for r in ROWS:
        by_status[_status(r)].append(r[0])
    seen: set[str] = set()
    for line in _section("## 2. 进度快照", "按工作流：").splitlines():
        m = re.match(r"^\| `(?:`)?(TODO|DOING|REVIEW|BLOCKED|DONE|CANCELLED)(?:`)? \| (\d+) \| (.*) \|$", line.strip())
        if not m:
            continue
        status, declared, listed = m.group(1), int(m.group(2)), m.group(3).strip()
        seen.add(status)
        actual = by_status.get(status, [])
        assert declared == len(actual), f"§2 把 {status} 写成 {declared}，§1 现算 {len(actual)}"
        if not actual:
            assert listed in {"—", "-"}, f"{status} 现算 0 条，§2 的任务列应写「—」而不是 {listed!r}"
        else:
            names = re.findall(r"KG-\d{2}", listed.split("（", 1)[0])
            assert sorted(names) == sorted(actual), (
                f"§2 的 {status} 任务清单与 §1 现算不一致：文档 {sorted(names)} / 现算 {sorted(actual)}"
            )
            assert str(len(actual)) in line, f"§2 的 {status} 行条数与清单数不符：{line!r}"
    assert set(seen) == set(STATUSES), f"§2 只解析到 {sorted(seen)} 个状态行，六态都要在册"
    total = re.search(r"\| \*\*合计\*\* \| \*\*(\d+)\*\* \|", TEXT)
    assert total and int(total.group(1)) == len(ROWS), (
        f"§2 合计写 {total.group(1) if total else '空'}，§1 现算 {len(ROWS)} 行"
    )


def test_workflow_table_matches_the_table() -> None:
    """「按工作流」五行：总数与 TODO/REVIEW/BLOCKED/DONE 四列全部由 §1 现算。"""
    by_flow: dict[str, Counter] = defaultdict(Counter)
    for r in ROWS:
        by_flow[r[1]][_status(r)] += 1
    seen: set[str] = set()
    for line in _section("按工作流：", "\n---").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 6 or not re.match(r"^[A-F] ", cells[0]):
            continue
        seen.add(cells[0])
        flow = cells[0]
        assert int(cells[1]) == sum(by_flow[flow].values()), (
            f"工作流 {flow} 总数写 {cells[1]}，§1 现算 {sum(by_flow[flow].values())}"
        )
        for col, value in zip(WORKFLOW_COLS, cells[2:]):
            assert int(value) == by_flow[flow][col], (
                f"工作流 {flow} 的 {col} 写 {value}，§1 现算 {by_flow[flow][col]}"
            )
    assert seen == set(by_flow), f"按工作流表漏行/多行：文档 {sorted(seen)} / 现算 {sorted(by_flow)}"


def test_detail_sections_cover_exactly_the_table() -> None:
    """§3 的 `#### KG-NN` 小节必须与 §1 的任务号**完全相同**——两边不许各漂一份。"""
    detailed = set(re.findall(r"^#### (KG-\d{2}) ·", TEXT, re.M))
    table = set(TABLE_IDS)
    assert detailed, "§3 一个详情小节都没解析到，判据没靶"
    missing = sorted(table - detailed)
    extra = sorted(detailed - table)
    assert not missing, f"§1 有任务但 §3 没写详情：{missing}"
    assert not extra, f"§3 写了 §1 里没有的任务号：{extra}"


@pytest.mark.parametrize("status", ["REVIEW", "DONE"])
def test_finished_rows_point_at_a_real_file(status: str) -> None:
    """`REVIEW`/`DONE` 是「交付物完成，等评审/已上线」——证据列必须指到一个仓内真存在的文件。

    这条防的是「状态改了、证据没落地」：把状态刷成 `REVIEW` 但证据列只有一句形容词，
    评审的人会当成有交付物。TODO/BLOCKED 不在这条覆盖面内——它们本来就没有交付物。
    """
    rows = [r for r in ROWS if _status(r) == status]
    if status == "DONE" and not rows:
        pytest.skip(f"当前没有 {status} 行，条件性判据未跑（不算绿）")
    for r in rows:
        evidence = r[7]
        paths = re.findall(r"`((?:docs|backend|scripts|frontend|site|web|\.github)/[^`:\s]+)(?::\d+)?`", evidence)
        hits = [p for p in paths if (REPO / p).exists()]
        assert hits, f"{r[0]} 标 {status} 但证据列没有指向仓内真存在的文件：{evidence[:120]!r}"


def test_full_citations_reach_the_real_line() -> None:
    """全文所有 `路径:行` 完整引用：文件必须在、行号不许越界（引用即自证）。"""
    cites = FULL_CITE.findall(TEXT)
    assert len(cites) >= 15, f"台账只有 {len(cites)} 处完整引用，判据覆盖面漂了"
    for rel, line_s in cites:
        path = REPO / rel
        assert path.is_file(), f"台账引用 {rel}:{line_s}，文件不存在"
        n = int(line_s)
        lines = path.read_text(encoding="utf-8").splitlines()
        assert 1 <= n <= len(lines), f"台账引用 {rel}:{line_s}，该文件只有 {len(lines)} 行 ⇒ 行号漂了"


#: 抄台账行号的下游文档（批次 3 的教训：台账里插一行说明，下面全体下移）
DOWNSTREAM = [
    "docs/aso/en-US-2026-11.md",
    "docs/aso/keyword-tracker-2026-11.md",
    "docs/keyword-claims-audit-2026-11.md",
]
BOARD_CITE = re.compile(r"keyword-growth-task-board-2026-11-01\.md:(\d+)")
BOARD_LINES = TEXT.splitlines()


def _line_owner(n: int) -> tuple[str, str]:
    """台账第 n 行落在第几节、属于哪个 KG 的行或详情小节（空串＝不属于任何任务）。"""
    assert 1 <= n <= len(BOARD_LINES), f"台账只有 {len(BOARD_LINES)} 行，被引用到 :{n}"
    section = kg = ""
    for line in BOARD_LINES[:n]:
        m = re.match(r"^## (\d)\.", line)
        if m:
            section, kg = m.group(1), ""
        d = re.match(r"^#### (KG-\d{2}) ·", line)
        if d:
            kg = d.group(1)
        r = re.match(r"^\| (KG-\d{2}) \|", line)
        if r:
            kg = r.group(1)
    return section, kg


def test_downstream_docs_quote_the_live_board_lines() -> None:
    """下游文档里每一处 `task-board-...:NNN` 必须同时满足两条：打到结构性真行，且同句带一个**逐字内容锚**落在那一行。

    内容锚是第七批补的。此前这条只判「那一行非空＋属同一节／同一任务」，于是台账 §2 插两行说明、
    §KG-04 小节里的 `:228` 漂成 `:226`（那行是「按 Apple 限制产出并校验」）照样全绿——**证人只挡住了
    漂进空行或别的小节，没挡住同小节内漂两格**。行号本来就是抄录，抄录要能自证就得把被抄的原文钉在旁边。
    """
    checked = 0
    for rel in DOWNSTREAM:
        doc = (REPO / rel).read_text(encoding="utf-8")
        for ln, line in enumerate(doc.splitlines(), start=1):
            for n in BOARD_CITE.findall(line):
                i = int(n)
                section, kg = _line_owner(i)
                assert BOARD_LINES[i - 1].strip(), f"{rel}:{ln} 引台账 :{i}，那一行是空的 ⇒ 行号漂了"
                assert section, f"{rel}:{ln} 引台账 :{i}，落在任何节之前 ⇒ §0 之前的前言不许当依据"
                ids = set(re.findall(r"KG-\d{2}", line))
                if ids:
                    assert kg in ids, (
                        f"{rel}:{ln} 引台账 :{i}，该行属 {kg or '非任务行'}，"
                        f"但这句话只提了 {sorted(ids)} ⇒ 引用的不是它说的那件事"
                    )
                secs = set(re.findall(r"§(\d)(?!\d)", line))
                if secs:
                    assert section in secs, (
                        f"{rel}:{ln} 说「§{sorted(secs)}」却引到台账第 {i} 行，该行在 §{section} ⇒ 节号漂了"
                    )
                anchors = [a for a in re.findall(r"`([^`]+)`", line)
                           if "keyword-growth-task-board" not in a and "/" not in a]
                assert anchors, f"{rel}:{ln} 引台账 :{i} 却不含逐字内容锚 ⇒ 行号只能靠人记，台账一插行就漂"
                assert any(a in BOARD_LINES[i - 1] for a in anchors), (
                    f"{rel}:{ln} 引台账 :{i}，那一行逐字是 {BOARD_LINES[i - 1].strip()[:48]!r}，"
                    f"不含本句点名的锚 {anchors} ⇒ 同小节内行号漂了"
                )
                checked += 1
    assert checked >= 3, f"只核到 {checked} 处下游引用，清单或引用面漂了（正向证人）"

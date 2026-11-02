"""KG-19：关键词渠道七格的文档闸。

这张表是**给增长读数用的第二张表**，刻意与 `docs/metrics-dictionary.md` 的第一张表
（核心循环七格）分开：`test_metrics_export.py:67-82` 从第一个 `| 机器码 |` 读到第一行
非表格就停，所以新增表不碰那条同源闸；本文件只认 `| 关键词码 |`。

五组判据，每条都能被打红一次（见 `scripts/mutate_kg19_kg20_gates.py`）：

1. **定位不串台**：`| 关键词码 |` 全文恰好出现一次，且在 `| 机器码 |` 之后。
2. **行与码写死**：恰好 7 行，机器码顺序与 `CODES` 逐项相等（不是集合比对，顺序也是口径）。
3. **九格不许空**：任一格落在占位符词表（`—` / `TBD` / `待补` …）即红，空字符串同红；
   「定义/来源/分群/查询入口/数据质量检查」必须写成一句口径（≥10 字符），
   「窗口」必须真的含一段时间（`7d` / `±7d` / `周` / `月`），短标签列只验非空。
4. **后四格必须指回真表**：`first_value` 指 `growth_records`，`first_evidence_insight`
   指 `insights` + `insight_evidence`，`first_action` 指 `personal_commitments`，
   `due_outcome` 指 `outcomes`。这四格不许另造一套口径。
5. **引用的行号必须真打到定义**：表里写 `metrics_export.py:69-78` 就必须让 69–78 这个区间
   真的含有那个 `key="…"` 行——行号写歪了不算证据。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "metrics-dictionary.md"
METRICS_PY = REPO / "backend" / "services" / "metrics_export.py"

HEADERS = [
    "关键词码", "中文", "定义", "来源", "窗口", "分群", "负责人", "查询入口", "数据质量检查",
]

CODES = [
    "search_reach",
    "store_page_view",
    "install",
    "first_value",
    "first_evidence_insight",
    "first_action",
    "due_outcome",
]

PLACEHOLDERS = {
    "", "-", "—", "–", "?", "？", "tbd", "todo", "n/a", "na", "待补", "待定", "暂无", "略",
}

# 后四格 = 服务端权威表；带反引号的表名必须出现在「来源」或「查询入口」里。
# 用反引号起头是为了防「GET /api/v1/insights」这种端点串冒充表名过关。
SERVER_TABLES = {
    "first_value": ["`growth_records"],
    "first_evidence_insight": ["`insights`", "`insight_evidence`"],
    "first_action": ["`personal_commitments"],
    "due_outcome": ["`outcomes`"],
}

# 前三格 = 只能人工从控制台导出；不许写成仓库里已有的自动接入。
MANUAL_SOURCES = ("Console", "控制台", "GSC", "站长", "导出")

# 这几列必须写成一句口径，不许一个词糊过去；中文/负责人/窗口走各自的判据。
SUBSTANTIVE_COLUMNS = ("定义", "来源", "分群", "查询入口", "数据质量检查")

# 「窗口」这一列短，但必须真的写出一段时间：周/月/24h/7d/±7d 至少要有一个。
WINDOW_PATTERN = re.compile(r"(\d+\s*(?:h|d|小时|天|周))|(?:周|月)")


def _tables(text: str) -> list[list[dict[str, str]]]:
    """按表头把 markdown 表格切出来，键为表头列名。"""
    tables: list[list[dict[str, str]]] = []
    headers: list[str] | None = None
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if headers is None:
                headers = cells
                rows = []
                continue
            if set("".join(cells)) <= set("-: "):
                continue
            rows.append(dict(zip(headers, cells)))
        else:
            if headers is not None and rows:
                tables.append(rows)
            headers = None
            rows = []
    if headers is not None and rows:
        tables.append(rows)
    return tables


def _keyword_table() -> list[dict[str, str]]:
    text = DOC.read_text(encoding="utf-8")
    hits = [i for i, line in enumerate(text.splitlines()) if line.startswith("| 关键词码 |")]
    assert len(hits) == 1, f"「| 关键词码 |」表头出现 {len(hits)} 次，应恰好 1 次"
    for table in _tables(text):
        if list(table[0].keys())[0] == "关键词码":
            return table
    raise AssertionError("定位到表头却没解析出行")


def _step_def_line(key: str) -> int:
    """`metrics_export.py` 里那个 `key="…"` 所在的行号（1 起）。"""
    for n, line in enumerate(METRICS_PY.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip() == f'key="{key}",':
            return n
    raise AssertionError(f"metrics_export.py 里找不到 key=\"{key}\"")


def test_locator_does_not_cross_tables() -> None:
    text = DOC.read_text(encoding="utf-8")
    core = text.index("| 机器码 |")
    keyword = text.index("| 关键词码 |")
    assert core < keyword, "关键词表排在核心表前面会把 test_metrics_export 的定位抢走"
    assert len(_keyword_table()) == 7


def test_seven_rows_in_fixed_order() -> None:
    rows = _keyword_table()
    codes = [row["关键词码"].strip("`") for row in rows]
    assert codes == CODES, f"机器码或顺序漂了：{codes}"


def test_headers_are_the_nine_columns() -> None:
    rows = _keyword_table()
    text = DOC.read_text(encoding="utf-8")
    header_line = next(
        line for line in text.splitlines() if line.startswith("| 关键词码 |")
    )
    got = [c.strip() for c in header_line.strip().strip("|").split("|")]
    assert got == HEADERS, f"列名或列序漂了：{got}"
    assert len(got) == 9
    assert rows  # 正向证人：确有 7 行按这九列解析出来


def test_no_cell_is_a_placeholder() -> None:
    for row in _keyword_table():
        code = row["关键词码"].strip("`")
        assert len(row) == 9, f"{code} 只解析出 {len(row)} 列"
        for header in HEADERS:
            value = row[header]
            assert value.strip().lower() not in PLACEHOLDERS, f"{code} 的「{header}」是占位符：{value!r}"
            assert value.strip(), f"{code} 的「{header}」是空的"
        for header in SUBSTANTIVE_COLUMNS:
            assert len(row[header]) >= 10, f"{code} 的「{header}」短得不像口径：{row[header]!r}"
        assert WINDOW_PATTERN.search(row["窗口"]), (
            f"{code} 的「窗口」没写出一段时间：{row['窗口']!r}"
        )


def test_last_four_cells_point_at_existing_server_tables() -> None:
    rows = {row["关键词码"].strip("`"): row for row in _keyword_table()}
    for code, needles in SERVER_TABLES.items():
        cell = rows[code]["来源"] + " " + rows[code]["查询入口"]
        for needle in needles:
            assert needle in cell, f"{code} 没把口径指回 {needle}：{cell}"


def test_cited_line_ranges_reach_the_real_definition() -> None:
    """文档里引用的 metrics_export.py 行号区间必须真的含那个 key 的行。"""
    rows = {row["关键词码"].strip("`"): row for row in _keyword_table()}
    cited = {
        "first_evidence_insight": "first_insight_with_evidence",
        "first_action": "first_action",
        "due_outcome": "first_outcome",
    }
    for code, step_key in cited.items():
        cell = rows[code]["查询入口"]
        ranges = re.findall(r"metrics_export\.py(?::|\s*L)(\d+)-(\d+)", cell)
        assert ranges, f"{code} 没引用 metrics_export.py 的行号区间，同源判据无法核验"
        target = _step_def_line(step_key)
        inside = [start <= target <= end for start, end in ((int(a), int(b)) for a, b in ranges)]
        assert any(inside), (
            f"{code} 引用了 {ranges}，但 {step_key} 的定义实际在第 {target} 行"
        )


def test_first_three_cells_admit_manual_console_source() -> None:
    rows = {row["关键词码"].strip("`"): row for row in _keyword_table()}
    for code in ("search_reach", "store_page_view", "install"):
        cell = rows[code]["查询入口"]
        assert any(word in cell for word in MANUAL_SOURCES), (
            f"{code} 的查询入口没写明是控制台人工导出，容易读成仓库里已有自动接入：{cell}"
        )


def test_uncovered_cells_declare_zero_traffic_or_missing_integration() -> None:
    """曝光与洞察两格必须自己承认数据还没接入／生产流量为零，不许留成既成事实。"""
    rows = {row["关键词码"].strip("`"): row for row in _keyword_table()}
    assert "BLOCKED" in rows["search_reach"]["查询入口"], "search_reach 没标 KG-21 仍未接入"
    assert "no_users" in rows["first_evidence_insight"]["数据质量检查"], (
        "first_evidence_insight 没写「零流量按 no_users 报」，容易被读成转化差"
    )

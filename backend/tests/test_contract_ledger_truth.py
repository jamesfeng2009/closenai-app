"""T0-04 契约台账真实性闸（G-01）。

被验对象：`contracts/endpoints.md` 第五节「前端调用」列的每一处 `api.ts:N` 引用。
判据（整改方案 §7.2 G-01：台账说接了线就必须真接了线）：
① 行号 N 必须落在某个 `export function` 体内——指向空行/注释/函数外的引用即失真；
② 不带「封装未接线」标记的引用，其封装函数必须有 ≥1 个屏/hook 调用点；
③ 台账必须与当前 `export_contract.py` 的对账结果逐字一致（行号漂移即红，
    这正是 2026-10-18 发现的两类失真：跨行模板串漏扫、重导出后未同步）。

变异自证：把 PaywallScreen 里 `getLifeArchivePdf(` 改成别的标识符 ⇒ ② 打红
（该端点从「已接线」变「封装未接线」）。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENDPOINTS_MD = ROOT / "contracts" / "endpoints.md"
API_TS = ROOT / "frontend" / "src" / "services" / "api.ts"

ROW_RE = re.compile(r"^\| (\d+) \| \w+ \| `[^`]+` \| [^|]+ \| \w+ \| ([^|]+) \|")
CALL_REF_RE = re.compile(r"frontend/src/services/api\.ts:(\d+)(（封装未接线）)?")
EXPORT_FN_RE = re.compile(r"^export (?:async )?function (\w+)")


def ledger_refs() -> list[tuple[int, int, bool]]:
    """返回台账「前端调用」列里的 (行号, api.ts 引用行, 是否标记未接线)。"""
    out = []
    for line in ENDPOINTS_MD.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if not m:
            continue
        for ref in CALL_REF_RE.finditer(m.group(2)):
            out.append((int(m.group(1)), int(ref.group(1)), ref.group(2) is not None))
    return out


def export_function_at(lines: list[str], lineno: int) -> str:
    """行号（1 起）所在/之前的最近 `export function`；行首缩进说明仍在函数体内。"""
    for j in range(lineno - 1, -1, -1):
        m = EXPORT_FN_RE.match(lines[j])
        if m:
            return m.group(1)
    return ""


def test_every_api_ts_ref_lands_in_export_function():
    lines = API_TS.read_text(encoding="utf-8").splitlines()
    refs = ledger_refs()
    assert refs, "台账没扫到任何 api.ts 引用——扫描器或表格格式坏了"
    bad = [
        (row, n, fn)
        for row, n, _ in refs
        for fn in [export_function_at(lines, n)]
        if not fn or not (0 < n <= len(lines))
    ]
    assert not bad, f"台账引用不落在任何 export function 内：{bad}"


def test_wired_refs_have_real_screen_or_hook_callers():
    from scripts.export_contract import frontend_calls, wrapper_is_used

    used_map = wrapper_is_used(frontend_calls())
    lines = API_TS.read_text(encoding="utf-8").splitlines()
    offenders = []
    for row, n, unwired_flagged in ledger_refs():
        if unwired_flagged:
            continue
        name = export_function_at(lines, n)
        ref = f"frontend/src/services/api.ts:{n}"
        if not used_map.get(ref, True):
            offenders.append((row, name, ref))
    assert not offenders, (
        f"台账标「已接线」但封装无屏/hook 调用点（应重导或标记未接线）：{offenders}"
    )


def test_ledger_matches_fresh_scan():
    """台账前端列必须与当前源码的扫描结果同源（漂移即红）。"""
    from scripts.export_contract import (
        collect_endpoints,
        match_frontend,
    )

    rows = sorted(collect_endpoints(), key=lambda x: (x["path"], x["method"]))
    by_register, _unmatched = match_frontend(rows)
    expected = {
        i: sorted(refs)
        for i, refs in by_register.items()
    }
    actual_by_row: dict[int, list[str]] = {}
    for line in ENDPOINTS_MD.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if not m:
            continue
        refs = [
            f"{r.group(0)}"
            for r in CALL_REF_RE.finditer(m.group(2))
        ]
        if refs:
            actual_by_row[int(m.group(1))] = sorted(refs)
    for row, refs in expected.items():
        assert row in actual_by_row, f"端点 #{row} 台账记为无前端调用，当前扫描却有 {refs}"
        for ref in refs:
            assert any(r.startswith(ref.split("（")[0]) for r in actual_by_row[row]), (
                f"端点 #{row} 台账引用 {ref} 与当前扫描 {refs} 不一致（行号漂移）"
            )
    for row in actual_by_row:
        assert row in expected, f"端点 #{row} 台账有引用但当前扫描为空"

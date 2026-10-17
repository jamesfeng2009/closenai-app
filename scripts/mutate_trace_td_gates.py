#!/usr/bin/env python3
"""批 T-D（前端每请求发 trace id）的变异自证（2026-10-17）。

T-D 的判据全是行为证人（真 import api.ts 走 fetch/XHR），所以这里的靶也全是行为级：
删头、断回执读取、松内存形状闸——每一种都不会让 TS 编译失败，只有 jest 看得见。

纪律同 mutate_trace_tc_gates.py：串行、copy2 备份写回＋filecmp、不用 git checkout、
先跑基线、变异体必须先过语法自检（TS 用 tsc 单文件判太慢，改走 ts.transpileModule 只判语法）。

用法：
    ./.venv/bin/python scripts/mutate_trace_td_gates.py --check
    ./.venv/bin/python scripts/mutate_trace_td_gates.py
    ./.venv/bin/python scripts/mutate_trace_td_gates.py --only D1
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FRONTEND = REPO / "frontend"
NODE_BIN = "node"  # npx/node 在 PATH 上（jest 走 node_modules/.bin）

API_TS = FRONTEND / "src" / "services" / "api.ts"
MONITORING_TS = FRONTEND / "src" / "services" / "monitoring.ts"
TRACEID_TS = FRONTEND / "src" / "utils" / "traceId.ts"
GATE = "__tests__/traceHeaderWiring.test.ts"

MUTATIONS: list[tuple] = [
    (
        "D1",
        "M-D1 本体：把 getHeaders 里 `'X-Trace-Id': traceId,` 整行删掉（traceId 仍被 noteLastTrace 用，"
        "编译无感）。fetch 面与 XHR 面两条行为证人必须同刻红——这证的是「头真的在每次请求上」",
        [(API_TS, "    'X-Trace-Id': traceId,\n", "")],
        "两次调用各带一个合规且互不相同的 X-Trace-Id",
    ),
    (
        "D2",
        "断掉「服务端采用值」这一环：responseTrace 恒回 undefined（我们照发头，但回执/ApiError/"
        "最近一次三个读法全瞎）。报障话术与崩溃关联就退回到「本地造的值」，"
        "而那可能在边缘层被换掉——D2 红的正是同源这件事",
        [(API_TS, "    return res.headers.get('X-Trace-Id') ?? undefined;",
          "    return undefined;")],
        "失败路径 ApiError 附带服务端采用的 traceId",
    ),
    (
        "D3",
        "monitoring 的内存形状闸松成「非空就收」：含 CR/LF 的候选值进内存后，"
        "下一次 reportError 会把它拼进 Sentry tag——前端侧的 T-8。这条红证明闸在不在是判据",
        [(MONITORING_TS, "  if (traceId && TRACE_SHAPE.test(traceId)) lastTrace = traceId;",
          "  if (traceId) lastTrace = traceId;")],
        "只收合形值：含 CR/LF 或超长的候选连内存都不进",
    ),
]

GREEN: list[tuple] = [
    (
        "GR1",
        "在 getHeaders 里加一行注释：行为闸判的是头与值，不是「api.ts 没被动过」。"
        "这条红 ⇒ 闸已漂成禁改面",
        [(API_TS, "  const traceId = newTraceId();",
          "  // 变异证人：加注释不该弄红任何闸。\n  const traceId = newTraceId();", 1)],
        "",
    ),
]

TS_SYNTAX_CHECK = """
const ts = require('typescript');
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const out = ts.transpileModule(src, { reportDiagnostics: true });
const d = (out.diagnostics || []).filter(x => x.category === 1);
if (d.length) { console.error(d.map(x => ts.flattenDiagnosticMessageText(x.messageText, ' ')).join('\\n')); process.exit(1); }
"""


def _apply(edits: list) -> None:
    for edit in edits:
        path, old, new = edit[0], edit[1], edit[2]
        want = edit[3] if len(edit) > 3 else 1
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        assert n == want, f"{path.name}：锚点命中 {n} 次，要求 {want} 次\n{old[:90]!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _verify_anchor(edits: list) -> str | None:
    for edit in edits:
        path, old = edit[0], edit[1]
        want = edit[3] if len(edit) > 3 else 1
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != want:
            return f"命中 {n} 次（要 {want}）"
    return None


def _sanity(edits: list) -> str | None:
    for path in {e[0] for e in edits}:
        proc = subprocess.run([NODE_BIN, "-e", TS_SYNTAX_CHECK, str(path)],
                              cwd=FRONTEND, capture_output=True, text=True)
        if proc.returncode != 0:
            return f"{path.name}：变异体 TS 语法自检失败\n{proc.stderr[:300]}"
    return None


def run_jest() -> tuple[int, str]:
    proc = subprocess.run(["npx", "jest", GATE, "--silent"], cwd=FRONTEND,
                          capture_output=True, text=True, timeout=600)
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    cases = [*MUTATIONS, *GREEN]
    if args.check:
        bad = 0
        for case in cases:
            problem = _verify_anchor(case[2])
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中要求次数' if bad == 0 else f'{bad} 条有问题'}（共 {len(cases)} 条）")
        return 1 if bad else 0

    base_code, base_out = run_jest()
    if base_code != 0:
        print(f"基线不绿，未改动任何文件：\n{base_out[-1500:]}")
        return 2

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-trace-td-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            rel = path.relative_to(REPO).as_posix().replace("/", "+")
            bak = Path(tmp) / f"{idx:02d}-{rel}"
            shutil.copy2(path, bak)
            originals[path] = bak

        def restore() -> None:
            for path, bak in originals.items():
                shutil.copy2(bak, path)
                assert filecmp.cmp(bak, path, shallow=False), f"{path} 恢复后字节不一致"

        red_ok = red_fail = green_ok = green_fail = 0
        try:
            for cid, desc, edits, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(edits)
                try:
                    broken = _sanity(edits)
                    code, out = (-1, broken) if broken else run_jest()
                finally:
                    restore()
                if code == -1 or code == 0 or marker not in out:
                    red_fail += 1
                    print(f"[{cid}] 打红 ✗（exit={code}，名片段 {marker!r} 命中={marker in out if code > 0 else 'n/a'}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-15:]))
                else:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")

            for cid, desc, edits, _m in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(edits)
                try:
                    code, out = run_jest()
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    print(f"[{cid}] 保持绿 ✗（exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-15:]))
        except Exception as exc:  # noqa: BLE001
            restore()
            print(f"异常中止，已恢复：{exc!r}")
            return 2

        code, out = run_jest()
        print(f"复核 {GATE}：{'绿' if code == 0 else '红（残留！）'}")
        if code != 0:
            print("  " + "\n  ".join(out.splitlines()[-15:]))
        print(f"\n批 T-D 变异自证：打红 {red_ok}/{len(MUTATIONS)}，保持绿 {green_ok}/{len(GREEN)}，"
              f"失败 {red_fail + green_fail}，残留 {0 if code == 0 else 1}")
        return 1 if (red_fail or green_fail or code != 0) else 0


if __name__ == "__main__":
    sys.exit(main())

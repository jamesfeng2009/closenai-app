#!/usr/bin/env python3
"""批 T-C（trace 全链路·Go 日志面＋两侧消毒）的变异自证（2026-10-17）。

本批判据的形状是「日志行里有没有那个字段」——它不会让任何功能坏掉，
只会让排障时 grep 不到。所以这些靶证的不是「代码能动」，而是：
摘掉注入 ⇒ G-C1 当场红；塞回 log.Printf ⇒ G-C2 当场红；
消毒退成照单全收 ⇒ 两语的消毒闸各当场红。

纪律（[[feedback-mutation-gates-no-concurrency]]／
[[feedback-mutation-restore-no-git-checkout]]／[[feedback-definition-of-done-tests]]）：
**串行跑**、copy2 备份写回＋filecmp 逐字节复核、不用 `git checkout` 恢复；
每条先跑基线，基线不绿记「未跑」不算打红；变异体自身必须先过语法自检。

用法（Go 工具链走 GOROOT，本机 PATH 上的 go 是混合安装）：
    ./.venv/bin/python scripts/mutate_trace_tc_gates.py --check
    GOROOT=/Users/fengyu/sdk/go1.27.1 \\
    ./.venv/bin/python scripts/mutate_trace_tc_gates.py
    ./.venv/bin/python scripts/mutate_trace_tc_gates.py --only C1,C2
"""
from __future__ import annotations

import argparse
import ast
import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GO_CORE = REPO / "go-core"

GOROOT = "/Users/fengyu/sdk/go1.27.1"
GO_BIN = f"{GOROOT}/bin/go"
GO_ENV = {
    **os.environ,
    "GOROOT": GOROOT,
    "PATH": f"{GOROOT}/bin:{os.environ.get('PATH', '')}",
    "GO_BIN": os.environ.get("GO_BIN", GO_BIN),
}
VENV_PY = REPO / ".venv" / "bin" / "python"

OBS_GO = GO_CORE / "internal" / "obs" / "obs.go"
MW_GO = GO_CORE / "internal" / "httpapi" / "middleware.go"
MAIN_GO = GO_CORE / "cmd" / "go-api" / "main.go"
MAIN_TEST_GO = GO_CORE / "cmd" / "go-api" / "main_test.go"
MW_PY = REPO / "backend" / "core" / "middleware.py"

OBS_PKG = "./internal/obs/"
HTTPAPI_PKG = "./internal/httpapi/"
CMD_PKG = "./cmd/go-api/"
SAN_PY = "backend/tests/test_trace_sanitize.py"

# ── 变异表 ────────────────────────────────────────────────────────────────
MUTATIONS: list[tuple] = [
    (
        "C1",
        "M-C1 本体：把 obs Handler 的 trace 注入摘掉（只留 user_id）。"
        "装配、格式、JSON 一切如常，只有 grep 不到了——这正是「摘掉注入」最真实的形态",
        [(OBS_GO,
          'r.AddAttrs(slog.String("trace_id", t), slog.String("user_id", u))',
          'r.AddAttrs(slog.String("user_id", u))')],
        "go", OBS_PKG, "^TestTraceFromCtxIsVerbatimInJSON$", "trace_id 字段必须逐字",
    ),
    (
        "C1b",
        "同一刀的第二格：摘掉 user_id 注入。这条与 C1 各红一次 ⇒ 两个字段不是同一句断言混过的",
        [(OBS_GO,
          'r.AddAttrs(slog.String("trace_id", t), slog.String("user_id", u))',
          'r.AddAttrs(slog.String("trace_id", t))')],
        "go", OBS_PKG, "^TestUserCarrierRoundtrip$", "user_id 字段必须逐字",
    ),
    (
        "C1c",
        "最隐蔽的一刀：WithAttrs 不再包 ctxHandler（`slog.With(...)` 派生的 logger 静默丢 trace，"
        "直接用 slog.InfoContext 的调用点全绿）。这条红的正是派生面",
        [(OBS_GO,
          "func (h ctxHandler) WithAttrs(a []slog.Attr) slog.Handler {\n"
          "\treturn ctxHandler{base: h.base.WithAttrs(a)}\n}",
          "func (h ctxHandler) WithAttrs(a []slog.Attr) slog.Handler {\n"
          "\treturn h.base.WithAttrs(a)\n}")],
        "go", OBS_PKG, "^TestWithAttrsKeepsInjection$", "必须仍注入 trace_id",
    ),
    (
        "C2",
        "M-C2：往 httpapi 的非测试源码塞回一条 log.Printf（连 import 一起，保证变异体可编译）。"
        "G-C2 归零闸必须当场抓到，同时正向证人不许跟着变红",
        [(MW_GO, '\t"log/slog"', '\t"log"\n\t"log/slog"'),
         (MW_GO, "\t\tsw := &statusWriter{",
          "\t\tlog.Printf(\"go-api: mutation witness\")\n\t\tsw := &statusWriter{")],
        "go", CMD_PKG, "^TestPrintfZeroedSlogWitnessed$", "不该再有 log.Printf",
    ),
    (
        "C3",
        "Go 消毒退成照单全收（sanitizeTraceID 恒回 raw）：形状表先红——"
        "CR/LF、超长、控制字符全放进来",
        [(MW_GO,
          "func sanitizeTraceID(raw string) string {\n"
          "\tif traceIDShape.MatchString(raw) {\n\t\treturn raw\n\t}\n\treturn \"\"\n}",
          "func sanitizeTraceID(raw string) string {\n\treturn raw\n}")],
        "go", HTTPAPI_PKG, "^TestSanitizeTraceIDShape$", "必须归零",
    ),
    (
        "C3b",
        "同一刀的功能证人：注入头一路穿透到日志行与回执。形状表红是「判据松了」，"
        "这一格红才是「现网会被伪造日志行」",
        [(MW_GO,
          "func sanitizeTraceID(raw string) string {\n"
          "\tif traceIDShape.MatchString(raw) {\n\t\treturn raw\n\t}\n\treturn \"\"\n}",
          "func sanitizeTraceID(raw string) string {\n\treturn raw\n}")],
        "go", HTTPAPI_PKG, "^TestMalformedHeaderIsRegeneratedNotEchoed$", "注入值进了日志行",
    ),
    (
        "C4",
        "摘掉 Wrap 里的 domain 载体注入（obs 那格还留着）：日志行照样有 trace_id，"
        "但 emit 写出的 outbox 行开始丢 trace——异步链断在没人盯的那一环。"
        "这条证的正是「ctx 双载体」不是装饰",
        [(MW_GO,
          '\t"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"\n', ""),
         (MW_GO,
          "\t\tctx = domain.WithTraceID(ctx, traceID)\n",
          "")],
        "go", HTTPAPI_PKG, "^TestDomainCarrierVisibleInHandler$", "载体应可见",
    ),
    (
        "C5",
        "Python 消毒退成照单全收（sanitize_trace_id 恒回 raw）：与 C3 的对面证人——"
        "两语各自红一次，「同一口径」才是真判据而不是抄写",
        [(MW_PY,
          "    if raw is not None and TRACE_SHAPE.fullmatch(raw):\n        return raw\n    return \"\"",
          "    return raw or \"\"")],
        "pytest", f"{SAN_PY}::test_malformed_values_are_zeroed", "", "== ''",
    ),
    (
        "C5b",
        "同一刀走真实中间件的功能证人：不合形头被原样采用并回显。红在「回执/日志/ctx 三点同源」"
        "那一格，证消毒在 dispatch 里真的被调用（不是只活在单测里）",
        [(MW_PY,
          "        trace_id = sanitize_trace_id(request.headers.get(TRACE_HEADER)) or str(uuid.uuid4())",
          "        trace_id = request.headers.get(TRACE_HEADER) or str(uuid.uuid4())")],
        "pytest", f"{SAN_PY}::test_injected_header_not_echoed_not_in_logs", "", "adopted != evil",
    ),
]

# GREEN：中性改动不许弄红任何闸（证闸判的是内容，不是「文件被改过」）。
GREEN: list[tuple] = [
    (
        "GR1",
        "在 main.go 的注释里多写一句 `slog.`：G-C2 的证人只判「少于基线」，"
        "加注释不该红。哪天这条变红，说明归零闸漂移成了「禁改面」",
        [(MAIN_GO, "func logBridge(",
          "// 变异证人：注释里提一句 slog. 不该弄红任何闸。\nfunc logBridge(", 1)],
        "go", CMD_PKG, "^TestPrintfZeroedSlogWitnessed$", "",
    ),
    (
        "GR2",
        "在 main_test.go（测试面）里写一条 log.Printf：G-C2 判的是**非测试**源码归零，"
        "排除层必须真在生效——去掉排除层的那天人会先撞红这条",
        [(MAIN_TEST_GO, "// blockingSession",
          "// 变异证人：log.Printf 在测试面不算实现痕迹。\n// blockingSession", 1)],
        "go", CMD_PKG, "^TestPrintfZeroedSlogWitnessed$", "",
    ),
]


def _apply(edits: list) -> None:
    for edit in edits:
        path, old, new = edit[0], edit[1], edit[2]
        want = edit[3] if len(edit) > 3 else 1
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        if want == "all":
            assert n >= 1, f"{path.name}：needle 0 命中"
            path.write_text(src.replace(old, new), encoding="utf-8")
        else:
            assert n == want, f"{path.name}：锚点命中 {n} 次，要求 {want} 次\n{old[:90]!r}"
            path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _sanity(edits: list) -> str | None:
    for edit in edits:
        path = edit[0]
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            try:
                ast.parse(text)
            except SyntaxError as exc:
                return f"{path.name}：变异体不是合法 Python（{exc.msg} @ line {exc.lineno}）"
        elif path.suffix == ".go":
            proc = subprocess.run([f"{GOROOT}/bin/gofmt", "-e", str(path)],
                                  capture_output=True, text=True)
            if proc.stderr.strip():
                return f"{path.name}：变异体 gofmt -e 报错\n{proc.stderr[:300]}"
    return None


def _verify_anchor(edits: list) -> str | None:
    for edit in edits:
        path, old = edit[0], edit[1]
        want = edit[3] if len(edit) > 3 else 1
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if want != "all" and n != want:
            return f"命中 {n} 次（要 {want}）"
    return None


def run(runner: str, target: str, node: str) -> tuple[int, str]:
    if runner == "go":
        cmd = [GO_BIN, "test", target, "-count=1", "-timeout", "300s"]
        if node:
            cmd += ["-run", node]
        cwd, timeout = GO_CORE, 900
    else:
        assert node == "", "pytest 的节点 id 就写在 target 里，node 留空"
        cmd = [str(VENV_PY), "-m", "pytest", target, "-q", "--no-header"]
        cwd, timeout = REPO, 900
    proc = subprocess.run(cmd, cwd=cwd, env=GO_ENV, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def is_red(code: int, out: str) -> bool:
    """Go 的红＝受害用例真的跑起来并断言失败；build failed／no tests to run 红的是工具链。"""
    if code == 0:
        return False
    if "[build failed]" in out or "no tests to run" in out or "no test files" in out:
        return False
    return "--- FAIL:" in out


def is_red_pytest(code: int, out: str, target: str) -> bool:
    if code == 0:
        return False
    if f"FAILED {target}" not in out:
        return False
    if "error" in out.splitlines()[-1].lower():
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
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
        print(f"锚点校验：{'全部命中要求次数' if bad == 0 else f'{bad} 条有问题'}"
              f"（共 {len(cases)} 条）")
        return 1 if bad else 0

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-trace-tc-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            rel = path.relative_to(REPO).as_posix().replace("/", "+")
            bak = Path(tmp) / f"{idx:02d}-{rel}"
            assert bak not in originals.values(), f"备份名碰撞：{bak}"
            shutil.copy2(path, bak)
            originals[path] = bak

        def restore() -> None:
            for path, bak in originals.items():
                shutil.copy2(bak, path)
                assert filecmp.cmp(bak, path, shallow=False), f"{path} 恢复后字节不一致"

        red_ok = red_fail = green_ok = green_fail = 0
        failures: list[str] = []
        try:
            for cid, desc, edits, runner, target, node, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = run(runner, target, node)
                if base_code != 0:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）：不能把环境的红记成闸的红\n  "
                          + "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                _apply(edits)
                try:
                    broken = _sanity(edits)
                    if broken is None:
                        code, out = run(runner, target, node)
                    else:
                        code, out = -1, broken
                finally:
                    restore()
                if code == -1:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（变异体自身不合法）：{out}")
                    continue
                hit = (is_red(code, out) if runner == "go"
                       else is_red_pytest(code, out, target))
                if hit and marker in out:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓（{runner}）  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（{runner} exit={code}，名片段 {marker!r} 命中={marker in out}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))

            for cid, desc, edits, runner, target, node, _marker in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(edits)
                try:
                    code, out = run(runner, target, node)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓（{runner}）  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（{runner} exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))
        except Exception as exc:  # noqa: BLE001
            restore()
            print(f"异常中止，已恢复：{exc!r}")
            return 2

        rechecks = [("go", OBS_PKG, ""), ("go", HTTPAPI_PKG, ""), ("go", CMD_PKG, ""),
                    ("pytest", SAN_PY, "")]
        residue = 0
        for runner, target, node in rechecks:
            code, out = run(runner, target, node)
            if code != 0:
                residue += 1
                print(f"残留（红！） {target}：\n  " + "\n  ".join(out.splitlines()[-15:]))
            else:
                print(f"复核 {target}：绿")
        total = len(MUTATIONS) + len(GREEN)
        print(f"\n批 T-C 变异自证：打红 {red_ok}/{len(MUTATIONS)}，保持绿 {green_ok}/{len(GREEN)}，"
              f"失败 {red_fail + green_fail}，残留 {residue}")
        if failures:
            print(f"失败清单：{failures}")
        return 1 if (red_fail or green_fail or residue) else 0


if __name__ == "__main__":
    sys.exit(main())

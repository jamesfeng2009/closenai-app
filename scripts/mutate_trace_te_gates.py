#!/usr/bin/env python3
"""批 T-E（trace 全链路·gRPC 面透传）的变异自证（2026-10-17）。

这一批判据的形状与 T-C 同族：透传断了**不会让任何功能坏掉**，只会让一条 trace
grep 不到整条链。所以靶证的不是「代码能动」，而是四件各自独立的事：
· 链上两个挂点（unary／stream）各摘各的，G-E1 各红一次——「挂了一个」不等于「都挂了」；
· 空值守卫摘掉 ⇒ carrier 单测红（空串占位的代价在对面才显形，这里先拦）；
· 服务端三形态缺任何一格 ⇒ G-E2 的形态证人红（M-E2 本体）；
· metadata／字段优先级颠倒、消毒退成照单全收 ⇒ 各自的判据红，而不是靠全量碰运气。

纪律（[[feedback-mutation-gates-no-concurrency]]／
[[feedback-mutation-restore-no-git-checkout]]／[[feedback-definition-of-done-tests]]）：
**串行跑**、copy2 备份写回＋filecmp 逐字节复核、不用 `git checkout` 恢复；
每条先跑基线，基线不绿记「未跑」不算打红；变异体自身必须先过语法自检。

用法（Go 工具链走 GOROOT，本机 PATH 上的 go 是混合安装）：
    ./.venv/bin/python scripts/mutate_trace_te_gates.py --check
    GOROOT=/Users/fengyu/sdk/go1.27.1 \\
    ./.venv/bin/python scripts/mutate_trace_te_gates.py
    ./.venv/bin/python scripts/mutate_trace_te_gates.py --only E2,E4
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

GRPC_GO = GO_CORE / "internal" / "aiclient" / "grpc.go"
TRACE_GO = GO_CORE / "internal" / "aiclient" / "trace.go"
SERVER_PY = REPO / "backend" / "ai" / "rpc" / "server.py"

AICLIENT_PKG = "./internal/aiclient/"
GE2 = "backend/tests/test_trace_grpc_rpc.py"

# 四条 trace 测的名字并集（E1／GR2 用整组跑，单点靶用锚定名单跑）
TRACE_ALL = ("^TestInjectTraceCarrierSemantics$|^TestMetadataKeyLiteral$"
             "|^TestUnaryCallsCarryTraceMetadata$|^TestStreamChatCarriesTraceMetadata$"
             "|^TestNoTraceMeansNoMetadata$")

UNARY_LINE = "\t\tgrpc.WithChainUnaryInterceptor(withTraceUnary),\n"
STREAM_LINE = "\t\tgrpc.WithChainStreamInterceptor(withTraceStream))"

# ── 变异表 ────────────────────────────────────────────────────────────────
MUTATIONS: list[tuple] = [
    (
        "E1",
        "M-E1 本体：newConn 的链上两个挂点一起摘（拦截器函数还在，只是没人挂）。"
        "编译、重试、熔断一切如常，只有对面读不到 x-trace-id",
        [(GRPC_GO, UNARY_LINE + STREAM_LINE, "\t)")],
        "go", AICLIENT_PKG, TRACE_ALL, "出站 metadata x-trace-id",
    ),
    (
        "E1a",
        "只摘 unary 挂点：流面必须保持绿，否则「三形态各判各的」这句话没被证过",
        [(GRPC_GO, UNARY_LINE, "")],
        "go", AICLIENT_PKG, "^TestUnaryCallsCarryTraceMetadata$", "Embed 出站 metadata",
    ),
    (
        "E1b",
        "只摘 stream 挂点（M-E1 的流面对称格）：红的必须且只有流面证人",
        [(GRPC_GO, UNARY_LINE + STREAM_LINE, UNARY_LINE + "\t)")],
        "go", AICLIENT_PKG, "^TestStreamChatCarriesTraceMetadata$", "StreamChat 出站 metadata",
    ),
    (
        "E5",
        "摘掉 injectTrace 的空值守卫：ctx 干净时开始带空串出站。单元直判，"
        "不取决于 grpc 版本对空值的容忍度（publisher.go 那条「缺席就是缺席」的取舍同款）",
        [(TRACE_GO,
          "\ttraceID := domain.TraceIDFrom(ctx)\n"
          "\tif traceID == \"\" {\n\t\treturn ctx\n\t}\n"
          "\treturn metadata.AppendToOutgoingContext(ctx, metadataTraceKey, traceID)",
          "\treturn metadata.AppendToOutgoingContext(ctx, metadataTraceKey, domain.TraceIDFrom(ctx))")],
        "go", AICLIENT_PKG, "^TestInjectTraceCarrierSemantics$", "空值不注入",
    ),
    (
        "E2",
        "M-E2 本体：服务端只在 Embed 应用 trace，ClassifySafety／StreamChat 的入口挂点摘掉。"
        "留痕与判定面照样能跑，只有这两跳的日志在 grep 里消失",
        [(SERVER_PY, "        _apply_trace(context)\n", "", "all")],
        "pytest", f"{GE2}::test_all_three_method_forms_adopt_metadata", "", "三形态缺任何一个",
    ),
    (
        "E3",
        "优先级颠倒：EmbedRequest.trace_id 抢过 metadata。字段可能被调用方漏填/填旧，"
        "拦截器搬运的值反而被丢——全链路于是「透传」成旧 id",
        [(SERVER_PY,
          "    if not raw and request is not None:\n        raw = getattr(request, \"trace_id\", \"\") or \"\"",
          "    if request is not None:\n        raw = getattr(request, \"trace_id\", \"\") or raw")],
        "pytest", f"{GE2}::test_metadata_wins_over_request_field", "", "字段抢过",
    ),
    (
        "E4",
        "消毒退成照单全收（fullmatch 判定被摘）：路径字符进 contextvar、进每一行日志前缀。"
        "T-C 在 HTTP 面立的规矩，这一侧单独红一次才算「两侧同守」",
        [(SERVER_PY, "    if TRACE_SHAPE.fullmatch(raw):", "    if True:")],
        "pytest", f"{GE2}::test_malformed_metadata_dropped_without_echoing_raw", "",
        "畸形 trace 被 adopt",
    ),
    (
        "E4b",
        "同一刀的第二证人：警示行不许回显原文。fullmatch 摘掉后畸形值被 adopt，"
        "红必须落在「丢弃＋不留原文」这条判据本身，而不是碰巧的断言错位",
        [(SERVER_PY, "    if TRACE_SHAPE.fullmatch(raw):", "    if True:")],
        "pytest", f"{GE2}::test_malformed_request_field_also_dropped", "", "== []",
    ),
]

# GREEN：中性改动不许弄红任何闸（证闸判的是内容，不是「文件被改过」）。
GREEN: list[tuple] = [
    (
        "GR1",
        "在 server.py 的 Embed 前加一句只提到 apply_trace 的注释：G-E2 判的是行为，"
        "不是文件指纹。哪天这条红，说明锚点漂成了「禁改面」",
        [(SERVER_PY, "    async def Embed(self, request, context):",
          "    # 变异证人：注释里提一句 apply_trace 不该弄红任何闸。\n"
          "    async def Embed(self, request, context):", 1)],
        "pytest", GE2, "", "",
    ),
    (
        "GR2",
        "在 grpc.go 注释里写 x-trace-id：同理，Go 侧的闸也不许判文件指纹",
        [(GRPC_GO, "// newConn 用明文。理由要说破：",
          "// 变异证人：注释里写 x-trace-id 不该弄红任何闸。\n// newConn 用明文。理由要说破：", 1)],
        "go", AICLIENT_PKG, TRACE_ALL, "",
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
    with tempfile.TemporaryDirectory(prefix="mutate-trace-te-") as tmp:
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

        rechecks = [("go", AICLIENT_PKG, TRACE_ALL), ("pytest", GE2, "")]
        residue = 0
        for runner, target, node in rechecks:
            code, out = run(runner, target, node)
            if code != 0:
                residue += 1
                print(f"残留（红！） {target}：\n  " + "\n  ".join(out.splitlines()[-15:]))
            else:
                print(f"复核 {target}：绿")
        total = len(MUTATIONS) + len(GREEN)
        print(f"\n批 T-E 变异自证：打红 {red_ok}/{len(MUTATIONS)}，保持绿 {green_ok}/{len(GREEN)}，"
              f"失败 {red_fail + green_fail}，残留 {residue}")
        if failures:
            print(f"失败清单：{failures}")
        return 1 if (red_fail or green_fail or residue) else 0


if __name__ == "__main__":
    sys.exit(main())

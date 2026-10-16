#!/usr/bin/env python3
"""R3（Python 侧 gRPC server + O0 边界 + 跨语言互操作 + 部署面）的变异自证（2026-10-16）。

靶点来自工作单 §三.7 的逐字要求：
  「变异自证 `scripts/mutate_r3_gates.py` 至少打红这四条：
    **超时被回成 OK（D-8）**／handler 里内嵌业务分支／畸形输入被重试／探针被改到 grpc 端口」
⇒ M1–M4 就是那四条，一条不打折。另加三条是本批自己交付却同样只有「坏了才知道」的闸：
M5/M6 是 O0 边界闸的两条禁令（工作单 §一.4 逐字「两条都要能用变异打红」），
M7 钉的是「gRPC 只在集群内，不许上 Ingress」（tasks.md:1105）——这条坏了等于把内部契约面变公网面。

闸的选择口径：**每条变异只喂它该打红的那一张闸**，且要求报错原文里出现名片段，
否则「红」可能是别的用例连带崩的假红。反过来 G1–G4 证明这些闸不是假红制造机：
加注释、调宽 grace period 都不该红（判据只看该看的东西）。

⚠️ M3 的受害闸是跨语言真进程测（起真 gRPC server + 真 `go run` 探针，单条约 20s），
所以本脚本整体要跑一两分钟；**严禁并发跑**（见 [[feedback-mutation-gates-no-concurrency]]），
也禁 `git checkout` 恢复，一律 copy2 + filecmp 逐字节校验
（见 [[feedback-mutation-restore-no-git-checkout]]）。

用法：
    ./.venv/bin/python scripts/mutate_r3_gates.py --check   # 只验锚点，不改文件
    ./.venv/bin/python scripts/mutate_r3_gates.py           # 全跑（7 红 + 4 绿）
    ./.venv/bin/python scripts/mutate_r3_gates.py --only M1,M4
"""
from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SAFETY_PY = REPO / "backend" / "ai" / "safety.py"
LLM_PY = REPO / "backend" / "ai" / "llm.py"
SERVER_PY = REPO / "backend" / "ai" / "rpc" / "server.py"
FAILURE_GO = REPO / "go-core" / "internal" / "aiclient" / "failure.go"
PY_API_YAML = REPO / "deploy" / "k8s" / "40-py-api.yaml"
AI_RPC_YAML = REPO / "deploy" / "k8s" / "50-ai-rpc.yaml"
INGRESS_YAML = REPO / "deploy" / "k8s" / "50-ingress.yaml"

TRI_GATE = "backend/tests/test_r3_safety_tri.py"
SHAPE_GATE = "backend/tests/test_r3_handler_shape.py"
BOUNDARY_GATE = "backend/tests/test_ai_boundary.py"
SURFACE_GATE = "backend/tests/test_r3_runtime_surface.py"
INTEROP_GATE = "backend/tests/test_r3_grpc_server_interop.py"

GOROOT = "/Users/fengyu/sdk/go1.27.1"
VENV_PY = str(REPO / ".venv/bin/python")
# M3 的受害闸要在真进程里跑 Go 探针；这台机器的 PATH 上是混合安装的 go
# （见 [[go-toolchain-mixed-install]]），所以点名 GOROOT 并把 GO_BIN 传给 pytest。
PY_ENV = {**os.environ, "GOROOT": GOROOT,
          "GO_BIN": f"{GOROOT}/bin/go",
          "PATH": f"{GOROOT}/bin:{os.environ.get('PATH', '')}"}

# ── 名片段（闸的报错原文片段；改文案要同时改这里，否则绿的是脚本、红的是真相）──
MARK_D8 = "超时被当成了放行（D-8 复活）"
MARK_BRANCH = "业务判断面被复制"
MARK_RETRY = "畸形被重投了"
MARK_PROBE = "不再是 httpGet"
MARK_LAYER = "越层 import"
MARK_DB = "出现直连符号"
MARK_INGRESS = "AI 面被对外暴露"

# 每条：(id, 说明, ("text", 文件, 原文, 坏文), 闸节点, 名片段)
MUTATIONS = [
    (
        "M1",
        "⚠️ §三.7 第①条「超时被回成 OK（D-8）」：把 `check_text_tri` 的异常面从 unavailable "
        "改成 ok。这一条坏掉**没有任何用户可见报错**，只会让该拦的内容发出去"
        "（Go 侧 gate.go:396 那条 reject 当场消失），所以它是本批不可协商的靶",
        ("text", SAFETY_PY,
         'logger.exception("safety(tri) 检测不可用，按 unavailable 回传: %.100s", text)\n'
         '            return _tri(SAFETY_UNAVAILABLE, "model", [], advice_blocked=False)',
         'logger.exception("safety(tri) 检测不可用，按 unavailable 回传: %.100s", text)\n'
         '            return _tri(SAFETY_OK, "", [], advice_blocked=False)'),
        f"{TRI_GATE}::test_timeout_is_unavailable_not_ok", MARK_D8,
    ),
    (
        "M2",
        "§三.7 第②条「handler 里内嵌业务分支」：在 ClassifySafety 里就地跑一份黑名单判定 ⇒ "
        "两侧各判一份，跨进程后没人知道哪份生效（R0 裁决要避免的正是这个）",
        ("text", SERVER_PY,
         '        scope = request.scope or "chat"',
         '        scope = request.scope or "chat"\n'
         "        if safety_service.banned_hit(request.text, request.language):\n"
         '            scope = "assertion"'),
        f"{SHAPE_GATE}::test_rpc_layer_has_zero_business_branches", MARK_BRANCH,
    ),
    (
        "M3",
        "§三.7 第③条「畸形输入被重试」：把 Go 侧 `malformed()` 的 Kind 从 Permanent 改成 "
        "Transient ⇒ 对面字节过不了 decode 校验时会被重投（V3 17 §8 的 Permanent 面失效），"
        "且一次字段误解还会计进熔断计数。这一条走的是**真 server 的真字节**，不是桩",
        ("text", FAILURE_GO,
         "Op: op, Code: codes.InvalidArgument, Kind: FailurePermanent,",
         "Op: op, Code: codes.InvalidArgument, Kind: FailureTransient,"),
        f"{INTEROP_GATE}::test_embed_count_mismatch_is_permanent_not_retried", MARK_RETRY,
    ),
    (
        "M4",
        "§三.7 第④条「探针被改到 grpc 端口」：把 py-api 的 readiness 换成 tcpSocket:50051 ⇒ "
        "改的不是端口而是**灰度语义**（/health 变红会让 Ingress 摘掉还在服务 88 个端点的 backend，"
        "而 50051 属于另一个进程）",
        ("text", PY_API_YAML,
         "          readinessProbe:\n            httpGet:\n              path: /health\n"
         "              port: http\n",
         "          readinessProbe:\n            tcpSocket:\n              port: 50051\n"),
        f"{SURFACE_GATE}::test_py_api_probes_stay_on_http_health", MARK_PROBE,
    ),
    (
        "M5",
        "O0 边界禁令①（工作单 §一.4）：往能力层文件里塞一条 `backend.models` 的越层 import ⇒ "
        "AI 能力层一旦碰模型，R3 的 server 会把数据库连带拖进 AI 进程。"
        "⚠️ 坏文必须是一条**真能 import 成功**的越层 import：第一版种的是 "
        "`from backend.models.schemas import ChatRequest`，那个名字不存在，于是红变成 conftest 的 "
        "ImportError 而不是断言失败——那正是 `is_red` 要挡的那类假红（名片段没命中，脚本判它未打红）",
        ("text", LLM_PY,
         "from backend.config import settings",
         "from backend.config import settings\nfrom backend.models.user import User"),
        f"{BOUNDARY_GATE}::test_no_forbidden_layer_import[llm.py]", MARK_LAYER,
    ),
    (
        "M6",
        "O0 边界禁令②（同上）：在能力层里出现 `SessionLocal` 直连符号 ⇒ "
        "「合法出口只有 candidate 回投」这条纪律的 AST 面",
        ("text", SAFETY_PY,
         'SAFETY_OK = "ok"\nSAFETY_CRISIS = "crisis"',
         "def _probe_session():\n    return SessionLocal()\n\n\n"
         'SAFETY_OK = "ok"\nSAFETY_CRISIS = "crisis"'),
        f"{BOUNDARY_GATE}::test_no_direct_db_or_http_symbol[safety.py]", MARK_DB,
    ),
    (
        "M7",
        "gRPC 面被挂上 Ingress：把 /api/v1 的 backend 改成 ai-rpc ⇒ 本批 server 没有任何鉴权"
        "（安全边界靠集群内网络，mTLS 归 R7），对外暴露它等于把内部契约面变成公网攻击面",
        ("text", INGRESS_YAML,
         "          - path: /api/v1\n            pathType: Prefix\n            backend:\n"
         "              service:\n                name: py-api",
         "          - path: /api/v1\n            pathType: Prefix\n            backend:\n"
         "              service:\n                name: ai-rpc"),
        f"{SURFACE_GATE}::test_ai_rpc_is_not_exposed_through_ingress", MARK_INGRESS,
    ),
]

# 绿闸：这些改动**不该**红。判据只认 AST 节点与真配置，注释密度与合法余量都不算违规。
GREEN = [
    (
        "G1",
        "给 rpc/server.py 加 12 行中文注释（不含任何业务标识符）：形状闸判的是 AST 里的名字，"
        "不是文本命中——否则「注释里提到 banned_hit」也会红，那种闸会被第一行文档杀掉",
        ("text", SERVER_PY,
         "    async def ClassifySafety(self, request, context):  # noqa: N802",
         "".join(f"    # 形状闸绿闸占位注释 {i}\n" for i in range(12))
         + "    async def ClassifySafety(self, request, context):  # noqa: N802"),
        SHAPE_GATE,
    ),
    (
        "G2",
        "给 backend/ai/llm.py 加 10 行注释，其中**一行原文写着**「from backend.models.user import User」"
        "外加「SessionLocal」字样：边界闸走 AST，注释与 docstring 里的同名字符串不许误报"
        "（本包 __init__.py 的 docstring 今天就写着 SessionLocal，是同一个活的反例）",
        ("text", LLM_PY,
         "from backend.config import settings",
         "".join(f"# 边界闸绿闸占位注释 {i}\n" for i in range(9))
         + "# 反例说明：from backend.models.user import User 与 SessionLocal 都只是文字\n"
         + "from backend.config import settings"),
        BOUNDARY_GATE,
    ),
    (
        "G3",
        "把 ai-rpc 的 terminationGracePeriodSeconds 从 30 调到 40（仍 > 进程要求的 3s）："
        "部署面闸判的是「grace 覆盖得住收尾」，不是钉死那个数字",
        ("text", AI_RPC_YAML, "terminationGracePeriodSeconds: 30",
         "terminationGracePeriodSeconds: 40"),
        SURFACE_GATE,
    ),
    (
        "G4",
        "给 `check_text_tri` 的日志文案改一个字（「回传」→「返回」）：三态闸判的是 status 取值，"
        "不判日志措辞——否则这条闸会因为改文案而红，而红过一次就会被当成噪音关掉",
        ("text", SAFETY_PY,
         'logger.exception("safety(tri) 检测不可用，按 unavailable 回传: %.100s", text)',
         'logger.exception("safety(tri) 检测不可用，按 unavailable 返回: %.100s", text)'),
        TRI_GATE,
    ),
]


def _apply(mutation: tuple) -> None:
    _kind, path, old, new = mutation
    src = path.read_text(encoding="utf-8")
    n = src.count(old)
    assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:90]!r}"
    path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _verify_anchor(mutation: tuple) -> str | None:
    _kind, path, old, _new = mutation
    if not path.exists():
        return "文件不存在"
    n = path.read_text(encoding="utf-8").count(old)
    return None if n == 1 else f"命中 {n} 次"


def run(target: str) -> tuple[int, str]:
    """只有一种闸＝pytest 节点（M3 那一条的「对面」是 go run 探针，由测试自己拉起）。"""
    proc = subprocess.run(
        [VENV_PY, "-m", "pytest", target, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=REPO, env=PY_ENV, capture_output=True, text=True, timeout=1800,
    )
    return proc.returncode, proc.stdout + proc.stderr


def is_red(code: int, out: str) -> bool:
    """「红」必须是**受害测试真的跑起来并断言失败**的红。

    口径升级沿 N2/P6 定下的那条：收集失败、`no tests ran`、编译崩溃都**不算打红**——
    那些情况下红的是工具链，不是闸。
    """
    if code == 0:
        return False
    if "no tests ran" in out or "internal error" in out:
        return False
    if "error: " in out and "collected 0 items" in out:
        return False
    return bool(out.count("failed") or out.count("error"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    if args.check:
        bad = 0
        for case in [*MUTATIONS, *GREEN]:
            problem = _verify_anchor(case[2])
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}"
              f"（共 {len(MUTATIONS) + len(GREEN)} 条）")
        return 1 if bad else 0

    paths = sorted({case[2][1] for case in [*MUTATIONS, *GREEN]})
    with tempfile.TemporaryDirectory(prefix="mutate-r3-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            # 备份名带序号而不是只用文件名：本批同时打 backend/ai/safety.py 与
            # backend/services/safety_service.py 这类同名异目录文件时，只用 name 会在 tmp 里
            # 互相覆盖，于是「恢复」把 A 的内容写回 B，而 filecmp 比的是同一份被覆盖后的备份
            # ——留残渣还不报错就是这一种错法。
            bak = Path(tmp) / f"{idx:02d}-{path.parent.name}-{path.name}"
            shutil.copy2(path, bak)
            originals[path] = bak

        def restore() -> None:
            for path, bak in originals.items():
                shutil.copy2(bak, path)
                assert filecmp.cmp(bak, path, shallow=False), f"{path} 恢复后字节不一致"

        red_ok = red_fail = green_ok = green_fail = 0
        failures: list[str] = []
        try:
            for cid, desc, mutation, node, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(mutation)
                try:
                    code, out = run(node)
                finally:
                    restore()
                if is_red(code, out) and marker in out:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（exit={code}，名片段 {marker!r} 命中={marker in out}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-16:]))

            for cid, desc, mutation, gate in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                _apply(mutation)
                try:
                    code, out = run(gate)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-16:]))
        except Exception as exc:  # noqa: BLE001
            restore()
            print(f"异常中止，已恢复：{exc!r}")
            return 2

        # 收尾自证：四张便宜的闸文件回到绿 ＋ Go 的 aiclient 包回到绿（M3 动过 Go 文件，
        # 残渣只有那边能验出来）。互操作那份 21s 的不在这里重跑，它由全量回归覆盖。
        for gate in (TRI_GATE, SHAPE_GATE, BOUNDARY_GATE, SURFACE_GATE):
            code, out = run(gate)
            print(f"复核 {gate.split('/')[-1]}：{'绿' if code == 0 else '红（残留！）'}")
            if code != 0:
                print("  " + "\n  ".join(out.splitlines()[-12:]))
                return 1
        proc = subprocess.run(
            ["go", "test", "./internal/aiclient/", "-count=1"],
            cwd=REPO / "go-core", env=PY_ENV, capture_output=True, text=True, timeout=1800,
        )
        print(f"复核 go test ./internal/aiclient/：{'绿' if proc.returncode == 0 else '红（残留！）'}")
        if proc.returncode != 0:
            print("  " + "\n  ".join((proc.stdout + proc.stderr).splitlines()[-12:]))
            return 1

        print(f"\n计数：红闸 {red_ok} 打红 / {red_fail} 未打红；"
              f"绿闸 {green_ok} 保持绿 / {green_fail} 被误伤；异常 0")
        if failures:
            print("未通过：" + ",".join(failures))
        return 1 if (red_fail or green_fail) else 0


if __name__ == "__main__":
    sys.exit(main())

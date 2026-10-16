#!/usr/bin/env python3
"""R1（`.proto` 单一契约源 + 双端 codegen）的变异自证（2026-10-16）。

tasks.md §R1 的验收逐字里有两句是「**红**」字收尾的：
  「改 `.proto` 不重生成就红（生成物字节/哈希闸）；缺 `schema_version` 红」
⇒ 这一批的 DoD 不是「闸现在是绿的」，而是「**闸能在该红的时候红**」。
本脚本把每个闸按它自己写的判据打红一次，再逐条恢复，报实测计数。

靶点全部选在「回到 R1 立项时那个坏状态」的路径上，不打稻草人：

- M1 改 `.proto` 没重生成（合法 additive，演进闸该放、漂移闸该拦）→ `--check` 红。
- M2 有人**手改生成物**（V3 15 §2 `:34`「Generated code must not be hand-edited」）→ `--check` 红。
  ⚠️ M1 与 M2 打在同一个闸上但**成因不同**：前者是忘跑脚本，后者是绕过脚本改产物。
- M3 删字段没留 reserved（15 §6）、M4 同号换类型（15 §7 逐字禁止）、
  M5 顶层消息丢了 schema_version（V3 10:151）→ 三条各打 `--compat` 红。
  这三条**只能**改 `.proto`：compat 的 current 侧就是从 .proto 现场重生成出来的，
  改基线侧（registry.json）只会得到反向的结论（把「删除」看成「新增」）。
- M6/M8/M10/M11 打的是 `backend/tests/test_proto_cross_language.py` 那四条**结构性**闸
  （面①只三个方法、schema_version 只一种类型、夹具盖尽每条消息、`.proto` 不许有反斜杠）。
  这四条读的是**登记表/源文件**，所以变异落在登记表与 .proto 文本上；
  「坏 .proto ⇒ 登记表变成那样」这一节链路另有 M1/M2 在同一份 `_build_registry` 上证明。
  ⚠️ 这里不假装 M6 端到端：它证明的是**测会反应**，不是**生成器会拦**。
- M7 路由表两边各改一边（`delivery.go` 改了、事件契约没改）→ 同形闸红。
  这条是 R1 里代价最高的一格：`RoutingKeyFor` 是纯函数表，R4 的 publisher 靠它决定投不投，
  漏改的那一半要**运行时**才暴露。
- M9 领域层 import protobuf（V3 07:8 逐字 "It must not depend on PostgreSQL, RabbitMQ,
  Protobuf or an LLM."）→ `go test` 的黑名单闸红。R1 之前仓里没有任何 protobuf 依赖，
  这半句是**空转**的；M9 就是为了证明补上那一行之后它真的有力。

四条绿闸（G1–G4）证明这些闸不是假红制造机：加注释、换 JSON 缩进、合法 additive
都不该红——判据只看该看的东西。

恢复一律 **copy2 + filecmp 逐字节校验**，不用 `git checkout`
（见 [[feedback-mutation-restore-no-git-checkout]]）；**严禁并发跑**
（见 [[feedback-mutation-gates-no-concurrency]]）。
跑完脚本会自己复核一次 `gen_proto.py --check`，确保没有变异残渣留在生成物里。

用法：
    ./.venv/bin/python scripts/mutate_r1_gates.py --check      # 只验锚点
    ./.venv/bin/python scripts/mutate_r1_gates.py              # 全跑
    ./.venv/bin/python scripts/mutate_r1_gates.py --only M1,M7
"""
from __future__ import annotations

import argparse
import filecmp
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GO_CORE = REPO / "go-core"
PROTO_DIR = REPO / "contracts" / "proto"
REGISTRY = PROTO_DIR / "registry.json"
EVENTS_PROTO = PROTO_DIR / "closenai_events.proto"
AI_RPC_PROTO = PROTO_DIR / "closenai_ai_rpc.proto"
GO_GEN_FILE = GO_CORE / "gen" / "closenai" / "v1" / "closenai_events.pb.go"
DELIVERY_GO = GO_CORE / "internal" / "domain" / "delivery.go"
EVIDENCE_SCORE_GO = GO_CORE / "internal" / "domain" / "evidence_score.go"
PY_GATE = "backend/tests/test_proto_cross_language.py"
GO_DOMAIN_PKG = "./internal/domain/"

GOROOT = "/Users/fengyu/sdk/go1.27.1"
GO_ENV = {**os.environ, "GOROOT": GOROOT, "PATH": f"{GOROOT}/bin:" + os.environ["PATH"]}
VENV_PY = str(REPO / ".venv/bin/python")

# GateOutcome 的两行原文（锚点，逐字取自 contracts/proto/closenai_events.proto:142/145）
SV_LINE = '  string schema_version = 1;  // 恒 "1"；V3 10:151「Every contract message has schema_version」逐字'
DETAIL_LINE = '  string detail = 4;          // :514 omitempty ＝ outcome.Detail'

# gen_proto.py 的判据输出名片段（改这些文案要同时改这里，否则绿的是脚本、红的是真相）
MARK_CHECK = "字节不同步"
MARK_COMPAT_DELETE = "被删却没 reserved"
MARK_COMPAT_TYPE = "换了类型"
MARK_COMPAT_SV = "丢了 schema_version"


def _load_gen_proto():
    """导入 scripts/gen_proto.py 只为复用它的 registry 序列化函数 ⇒ 格式不会漂。"""
    spec = importlib.util.spec_from_file_location("gen_proto", REPO / "scripts" / "gen_proto.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── registry 侧的四个变异（JSON 语义改写，不做文本 replace）──


def _reg_read() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def _reg_write(registry: dict) -> None:
    REGISTRY.write_text(_load_gen_proto()._registry_text(registry), encoding="utf-8")


def _reg_add_rpc_method() -> None:
    registry = _reg_read()
    methods = registry["services"][0]["methods"]
    assert {m["name"] for m in methods} == {"Embed", "ClassifySafety", "StreamChat"}
    methods.append({
        "client_streaming": False,
        "input": ".closenai.v1.TextInput",
        "name": "Rerank",
        "output": ".closenai.v1.EmbedResponse",
        "server_streaming": False,
    })
    registry["services"][0]["methods"] = sorted(methods, key=lambda m: m["name"])
    _reg_write(registry)


def _reg_mixed_schema_version_type() -> None:
    registry = _reg_read()
    target = next(m for m in registry["messages"] if m["name"] == "closenai.v1.EventEnvelope")
    field = next(f for f in target["fields"] if f["name"] == "schema_version")
    assert field["type"] == 9, "schema_version 已不是 TYPE_STRING，锚点要跟着改"
    field["type"] = 13  # TYPE_UINT32
    _reg_write(registry)


def _reg_drop_schema_version() -> None:
    registry = _reg_read()
    target = next(m for m in registry["messages"] if m["name"] == "closenai.v1.GateOutcome")
    assert target["has_schema_version"] and not target["referenced_by"]
    target["has_schema_version"] = False
    target["fields"] = [f for f in target["fields"] if f["name"] != "schema_version"]
    _reg_write(registry)


def _reg_add_uncovered_message() -> None:
    registry = _reg_read()
    assert all(m["name"] != "closenai.v1.ProbeMessage" for m in registry["messages"])
    registry["messages"].append({
        "file": "closenai_events.proto",
        "fields": [{
            "json_name": "schemaVersion", "label": 1, "name": "schema_version",
            "number": 1, "type": 9,
        }],
        "has_schema_version": True,
        "name": "closenai.v1.ProbeMessage",
        "referenced_by": [],
        "reserved_names": [],
        "reserved_numbers": [],
    })
    registry["messages"] = sorted(registry["messages"], key=lambda m: m["name"])
    _reg_write(registry)


REGISTRY_MUTATIONS = {
    "rpc_method": _reg_add_rpc_method,
    "sv_type": _reg_mixed_schema_version_type,
    "sv_missing": _reg_drop_schema_version,
    "uncovered_message": _reg_add_uncovered_message,
}

# 每条：(id, 说明, 变异, 闸, 名片段)
#   变异 ＝ ("text", 文件, 原文, 坏文) | ("registry", REGISTRY_MUTATIONS 的键)
#   闸   ＝ ("pytest", 节点) | ("gen", "--check|--compat") | ("go", "-run 名字")
MUTATIONS = [
    (
        "M1",
        "改了 .proto 没重生成（新号位、新字段，additive 本身合法）⇒ 漂移闸必须红",
        ("text", EVENTS_PROTO, DETAIL_LINE, DETAIL_LINE + "\n  string probe = 14;"),
        ("gen", "--check"), MARK_CHECK,
    ),
    (
        "M2",
        "手改生成物（V3 15 §2 :34 禁的那件事）⇒ 同一道闸必须从另一侧抓住",
        ("text", GO_GEN_FILE, "// Code generated by protoc-gen-go.",
         "// Code generated by protoc-gen-go. 手改一行\n// 手改占位"),
        ("gen", "--check"), MARK_CHECK,
    ),
    (
        "M3",
        "删字段却没留 reserved 号位（15 §6）⇒ 演进闸红",
        ("text", EVENTS_PROTO, "\n" + DETAIL_LINE, ""),
        ("gen", "--compat"), MARK_COMPAT_DELETE,
    ),
    (
        "M4",
        "同号换类型（15 §7 逐字禁止：detail 4 号位 string→int32）⇒ 演进闸红",
        ("text", EVENTS_PROTO, DETAIL_LINE,
         '  int32 detail = 4;           // :514 变异：同号换类型'),
        ("gen", "--compat"), MARK_COMPAT_TYPE,
    ),
    (
        "M5",
        "顶层消息丢掉 schema_version（V3 10:151）⇒ 演进闸红",
        ("text", EVENTS_PROTO, "\n" + SV_LINE, ""),
        ("gen", "--compat"), MARK_COMPAT_SV,
    ),
    (
        "M6",
        "面①冒出第四个方法 Rerank（ownership.md §1 逐字「多一个都不加」，判据 2 没回答）",
        ("registry", "rpc_method"),
        ("pytest", f"{PY_GATE}::test_exactly_three_rpc_methods"), "Rerank",
    ),
    (
        "M7",
        "路由表只改 Go 一侧（insight_expired 的键名换了，事件契约没跟着改）",
        ("text", DELIVERY_GO, '"insight_expired":        "insight.expired"',
         '"insight_expired":        "insight.removed"'),
        ("pytest", f"{PY_GATE}::test_outbox_routing_table_matches_contract_text"),
        "路由表与事件契约不一致",
    ),
    (
        "M8",
        "schema_version 出现第二种类型（信封 string、事件信封 uint32 ⇒ 映射层要写两遍）",
        ("registry", "sv_type"),
        ("pytest", f"{PY_GATE}::test_schema_version_is_one_type_everywhere"),
        "schema_version",
    ),
    (
        "M9",
        "领域层 import protobuf（V3 07:8 的 Protobuf 半句；R1 前这条是空转的）",
        ("text", EVIDENCE_SCORE_GO, "import (\n\t\"fmt\"",
         'import (\n\t_ "google.golang.org/protobuf/proto"\n\t"fmt"'),
        ("go", "TestDomainDoesNotDependOnTransportLayers"), "领域层越界依赖",
    ),
    (
        "M10",
        "新加了一条顶层消息但没加夹具 ⇒ 26 条覆盖闸必须红，不许靠人记得",
        ("registry", "uncovered_message"),
        ("pytest", f"{PY_GATE}::test_fixture_covers_every_registry_message"),
        "ProbeMessage",
    ),
    (
        "M11",
        ".proto 注释里混进反斜杠（当天真踩过的坑：它会被抄进 Python docstring ⇒ SyntaxWarning）",
        ("text", EVENTS_PROTO, DETAIL_LINE,
         '  string detail = 4;          // :514 omitempty ＝ outcome.Detail 或 A\\|B'),
        ("pytest", f"{PY_GATE}::test_proto_sources_have_no_backslash"), "反斜杠",
    ),
]

GREEN = [
    (
        "G1",
        "往 outboxRouting 上方插 15 行注释：同形闸读的是那张表，不是文本行数",
        ("text", DELIVERY_GO, "var outboxRouting = map[string]string{",
         "".join(f"// 增长占位 {i}\n" for i in range(15)) + "var outboxRouting = map[string]string{"),
        ("pytest", f"{PY_GATE}::test_outbox_routing_table_matches_contract_text"),
    ),
    (
        "G2",
        "把 registry.json 缩进从 2 改成 4（语义一字未动）：结构闸判的是 JSON，不是字节",
        ("registry", "reformat"),
        ("pytest", f"{PY_GATE}::test_schema_version_rule_holds"),
    ),
    (
        "G3",
        "给 GateOutcome 加一个全新号位字段（合法 additive）：演进闸该放行，不许误伤",
        ("text", EVENTS_PROTO, DETAIL_LINE, DETAIL_LINE + "\n  string probe = 14;"),
        ("gen", "--compat"),
    ),
    (
        "G4",
        "在事件契约里加 15 行中文注释（不含反斜杠）：反斜杠闸只数反斜杠",
        ("text", EVENTS_PROTO, SV_LINE,
         "".join(f"// 增长占位 {i}\n" for i in range(15)) + SV_LINE),
        ("pytest", f"{PY_GATE}::test_proto_sources_have_no_backslash"),
    ),
]


def _reformat_registry() -> None:
    REGISTRY.write_text(
        json.dumps(_reg_read(), ensure_ascii=False, indent=4, sort_keys=True) + "\n",
        encoding="utf-8",
    )


REGISTRY_MUTATIONS["reformat"] = _reformat_registry


def _apply(mutation: tuple) -> None:
    kind, *rest = mutation
    if kind == "text":
        path, old, new = rest
        src = path.read_text(encoding="utf-8")
        count = src.count(old)
        assert count == 1, f"{path.name}：锚点命中 {count} 次（要求恰好 1 次）\n{old[:80]!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")
    else:
        REGISTRY_MUTATIONS[rest[0]]()


def _verify_anchor(mutation: tuple) -> str | None:
    kind, *rest = mutation
    if kind == "text":
        path, old, _new = rest
        n = path.read_text(encoding="utf-8").count(old)
        return None if n == 1 else f"命中 {n} 次"
    if rest[0] == "reformat":
        return None
    # registry 类变异各自带 assert 前置条件，跑的时候自然校验；这里只确认文件可读。
    try:
        _reg_read()
    except Exception as exc:  # noqa: BLE001
        return f"登记表读不出来：{exc}"
    return None


def run(gate: tuple) -> tuple[int, str]:
    kind, target = gate
    if kind == "gen":
        proc = subprocess.run([VENV_PY, str(REPO / "scripts" / "gen_proto.py"), target],
                              cwd=REPO, capture_output=True, text=True,
                              env=GO_ENV, timeout=900)
    elif kind == "go":
        proc = subprocess.run(
            ["go", "test", GO_DOMAIN_PKG, "-run", target, "-count=1"],
            cwd=GO_CORE, env=GO_ENV, capture_output=True, text=True, timeout=900,
        )
    else:
        proc = subprocess.run([VENV_PY, "-m", "pytest", target, "-q", "--no-header"],
                              cwd=REPO, capture_output=True, text=True, timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


def is_red(kind: str, code: int, out: str) -> bool:
    """「红」必须是**断言失败**的红，不是收集失败/编译崩溃的红。"""
    if code == 0:
        return False
    if kind == "pytest":
        return bool(re.search(r"\d+ (failed|error)", out))
    if kind == "go":
        return bool(re.search(r"^FAIL\t", out, re.M))
    return True  # gen 脚本自己用退出码表态（1＝漂移/演进违规）


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

    touched = [case[2][1] for case in [*MUTATIONS, *GREEN] if case[2][0] == "text"]
    touched.append(REGISTRY)
    tmp = Path(tempfile.mkdtemp(prefix="r1-gate-backup-"))
    originals: dict[Path, Path] = {}
    for path in dict.fromkeys(touched):
        bak = tmp / f"{len(originals)}-{path.name}"
        shutil.copy2(path, bak)
        originals[path] = bak

    def restore() -> None:
        for path, bak in originals.items():
            shutil.copy2(bak, path)
            assert filecmp.cmp(bak, path, shallow=False), f"{path} 恢复后字节不一致"

    red_ok = red_fail = green_ok = green_fail = 0
    failures: list[str] = []
    try:
        for cid, desc, mutation, gate, marker in MUTATIONS:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                _apply(mutation)
                code, out = run(gate)
                if is_red(gate[0], code, out) and marker in out:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（exit={code}，名片段 {marker!r} 命中={marker in out}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-12:]))
            finally:
                restore()
        for cid, desc, mutation, gate in GREEN:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                _apply(mutation)
                code, out = run(gate)
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 应保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 应保持绿 ✗（exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-12:]))
            finally:
                restore()
    finally:
        restore()
        shutil.rmtree(tmp, ignore_errors=True)

    # 收尾自证：所有靶文件与登记表都回到生成态，漂移闸必须重新变绿。
    code, out = run(("gen", "--check"))
    print(f"收尾复核 gen_proto.py --check：{'绿' if code == 0 else '红（残留！）'}")
    if code != 0:
        print("  " + "\n  ".join(out.splitlines()[-8:]))
        return 1

    total = red_ok + red_fail + green_ok + green_fail
    print(
        f"\n变异自证合计：{total} 条｜红闸 {red_ok}/{red_ok + red_fail}"
        f"｜绿闸 {green_ok}/{green_ok + green_fail}"
    )
    if failures:
        print(f"未如预期：{', '.join(failures)}")
    print("靶文件与登记表已全部按备份写回并逐字节校验一致。")
    return 0 if red_fail == 0 and green_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

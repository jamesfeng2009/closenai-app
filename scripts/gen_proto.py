"""Regenerate the dual-side protobuf code from contracts/proto/*.proto（R1）.

单一契约源 ＝ `contracts/proto/` 那四个 `.proto`；本脚本是**唯一的生成入口**，产出三样东西：

1. `go-core/gen/closenai/v1/*.pb.go` ＋ `*_grpc.pb.go`     （Go 侧）
2. `backend/gen/*_pb2.py` ＋ `*_pb2_grpc.py`               （Python 侧）
3. `contracts/proto/registry.json`                         （契约登记表，V3 15 §11）

为什么要第 3 样：它是「消息 → 号位 → 名字 → 类型 → reserved」的**机器可读快照**，
既是 additive 演进闸的比对基线（号位复用／同号换类型／枚举重编号＝红），
也是跨语言互认测的夹具清单。

版本钉法（实测出来的，不是抄的）：
  · protoc ＝ `python -m grpc_tools.protoc`，本机实测 **libprotoc 35.1**；
             PATH 上的 homebrew protoc 是 **29.3**。两个 protoc 写出的 descriptor
             字节不同 ⇒ 固定用 grpcio-tools 自带那个（它随 requirements.txt 的钉版本走，
             换机器不依赖 homebrew）。
  · 插件   ＝ `go-core/tools/protogen/go.mod` 里钉住的 protoc-gen-go / protoc-gen-go-grpc；
             生成的 .pb.go 头部会写插件版本 ⇒ 版本漂移同样被 --check 抓到。

Python 侧为什么是**平铺**在 backend/gen/ 而不是 closenai/v1/ 子目录：protoc 的 python 插件
按「.proto 相对 --proto_path 的路径」决定输出位置**和**生成物之间的 import 形式。
本仓四个 .proto 平铺在 contracts/proto/（tasks.md §R1、ownership.md、V3 15 §1 都按这个路径点名，
不移目录）⇒ 生成物平铺、彼此以顶层名互引（`import closenai_common_pb2`）。
所以 `backend/gen/__init__.py` 这个**手写**文件把自己加进 sys.path；消费方一律
`import backend.gen` 之后再 `from closenai_ai_pb2 import …`。

跑法：
    .venv/bin/python scripts/gen_proto.py                     # 生成
    .venv/bin/python scripts/gen_proto.py --check             # 改 .proto 没重生成 ⇒ 退出 1
    .venv/bin/python scripts/gen_proto.py --compat            # additive 演进闸
    .venv/bin/python scripts/gen_proto.py --print-versions    # 取证用
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROTO_DIR = ROOT / "contracts" / "proto"
# Go 生成物的落盘位置＝go_package 去掉 module 前缀剩下的那段（gen/closenai/v1）。
# 四个 .proto 里那行 option 逐字是
# `github.com/jamesfeng2009/closenai-app/go-core/gen/closenai/v1;closenaiv1`，
# 前缀必须等于 go-core/go.mod:1 的 module 路径，否则生成的包 import 不进来。
GO_OUT = ROOT / "go-core" / "gen" / "closenai" / "v1"
PY_OUT = ROOT / "backend" / "gen"
REGISTRY = PROTO_DIR / "registry.json"
PROTO_MODULE = "github.com/jamesfeng2009/closenai-app/go-core"

# 生成顺序＝依赖顺序（common 被 ai/rpc import，ai 被 rpc import）。
PROTO_FILES = [
    "closenai_common.proto",
    "closenai_ai.proto",
    "closenai_events.proto",
    "closenai_ai_rpc.proto",
]

# 手写文件：protoc 不产它，_prune_stale 也不许删它（理由见模块 docstring）。
PY_INIT = '''"""protobuf 生成物的存放地（**除本文件外全是生成物，勿手改**）。

生成入口＝`scripts/gen_proto.py`；改了 `contracts/proto/*.proto` 必须重跑它，
否则 `--check` 闸会红（逐字节比对）。

为什么要动 sys.path：四个 .proto 平铺在 contracts/proto/，protoc 的 python 插件
就把生成物也平铺在这里、并让彼此以**顶层名**互引（`import closenai_common_pb2`）。
V3 15 §2（`:34`）写明生成物不许手改 ⇒ 不改生成物的 import 语句，改成把本目录挂上路径。
"""
import pathlib
import sys

_HERE = str(pathlib.Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
'''


def _go_binary() -> str:
    """按可靠性找 go（本机混合安装：PATH 上的 go 一编译就报版本不符）。

    与 `backend/tests/test_go_auth_interop.py:_go_candidates` 同一口径，
    差别只在这里不需要真编译产物，`go version` 能过即采用。
    """
    raw = [
        os.environ.get("GO_BIN"),
        os.environ.get("GOROOT") and str(pathlib.Path(os.environ["GOROOT"]) / "bin" / "go"),
        shutil.which("go"),
    ]
    for candidate in raw:
        if not candidate or not os.access(candidate, os.X_OK):
            # GO_BIN 指到目录（而不是可执行文件）是这里最容易犯的错，
            # 直接进 subprocess 会抛 PermissionError 而不是「这个候选不行」⇒ 先验一次。
            continue
        probe = subprocess.run([candidate, "version"], capture_output=True, text=True)
        if probe.returncode == 0 and "go1." in probe.stdout:
            return candidate
    raise SystemExit(
        "找不到可用的 go（生成 Go 侧插件需要它）。用 GO_BIN=… 或 GOROOT=… 指定。\n"
        f"试过：{[r for r in raw if r]}"
    )


def _plugins(go_bin: str) -> tuple[str, str]:
    """构建（并缓存）两个 protoc 插件。

    缓存目录按钉住的插件版本命名 ⇒ 换 go.mod 里的版本自动换目录，不会拿旧插件生成新字节。
    """
    mod_dir = ROOT / "go-core" / "tools" / "protogen"
    mod = (mod_dir / "go.mod").read_text(encoding="utf-8")

    def pinned(pkg: str) -> str:
        m = re.search(rf"{re.escape(pkg)} (v[\w.\-+]+)", mod)
        if not m:
            raise SystemExit(f"go-core/tools/protogen/go.mod 里没钉住 {pkg}")
        return m.group(1)

    cache = pathlib.Path(tempfile.gettempdir()) / "closenai-protogen-{}-{}".format(
        pinned("google.golang.org/protobuf"),
        pinned("google.golang.org/grpc/cmd/protoc-gen-go-grpc"),
    )
    cache.mkdir(exist_ok=True)
    targets = [
        (cache / "protoc-gen-go", "google.golang.org/protobuf/cmd/protoc-gen-go"),
        (cache / "protoc-gen-go-grpc", "google.golang.org/grpc/cmd/protoc-gen-go-grpc"),
    ]
    for path, pkg in targets:
        if not path.exists():
            proc = subprocess.run(
                [go_bin, "build", "-o", str(path), pkg],
                cwd=mod_dir, capture_output=True, text=True,
            )
            if proc.returncode != 0:
                raise SystemExit(f"构建插件 {pkg} 失败：\n{proc.stdout}{proc.stderr}")
    return str(targets[0][0]), str(targets[1][0])


def _run_protoc(out_go: pathlib.Path, out_py: pathlib.Path, descriptor: pathlib.Path,
                plugin_go: str, plugin_grpc: str) -> None:
    cmd = [
        sys.executable, "-m", "grpc_tools.protoc",
        f"--proto_path={PROTO_DIR}",
        f"--plugin=protoc-gen-go={plugin_go}",
        f"--plugin=protoc-gen-go-grpc={plugin_grpc}",
        f"--descriptor_set_out={descriptor}",
        f"--go_out={out_go}", f"--go_opt=module={PROTO_MODULE}",
        f"--go-grpc_out={out_go}", f"--go-grpc_opt=module={PROTO_MODULE}",
        f"--python_out={out_py}",
        f"--grpc_python_out={out_py}",
    ]
    cmd += [str(PROTO_DIR / name) for name in PROTO_FILES]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"protoc 失败：\n{proc.stdout}{proc.stderr}")


def _generate_into(tmp: pathlib.Path) -> pathlib.Path:
    """跑一次 protoc，生成物落 tmp，返回 descriptor 路径。"""
    go_bin = _go_binary()
    plugin_go, plugin_grpc = _plugins(go_bin)
    out_go, out_py = tmp / "go", tmp / "py"
    out_go.mkdir(parents=True, exist_ok=True), out_py.mkdir(parents=True, exist_ok=True)
    descriptor = tmp / "descriptor.pb"
    _run_protoc(out_go, out_py, descriptor, plugin_go, plugin_grpc)
    return descriptor


def _fresh_dirs(tmp: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """把「本次新生成的」两侧目录映射到仓内目录的同名位置。

    Go 侧要穿过 `gen/closenai/v1` 这层：protoc-gen-go 的输出路径＝go_package 去掉
    `--go_opt=module=` 前缀之后剩下的那段（＝`gen/closenai/v1`），它相对 `--go_out` 落盘。
    所以 `--go_out` 给 module 根（tmp/go），比对/落盘时再钻进这层目录，
    免得把 tmp/go 当成 GO_OUT 去 prune（那会误删整个 go-core）。
    """
    return tmp / "go" / "gen" / "closenai" / "v1", tmp / "py"


def _is_init(rel: str) -> bool:
    return pathlib.PurePosixPath(rel).name == "__init__.py"


def _rel_files(root: pathlib.Path) -> list[str]:
    return sorted(
        str(p.relative_to(root)) for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def _walk_messages(message_types):
    """嵌套消息也要登记（今天没有嵌套消息，留着是为了将来不漏）。"""
    for mt in message_types:
        yield mt
        yield from _walk_messages(mt.nested_type)


def _build_registry(descriptor_path: pathlib.Path) -> dict:
    """从 FileDescriptorSet 抽登记表。

    只认 descriptor，不解析 .proto 文本 ⇒ 中文注释、空行、字段书写顺序都不影响字节。
    """
    from google.protobuf import descriptor_pb2

    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString(descriptor_path.read_bytes())

    def field_view(f) -> dict:
        out = {
            "number": f.number,
            "name": f.name,
            "type": f.type,
            "label": f.label,
            "json_name": f.json_name,
        }
        if f.type_name:
            out["type_name"] = f.type_name
        return out

    messages, enums, services = [], [], []
    for fd in fds.file:
        for m in _walk_messages(fd.message_type):
            sv = next((f for f in m.field if f.name == "schema_version"), None)
            messages.append({
                "file": fd.name,
                "name": f"{fd.package}.{m.name}" if fd.package else m.name,
                "has_schema_version": sv is not None,
                "schema_version_number": sv.number if sv is not None else None,
                "schema_version_type": sv.type if sv is not None else None,
                "fields": [field_view(f) for f in m.field],
                "reserved_numbers": sorted(
                    n for r in m.reserved_range for n in range(r.start, r.end)
                ),
                "reserved_names": sorted(m.reserved_name),
            })
        for e in fd.enum_type:
            enums.append({
                "file": fd.name,
                "name": f"{fd.package}.{e.name}" if fd.package else e.name,
                "values": sorted(
                    ({"number": v.number, "name": v.name} for v in e.value),
                    key=lambda item: item["number"],
                ),
            })
        for s in fd.service:
            services.append({
                "file": fd.name,
                "name": f"{fd.package}.{s.name}" if fd.package else s.name,
                "methods": [
                    {
                        "name": meth.name,
                        "input": meth.input_type,
                        "output": meth.output_type,
                        "client_streaming": meth.client_streaming,
                        "server_streaming": meth.server_streaming,
                    }
                    for meth in s.method
                ],
            })

    messages.sort(key=lambda item: item["name"])
    enums.sort(key=lambda item: item["name"])
    services.sort(key=lambda item: item["name"])

    # 「被别的消息当字段引用」＝值对象（Safety/SourcePointer/EmbeddingVector/StreamMemory）。
    # 这条判据是为了让 schema_version 的规则**可机器检查且不靠名单**：
    # V3 10:151 说的「Every contract message」＝**自己就是契约**的那些（RPC 的请求/响应、
    # 信封、装进 Envelope.payload 的业务消息）；值对象跟着外层信封的版本走，
    # 给它塞 schema_version 只会多一个没人读的常量。
    # ⇒ 判据＝「没被任何字段引用」的消息必须有 schema_version；
    #   将来谁新增一条顶层消息却忘了版本号，这里立刻红，不需要改脚本。
    message_names = {m["name"] for m in messages}
    referenced: dict[str, list[str]] = {}
    for m in messages:
        for f in m["fields"]:
            target = f.get("type_name", "").lstrip(".")
            if target in message_names:
                referenced.setdefault(target, []).append(m["name"])
    for m in messages:
        m["referenced_by"] = sorted(set(referenced.get(m["name"], [])))

    return {
        "generated_by": "scripts/gen_proto.py",
        "note": "生成物，勿手改（V3 15 §2 `:34`「Generated code must not be hand-edited」）。"
                "要改字段面，先改 contracts/proto/*.proto 里带出处注释的那一行，再重跑脚本。",
        "proto_files": PROTO_FILES,
        "messages": messages,
        "enums": enums,
        "services": services,
    }


def _registry_text(registry: dict) -> str:
    return json.dumps(registry, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _sync(src: pathlib.Path, dst: pathlib.Path) -> set[str]:
    """把 src 下的生成物覆盖到 dst，返回写过的相对路径。"""
    written = set()
    for rel in _rel_files(src):
        data = (src / rel).read_bytes()
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_bytes() != data:
            target.write_bytes(data)
        written.add(rel)
    return written


def _prune_stale(root: pathlib.Path, keep: set[str]) -> list[str]:
    """删掉「.proto 里已经不存在、但生成物还留着」的文件（手写 __init__.py 除外）。"""
    removed = []
    for rel in _rel_files(root):
        if rel in keep or _is_init(rel):
            continue
        (root / rel).unlink()
        removed.append(rel)
    return removed


def _ensure_py_init() -> None:
    PY_OUT.mkdir(parents=True, exist_ok=True)
    init = PY_OUT / "__init__.py"
    if not init.exists() or init.read_text(encoding="utf-8") != PY_INIT:
        init.write_text(PY_INIT, encoding="utf-8")


def generate() -> dict:
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        descriptor = _generate_into(tmp)
        registry = _build_registry(descriptor)
        fresh_go, fresh_py = _fresh_dirs(tmp)
        go_written = _sync(fresh_go, GO_OUT)
        py_written = _sync(fresh_py, PY_OUT)
    _prune_stale(GO_OUT, go_written)
    _prune_stale(PY_OUT, py_written)
    _ensure_py_init()
    REGISTRY.write_text(_registry_text(registry), encoding="utf-8")
    return registry


def check() -> int:
    """重生成到临时目录后逐字节比对——**改 .proto 不重新生成就在这里红**。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        descriptor = _generate_into(tmp)
        fresh_registry = _registry_text(_build_registry(descriptor))
        fresh_go, fresh_py = _fresh_dirs(tmp)
        drift: list[str] = []
        for label, root, fresh in (
            ("Go", GO_OUT, fresh_go),
            ("Python", PY_OUT, fresh_py),
        ):
            expected = set(_rel_files(fresh))
            actual = {r for r in _rel_files(root) if not _is_init(r)} if root.exists() else set()
            for rel in sorted(expected - actual):
                drift.append(f"[{label}] 缺失（改了 .proto 没重生成？）：{rel}")
            for rel in sorted(actual - expected):
                drift.append(f"[{label}] 多余（.proto 里已删却没重生成）：{rel}")
            for rel in sorted(expected & actual):
                if (root / rel).read_bytes() != (fresh / rel).read_bytes():
                    drift.append(f"[{label}] 字节不同步：{rel}")
        committed = REGISTRY.read_text(encoding="utf-8") if REGISTRY.exists() else ""
        if committed != fresh_registry:
            drift.append("[registry] 与 .proto 不同步")
    if drift:
        print("生成物漂移：\n" + "\n".join(f"  - {d}" for d in drift), file=sys.stderr)
        return 1
    print("生成物与 .proto 逐字节同步。")
    return 0


def _diff_registry(baseline: dict, current: dict) -> list[str]:
    """additive 演进闸的判据，逐条对 V3 15 §6/§7/§8 与 10:151。"""
    problems: list[str] = []
    base_msgs = {m["name"]: m for m in baseline["messages"]}
    cur_msgs = {m["name"]: m for m in current["messages"]}

    for name in sorted(set(cur_msgs) - set(base_msgs)):
        problems.append(f"新增消息 {name}：先进 ownership.md 与登记表，再进 .proto")
    for name in sorted(base_msgs):
        old = base_msgs[name]
        new = cur_msgs.get(name)
        if new is None:
            problems.append(f"消息被删除 {name}：v1 契约不许删，要在原处留消息并注明停用")
            continue
        old_fields = {f["number"]: f for f in old["fields"]}
        new_fields = {f["number"]: f for f in new["fields"]}
        reserved_now = set(new["reserved_numbers"])
        for number, field in sorted(old_fields.items()):
            fresh = new_fields.get(number)
            if fresh is None:
                if number not in reserved_now:
                    problems.append(
                        f"{name} 号位 {number}（{field['name']}）被删却没 reserved（15 §6）"
                    )
                continue
            if fresh["type"] != field["type"] or fresh.get("type_name") != field.get("type_name"):
                problems.append(
                    f"{name} 号位 {number} 换了类型（15 §7 明令禁止同号换类型）："
                    f"{field['type']}→{fresh['type']}"
                )
            elif fresh["name"] != field["name"]:
                problems.append(f"{name} 号位 {number} 换了名字：{field['name']}→{fresh['name']}")
            elif fresh["label"] != field["label"]:
                problems.append(f"{name} 号位 {number} 换了 label（single→repeated 不是 additive）")
        reused = set(new_fields) & set(old["reserved_numbers"])
        if reused:
            problems.append(f"{name} 复用了 reserved 号位：{sorted(reused)}（15 §6）")
        name_reuse = set(old["reserved_names"]) & {f["name"] for f in new["fields"]}
        if name_reuse:
            problems.append(f"{name} 复用了 reserved 名字：{sorted(name_reuse)}（15 §6）")
        if old["has_schema_version"] and not new["has_schema_version"]:
            problems.append(f"{name} 丢了 schema_version（V3 10:151「Every contract message has」）")

    base_enums = {e["name"]: e for e in baseline["enums"]}
    cur_enums = {e["name"]: e for e in current["enums"]}
    for name in sorted(set(cur_enums) - set(base_enums)):
        problems.append(f"新增枚举 {name}：先进 ownership.md 再进 .proto")
    for name, old in sorted(base_enums.items()):
        new = cur_enums.get(name)
        if new is None:
            problems.append(f"枚举被删除：{name}（15 §8 只许加值）")
            continue
        old_map = {v["name"]: v["number"] for v in old["values"]}
        new_map = {v["name"]: v["number"] for v in new["values"]}
        for value, number in sorted(old_map.items()):
            if value not in new_map:
                problems.append(f"枚举值被删除：{name}.{value}（15 §8）")
            elif new_map[value] != number:
                problems.append(f"枚举值重新编号：{name}.{value} {number}→{new_map[value]}（15 §8）")

    base_svcs = {s["name"]: s for s in baseline["services"]}
    cur_svcs = {s["name"]: s for s in current["services"]}
    for name, old in sorted(base_svcs.items()):
        new = cur_svcs.get(name)
        if new is None:
            problems.append(f"service 被删除：{name}")
            continue
        old_methods = {m["name"]: m for m in old["methods"]}
        new_methods = {m["name"]: m for m in new["methods"]}
        for method in sorted(set(new_methods) - set(old_methods)):
            problems.append(
                f"新增 RPC 方法 {name}.{method}：判据 2 要先回答「为什么不能等 MQ 回投」"
                f"（ownership.md §1 逐字「多一个都不加」）"
            )
        for method, spec in sorted(old_methods.items()):
            fresh = new_methods.get(method)
            if fresh is None:
                problems.append(f"RPC 方法被删除：{name}.{method}")
            elif (spec["input"], spec["output"]) != (fresh["input"], fresh["output"]):
                problems.append(f"RPC 方法签名变了：{name}.{method}（15 §7）")
            elif spec["server_streaming"] != fresh["server_streaming"]:
                problems.append(f"RPC 流式性变了：{name}.{method}")
    for name in sorted(set(cur_svcs) - set(base_svcs)):
        problems.append(f"新增 service：{name}（要先进 ownership.md §1 再进 proto）")
    return problems


def compat() -> int:
    """拿**已提交的 registry.json** 当上一版基线，比对当前 .proto 的生成结果。

    本批是首次登记 ⇒ 基线＝自身，必然通过；从下一个改 .proto 的人开始才有力。
    这也是 `scripts/mutate_r1_gates.py` 要临时替换 registry.json 当「旧基线」的原因。
    """
    if not REGISTRY.exists():
        print("registry.json 不存在：先跑 scripts/gen_proto.py", file=sys.stderr)
        return 1
    baseline = json.loads(REGISTRY.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as td:
        descriptor = _generate_into(pathlib.Path(td))
        current = _build_registry(descriptor)
    problems = _diff_registry(baseline, current)
    if problems:
        print("契约演进违规：\n" + "\n".join(f"  - {p}" for p in problems), file=sys.stderr)
        return 1
    print("additive 演进检查通过（基线＝committed registry.json）。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="从 contracts/proto 生成双端代码与登记表")
    ap.add_argument("--check", action="store_true",
                    help="只比对生成物字节与 registry，不写文件（漂移⇒退出 1）")
    ap.add_argument("--compat", action="store_true",
                    help="additive 演进闸：以 committed registry.json 为基线")
    ap.add_argument("--print-versions", action="store_true", help="打印工具链版本（取证用）")
    args = ap.parse_args()

    if args.print_versions:
        go_bin = _go_binary()
        plugin_go, plugin_grpc = _plugins(go_bin)
        for label, cmd in (
            ("protoc", [sys.executable, "-m", "grpc_tools.protoc", "--version"]),
            ("protoc-gen-go", [plugin_go, "--version"]),
            ("protoc-gen-go-grpc", [plugin_grpc, "--version"]),
            ("go", [go_bin, "version"]),
        ):
            proc = subprocess.run(cmd, capture_output=True, text=True)
            print(f"{label}: {(proc.stdout or proc.stderr).strip()}")
        return 0
    if args.compat:
        return compat()
    if args.check:
        return check()
    registry = generate()
    print(
        f"生成完成：{len(registry['messages'])} 条消息 / {len(registry['enums'])} 个枚举 / "
        f"{len(registry['services'])} 个 service"
    )
    print(f"  Go      → {GO_OUT.relative_to(ROOT)}")
    print(f"  Python  → {PY_OUT.relative_to(ROOT)}")
    print(f"  登记表  → {REGISTRY.relative_to(ROOT)}")
    missing = [
        m["name"] for m in registry["messages"]
        if not m["has_schema_version"] and not m["referenced_by"]
    ]
    if missing:
        print(
            "⚠️ 顶层契约消息缺 schema_version（V3 10:151「Every contract message has」）：\n  "
            + "\n  ".join(missing),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())

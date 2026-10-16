"""
R1 跨语言契约互认 + 生成物闸（Python ⇄ Go）。

被验对象：
  · 契约源    `contracts/proto/*.proto`（单一来源）
  · 生成物    `go-core/gen/closenai/v1/*.pb.go`、`backend/gen/*_pb2*.py`
  · 登记表    `contracts/proto/registry.json`（从 protoc descriptor 派生，不是手写的第二真相源）
  · 期望值    `go-core/testdata/proto_contract_cases.json`（26 条，**唯一**的期望值来源）
  · Go 工具   `go-core/tools/protocross`（emit / check / reject 三个模式）
  · 生成器    `scripts/gen_proto.py`（--check 漂移闸、--compat additive 演进闸）

为什么要这一份文件——tasks.md §R1 的验收逐字是：
  「跨语言 fixture 测——同一 payload Go marshal → Python unmarshal 且字段逐字相等（反向同理）；
    **改 `.proto` 不重生成就红**（生成物字节/哈希闸）；缺 `schema_version` 红。
    `scripts/mutate_r1_gates.py` 变异自证（备份写回，禁 `git checkout`）」
而 V3 `15_PROTOBUF_WIRE_CONTRACT.md` §12 `:160-170` 只给了五条 contract-test 的**类**，
没给判定手段 ⇒ 五条各自落在哪个模式，写在下面每一行的 docstring 里，不靠口头对应。

五条 × 落点（`15:160-170` 逐字顺序）：
  1. old message → new consumer            → test_old_payload_is_readable_by_current_contract
  2. new additive message → old tolerant   → test_added_field_is_tolerated_by_current_contract
     consumer
  3. invalid message → rejection path      → test_malformed_bytes_rejected_by_both_languages
  4. Go encode → Python decode             → test_go_marshal_python_unmarshal_field_by_field
  5. Python encode → Go decode             → test_python_marshal_go_unmarshal_roundtrip
⚠️ 15 §12 那五行是**散文**（`:162-166`），逐字只有「Contract tests must cover:」这种句式；
   把它们变成可执行断言是本仓补齐＝已登记的本地口径，不是 V3 原文。

夹具只有**一份**（Go 与 Python 共用），因为「两侧各自写期望值」＝两个真相源，
第一版就会在某个字段上漂移；改期望值只改那个 JSON，本文件不许内联任何字段值。

跑法（本机 go 的 PATH 是混合安装，须点名；没有 go 时前两类测 skip，闸门测照跑）：
    GO_BIN=/path/to/go1.27.1/bin/go .venv/bin/python -m pytest backend/tests/test_proto_cross_language.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GO_CORE = ROOT / "go-core"
PROTO_DIR = ROOT / "contracts" / "proto"
REGISTRY_PATH = PROTO_DIR / "registry.json"
FIXTURE_PATH = GO_CORE / "testdata" / "proto_contract_cases.json"
GEN_PROTO = ROOT / "scripts" / "gen_proto.py"
DELIVERY_GO = GO_CORE / "internal" / "domain" / "delivery.go"
EVENTS_PROTO = PROTO_DIR / "closenai_events.proto"
ARCH_TEST_GO = GO_CORE / "internal" / "domain" / "arch_test.go"
GO_TIMEOUT = 300

# ── Go 工具链发现：与 backend/tests/test_go_auth_interop.py 同一口径 ──


def _go_candidates() -> list[str]:
    raw = [
        os.environ.get("GO_BIN"),
        os.environ.get("GOROOT") and str(Path(os.environ["GOROOT"]) / "bin" / "go"),
        shutil.which("go"),
    ]
    out: list[str] = []
    for item in raw:
        if item and item not in out:
            out.append(item)
    return out


def _run_go(go_bin: str, args: list[str], timeout: int = GO_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [go_bin, *args],
        cwd=GO_CORE,
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=timeout,
    )


def _require_go() -> str:
    """返回第一台能真正编译 protocross 的 go；都不可用则 skip（本机没 go 不该让 CI 变红）。"""
    failures = []
    for candidate in _go_candidates():
        probe = _run_go(candidate, ["run", "./tools/protocross", "-h"], 180)
        # `-h` 让 flag 包打印用法并以 2 退出；没编译成功也是 2 ⇒ 以 stderr 的用法行判据。
        if "Usage of" in probe.stderr or "-mode" in probe.stderr:
            return candidate
        failures.append(f"{candidate}: rc={probe.returncode} {probe.stderr.strip()[:200]}")
    if not failures:
        pytest.skip("未找到 go 可执行文件（可用 GO_BIN/GOROOT 指定）")
    pytest.skip("go 工具链不可用：\n" + "\n".join(failures))


@pytest.fixture(scope="module")
def go_bin() -> str:
    return _require_go()


# ── 契约侧的数据结构（全部从**生成物/登记表**取，不手写）──


@pytest.fixture(scope="module")
def registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fixture_file() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def py_class(registry):
    """契约全名 → Python 生成类。

    ⚠️ `import backend.gen` 必须**先于** `importlib.import_module('closenai_ai_pb2')`：
    .proto 是平铺的（15 §2 没有目录层级），protoc 因此把 Python 生成物也平铺，
    模块之间用顶层名互相 import ⇒ 由 `backend/gen/__init__.py` 把自己塞进 sys.path。
    """
    import backend.gen  # noqa: F401  副作用：把 backend/gen 加进 sys.path
    import importlib

    cache: dict[str, type] = {}
    for msg in registry["messages"]:
        module = msg["file"][: -len(".proto")] + "_pb2"
        cache[msg["name"]] = getattr(
            importlib.import_module(module), msg["name"].split(".")[-1]
        )
    return cache


def _json_name_of(msg: dict, number: int) -> str:
    for field in msg["fields"]:
        if field["number"] == number:
            return field["json_name"]
    raise AssertionError(f"登记表里没有字段号 {number}")


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        chunk = value & 0x7F
        value >>= 7
        out.append(chunk | (0x80 if value else 0))
        if not value:
            return bytes(out)


# ── Go 侧三个模式的产出入 fixture，一次进程调用喂多个测 ──


@pytest.fixture(scope="module")
def go_emitted(go_bin, fixture_file) -> dict:
    """`-mode emit`：夹具 JSON → 生成物 → wire 字节（hex）。＝「Go marshal」。"""
    proc = _run_go(go_bin, ["run", "./tools/protocross", "-mode", "emit"])
    assert proc.returncode == 0, proc.stderr
    cases = json.loads(proc.stdout)["cases"]
    assert len(cases) == len(fixture_file["cases"]), "Go 侧漏测夹具条目（构造函数表少加？）"
    return {c["name"]: c for c in cases}


@pytest.fixture(scope="module")
def py_encoded(fixture_file, py_class) -> dict:
    """Python 独立把同一份 JSON 编成 wire 字节。＝「Python marshal」，不经 Go。"""
    out: dict[str, bytes] = {}
    for case in fixture_file["cases"]:
        msg = py_class[case["message"]]()
        from google.protobuf import json_format

        json_format.ParseDict(case["json"], msg)
        out[case["name"]] = msg.SerializeToString()
    return out


def _go_check(go_bin, entries: list[dict], tmp_path) -> dict:
    """把 hex 交给 Go 反向解码，返回 {name: {hex_roundtrip, json}}。"""
    in_file = tmp_path / "go_check_in.json"
    out_file = tmp_path / "go_check_out.json"
    in_file.write_text(json.dumps({"cases": entries}), encoding="utf-8")
    proc = _run_go(go_bin, ["run", "./tools/protocross", "-mode", "check",
                            "-input", str(in_file)])
    assert proc.returncode == 0, proc.stderr
    out_file.write_text(proc.stdout, encoding="utf-8")
    return {c["name"]: c for c in json.loads(proc.stdout)["cases"]}


# ════════════════ 15 §12 第 4 行：Go encode → Python decode ════════════════


def test_go_marshal_python_unmarshal_field_by_field(go_emitted, py_encoded, fixture_file, py_class):
    """逐字段相等 ＋ 逐字节相等，两个都要。

    只有 `==` 不够：proto3 把「显式设成默认值」和「没设」看成相等，逐字节才能证明
    两侧的**编码**也一致（字段号、wire type、重复字段顺序）。反过来只有字节等也不够：
    那不能说明字段名↔字段号的映射读对了。⇒ 三条断言各堵一个洞。
    """
    from google.protobuf import json_format

    for case in fixture_file["cases"]:
        cls = py_class[case["message"]]
        go_bytes = bytes.fromhex(go_emitted[case["name"]]["hex"])
        mine = json_format.ParseDict(case["json"], cls())

        parsed = cls()
        parsed.ParseFromString(go_bytes)  # Python unmarshal Go 的字节
        assert parsed == mine, f"{case['name']}：Go 编出来的字节 Python 读出的字段不一样"
        assert parsed.SerializeToString() == go_bytes, (
            f"{case['name']}：Python 反向编码与 Go 的字节不同 ⇒ 有一侧编码不是确定性的"
        )
        assert py_encoded[case["name"]] == go_bytes, (
            f"{case['name']}：两侧独立编码同一份 JSON 得出不同字节"
        )


# ════════════════ 15 §12 第 5 行：Python encode → Go decode ════════════════


def test_python_marshal_go_unmarshal_roundtrip(go_bin, py_encoded, fixture_file, py_class, tmp_path):
    """Go 必须能读 Python 的字节，且**重编码逐位相同**（不是「能读就算过」）。"""
    from google.protobuf import json_format

    entries = [
        {"name": c["name"], "message": c["message"], "hex": py_encoded[c["name"]].hex()}
        for c in fixture_file["cases"]
    ]
    got = _go_check(go_bin, entries, tmp_path)
    assert set(got) == {e["name"] for e in entries}
    for case in fixture_file["cases"]:
        item = got[case["name"]]
        assert item["hex_roundtrip"] == py_encoded[case["name"]].hex(), (
            f"{case['name']}：Go 解码后重编码与 Python 的字节不一致"
        )
        back = json_format.ParseDict(item["json"], py_class[case["message"]]())
        assert back == json_format.ParseDict(case["json"], py_class[case["message"]]()), (
            f"{case['name']}：Go 回吐的 JSON 与夹具期望值不等价"
        )


# ════════════════ 15 §12 第 1 行：old message → new consumer ════════════════


def test_old_payload_is_readable_by_current_contract(go_bin, fixture_file, registry, py_class, tmp_path):
    """「老消息」＝少数字段的那一份字节；当前契约必须照读，缺的字段落默认值。

    这就是灰度期的真实形状：Go 已经升到新字段面，Python 还在发老 payload。
    """
    from google.protobuf import json_format

    case = next(c for c in fixture_file["cases"] if c["name"] == "envelope_command")
    spec = next(m for m in registry["messages"] if m["name"] == case["message"])
    numbers = sorted(f["number"] for f in spec["fields"])
    dropped = numbers[-3:]
    old_json = {k: v for k, v in case["json"].items()
                if k not in {_json_name_of(spec, n) for n in dropped}}

    old_bytes = json_format.ParseDict(old_json, py_class[case["message"]]()).SerializeToString()
    reread = py_class[case["message"]]()
    reread.ParseFromString(old_bytes)
    for number in dropped:
        field = next(f for f in spec["fields"] if f["number"] == number)
        got = getattr(reread, field["name"])
        assert not (got if isinstance(got, (list, dict, str, bytes)) else bool(got)) or got == 0, (
            f"老 payload 少了 {field['name']}，当前契约读出来却不是默认值：{got!r}"
        )

    got = _go_check(go_bin, [
        {"name": case["name"], "message": case["message"], "hex": old_bytes.hex()}
    ], tmp_path)[case["name"]]
    assert bytes.fromhex(got["hex_roundtrip"]) == old_bytes, "Go 读老消息后重编码变了字节"
    assert set(got["json"]) == set(old_json), (
        f"Go 读老消息吐出的字段集不等于在场字段：多出来 {set(got['json']) - set(old_json)}"
    )


# ════════════════ 15 §12 第 2 行：additive message → tolerant consumer ════════════════


def test_added_field_is_tolerated_by_current_contract(go_emitted, registry, fixture_file, py_class):
    """同号位之后**新增**的字段号，老消费方必须「读得懂在场字段、看不见的字段不报错」。

    ⚠️ 这里刻意用 max+9：既不在已用号位里，也避开紧邻的 reserved 空位，
    模拟的是「对端已经加了字段、我这版契约还没有」。
    reserved 是**编译期**约束，解码器一律当未知字段处理 ⇒ 正是 15 §7 要求的容错。
    """
    from google.protobuf import json_format

    target = next(c for c in fixture_file["cases"] if c["name"] == "ev_gate_outcome")
    spec = next(m for m in registry["messages"] if m["name"] == target["message"])
    max_number = max(f["number"] for f in spec["fields"])
    unknown_number = max_number + 9

    base = bytes.fromhex(go_emitted[target["name"]]["hex"])
    augmented = base + _varint((unknown_number << 3) | 2) + _varint(7) + b"unknown"

    cls = py_class[target["message"]]
    tolerant = cls()
    tolerant.ParseFromString(augmented)  # 不抛异常＝容错
    original = json_format.ParseDict(target["json"], cls())
    assert json_format.MessageToDict(tolerant) == json_format.MessageToDict(original), (
        "未知字段把已知字段的读取结果带偏了"
    )
    assert tolerant.SerializeToString() == augmented, (
        "Python 丢了未知字段 ⇒ R2 之后转发的消息会把新字段吃掉（15 §7 additive 的前提）"
    )


def test_added_field_is_tolerated_by_go(go_bin, go_emitted, registry, fixture_file, py_class, tmp_path):
    """同一份增广字节交给 Go ⇒ 必须不报错，且**保留**未知字段后原样重编码。"""
    from google.protobuf import json_format

    target = next(c for c in fixture_file["cases"] if c["name"] == "ev_gate_outcome")
    spec = next(m for m in registry["messages"] if m["name"] == target["message"])
    unknown_number = max(f["number"] for f in spec["fields"]) + 9
    base = bytes.fromhex(go_emitted[target["name"]]["hex"])
    augmented = base + _varint((unknown_number << 3) | 2) + _varint(7) + b"unknown"

    got = _go_check(go_bin, [
        {"name": target["name"], "message": target["message"], "hex": augmented.hex()}
    ], tmp_path)[target["name"]]
    assert bytes.fromhex(got["hex_roundtrip"]) == augmented, (
        "Go 丢了未知字段 ⇒ 转发链路上新字段会消失（protobuf-go 的 unknownFields 没生效）"
    )
    cls = py_class[target["message"]]
    assert json_format.MessageToDict(json_format.ParseDict(got["json"], cls())) == (
        json_format.MessageToDict(json_format.ParseDict(target["json"], cls()))
    ), "Go 读增广字节后已知字段读错了"


# ════════════════ 15 §12 第 3 行：invalid message → rejection path ════════════════

# 四种畸形字节，全部**当天实测**两侧都给错（不是照抄教科书例子）：
#   truncated      长度前缀说 5 字节只给了 1 字节
#   varint_overflow 11 个 0xff 的 varint，超过 64 位
#   bad_wiretype   wire type 7 不存在
#   bad_utf8       string 字段装非法 UTF-8
MALFORMED = {
    "truncated": b"\x0a\x05A",
    "varint_overflow": b"\x08" + b"\xff" * 11,
    "bad_wiretype": b"\x06",
    "bad_utf8": b"\x12\x02\xff\xff",
}


def test_malformed_bytes_rejected_by_python(py_class):
    """Python 侧的拒绝路径＝DecodeError（R3 的 server 要靠它区分「坏字节」与「业务拒绝」）。"""
    from google.protobuf.message import DecodeError

    for label, raw in MALFORMED.items():
        assert raw, f"{label} 夹具空了 ⇒ 下面这条断言会空转"
        with pytest.raises(DecodeError, match=r"[Ww]ire|UTF-8"):
            py_class["closenai.v1.Envelope"]().ParseFromString(raw)


def test_malformed_bytes_rejected_by_go(go_bin, tmp_path):
    """Go 侧的拒绝路径。R4 的 broker 消费方要靠它把坏字节投死信，**不能静默吞**。"""
    entries = [
        {"name": label, "message": "closenai.v1.Envelope", "hex": raw.hex()}
        for label, raw in MALFORMED.items()
    ]
    in_file = tmp_path / "reject_in.json"
    in_file.write_text(json.dumps({"cases": entries}), encoding="utf-8")
    proc = _run_go(go_bin, ["run", "./tools/protocross", "-mode", "reject",
                            "-input", str(in_file)])
    assert proc.returncode == 0, proc.stderr
    got = {c["name"]: c for c in json.loads(proc.stdout)["cases"]}
    assert set(got) == set(MALFORMED)
    for label, item in got.items():
        assert item["ok"] is False, f"{label} 被 Go 静默接受了，畸形字节就没法进死信"
        assert item.get("error"), f"{label} 拒绝了但没给原因"


# ═══════════════════════ 生成物闸（§R1 的另两句验收）═══════════════════════


def _run_gen_proto(go_bin, flag: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(GEN_PROTO), flag],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=GO_TIMEOUT,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_generated_code_is_byte_in_sync(go_bin):
    """§R1 逐字「改 `.proto` 不重生成就红」：三个产物面全部逐字节比对。"""
    rc, text = _run_gen_proto(go_bin, "--check")
    assert rc == 0, f"生成物漂移（改了 .proto 没跑 scripts/gen_proto.py？）：\n{text}"


def test_additive_compat_gate_is_green(go_bin):
    """§R1 的演进闸：以 committed registry.json 为基线，删字段不 reserved、
    同号换类型、枚举改号、service 签名变化、新增未登记 ⇒ 全都要红。"""
    rc, text = _run_gen_proto(go_bin, "--compat")
    assert rc == 0, f"additive 演进违规：\n{text}"


def test_schema_version_rule_holds(registry):
    """§R1 逐字「缺 `schema_version` 红」，判据取自 `gen_proto.py` 的派生规则：

    **没被任何字段引用**的消息＝顶层契约消息 ⇒ 必须带 schema_version；
    被引用的值对象（Safety / SourcePointer / EmbeddingVector / StreamMemory）不在此列，
    因为它们的版本由外层信封带——V3 10:151 那句「Every contract message has
    schema_version」的「contract message」按本仓落点＝**能被单独投递的那一层**。
    ⚠️ 这里不写消息名单：名单会漏掉将来新加的顶层消息，派生规则不会。
    """
    missing = [m["name"] for m in registry["messages"]
               if not m["has_schema_version"] and not m["referenced_by"]]
    assert missing == [], f"顶层契约消息缺 schema_version：{missing}"
    # 反向自证：派生规则真的分得出两类，否则上面那条断言是空转。
    assert len(registry["messages"]) > len(missing)
    nested = [m["name"] for m in registry["messages"] if m["referenced_by"]]
    assert nested, "referenced_by 全空 ⇒ 派生规则没生效，missing 永远为空、闸永远绿"


def test_schema_version_is_one_type_everywhere(registry):
    """schema_version 在**所有**消息里必须是同一类型。

    本仓实测口径＝string（V3 15:44 逐字 `string schema_version = 3;`，
    而 outbox 列是 integer ⇒ 转换只活在映射层）。
    混用（信封 string、payload uint32）会让 R4 的映射写两遍并迟早漂移。
    """
    types = {
        (f["type"], f.get("type_name"))
        for m in registry["messages"] for f in m["fields"]
        if f["name"] == "schema_version"
    }
    assert len(types) == 1, f"schema_version 出现多种类型：{types}"
    assert types == {(9, None)}, (
        f"schema_version 不再是 TYPE_STRING=9（本仓口径）：{types}"
    )


def test_fixture_covers_every_registry_message(fixture_file, registry):
    """26 条夹具必须盖住登记表里**每一条**消息：要么自己是被测的顶层消息，
    要么从某个顶层消息沿字段 type_name 可达（值对象就靠这一支覆盖）。

    堵的是「加了消息忘了加夹具」——emit 的构造函数表也堵一半，
    但漏在 constructors 里的那条会直接被 `-mode emit` 判红。
    """
    covered = {c["message"] for c in fixture_file["cases"]}
    by_name = {m["name"]: m for m in registry["messages"]}
    # 传递闭包：从每条被测顶层消息出发，把所有字段引用的消息收进来。
    # ⚠️ descriptor 里的 type_name 带前导点（`.closenai.v1.Safety`），要去掉才能对表。
    frontier = sorted(covered)
    seen = set(covered)
    while frontier:
        current = by_name[frontier.pop()]
        for field in current["fields"]:
            ref = (field.get("type_name") or "").lstrip(".")
            if ref in by_name and ref not in seen:
                seen.add(ref)
                frontier.append(ref)
    uncovered = sorted(set(by_name) - seen)
    assert uncovered == [], f"夹具没覆盖到的消息（改 .proto 没加 case？）：{uncovered}"
    # 自证：可达闭包确实覆盖到了「不是被测顶层消息」的那些，否则上面那条会退化成名单。
    assert covered < seen, "没有任何消息靠字段引用被覆盖 ⇒ 可达闭包这段是空转"


def test_proto_sources_have_no_backslash():
    """`.proto` 注释里不许出现反斜杠（当天实测出来的坑，规则化）。

    protoc 把注释原样抄进生成物：Go 侧是行注释（无害），Python 侧是**docstring** ⇒
    `"\\|"` 变成非法转义序列，import 时报 SyntaxWarning。本仓 pytest 配了
    `-W error::SyntaxWarning` 的路子一旦开就红，而更糟的是「Python 读不懂自己的生成物」。
    正则在 ERE 里用字符类 [Aa] 或改写成「A 或 B」，不许用转义竖线。
    """
    offenders = []
    for path in sorted(PROTO_DIR.glob("*.proto")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "\\" in line:
                offenders.append(f"{path.name}:{lineno}")
    assert offenders == [], f".proto 含反斜杠（会被抄进 Python docstring）：{offenders}"


def test_exactly_three_rpc_methods(registry):
    """面①只有三个方法，多一个都不加（contracts/rpc/ownership.md §1）。

    ⚠️ 这条闸是**表**的可执行形态：ownership.md 判据 2 逐字
    「Do not introduce gRPC merely because both runtimes use Protobuf.」（V3 06:173），
    而口头约定挡不住有人顺手加第四个方法。要加就先回那张表加一行并单独回答判据 2。
    """
    services = registry["services"]
    assert len(services) == 1, f"service 数量漂移（本批只允许一个）：{[s['name'] for s in services]}"
    methods = {m["name"] for m in services[0]["methods"]}
    assert methods == {"Embed", "ClassifySafety", "StreamChat"}, (
        f"面①方法集与 ownership.md §1 不符：{sorted(methods)}"
    )
    streaming = {m["name"]: (m["client_streaming"], m["server_streaming"])
                 for m in services[0]["methods"]}
    assert streaming["StreamChat"] == (False, True), (
        f"StreamChat 必须是 server streaming（SSE 透传的前提）：{streaming['StreamChat']}"
    )
    assert streaming["Embed"] == (False, False) and streaming["ClassifySafety"] == (False, False)


def test_outbox_routing_table_matches_contract_text(registry):
    """`delivery.go` 的 outboxRouting ＝ 事件契约注释里的箭头，两个集合**完全相等**。

    为什么值得为注释写闸：`RoutingKeyFor` 是纯函数表（R4 的 publisher 靠它决定投不投递），
    而这份 `.proto` 是 R4/R5 消费方唯一的字段面来源。两边各写各的 ⇒ 第一个改路由的人
    只会改一边，而漏掉的那一边是**运行时**才发现。注释在这里不是文档，是登记表。
    ⚠️ 正则只认反引号里的「event_type → routing_key」；`evidence.accepted`（待补，
    ownership.md §2）不带箭头 ⇒ 不会被误当成已有路由。
    """
    arrow = re.compile(r"`([a-z][a-z0-9_]*) → ([a-z][a-z0-9_.]*)`")
    proto_pairs = set(arrow.findall(EVENTS_PROTO.read_text(encoding="utf-8")))
    go_text = DELIVERY_GO.read_text(encoding="utf-8")
    block = re.search(r"var outboxRouting = map\[string\]string\{(.*?)\n\}", go_text, re.S)
    assert block, "delivery.go 里找不到 outboxRouting ⇒ 这张闸失效了"
    go_pairs = set(re.findall(r'"([a-z_]+)":\s*"([a-z._]+)"', block.group(1)))
    assert proto_pairs == go_pairs, (
        f"路由表与事件契约不一致：只在 .proto {sorted(proto_pairs - go_pairs)}，"
        f"只在 delivery.go {sorted(go_pairs - proto_pairs)}"
    )
    assert len(go_pairs) == 10, f"路由表条数变了（登记为十条）：{len(go_pairs)}"
    # 自证：payload 消息数与「有 payload 的事件族」不是空对空。
    payload_msgs = [m["name"] for m in registry["messages"]
                    if m["file"] == "closenai_events.proto" and m["name"] != "closenai.v1.EventEnvelope"]
    assert len(payload_msgs) == 6, f"事件 payload 消息不再是六个：{payload_msgs}"


def _go_imports(text: str) -> set[str]:
    """从 Go 源文本里取 import 路径（单行式 + 括号块两种写法）。

    ⚠️ 只取**声明**，不做全文 grep：`arch_test.go` 的黑名单本身就写着
    `"google.golang.org/protobuf"` 这个字符串，grep 会把它当成违规命中。
    """
    out = set(re.findall(r'^import\s+(?:[\w.]+\s+)?"([^"]+)"', text, re.M))
    for block in re.findall(r'^import\s*\((.*?)^\)', text, re.M | re.S):
        out.update(re.findall(r'"([^"]+)"', block))
    return out


def test_domain_layer_still_forbids_transport_imports():
    """领域层不许 import gRPC / protobuf，且黑名单**只准加不准减**（ownership.md §5）。

    V3 07:8 逐字 "It must not depend on PostgreSQL, RabbitMQ, Protobuf or an LLM."
    ⇒ R1 引入 protobuf 依赖时，`arch_test.go` 的 transportImports 必须同时补上
    `google.golang.org/protobuf`；不补，07:8 的 Protobuf 半句就永远是空转。
    `ownership.md` §5 那句「不改这个闸」的读法＝不许放松它，本批只加不减。
    """
    text = ARCH_TEST_GO.read_text(encoding="utf-8")
    block = re.search(r"var transportImports = \[\]string\{(.*?)\n\}", text, re.S)
    assert block, "arch_test.go 里找不到 transportImports ⇒ 闸的形状变了，这条测要跟着改"
    entries = set(re.findall(r'"([^"]+)"', block.group(1)))
    for required in ("google.golang.org/grpc", "google.golang.org/protobuf",
                     "github.com/rabbitmq", "database/sql", "net/http"):
        assert required in entries, f"黑名单少了 {required}（只准加不准减）：{sorted(entries)}"

    domain_files = [p for p in (GO_CORE / "internal" / "domain").glob("*.go")
                    if not p.name.endswith("_test.go")]
    assert len(domain_files) > 10, f"没扫到领域层文件（路径变了？）：{len(domain_files)}"
    violations = {
        f"{p.name}: {imp}"
        for p in domain_files
        for imp in _go_imports(p.read_text(encoding="utf-8"))
        for prefix in entries
        if imp == prefix or imp.startswith(prefix + "/")
    }
    assert violations == set(), f"领域层越界依赖（V3 07:8）：{sorted(violations)}"

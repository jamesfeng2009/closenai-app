"""R5 · Python 侧的 MQ 名字与 Go 侧**逐字相同**的闸（工作单 §四 R5-① 的第二条测）。

要拦的事故只有一类：**一侧改了名字**。AMQP 的 exchange／queue／routing key／header 都是
自由字符串，写错了 broker 不会告诉你——它只会把消息投给一个没人认领的队列然后静默丢弃，
两侧各自的测试还都是绿的。所以这张闸拿三个事实源交叉比：

* ``go-core/internal/messaging/amqp/topology.go``（声明侧的表与常量，D-R5-3 认定的唯一参数源）
* ``contracts/events.md``（由 ``tools/eventscontract`` 从上面那张表**生成**的契约文档）
* ``go-core/internal/domain/{commands,delivery}.go``（键串与 message_type 与 schema_version 的出生地）

比对全部走文本解析，不 import Go、不跑 Go：这样在没装 go 工具链的机器上这条闸照样有效
（R3 结案时就登记过「没装 go 不许报成没跑齐」这条口径）。真跑通要靠 R5-③ 的 broker 互操作闸。
"""
from __future__ import annotations

import ast
import pathlib
import re

from backend.ai.mq import config, contract, topology
from backend.ai.mq.contract import (
    CONTENT_TYPE_JSON,
    CANDIDATE_KIND_TO_ENTRY,
    HEADER_ATTEMPT_COUNT,
    HEADER_CAUSATION_ID,
    HEADER_SCHEMA_VERSION,
    HEADER_USER_ID,
    ROUTING_KEY_TO_TASK_KIND,
    ROUTING_KEY_TO_TASK_TYPE,
    SUPPORTED_SCHEMA_VERSIONS,
    candidate_message_id,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOPOLOGY_GO = ROOT / "go-core" / "internal" / "messaging" / "amqp" / "topology.go"
PUBLISHER_GO = ROOT / "go-core" / "internal" / "messaging" / "amqp" / "publisher.go"
COMMANDS_GO = ROOT / "go-core" / "internal" / "domain" / "commands.go"
DELIVERY_GO = ROOT / "go-core" / "internal" / "domain" / "delivery.go"
INBOUND_GO = ROOT / "go-core" / "internal" / "messaging" / "inbound" / "inbound.go"
INBOUND_DECODE_GO = (
    ROOT / "go-core" / "internal" / "messaging" / "inbound" / "candidate_decode.go"
)
EVENTS_MD = ROOT / "contracts" / "events.md"
MQ_DIR = ROOT / "backend" / "ai" / "mq"
BOUNDARY_TEST = ROOT / "backend" / "tests" / "test_ai_boundary.py"


def _read(path: pathlib.Path) -> str:
    assert path.exists(), f"事实源文件不存在：{path}（改名或移动会让本闸静默放行，先修这条）"
    return path.read_text(encoding="utf-8")


def _boundary_literal(name: str) -> str:
    """从 ``test_ai_boundary.py`` 里取一个字面量常量。

    本文件的判据必须与那条闸读**同一条**口径：这里再抄一份 ``"backend.gen"``，
    将来那条闸改成别的根名，这边就会一直绿着测一个不存在的东西。
    """
    for node in ast.parse(_read(BOUNDARY_TEST)).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
    raise AssertionError(f"test_ai_boundary.py 里没有字符串常量 {name}：那条闸写法变了，同步改这里")


# 与边界闸共用同一条生成物根名，不在这里抄第二份
GEN_ROOT = _boundary_literal("GEN_ROOT")


def _go_string_consts(src: str) -> dict[str, str]:
    """取出 Go 的导出字符串常量，两种写法都认。

    分两条正则而不是放宽成「任意缩进」：``publisher.go:33`` 的 ``ContentTypeJSON`` 是**顶层单条**
    ``const``，``:37-42`` 那四个 header 在 ``const ( … )`` 块里（块内才带制表符）。
    写成 ``^[ \\t]*`` 会把函数体里缩进的赋值也当常量——那条闸要的是「常量表」，不是「任意赋值」。
    """
    out = {
        m.group(1): m.group(2)
        for m in re.finditer(r'^\t([A-Z][A-Za-z0-9]*)\s*=\s*"([^"]*)"', src, re.MULTILINE)
    }
    out.update(
        {
            m.group(1): m.group(2)
            for m in re.finditer(r'^const ([A-Z][A-Za-z0-9]*) = "([^"]*)"', src, re.MULTILINE)
        }
    )
    return out


def _events_routes() -> list[tuple[str, str, str, str]]:
    """解析 ``contracts/events.md`` §二 的表行 → ``(exchange, routing key, queue, 面)``。"""
    rows = []
    for line in _read(EVENTS_MD).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 5 or not cells[0].startswith("`"):
            continue
        unwrap = lambda c: c.strip("`")  # noqa: E731 — 只在这里用，写成局部函数最贴近读法
        rows.append((unwrap(cells[0]), unwrap(cells[1]), unwrap(cells[2]), cells[3]))
    return rows


# ── exchange / queue 名字 ────────────────────────────────────────────────────


def test_python_exchange_names_match_go_topology_constants() -> None:
    consts = _go_string_consts(_read(TOPOLOGY_GO))
    assert consts["ExchangeEvents"] == topology.EXCHANGE_EVENTS
    assert consts["ExchangeAITasks"] == topology.EXCHANGE_AI_TASKS
    assert consts["ExchangeAICandidate"] == topology.EXCHANGE_AI_CANDIDATES
    assert consts["ExchangeDLQ"] == topology.EXCHANGE_DLQ
    # 反过来也要齐：Go 侧新加第五个 exchange 而 Python 不知道，同样是一次静默丢消息
    assert {topology.EXCHANGE_EVENTS, topology.EXCHANGE_AI_TASKS,
            topology.EXCHANGE_AI_CANDIDATES, topology.EXCHANGE_DLQ} == {
        consts["ExchangeEvents"], consts["ExchangeAITasks"],
        consts["ExchangeAICandidate"], consts["ExchangeDLQ"],
    }


def test_python_consumed_queues_match_go_command_routes() -> None:
    """四个 purpose 队列名逐字：Python 是消费者，队列名拼错 ⇒ ``passive`` 声明当场 404。"""
    src = _read(TOPOLOGY_GO)
    block = src[src.index("var CommandRoutes"):]
    go_queues = set(re.findall(r'Queue:\s*"([^"]+)"', block[: block.index("var CandidateRoutes")]))
    assert go_queues == set(topology.CONSUMED_QUEUES), (go_queues, topology.CONSUMED_QUEUES)


def test_queue_names_match_generated_events_doc() -> None:
    """同一组名字在**生成物**里也必须一致（拓扑表 ↔ events.md ↔ Python 三方对齐）。"""
    commands = [r for r in _events_routes() if r[3] == "command"]
    assert {r[1] for r in commands} == set(ROUTING_KEY_TO_TASK_TYPE), commands
    assert {r[2] for r in commands} == set(topology.CONSUMED_QUEUES), commands
    assert {r[0] for r in commands} == {topology.EXCHANGE_AI_TASKS}, commands


# ── routing key / message_type / schema_version ─────────────────────────────


def test_command_routing_keys_match_domain_kind_values() -> None:
    """键串的唯一生成点是 ``AITaskKind.RoutingKey()``（``commands.go:43``）。

    这里比的是「中间段」的四个值：``commands.go`` 的枚举值改了而 Python 的表没改，
    命令就会投到没人绑定的键上——broker 照单 ack，消息消失，两侧测试全绿。
    """
    src = _read(COMMANDS_GO)
    kinds = set(re.findall(r'AITask(\w+)\s+AITaskKind\s*=\s*"([^"]+)"', src))
    assert {k for _, k in kinds} == set(ROUTING_KEY_TO_TASK_KIND.values()), kinds
    for kind in ("observation", "pattern", "insight", "proactive"):
        assert f"ai.{kind}.requested" in ROUTING_KEY_TO_TASK_TYPE, kind
    # message_type 的命名法必须是「键把点换成下划线」这一条规则（Go 侧字面量在此逐字比）
    assert re.search(
        r'func \(k AITaskKind\) MessageType\(\) string \{ return "ai_" \+ string\(k\) \+ "_requested" \}',
        src,
    ), (
        "commands.go 的 MessageType() 写法变了：Python 侧 parse_command 里的 "
        "ai_<kind>_requested 校验要同步改，别默默放宽这条闸"
    )
    for key, kind in ROUTING_KEY_TO_TASK_KIND.items():
        assert key.replace(".", "_") == f"ai_{kind}_requested", key


def test_task_type_names_match_proto_enum() -> None:
    """``taskType`` 的四个值取自 proto 的 ``TaskType``；改名＝契约变更，必须两边同日改。"""
    proto = (ROOT / "contracts" / "proto" / "closenai_ai.proto").read_text(encoding="utf-8")
    block = proto[proto.index("enum TaskType"):]
    values = set(re.findall(r"\b(TASK_TYPE_[A-Z_]+) = \d+;", block))
    assert values - {"TASK_TYPE_UNSPECIFIED"} == set(ROUTING_KEY_TO_TASK_TYPE.values()), values


def test_schema_version_supported_matches_go_default() -> None:
    src = _read(DELIVERY_GO)
    default = re.search(r"const outboxSchemaVersion = (\d+)", src)
    assert default, "delivery.go 里 outboxSchemaVersion 的写法变了（N7-5 的默认值是本闸的比对对象）"
    assert SUPPORTED_SCHEMA_VERSIONS == {default.group(1)}, SUPPORTED_SCHEMA_VERSIONS


def test_amqp_header_names_match_publisher_go() -> None:
    """四个 header 串必须与 Go 发布侧同名同值（读不到对方的头＝拿不到归属，只能猜）。"""
    consts = _go_string_consts(_read(PUBLISHER_GO))
    assert consts["HeaderSchemaVersion"] == HEADER_SCHEMA_VERSION
    assert consts["HeaderUserID"] == HEADER_USER_ID
    assert consts["HeaderCausationID"] == HEADER_CAUSATION_ID
    assert consts["HeaderAttemptCount"] == HEADER_ATTEMPT_COUNT
    # 载荷的内容类型也比一次：Python 的分派表认的是对面**实际**写的串（D-R4-1）
    assert consts["ContentTypeJSON"] == CONTENT_TYPE_JSON


# ── 阶梯与终态面的命名规则（消费者换层要靠它）────────────────────────────────

def test_retry_tier_naming_matches_go() -> None:
    src = _read(TOPOLOGY_GO)
    block = src[src.index("var RetryTiers"): src.index("type Route struct")]
    suffixes = re.findall(r'\{"([^"]+)",\s*[\d_]+\}', block)
    assert suffixes == list(topology.RETRY_SUFFIXES), (suffixes, topology.RETRY_SUFFIXES)
    # Go 的 TierQueueName／FinalQueueName 与 Python 的两个函数必须同一条拼装规则
    queue = "ai.pattern.detect"
    assert re.search(r'return "retry\." \+ RetryTiers\[i\]\.Suffix \+ "\." \+ queue', src)
    assert topology.tier_routing_key(queue, 0) == f"retry.30s.{queue}"
    assert topology.tier_routing_key(queue, 2) == f"retry.10m.{queue}"
    assert topology.final_routing_key(queue) == f"dlq.{queue}"
    assert re.search(r'return "dlq\." \+ queue', src)


def test_ladder_names_exist_in_events_doc() -> None:
    """换层投错键的后果是**静默丢弃**（``closenai.dlq`` 上没有绑定的键会被直接扔），
    所以这三层＋终态的名字必须在生成物里逐个在场。"""
    doc = _read(EVENTS_MD)
    for queue in topology.CONSUMED_QUEUES:
        for tier in range(len(topology.RETRY_SUFFIXES)):
            assert f"`{topology.tier_routing_key(queue, tier)}`" in doc, queue
        assert f"`{topology.final_routing_key(queue)}`" in doc, queue


# ── 候选回投面（D-R5-8）──────────────────────────────────────────────────────


def test_candidate_kinds_are_routable_on_go_declared_bindings() -> None:
    """Python 能投的五种 ``candidate.<kind>`` 必须在 Go 声明的绑定之下（显式键或通配）。"""
    src = _read(TOPOLOGY_GO)
    start = src.index("var CandidateRoutes")
    block = src[start : src.index("const (", start)]
    keys = set(re.findall(r'RoutingKey:\s*"([^"]+)"', block))
    assert {"candidate.pattern", "candidate.insight", "candidate.*"} == keys, keys
    queue = set(re.findall(r'Queue:\s*"([^"]+)"', block))
    assert queue == {"go.candidate.accept"}, queue
    for kind in CANDIDATE_KIND_TO_ENTRY:
        key = f"candidate.{kind}"
        assert key in keys or "candidate.*" in keys, key
        assert key != "candidate.*", "通配是绑定模式，不是可投递的键（publisher.go:186-188）"


def test_candidate_mapping_has_no_default_entry() -> None:
    """D-R5-8 的反面写法（「默认走 Admit」）不许出现：表里没有的 kind 必须**无映射**。"""
    assert CANDIDATE_KIND_TO_ENTRY["evidence"] == "AdmitEvidence"
    assert CANDIDATE_KIND_TO_ENTRY["proactive"] == "AdmitProactive"
    assert {CANDIDATE_KIND_TO_ENTRY[k] for k in ("fact", "pattern", "insight")} == {"Admit"}
    assert "unknown" not in CANDIDATE_KIND_TO_ENTRY
    assert "" not in CANDIDATE_KIND_TO_ENTRY


# ── D-R5-5 的派生规则 ────────────────────────────────────────────────────────


def test_candidate_message_id_is_deterministic_across_calls() -> None:
    mid = "01JZ8W2Q9K4N7RTVSBXMDAYE6C"  # ULID 形态：Go 的 OperationID 就是这种串
    first = {candidate_message_id(mid) for _ in range(1000)}
    assert len(first) == 1, f"同一命令 id 派生出 {len(first)} 个候选 id：幂等失效（18:137-144）"
    got = first.pop()
    assert got.startswith("cand-") and len(got) == 5 + 40, got
    assert re.fullmatch(r"cand-[0-9a-f]{40}", got), got


def test_candidate_message_id_differs_per_command() -> None:
    ids = {candidate_message_id(f"cmd-{i}") for i in range(200)}
    assert len(ids) == 200, "不同命令派生出同 id ⇒ 两条无关命令会被 inbox 判成重复"


def test_candidate_message_id_is_one_per_command() -> None:
    """钉住 D-R5-5 的**连带后果**：一条命令只能有一条候选。

    这是有意登记的约束而不是已完成的能力：R6 若要一次命令出多条候选，必须先给
    「同一次生成内的稳定序号」定出口径（不许拿输出顺序、不许拿 uuid），改完连同这条一起更新。
    """
    assert candidate_message_id("cmd-1") == candidate_message_id("cmd-1")
    # 同一命令的第二条候选会拿到同一个 id ⇒ Go 的 inbox 唯一键 (message_id, consumer)
    # 把第二条判成重复并静默丢掉。pipeline 用 multi_candidate_unsupported 在出网前拦住，
    # 所以这个后果必须能在契约词表里查到（变异靶 M2 摘掉派生规则时也由这里报红）。
    from backend.ai.mq.contract import REASON_MULTI_CANDIDATE

    assert REASON_MULTI_CANDIDATE == "multi_candidate_unsupported"


# ── 分层事实的另一种写法：本批不许 import 生成物 ─────────────────────────────


def test_mq_layer_does_not_import_generated_protos() -> None:
    """``backend/ai/mq/`` 今天**不** import ``backend.gen``（D-R5-4 的就地偏离留痕）。

    放开这条＝两件事必须同时做：O1 把载荷切成 protobuf，**且** ``test_ai_boundary.py``
    的生成物禁令从 ``rpc/`` 扩到 ``mq/``。只放开本函数＝留下一个没人走的 protobuf 分支。

    与那条闸同口径走 **AST** 而不是文本匹配：本包多处 docstring 就写着 ``backend.gen``
    （包括这一条），文本判据会把自己的说明当违规——那是 ``test_ai_boundary.py`` 的
    ⚠️ 段点名要躲开的误报形态。
    """
    offenders = []
    for path in sorted(MQ_DIR.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            if any(n == GEN_ROOT or n.startswith(GEN_ROOT + ".") for n in names):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], f"mq 层出现了 protobuf 生成物 import：{offenders}"


def test_mq_files_are_registered_in_the_boundary_gate() -> None:
    """新增文件必须同时进 ``test_ai_boundary.py`` 的 ``EXPECTED_FILES``。

    那条闸的判据是子集（``EXPECTED_FILES - AI_FILES``），所以「新文件没登记」不会让它红——
    边界禁令会静默少扫一个文件。这条反向比对把那一格补上。
    """
    src = _read(BOUNDARY_TEST)
    tree = ast.parse(src)
    declared = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "EXPECTED_FILES":
                    declared = {
                        e.value for e in node.value.elts if isinstance(e, ast.Constant)
                    }
    assert declared is not None, "test_ai_boundary.py 里没有 EXPECTED_FILES 了：先修那条闸再跑这里"
    on_disk = {f"mq/{p.name}" for p in sorted(MQ_DIR.glob("*.py"))}
    missing = sorted(on_disk - declared)
    assert not missing, f"mq 层新增/改名未登记到边界闸，禁令会漏扫这些文件：{missing}"


# ── R5-②：对面是 Go 的 **入站** 消费者（``internal/messaging/inbound``）────────
#
# 上面那批闸比的是「Go 声明拓扑 ⇄ Python 投字节」；这一节比的是新出现的那一半：
# Python 投出去的字节，Go 的 worker 到底能不能解、解错了怎么分道。
# 判据同源的理由只有一条：这些串一旦在两侧拼写不同，**两侧的单测都照样绿**，
# 而线上表现是候选永久落在 30s 层重投、永远进不了权威表（14 §7、18 §9）。

# Go 入站面自己发明、Python 侧没有对应物的三个码。每一条都必须有**为什么只在一侧**的说法，
# 加进来却不写理由＝这张表变成「谁都能塞一个新码」的口子。
GO_ONLY_REASONS = {
    # protojson 解不进生成物：只有 Go 会解生成物（D-R5-4，Python 的 mq 层不 import 生成物）。
    "candidate_payload_invalid",
    # metadata 语法：Go 把它**原样**写进 evidence.metadata_json，非法语法在那一侧才是契约级。
    "metadata_not_json",
    # domain.ErrUnknownSignal 的转写：枚举闸门在 Go 的领域层，Python 根本没有这个哨兵。
    "unknown_domain_value",
}


def _go_int_consts(src: str) -> dict[str, int]:
    """取 ``const ( … )`` 块里的**整型**导出常量（``_go_string_consts`` 的数值版）。"""
    return {
        m.group(1): int(m.group(2))
        for m in re.finditer(r'^\t([A-Z][A-Za-z0-9]*)\s*=\s*(\d+)\b', src, re.MULTILINE)
    }


def _python_reason_values() -> set[str]:
    """contract.py 里所有 ``REASON_*`` 的**值**。

    扫模块属性而不是逐条 import：将来 Python 新增一个码，不该要求这里先改 import 名单，
    否则「忘了加」就等于「悄悄少比一条」。"""
    return {
        value
        for name, value in vars(contract).items()
        if name.startswith("REASON_") and isinstance(value, str)
    }


def test_go_inbound_reason_codes_use_the_shared_vocabulary() -> None:
    """Go 入站面的永久拒绝码必须落在 Python 的词表里（或在这张有理由的例外表上）。

    排查时一条 ``x-reject-reason`` 要能同时指回两边的代码；同一件事两种写法，
    等于运维要维护两份「这个串是哪侧发的」记忆——那正是 R5 工作单点名的反面。
    """
    go_codes = {
        name: value
        for name, value in _go_string_consts(_read(INBOUND_DECODE_GO)).items()
        if name.startswith("Reason")
    }
    assert len(go_codes) >= 10, f"Go 的 Reason* 只解出 {len(go_codes)} 条 ⇒ 常量表写法变了"
    unexpected = {
        name: value
        for name, value in go_codes.items()
        if value not in _python_reason_values() and value not in GO_ONLY_REASONS
    }
    assert not unexpected, f"Go 入站面出现了没有对应物也没有理由的码：{unexpected}"
    # 码面本身还得是「同名的串只出现一次」：两个 Go 常量指同一个串＝留痕面上分不清谁发的
    values = list(go_codes.values())
    assert len(values) == len(set(values)), f"两个 Go 常量写了同一个码：{values}"


def test_go_inbound_wire_constants_match_python() -> None:
    """header 串、内容类型、schema 版本在 **Python ⇄ Go 发布侧 ⇄ Go 入站侧** 三方同值。

    为什么要三方而不是两方：Go 的入站包里为这几个串留了**第二份副本**（它不 import
    ``amqp``，端口与实现分家的代价），Go 自己有一条闸把副本钉回发布侧；这里钉的是
    「副本与 Python 也必须同值」，否则漂移会在中间那一格静默通过。
    """
    inbound_consts = _go_string_consts(_read(INBOUND_GO))
    publisher = _go_string_consts(_read(PUBLISHER_GO))
    for name, py_value in (
        ("HeaderSchemaVersion", HEADER_SCHEMA_VERSION),
        ("HeaderUserID", HEADER_USER_ID),
        ("HeaderCausationID", HEADER_CAUSATION_ID),
        ("HeaderAttemptCount", HEADER_ATTEMPT_COUNT),
        ("ContentTypeJSON", CONTENT_TYPE_JSON),
    ):
        assert inbound_consts[name] == py_value, name
        assert inbound_consts[name] == publisher[name], f"{name}：Go 的两份副本漂了"
    # Go 把「本消费者只认哪一版」写在解码文件里（inbound.go 只有串与阈值），两处都要比到
    decode_consts = _go_string_consts(_read(INBOUND_DECODE_GO))
    assert decode_consts["SupportedSchemaVersion"] in SUPPORTED_SCHEMA_VERSIONS
    assert len(SUPPORTED_SCHEMA_VERSIONS) == 1, "Python 认两个版本时这条判据要连规则一起改"


def test_go_inbound_process_defaults_match_python() -> None:
    """prefetch 与消息体上限两侧同值（**默认值**也是契约的一部分）。

    体上限不同值的后果最阴：发的那侧按自己的上限放行、收的那侧按更小的上限拒，
    于是每条「合法但偏大」的候选都变成永久拒绝＋一行留痕，看起来像对方的 bug。
    """
    consts = _go_int_consts(_read(INBOUND_GO))
    assert consts["DefaultMaxBodyBytes"] == config.DEFAULT_MAX_BODY_BYTES
    assert consts["DefaultPrefetch"] == config.DEFAULT_PREFETCH


def test_go_inbound_entry_table_is_the_same_closed_mapping() -> None:
    """D-R5-8 那张「routing key → 领域入口」表在两侧必须是**同一张表**（逐键比）。

    两侧各自的测试都能验证自己那张表完整——只有逐键比对才能发现「evidence 这一格
    对面接进了 Admit」。写进权威表就收不回，所以这一格是本节里最贵的一条。
    """
    src = _read(INBOUND_DECODE_GO)
    entry_consts = {
        m.group(1): m.group(2)
        for m in re.finditer(r'^\t([A-Z][A-Za-z0-9]*)\s+Entry\s*=\s*"([^"]*)"', src, re.MULTILINE)
    }
    block = src[src.index("var CandidateEntries"):]
    block = block[: block.index("\n}")]
    go_table = {
        key.removeprefix("candidate."): entry_consts[entry]
        for key, entry in re.findall(r'"(candidate\.[a-z]+)":\s*(Entry[A-Za-z]+)', block)
    }
    assert go_table == dict(CANDIDATE_KIND_TO_ENTRY), (go_table, CANDIDATE_KIND_TO_ENTRY)
    # 表里的每个入口名必须真的是 Go 那边声明的常量，而不是解析器抓到的裸串
    assert set(entry_consts.values()) >= set(go_table.values()), entry_consts


def test_go_inbound_message_type_rule_is_the_python_one() -> None:
    """``candidate.<kind>`` → ``candidate_<kind>`` 是同一条规则（点换下划线）的两次实现。"""
    assert 'strings.ReplaceAll(routingKey, ".", "_")' in _read(INBOUND_DECODE_GO)
    assert 'message_type=f"candidate_{kind}"' in _read(MQ_DIR / "contract.py")
    for kind in CANDIDATE_KIND_TO_ENTRY:
        assert f"candidate.{kind}".replace(".", "_") == f"candidate_{kind}", kind


def test_go_inbound_switch_keys_do_not_collide_with_python() -> None:
    """``CLOSEN_AMQP_URL`` 两侧**共用**（同一个 broker 只有一个地址键），
    而 worker 开关各自独立——同名不同义会让一个 ConfigMap 值同时启停两个进程。
    """
    consts = _go_string_consts(_read(INBOUND_GO))
    assert consts["EnvURL"] == config.ENV_URL, "两个进程读同一个地址键，串必须逐字相同"
    python_keys = {
        value
        for name, value in vars(config).items()
        if name.startswith("ENV_") and isinstance(value, str)
    }
    assert consts["EnvWorker"] not in python_keys, (
        f"Go 的开关键 {consts['EnvWorker']!r} 撞上 Python 的键面 {sorted(python_keys)}"
    )

"""R5 · Python 侧的 MQ 名字与 Go 侧**逐字相同**的闸（工作单 §四 R5-① 的第二条测）。

要拦的事故只有一类：**一侧改了名字**。AMQP 的 exchange／queue／routing key／header 都是
自由字符串，写错了 broker 不会告诉你——它只会把消息投给一个没人认领的队列然后静默丢弃，
两侧各自的测试还都是绿的。所以这张闸拿三个事实源交叉比：

* ``go-core/internal/messaging/amqp/topology.go``（声明侧的表与常量，D-R5-3 认定的唯一参数源）
* ``contracts/events.md``（由 ``tools/eventscontract`` 从上面那张表**生成**的契约文档）
* ``go-core/internal/domain/{commands,delivery}.go``（键串与 message_type 与 schema_version 的出生地）
* ``go-core/internal/domain/task_chain.go`` ＋ ``contracts/proto/registry.json``
  （T2-01 的来源指针两键：词表在 Go 侧、号位在生成物侧，两边都只有一份事实源）
* ``go-core/internal/domain/source_pointer.go`` ＋ ``go-core/internal/store/source_owner.go``
  （T2-03 的来源准入面：Python 的开关、Go 的白名单、Go 的回查语句，三处必须同集合）
* ``go-core/internal/messaging/inbound/source_deleted.go``
  （T2-06 事件面：四个串、体里的四个键、那条只有 Go 会给的归属码）

比对全部走文本解析，不 import Go、不跑 Go：这样在没装 go 工具链的机器上这条闸照样有效
（R3 结案时就登记过「没装 go 不许报成没跑齐」这条口径）。真跑通要靠 R5-③ 的 broker 互操作闸。
"""
from __future__ import annotations

import ast
import json
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
    REASON_SOURCE_OCCURRED_AT,
    REASON_SOURCE_REF_SHAPE,
    REASON_SOURCE_TYPE_NOT_ENABLED,
    ROUTING_KEY_TO_TASK_KIND,
    ROUTING_KEY_TO_TASK_TYPE,
    SOURCE_OCCURRED_AT_FIELD,
    SOURCE_DELETED_BODY_KEYS,
    SOURCE_REF_FIELD,
    SOURCE_REF_PATTERN,
    SOURCE_REF_TABLES,
    SUPPORTED_SCHEMA_VERSIONS,
    candidate_message_id,
    encode_source_deleted,
)
from backend.ai.prompts.observation import MAX_EXCERPT_CHARS

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOPOLOGY_GO = ROOT / "go-core" / "internal" / "messaging" / "amqp" / "topology.go"
PUBLISHER_GO = ROOT / "go-core" / "internal" / "messaging" / "amqp" / "publisher.go"
COMMANDS_GO = ROOT / "go-core" / "internal" / "domain" / "commands.go"
DELIVERY_GO = ROOT / "go-core" / "internal" / "domain" / "delivery.go"
TASK_CHAIN_GO = ROOT / "go-core" / "internal" / "domain" / "task_chain.go"
REGISTRY_JSON = ROOT / "contracts" / "proto" / "registry.json"
PROTO_CASES = ROOT / "go-core" / "testdata" / "proto_contract_cases.json"
INBOUND_GO = ROOT / "go-core" / "internal" / "messaging" / "inbound" / "inbound.go"
INBOUND_DECODE_GO = (
    ROOT / "go-core" / "internal" / "messaging" / "inbound" / "candidate_decode.go"
)
#: T2-06 事件面的四个串与体形状都住在这一份文件里（不与候选面共用映射表）。
SOURCE_DELETED_GO = (
    ROOT / "go-core" / "internal" / "messaging" / "inbound" / "source_deleted.go"
)
#: T2-03 的来源准入面三格事实源：命令面的开关（Python）、候选面的白名单（Go domain）、
#: 归属回查的 SQL（Go store）。三处只许有一处说得算，其余两处必须被钉回它。
SOURCE_POINTER_GO = ROOT / "go-core" / "internal" / "domain" / "source_pointer.go"
#: T2-04 §口径.2 的摘录上限：Go 侧那一格住在准入校验里（不是列宽，DDL 是 TEXT）。
SERVICE_EVIDENCE_GO = ROOT / "go-core" / "internal" / "domain" / "service_evidence.go"
EVIDENCE_READ_GO = ROOT / "go-core" / "internal" / "domain" / "evidence_read.go"
SOURCE_OWNER_GO = ROOT / "go-core" / "internal" / "store" / "source_owner.go"
EVENTS_MD = ROOT / "contracts" / "events.md"
MQ_DIR = ROOT / "backend" / "ai" / "mq"
AI_DIR = ROOT / "backend" / "ai"
#: T2-02 起 ``backend/ai/`` 多了两棵子树（``mq/handlers/``、``prompts/``）。
#: 子目录不是「加个文件」那么无害：``glob("*.py")`` 只扫一层，
#: 新子目录里的文件既进不了边界闸的扫描面、也进不了这里的生成物禁令，两边同时静默放行。
SCAN_SUBTREES = ("mq", "prompts")
BOUNDARY_TEST = ROOT / "backend" / "tests" / "test_ai_boundary.py"


def _scan_files() -> list[pathlib.Path]:
    """``mq/`` 与 ``prompts/`` 两棵子树里的全部 .py（排除 __pycache__）。"""
    out: list[pathlib.Path] = []
    for sub in SCAN_SUBTREES:
        root = AI_DIR / sub
        assert root.is_dir(), f"扫描面缺一棵子树：{root}"
        out.extend(p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts)
    return out


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
    for path in _scan_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            if any(n == GEN_ROOT or n.startswith(GEN_ROOT + ".") for n in names):
                offenders.append(f"{path.relative_to(AI_DIR).as_posix()}:{node.lineno}")
    assert offenders == [], f"mq/prompts 层出现了 protobuf 生成物 import：{offenders}"


def test_mq_files_are_registered_in_the_boundary_gate() -> None:
    """新增文件必须同时进 ``test_ai_boundary.py`` 的 ``EXPECTED_FILES``。

    那条闸的判据是子集（``EXPECTED_FILES - AI_FILES``），所以「新文件没登记」不会让它红——
    边界禁令会静默少扫一个文件。这条反向比对把那一格补上。

    扫描面用 ``rglob``：``mq/`` 直属那九个文件是 R5 的形态，T2-02 之后还有 ``mq/handlers/``
    和 ``prompts/`` 两棵子树；只比对一层＝新子目录整棵都不在这条反向闸的管辖内。
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
    on_disk = {
        p.relative_to(AI_DIR).as_posix() for p in _scan_files()
    }
    missing = sorted(on_disk - declared)
    assert not missing, (
        f"backend/ai/ 的 mq/prompts 子树新增或改名未登记到边界闸，"
        f"禁令会漏扫这些文件：{missing}"
    )


# ── R5-②：对面是 Go 的 **入站** 消费者（``internal/messaging/inbound``）────────
#
# 上面那批闸比的是「Go 声明拓扑 ⇄ Python 投字节」；这一节比的是新出现的那一半：
# Python 投出去的字节，Go 的 worker 到底能不能解、解错了怎么分道。
# 判据同源的理由只有一条：这些串一旦在两侧拼写不同，**两侧的单测都照样绿**，
# 而线上表现是候选永久落在 30s 层重投、永远进不了权威表（14 §7、18 §9）。

# Go 入站面自己发明、Python 侧没有对应物的那几个码。每一条都必须有**为什么只在一侧**的说法，
# 加进来却不写理由＝这张表变成「谁都能塞一个新码」的口子。
GO_ONLY_REASONS = {
    # protojson 解不进生成物：只有 Go 会解生成物（D-R5-4，Python 的 mq 层不 import 生成物）。
    "candidate_payload_invalid",
    # metadata 语法：Go 把它**原样**写进 evidence.metadata_json，非法语法在那一侧才是契约级。
    "metadata_not_json",
    # domain.ErrUnknownSignal 的转写：枚举闸门在 Go 的领域层，Python 根本没有这个哨兵。
    "unknown_domain_value",
    # 下面两条是 T2-03 的来源指针面：Python 只投候选、不回查权威库，
    # 「那一行是不是这个用户的」与「那一行还在不在」只有 Go 答得出。
    # ⚠️ 第三个来源码 `source_type_not_enabled` **故意不在这里**——它与 Python 的
    # `REASON_SOURCE_TYPE_NOT_ENABLED` 同值（同一条准入面的两端，见下面的三方闸），
    # 一次 Loki 搜索要能同时命中命令侧的拒绝与候选侧的拒绝。
    "foreign_source",
    "unknown_source_row",
    # T2-04 §口径.3（2026-10-19）：`domain.ErrCandidateShape` 的转写。它盖的六格
    # （缺必填 / evidence_type 词表外 / role 非法 / metadata 不是 JSON / 超列宽 / 摘录超上限）
    # 里只有「摘录超上限」Python 也拦——但拦在出网前（`MAX_EXCERPT_CHARS`），
    # 不是同一个码：**两侧同值反而危险**，那会让一条 Go 侧的配置错误（终局分越界）
    # 在 Loki 里被读成「模型又写长了」。
    # ⚠️ `occurred_at 晚于当下` 刻意**不挂**这个哨兵（保持瞬态上阶梯），见 §口径.3。
    "candidate_shape_invalid",
    # T2-06 事件面（`source_deleted.go:119`）：`x-user-id` 与体里自报的 `userId` 不一致。
    # 只有入站侧答得出这件事——Python 自己编码的字节两侧必然同值（同一个 uid 写两次），
    # 真不一致时它是那条越权删除尝试或一个拼错的生产者，两者都是「按头查库」绝对不许做的。
    # 反过来 Python 侧**故意没有**对应常量：生产者侧同类的形状错走
    # `source_pointer_incomplete`（指针少一半），那是另一件事。
    "source_user_id_mismatch",
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
        for src in (INBOUND_DECODE_GO, SOURCE_DELETED_GO)
        for name, value in _go_string_consts(_read(src)).items()
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


def _go_source_table_consts() -> dict[str, str]:
    """``evidence_read.go`` 里那批 ``SourceTable*`` 常量（名字 → 值）。"""
    return {
        name: value
        for name, value in _go_string_consts(_read(EVIDENCE_READ_GO)).items()
        if name.startswith("SourceTable")
    }


def _go_admissible_names() -> set[str]:
    """``domain.AdmissibleSourceTables`` 那条 slice 里的标识符。"""
    m = re.search(
        r"var AdmissibleSourceTables = \[\]string\{([^}]*)\}",
        _read(SOURCE_POINTER_GO),
    )
    assert m, (
        "source_pointer.go 里解不出 var AdmissibleSourceTables = []string{…}："
        "Go 侧写法变了，先修这条闸（否则这里静默扫空集，判据恒真）"
    )
    names = {n.strip() for n in m.group(1).split(",") if n.strip()}
    assert names, "AdmissibleSourceTables 是空 slice：准入面被清空了，扩来源之前这格本就不该空"
    return names


def _go_probe_names() -> set[str]:
    """``store.sourceOwnerQueries`` 的**键**（每行形如 ``domain.SourceTableX: `SQL` ``）。

    只取冒号左边、反引号右边的东西一概不要：把 SQL 里的大写单词（SELECT／FROM）
    当成表名，这条闸就变成了一条永远对不上的闸。
    """
    src = _read(SOURCE_OWNER_GO)
    m = re.search(r"var sourceOwnerQueries = map\[string\]string\{", src)
    assert m, "source_owner.go 里解不出 var sourceOwnerQueries = map[string]string{：写法变了先修闸"
    names = {
        one.group(1)
        for one in re.finditer(
            r"^\t(?:[a-z][a-z0-9_]*\.)?([A-Z][A-Za-z0-9]+):\s*`", src, re.MULTILINE
        )
    }
    assert names, "sourceOwnerQueries 一个键都没解出来：回查面是空的"
    return names


def _resolve_source_tables(names: set[str], consts: dict[str, str], what: str) -> set[str]:
    unknown = sorted(names - set(consts))
    assert not unknown, (
        f"{what} 写了 evidence_read.go 里不存在的标识符：{unknown} ⇒ "
        "要么改了常量名，要么白名单里混进了一个没有值的东西"
    )
    return {consts[name] for name in names}


def test_source_admission_is_one_table_across_three_places() -> None:
    """来源准入面的**三方**同值：Python 的开关 ⇄ Go 的 domain 白名单 ⇄ Go 的 store 回查面。

    为什么三方而不是两方（T2-03）：命令面「这张表本期开没开」住在 Python 的
    ``SOURCE_REF_TABLES``，候选面「这张表认不认」住在 Go 的 ``domain``，
    而归属回查的 SQL 住在 Go 的 ``internal/store``。三处任意一处漂了，
    两侧的**各自单测都还是绿的**，线上表现却是：

    * Python 开了、Go 没认 ⇒ 每条正常候选永久拒绝（``source_type_not_enabled``）；
    * Go 认了、store 没语句 ⇒ 同样永久拒绝，但读起来像「那一行不存在」，
      排障的人会去查数据而不是查接线。

    Go 自己也有一条同集合的闸（``internal/store/source_owner_test.go``）；这里重复一遍
    不是冗余：那条要 Go 工具链，这一条在只跑 pytest 的作业里也生效
    （CI 的 Go 作业没装库时会 Skip，见「取证能力缺口」台账）。
    """
    enabled = {table for table, on in SOURCE_REF_TABLES.items() if on}
    assert enabled, "SOURCE_REF_TABLES 一格都没开：那等于整条异步来源面没上线，判据无从可验"

    consts = _go_source_table_consts()
    assert consts, "evidence_read.go 里没有 SourceTable* 常量：白名单的值没处解析"

    admissible = _resolve_source_tables(
        _go_admissible_names(), consts, "domain.AdmissibleSourceTables"
    )
    assert admissible == enabled, (
        f"Go 的候选准入面={sorted(admissible)} 与 Python 启用的来源={sorted(enabled)} 不齐："
        "扩来源要三处一起改（SOURCE_REF_TABLES、AdmissibleSourceTables、sourceOwnerQueries）"
    )

    probes = _resolve_source_tables(_go_probe_names(), consts, "store.sourceOwnerQueries")
    assert probes == enabled, f"store 的归属回查面={sorted(probes)}，与准入面不齐"


def test_go_inbound_process_defaults_match_python() -> None:
    """prefetch 与消息体上限两侧同值（**默认值**也是契约的一部分）。

    体上限不同值的后果最阴：发的那侧按自己的上限放行、收的那侧按更小的上限拒，
    于是每条「合法但偏大」的候选都变成永久拒绝＋一行留痕，看起来像对方的 bug。
    """
    consts = _go_int_consts(_read(INBOUND_GO))
    assert consts["DefaultMaxBodyBytes"] == config.DEFAULT_MAX_BODY_BYTES
    assert consts["DefaultPrefetch"] == config.DEFAULT_PREFETCH


def test_excerpt_length_cap_is_the_same_number_on_both_sides() -> None:
    """T2-04 §口径.2：摘录上限是**产物面规则**，两侧各判一次但只许有一个数字。

    判错方向的代价两侧不同：Python 侧超限＝出网前丢条（少一条证据），
    Go 侧超限＝永久拒（一条候选换一次 DLQ）。同值才能让「同一条摘录」在两处
    得出同一个结论；一个 500 一个 800 时，中间那 300 字符会稳定地生产 DLQ 行。

    字面量 500 也要比：工单 §怎么做.2 写的就是 500，只比两个常量的话，
    一起改成任意数这条闸照样绿。
    """
    m = re.search(r"^const MaxExcerptChars = (\d+)$", _read(SERVICE_EVIDENCE_GO), re.MULTILINE)
    assert m, (
        "service_evidence.go 里解不出 `const MaxExcerptChars = <数字>`：Go 侧写法变了，"
        "先修这条闸（否则这里扫空集，判据恒真）"
    )
    go_value = int(m.group(1))
    assert go_value == MAX_EXCERPT_CHARS, (
        f"Go={go_value} / Python={MAX_EXCERPT_CHARS}：两侧对同一条摘录结论不同 ⇒ 中间那段稳定进 DLQ"
    )
    assert go_value == 500, "改这一格要连着改工单 T2-04 §怎么做.2 与两侧常量"


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


# ── T2-06 事件面（Python 发射 ⇄ Go 认领）──────────────────────────────────────


def test_source_deleted_wire_constants_match_go() -> None:
    """事件面四个串的逐字比对（键／type／队列／幂等名）。

    四个里最贵的是最后一个：`go.core.source` 与 `go.core.candidate` 并成一个串，
    两侧的单测都不会红，而线上表现是「删了记忆，依据回链还指着那一行」——
    Go 的 inbox 唯一键 `(message_id, consumer)` 会让候选面那句「这条 id 我吃过了」
    替事件面宣告吃过了（`source_deleted.go:40-45` 逐字写了同一件事）。
    这里比的是**值**，不是名字：Go 把常量改成 `SourceDeletedConsumer` 而 Python 改成
    `EVENT_CONSUMER` 也能对得上，反过来两边各自改名而值漂了才是要拦的那种。
    """
    consts = _go_string_consts(_read(SOURCE_DELETED_GO))
    for name, value in (
        ("SourceDeletedRoutingKey", contract.SOURCE_DELETED_ROUTING_KEY),
        ("SourceDeletedMessageType", contract.SOURCE_DELETED_MESSAGE_TYPE),
        ("SourceDeletedQueue", contract.SOURCE_DELETED_QUEUE),
        ("SourceDeletedConsumer", contract.SOURCE_DELETED_CONSUMER),
    ):
        assert consts[name] == value, f"{name}: Go={consts[name]!r} Python={value!r}"
    # 幂等名必须真的与候选面**不同**（正向证人：先把候选面那个串从对侧取出来）。
    inbound_consts = _go_string_consts(_read(INBOUND_GO))
    assert consts["SourceDeletedConsumer"] != inbound_consts["Consumer"], (
        "两个消费者共用同一个幂等名 ⇒ 删除事件会被候选面那句「已吃过」吞掉"
    )
    # 队列名与 exchange 也必须对得上拓扑（键写错＝投进没人绑定的队列，broker 直接扔）。
    routes = [r for r in _events_routes() if r[3] == "event"]
    assert [(r[1], r[2]) for r in routes] == [
        (contract.SOURCE_DELETED_ROUTING_KEY, contract.SOURCE_DELETED_QUEUE)
    ], routes
    assert routes[0][0] == topology.EXCHANGE_EVENTS


def test_source_deleted_body_keys_match_the_go_struct() -> None:
    """体里那四个键与对侧 `sourceDeletedBody` 的 json tag **双向**相等。

    为什么必须双向：对侧 `DisallowUnknownFields`（`source_deleted.go:98`）意味着
    「Python 多写一个键」与「Go 多读一个键」都是永久拒绝，而这两者都会把一次
    正常删除送进 DLQ——重投一万次也不会变成对。
    """
    src = _read(SOURCE_DELETED_GO)
    block = src[src.index("type sourceDeletedBody struct"): src.index("func DecodeEvent")]
    tags = set(re.findall(r'`json:"([A-Za-z]+)"', block))
    assert tags, "解不出 sourceDeletedBody 的 json tag：那一段的写法变了，先修这条闸"
    assert tags == set(SOURCE_DELETED_BODY_KEYS), (
        f"Go 的体字段面={sorted(tags)}，Python 的={sorted(SOURCE_DELETED_BODY_KEYS)}"
    )
    # 生产侧真正写出去的键必须是同一组（比常量更近一步：防止「常量对了、编码函数没用它」）。
    body = json.loads(encode_source_deleted(
        user_id="u-1", source_id="m-1", exchange="closenai.events", max_body_bytes=4096
    ).body)
    assert set(body) == tags, body


def test_source_deleted_entry_table_is_one_key_on_both_sides() -> None:
    """事件面的映射表只有一格，两侧都不许有默认分支。

    D-R5-8 那条纪律在候选面已经证过一次（`test_go_inbound_entry_table_is_the_same_closed_mapping`）；
    这里证的是「加第四把键而不加认领者」：Go 的 `EventEntries` 只有 `source.deleted`，
    Python 侧今天也只有一个发射函数，两边各多一个都是红。

    ⚠️ 那张表的**键写的是常量名**（`SourceDeletedRoutingKey:`）而不是字面量，
    所以这里必须先解常量再比值——用候选面那条 `"..."` 正则在这一份文件上会解出空集，
    而空集与 `{source.deleted}` 的差只会红在「少了」那一侧，读起来像「表被清空了」。
    """
    src = _read(SOURCE_DELETED_GO)
    block = src[src.index("var EventEntries"):]
    block = block[: block.index("\n}")]
    consts = _go_string_consts(src)
    entries = dict(re.findall(r"^\t([A-Za-z]+):\s*(Entry[A-Za-z]+)", block, re.MULTILINE))
    assert entries, "EventEntries 一格都没解出来：那张表的写法变了，先修这条闸"
    keys = {consts[k] for k in entries}
    assert keys == {contract.SOURCE_DELETED_ROUTING_KEY}, keys
    assert set(entries.values()) == {"EntryInvalidateSource"}, entries
    # Python 侧的准入门面：能投的来源表只有一张（ADR-003），与命令面同一张开关。
    assert contract.SOURCE_DELETED_TABLE == "memories"
    assert contract.SOURCE_REF_TABLES["memories"] is True


def test_source_deleted_message_type_rule_is_the_same_on_both_sides() -> None:
    """事件面同守「键把点换成下划线」：`source.deleted` ⇒ `source_deleted`。

    对侧把这条规则实现成 `messageTypeFor(d.RoutingKey)`（不是常量比对），
    所以 Python 写错 type 时得到的是 `message_type_mismatch` 永久拒绝。
    """
    assert contract.SOURCE_DELETED_MESSAGE_TYPE == \
        contract.SOURCE_DELETED_ROUTING_KEY.replace(".", "_")


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


# ── T2-01：来源指针两键（``sourceRef``／``sourceOccurredAt``）的词表与号位 ──────


def _go_block(src: str, start: str) -> str:
    """取 Go 源码里从 ``start`` 到下一个顶层 ``}`` 的那一段（函数体）。

    只按索引切、不写跨段正则：本文件比的是**同一张表里的那几个字面量**，
    放宽成跨段匹配会把别的函数的字面量吃进来，比对结果看着丰富实则错位。
    """
    i = src.index(start)
    return src[i : src.index("\n}", i)]


def test_source_ref_vocabulary_is_the_same_on_both_sides() -> None:
    """正则、开关表、三条 reason：Go 与 Python 必须是**同一份**口径。

    两侧各写一份就会漂，而漂的方向一定是「一边放行、另一边拒」——那种命令进了 DLQ，
    两侧单测却各自全绿。这里逐字比，且比之前先确认**取到了东西**（读空即红）。

    真解码器收不收这份字节由 ``test_go_task_command_body_accepted.py`` 判；本条只管名字面。
    """
    src = _read(TASK_CHAIN_GO)

    go_regex = re.search(r"sourceRefPattern\s*=\s*regexp\.MustCompile\(\s*\n?\s*`([^`]+)`", src)
    assert go_regex, "task_chain.go 里读不到 sourceRefPattern ⇒ 形状判据改名了，这条对账要跟着改"
    assert go_regex.group(1) == SOURCE_REF_PATTERN.pattern, (
        f"Go 的正则 {go_regex.group(1)!r} 与 Python 的 {SOURCE_REF_PATTERN.pattern!r} 不是同一条"
    )

    alt = re.search(r"^\^\(([^)]*)\):", go_regex.group(1))
    assert alt, f"Go 的正则开头不是「(表名|…):」这个形状：{go_regex.group(1)!r}"
    go_names = set(alt.group(1).split("|"))
    assert go_names == set(SOURCE_REF_TABLES), (go_names, sorted(SOURCE_REF_TABLES))

    go_enabled = re.search(r"enabledSourceTables\s*=\s*map\[string\]bool\{([^}]*)\}", src)
    assert go_enabled, "task_chain.go 里读不到 enabledSourceTables ⇒ 开关表改名了，扩来源的纪律没了"
    go_open = set(re.findall(r'"([a-z_]+)":\s*true', go_enabled.group(1)))
    py_open = {k for k, v in SOURCE_REF_TABLES.items() if v}
    assert go_open == py_open, (
        f"本期启用的来源两侧不一致：Go {sorted(go_open)}／Python {sorted(py_open)}"
        " ⇒ 同一条命令一边产得出、另一边判成 source_type_not_enabled"
    )

    reasons = dict(re.findall(r'(Reason\w+)\s*=\s*"([^"]+)"', src))
    assert reasons, "读不到 Go 侧的 reason 常量 ⇒ 词表改名了"
    python_reasons = {
        REASON_SOURCE_REF_SHAPE,
        REASON_SOURCE_TYPE_NOT_ENABLED,
        REASON_SOURCE_OCCURRED_AT,
    }
    for const, value in reasons.items():
        assert value in python_reasons, (
            f"Go 多了第 {const} 条 reason（{value!r}），Python 侧的词表里没有它 ⇒ 值班对着 DLQ 查不到根因"
        )
    assert {
        reasons.get("ReasonSourceRefShape"),
        reasons.get("ReasonSourceTypeNotEnabled"),
        reasons.get("ReasonSourceOccurredAtInvalid"),
    } == python_reasons, reasons


def test_source_ref_json_names_and_field_numbers_match_the_registry() -> None:
    """``TaskRequested`` 那两个新字段的**键名与号位**：.proto 派生的 registry 是唯一事实源。

    号位只增不复用是 15 §6 的硬约束（8–12 是 ``reserved``）：把 ``source_ref`` 写成 9
    会让新旧两版字节流互相错读——那种 bug 两侧单测都看不见，只有对照生成物能看见。
    键名一侧写 ``sourceRef``、另一侧写 ``source_ref`` 也一样静默：Go 的体是手拼 map，
    Python 只按 ``parsed.get("sourceRef")`` 取，取不到就当「没有体外来源」放行。
    """
    registry = json.loads(_read(REGISTRY_JSON))
    message = next(
        (m for m in registry["messages"] if m["name"] == "closenai.v1.TaskRequested"), None
    )
    assert message, "registry.json 里没有 closenai.v1.TaskRequested ⇒ 生成物或改名或本闸取错了名"

    by_number = {f["number"]: f for f in message["fields"]}
    reserved = set(message["reserved_numbers"])
    for number, name, json_name in (
        (13, "source_ref", "sourceRef"),
        (14, "source_occurred_at", "sourceOccurredAt"),
    ):
        field = by_number.get(number)
        assert field, f"号位 {number}（{name}）不在 TaskRequested 里 ⇒ .proto 改了没重导，或号位被挪走"
        assert (field["name"], field["json_name"]) == (name, json_name), field
        assert number not in reserved, f"{name} 占用了 reserved 号位 {number}（15 §6）"
        assert field["type"] == 9, f"{name} 不是 string（type 9）：{field}"

    assert by_number[13]["json_name"] == SOURCE_REF_FIELD
    assert by_number[14]["json_name"] == SOURCE_OCCURRED_AT_FIELD
    assert "user_id" in set(message["reserved_names"]), (
        "体内身份位没被 reserved ⇒ 「按 A 跑、记到 B 头上」少了一层结构性拦截"
    )

    go_body = _go_block(_read(TASK_CHAIN_GO), "func TaskRequestBody")
    assert f'"{SOURCE_REF_FIELD}":' in go_body, (
        "Go 的体写的键名不再是 registry 的 json_name ⇒ 对面读成缺省，来源指针静默丢失"
    )
    assert f'"{SOURCE_OCCURRED_AT_FIELD}":' in go_body, go_body

    # 夹具证人：这两个新键必须真的在**跨语言字节**上跑过一遍。只在 Go 的 map 字面量里出现、
    # 夹具却不带，等于「契约上有、真字节从没解过」——那种字段要到 T2-02 的 handler 才第一次
    # 被解码，而那时两侧的单测早已各自绿了一整轮。
    cases = json.loads(_read(PROTO_CASES))["cases"]
    task_case = next((c for c in cases if c["message"] == "closenai.v1.TaskRequested"), None)
    assert task_case, "夹具里没有 closenai.v1.TaskRequested 那条 case ⇒ 命令体的字节面没有证人"
    assert task_case["json"].get(SOURCE_REF_FIELD), (
        f"夹具里那条命令体没带 {SOURCE_REF_FIELD} ⇒ 来源指针从没经过真 proto 字节"
    )
    assert task_case["json"].get(SOURCE_OCCURRED_AT_FIELD), (
        f"夹具里那条命令体没带 {SOURCE_OCCURRED_AT_FIELD} ⇒ 同上，发生时刻只存在于 JSON 层"
    )

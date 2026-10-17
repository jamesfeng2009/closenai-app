"""批 3 · D-G14-② 的**验收面**闸：Go 现在真会产出的那条命令体，对面的真解码器读不读得通。

为什么光有 Go 侧的 `task_chain_test.go` 不算数（那条闸比的是三张表的**字面量**）：
D-G14-② 之后 `domain.Service.RequestAITask` 第一次有了生产调用点
（`go-core/internal/messaging/inbound/inbound.go` 的 `requestFollowUp`），
于是「Go 产出的字节」从**假设**变成了**现网会发出去的东西**。字面量表对得上，不等于
Python 的 `parse_command` 真会点头——它有一串带次序的判据（键 → 体 → type → taskType →
版本 → 归属 → 体内身份），任何一条把 Go 的体打掉，表现就是命令进 DLQ、
用户永远等不到那条洞察，而两侧单测全绿。所以这里**真的调用** `parse_command`，
输入全部由 Go 源码现推，不许在本文件里抄一份「我以为 Go 写成什么样」。

第三格（`...CarriesExactlyTheThreeAgreedFields`）与下面的
`...WithAnyUserIdentityFormIsRejected` 是**配对着写**的：只测「Go 没写 user_id」
会放过一个空实现（把 `TaskRequestBody` 写成 `{}` 照样绿），只测「写了就拒」
则管不住 Go 侧今天到底写没写。两条合起来才钉住那条约束。

最后一格（`TheProducerSideIsStillUnregistered`）把自己也钉住：`events.md` 的口径写的是
「命令体合法但注册表为空 ⇒ 流量仍是零」。这句话必须可判，否则下一个读台账的人
会把「入队口有了调用点」读成「异步面已通」。R6 真注册 handler 的那天，这条闸会红，
而它红正是该红——改口径的人必须连着改文档。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re

import pytest

from backend.ai.mq import registry as registry_module
from backend.ai.mq.contract import (
    CONTENT_TYPE_JSON,
    HEADER_ATTEMPT_COUNT,
    HEADER_CAUSATION_ID,
    HEADER_SCHEMA_VERSION,
    HEADER_USER_ID,
    REASON_FOREIGN_USER_ID,
    REASON_HANDLER_UNREGISTERED,
    REASON_MESSAGE_TYPE_MISMATCH,
    REASON_MISSING_USER_ID,
    REASON_TASK_TYPE_MISMATCH,
    ROUTING_KEY_TO_TASK_KIND,
    ROUTING_KEY_TO_TASK_TYPE,
    TASK_TYPE_FIELD,
    Command,
    ContractError,
    parse_command,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
TASK_CHAIN_GO = ROOT / "go-core" / "internal" / "domain" / "task_chain.go"
COMMANDS_GO = ROOT / "go-core" / "internal" / "domain" / "commands.go"
CANDIDATE_GO = ROOT / "go-core" / "internal" / "domain" / "candidate.go"
PUBLISHER_GO = ROOT / "go-core" / "internal" / "messaging" / "amqp" / "publisher.go"
INBOUND_GO = ROOT / "go-core" / "internal" / "messaging" / "inbound" / "inbound.go"
INBOUND_FOLLOWUP_TEST_GO = (
    ROOT / "go-core" / "internal" / "messaging" / "inbound" / "inbound_followup_test.go"
)

#: 与 `config.DEFAULT_MAX_BODY_BYTES` 同值；本闸只喂几十字节的体，取默认口径即可。
MAX_BODY = 64 * 1024

#: 与 `delivery.go` 的 `outboxSchemaVersion`（N7-5 默认 1）同值：publisher 把它写进 header。
GO_SCHEMA_VERSION = "1"


def _read(path: pathlib.Path) -> str:
    assert path.exists(), f"事实源文件不存在：{path}（改名或移动会让本闸静默放行，先修这条）"
    return path.read_text(encoding="utf-8")


def _block(src: str, start: str, end: str | None) -> str:
    """取 Go 源码里从 `start` 到下一个 `end`（或顶层 ``}``）的那一段。

    宁可用索引切也不写「整文件正则」：本文件比的四张表都在同一个包里，
    放宽成跨段匹配会把别的表的字面量吃进来，比对结果看着丰富实则错位。
    """
    i = src.index(start)
    j = src.index(end, i) if end else src.index("\n}", i)
    return src[i:j]


def _go_kind_values() -> dict[str, str]:
    """`AITaskXxx` → 值串（`commands.go:29-32`），键串的中间段就是它。"""
    return dict(re.findall(r'AITask(\w+)\s+AITaskKind\s*=\s*"([^"]+)"', _read(COMMANDS_GO)))


def _go_candidate_kinds() -> dict[str, str]:
    """`KindXxx` → 值串（`candidate.go:15-17` 等）；链表键侧用它换算。"""
    out = dict(re.findall(r'Kind(\w+)\s+Kind\s*=\s*"([^"]+)"', _read(CANDIDATE_GO)))
    assert out, "candidate.go 里读不到 Kind 常量 ⇒ Kind 的写法变了，本闸的比对对象没了"
    return out


def _go_task_types() -> dict[str, str]:
    """`taskTypeOf`（`task_chain.go:46-51`）：`AITask` 之后的名字 → protojson 的 TaskType。"""
    src = _read(TASK_CHAIN_GO)
    out = dict(re.findall(r'\tAITask(\w+):\s+"([^"]+)"', _block(src, "var taskTypeOf", None)))
    assert out, "task_chain.go 的 taskTypeOf 表读空 ⇒ 改名了，闸要跟着改而不是空跑"
    return out


def _go_follow_up_edges() -> dict[str, str]:
    """`followUpAfter`（`task_chain.go:38-41`）：已入库候选 kind 值 → 下一段任务名（去前缀）。"""
    src = _read(TASK_CHAIN_GO)
    pairs = re.findall(r"\tKind(\w+):\s+AITask(\w+)", _block(src, "var followUpAfter", None))
    assert pairs, "followUpAfter 读空 ⇒ D-G14-② 的链表没了或写法变了，本闸的比对对象消失"
    kinds = _go_candidate_kinds()
    return {kinds[name]: task for name, task in pairs}


def _go_body_field_names() -> tuple[str, ...]:
    """`TaskRequestBody` 写出的键名，逐字取自 Go 源码（不在本文件抄第二份）。"""
    src = _read(TASK_CHAIN_GO)
    names = re.findall(r'"([A-Za-z]+)":\s*(?:stage\.TaskType\(\)|string\(admitted\)|sourceMessageID)',
                       _block(src, "func TaskRequestBody", None))
    assert names, "TaskRequestBody 的写法变了：本闸要的是「Go 实际写了哪几个键」，空跑不算测"
    return tuple(names)


def go_routing_key(kind: str) -> str:
    """`AITaskKind.RoutingKey()`（`commands.go:43`）——先确认命名法没改，再按同一条规则拼。"""
    src = _read(COMMANDS_GO)
    assert re.search(
        r'func \(k AITaskKind\) RoutingKey\(\) string \{ return "ai\." \+ string\(k\) \+ "\.requested" \}',
        src,
    ), "commands.go 的 RoutingKey() 写法变了：Python 侧的键表与投出去的键会各写一套"
    return f"ai.{kind}.requested"


def go_message_type(kind: str) -> str:
    """`AITaskKind.MessageType()`（`commands.go:47`），同上一条：判据先钉规则，再按规则拼。"""
    src = _read(COMMANDS_GO)
    assert re.search(
        r'func \(k AITaskKind\) MessageType\(\) string \{ return "ai_" \+ string\(k\) \+ "_requested" \}',
        src,
    ), "commands.go 的 MessageType() 写法变了：parse_command 的 message_type 判据要同步改"
    return f"ai_{kind}_requested"


def go_command_body(stage_kind: str, admitted_kind: str, source_message_id: str) -> bytes:
    """Go `TaskRequestBody` 的**等价物**：同键、同值来源、同排序规则。

    Go 用 `json.Marshal(map[string]string{...})`，Go 的 map 序列化按键名升序 ⇒
    这里用 `sort_keys=True` 复现。这条等价性由 `TestTaskRequestBodyIsByteStable`
    （Go 侧）与下面的键名比对共同兜住：两侧任何一边换了写法，其中一条闸先红。
    """
    task_types = _go_task_types()
    kind_value = _go_kind_values()[stage_kind]
    fields = dict(zip(
        _go_body_field_names(),
        [task_types[stage_kind], admitted_kind, source_message_id],
        strict=True,
    ))
    return json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()


def deliver(body: bytes, *, routing_key: str, kind: str, message_id: str, user_id: str = "u-1",
            message_type: str | None = None, headers: dict | None = None) -> Command:
    """按 **publisher.go 实际发的形状**投递一条命令给真解码器。

    header 四项与 `publisher.go:129-134` 同构（版本与次数是**整数**形态，正是
    `_normalise_schema_version` 要归一的那种），`type` 属性是 `msg.MessageType`。
    """
    table = {
        HEADER_SCHEMA_VERSION: int(GO_SCHEMA_VERSION),
        HEADER_USER_ID: user_id,
        HEADER_CAUSATION_ID: "cause-1",
        HEADER_ATTEMPT_COUNT: 1,
    } | (headers or {})
    return parse_command(
        body=body,
        content_type=CONTENT_TYPE_JSON,
        routing_key=routing_key,
        message_id=message_id,
        message_type=go_message_type(kind) if message_type is None else message_type,
        headers=table,
        correlation_id=message_id,
        max_body_bytes=MAX_BODY,
    )


# ── 正例：Go 现网会发的那两条边，Python 逐条点头 ─────────────────────────────


@pytest.mark.parametrize(
    "admitted,stage",
    sorted(_go_follow_up_edges().items()),
    ids=[f"{k}->{v}" for k, v in sorted(_go_follow_up_edges().items())],
)
def test_go_followup_command_is_accepted_by_the_real_decoder(admitted: str, stage: str) -> None:
    """D-G14-② 接的两条边（fact→pattern、pattern→insight）必须能通过 `parse_command`。

    断言的是**读出来的语义**而不是「没抛异常」：`task_type` 必须等于 Go 写进体的那个值、
    `task_kind` 必须等于入队时用的那个 kind、`user_id` 必须来自 header 而不是体。
    这三格任何一格读歪，就是「命令跑通了但跑成了别的任务」。
    """
    kind_value = _go_kind_values()[stage]
    src = "cnd-src-1"
    command = deliver(
        go_command_body(stage, admitted, src),
        routing_key=go_routing_key(kind_value),
        kind=kind_value,
        message_id=derive_task_operation_id(stage, src),
    )
    assert command.task_type == _go_task_types()[stage], command
    assert command.task_kind == kind_value, command
    assert command.user_id == "u-1", command
    assert command.schema_version == GO_SCHEMA_VERSION, command
    assert ROUTING_KEY_TO_TASK_TYPE[command.routing_key] == command.task_type


def test_every_command_kind_on_the_face_is_decodable() -> None:
    """四条键全喂一遍（含 R6 会用的 observation／proactive）。

    今天只有两条边被调用，但命令面的四张队列都已声明；等 R6 接生产者时若某一侧
    名字漂了，这条先红而不是等那条队列空转。
    """
    for stage, kind_value in sorted(_go_kind_values().items()):
        command = deliver(
            go_command_body(stage, "fact", "cnd-x"),
            routing_key=go_routing_key(kind_value),
            kind=kind_value,
            message_id="task-" + "0" * 40,
        )
        assert command.routing_key == go_routing_key(kind_value)
        assert command.task_type == ROUTING_KEY_TO_TASK_TYPE[go_routing_key(kind_value)]


def test_body_carries_exactly_the_three_agreed_fields() -> None:
    """体的形状：三个键，一个不多。

    多了 `schemaVersion` ⇒ 与 header 形成第二个可能不一致的来源（`TaskRequestBody` 的
    注释就是为这个不写的）；多了任何身份键 ⇒ 被 `foreign_user_id` 永久拒绝。
    """
    assert _go_body_field_names() == (TASK_TYPE_FIELD, "sourceKind", "sourceMessageId"), (
        "Go 的 `TaskRequestBody` 实际写出的键名不再是本批约定的三键 ⇒ 两侧对「体的形状」各写一套"
    )
    parsed = json.loads(go_command_body("Insight", "pattern", "cnd-1"))
    assert set(parsed) == {TASK_TYPE_FIELD, "sourceKind", "sourceMessageId"}, parsed
    assert not any(k.lower().replace("_", "") == "userid" for k in parsed), parsed


# ── 反例：把 Go 的约束拆掉之后，对面确实会拒（否则那条约束是装饰）────────────


def test_the_wire_shape_mirror_is_publishers_own_code() -> None:
    """本文件 `deliver()` 的输入形状必须是 `publisher.go` **自己**写出来的那种。

    这条不判 Go 的对错，判的是「我这份镜像还成立吗」：`type` 属性取 outbox 的
    `message_type` 列、`x-user-id` 取 `user_id` 列、`x-schema-version` 是**整数**形态。
    对面改了写法（比如把版本改成字符串或换掉 `Type` 的来源），本文件的正例就可能
    正在测一个现网不会再发的形状——那时先红的是这条，而不是那几条永远绿的假绿。
    """
    src = _read(PUBLISHER_GO)
    assert re.search(r"Type:\s*msg\.MessageType,", src), "publisher 的 type 属性来源变了"
    assert re.search(r"HeaderUserID:\s*msg\.UserID,", src), "publisher 的 x-user-id 来源变了"
    assert re.search(r"HeaderSchemaVersion:\s*msg\.SchemaVersion,", src)
    assert re.search(r"Body:\s*\[\]byte\(msg\.Payload\),", src), "publisher 的体来源变了"


@pytest.mark.parametrize("junk", ["user_id", "userId", "UserID", "USER_ID", "userid"])
def test_body_with_any_user_identity_form_is_permanently_rejected(junk: str) -> None:
    """`TaskRequestBody` 那条「连键名都不写」的约束，对面是**真判**的。

    Python 的归一规则是 `k.lower().replace("_","")=="userid"`（`contract.py:284`），
    这里逐字复现它去构造变体：Go 侧将来若图省事把候选所属用户写进体里，
    这条命令不是「被忽略」而是被永久拒绝 ⇒ 链断在第一步。

    ⚠️ 变体面只列**那条规则真的覆盖的形态**（大小写＋下划线）。测「user-id」这种连字符
    写法会被拒是**假的**：`replace("_","")` 不动连字符，`"user-id"` 归一后是 `"user-id"`，
    判不出 userid ⇒ 今天它会被放行（本闸 2026-10-17 实测）。这条已按「发现的缺陷不顺手修」
    记进工作单的 push/契约桶，不混进 D-G14 的点名范围。
    """
    body = json.loads(go_command_body("Insight", "pattern", "cnd-1"))
    body[junk] = "u-someone-else"
    assert junk.lower().replace("_", "") == "userid", junk
    with pytest.raises(ContractError) as exc:
        deliver(
            json.dumps(body, sort_keys=True).encode(),
            routing_key=go_routing_key("insight"),
            kind="insight",
            message_id="task-" + "1" * 40,
        )
    assert exc.value.reason == REASON_FOREIGN_USER_ID, exc.value.reason


def test_key_and_task_type_mismatch_is_permanently_rejected() -> None:
    """键与体里的 `taskType` 必须同义：错配即拒，不会「按 A 的键跑了 B 的 handler」。

    构造方式是把四条边**轮换一格**——比随手配一对覆盖面广，而且每条键都当过一次靶。
    `type` 属性保持与**键**一致：现网那一列就是由同一个 `Kind` 写的（`commands.go:100-102`），
    若连 type 也一起错，先红的是 `message_type_mismatch`（判据在 `contract.py:249`，
    排在 taskType 之前），这条想证的那一格就永远测不到。
    """
    stages = sorted(_go_kind_values())
    for i, stage in enumerate(stages):
        wrong = stages[(i + 1) % len(stages)]
        wrong_value = _go_kind_values()[wrong]
        with pytest.raises(ContractError) as exc:
            deliver(
                go_command_body(stage, "pattern", "cnd-1"),  # 体写的是 stage
                routing_key=go_routing_key(wrong_value),  # 键却是 wrong
                kind=wrong_value,
                message_id="task-" + "2" * 40,
            )
        assert exc.value.reason == REASON_TASK_TYPE_MISMATCH, (stage, wrong, exc.value.reason)


def test_message_type_property_disagreeing_with_key_is_rejected() -> None:
    """`type` 属性（`publisher.go:125` 取 outbox 的 `message_type` 列）与键也必须同义。

    这条打的是**入队那一步**：`RequestAITask` 用 `req.Kind.MessageType()` 与
    `req.Kind.RoutingKey()` 写同一行，两处都来自同一个 `Kind`，所以今天不可能不一致；
    但 `inbound.requestFollowUp` 是拼 `AITaskRequest` 的，若将来把 Kind 与别的字段分开赋值，
    这里就是那条能拦住它的闸。
    """
    with pytest.raises(ContractError) as exc:
        deliver(
            go_command_body("Insight", "pattern", "cnd-1"),
            routing_key=go_routing_key("insight"),
            kind="insight",
            message_id="task-" + "3" * 40,
            message_type="ai_pattern_requested",
        )
    assert exc.value.reason == REASON_MESSAGE_TYPE_MISMATCH, exc.value.reason


def test_missing_user_header_is_permanently_rejected() -> None:
    """归属只有 header 一个来源：头空着 ⇒ 拒，不猜。

    这也是 `RequestAITask` 那条 `user_id 缺` 的域内校验（`commands.go:132`）之外
    的唯一入口：publisher 用 `msg.UserID` 写头，而 `msg.UserID` 来自 outbox 行的
    `user_id` 列——`inbound.requestFollowUp` 传的是交付自带的 `d.UserID`。
    """
    with pytest.raises(ContractError) as exc:
        deliver(
            go_command_body("Insight", "pattern", "cnd-1"),
            routing_key=go_routing_key("insight"),
            kind="insight",
            message_id="task-" + "4" * 40,
            headers={HEADER_USER_ID: ""},
        )
    assert exc.value.reason == REASON_MISSING_USER_ID, exc.value.reason


# ── 幂等 id：与 Go 测试里钉死的两条字面量互钉 ────────────────────────────────


def derive_task_operation_id(stage_const: str, source_message_id: str) -> str:
    """`DeriveTaskOperationID`（`task_chain.go:69-72`）的独立实现。

    刻意不 import Go、也不让被测函数自证：这里用 `hashlib` 另算一遍，
    再与 Go 测试文件里的字面量比（下一条测）。两侧都改的话改不成同一个东西，
    而只有「重放给出同一个 id」这件事是可判的。
    """
    src = _read(TASK_CHAIN_GO)
    body = _block(src, "func DeriveTaskOperationID", None)
    assert '"task|" + string(stage) + "|" + sourceMessageID' in body, (
        "派生串的拼法变了：命令 id 会整体换值，重放／幂等要重新取证")
    assert '[:40]' in body and '"task-"' in body, "摘要截断长度或前缀变了（45 字符落在 128 列宽里）"
    digest = hashlib.sha256(
        f"task|{_go_kind_values()[stage_const]}|{source_message_id}".encode()
    ).hexdigest()[:40]
    return "task-" + digest


def test_command_ids_match_the_literals_pinned_in_the_go_test() -> None:
    """与 `inbound_followup_test.go` 里那两条期望值互钉（它们是 `shasum` 独立算出来的）。

    取名而不是取序：`expectedInsightIDForCnd0001` 这个名字把「哪一段任务、哪个来源候选」
    都写出来了，所以 stage 与 source id 从名字现推，比按顺序 zip 更难被我写歪。
    """
    literals = re.findall(
        r'(expected(\w+)IDForCnd(\d+))\s*=\s*"([a-z0-9\-]+)"', _read(INBOUND_FOLLOWUP_TEST_GO)
    )
    assert literals, "Go 测试里读不到期望 id 字面量 ⇒ 改名了，这条互钉要跟着改而不是空跑"
    stage_names = set(_go_kind_values())
    for _, stage, digits, value in literals:
        assert stage in stage_names, (stage, sorted(stage_names))
        assert derive_task_operation_id(stage, f"cnd-{digits}") == value, stage
        assert value.startswith("task-") and len(value) == 45 <= 128, value


# ── 调用点还在，但对面没人接：口径「流量仍是零」的可判版本 ───────────────────


def test_the_producer_side_is_still_unregistered_so_traffic_is_zero() -> None:
    """命令体合法 ≠ 有人处理：注册表今天仍为空 ⇒ 每条真投出来的命令走 `handler_unregistered`。

    `contracts/events.md` 与 `topology.go` 的 Document() 写的就是这个口径。
    R6 把 handler 注册进去那天这条会红——那是**要求改文档**的红，不是故障。
    """
    reg = registry_module.Registry()
    assert len(reg) == 0, reg.registered()
    assert reg.registered() == ()
    assert registry_module.KNOWN_TASK_KINDS == tuple(sorted(ROUTING_KEY_TO_TASK_KIND.values()))
    for kind in registry_module.KNOWN_TASK_KINDS:
        with pytest.raises(ContractError) as exc:
            reg.handler_for(kind)
        assert exc.value.reason == REASON_HANDLER_UNREGISTERED, kind


def test_the_inbound_caller_is_still_wired_in_source() -> None:
    """D-G14-② 的调用点闸（Python 侧的第二证人）。

    `go-core/cmd/go-api/domain_caller_ledger_test.go` 已经数过引用，但它跑在 go 工具链上；
    这台机器没装 go 时 pytest 基线照样要能证明「调用点在」，否则批 3 的结案在 CI 里会退化成
    「今天没跑」。这里判的是源码形状：入队发生在 `call(inner, …)`（候选入库）**之后**，
    且用的是同一个事务句柄 `inner`。
    """
    src = _read(INBOUND_GO)
    assert "func (r *Runner) requestFollowUp(" in src, "requestFollowUp 这个函数已经不在了"
    # 各只许出现一次：出现两次就是「候选进门的路径上有两条链」，
    # 而 id 只由来源候选派生 ⇒ 第二条永远撞 outbox 的 UNIQUE(message_id)。
    assert src.count("call(inner, r.deps.Domain)") == 1, "候选入库的调用点形状变了"
    assert src.count("return r.requestFollowUp(inner, st, d)") == 1, (
        "D-G14-② 的入队调用点没了 ⇒ `RequestAITask` 又回到零调用方，"
        "而 inbound 自己的用例只在有 fake 域时才看得见"
    )
    i_call = src.index("call(inner, r.deps.Domain)")
    i_follow = src.index("return r.requestFollowUp(inner, st, d)")
    assert i_call < i_follow, "候选入库之前就在入队 ⇒ 命令先于决策，回滚后会有孤儿命令"

"""批 3 · D-G14-② 的**验收面**闸：Go 现在真会产出的那条命令体，对面的真解码器读不读得通。

为什么光有 Go 侧的 `task_chain_test.go` 不算数（那条闸比的是三张表的**字面量**）：
D-G14-② 之后 `domain.Service.RequestAITask` 第一次有了生产调用点
（`go-core/internal/messaging/inbound/inbound.go` 的 `requestFollowUp`），
于是「Go 产出的字节」从**假设**变成了**现网会发出去的东西**。字面量表对得上，不等于
Python 的 `parse_command` 真会点头——它有一串带次序的判据（键 → 体 → type → taskType →
版本 → 归属 → 体内身份），任何一条把 Go 的体打掉，表现就是命令进 DLQ、
用户永远等不到那条洞察，而两侧单测全绿。所以这里**真的调用** `parse_command`，
输入全部由 Go 源码现推，不许在本文件里抄一份「我以为 Go 写成什么样」。

第三格（`...CarriesExactlyTheAgreedFields`，T2-01 起是五键）与下面的
`...WithAnyUserIdentityFormIsRejected` 是**配对着写**的：只测「Go 没写 user_id」
会放过一个空实现（把 `TaskRequestBody` 写成 `{}` 照样绿），只测「写了就拒」
则管不住 Go 侧今天到底写没写。两条合起来才钉住那条约束。

T2-01 那一组（`test_go_body_with_a_memory_source_ref_*` 起四格）管的是**来源指针**：
Go 写得出来、对面读得懂、非法形状永久拒，且两侧的正则／开关表／reason 词表逐字同一份。
体是 JSON 而号位在 proto 上，所以「号位没被复用」由 `gen_proto.py --check` 与
`contracts/proto/registry.json` 那一头管，本文件不重复发明一套号位判据。

最后一格（`...ZeroTrafficForTwoMeasurableReasons`）把自己也钉住：`events.md` 的口径写的是
「命令体合法但**第一段没有发射点**、另三类没有 handler ⇒ 流量仍是零」。这句话必须可判，
否则下一个读台账的人会把「入队口有了调用点」读成「异步面已通」。
2026-10-18（T2-02）之前这一格判的是「注册表为空」，那是**刚 new 出来的对象**、
与进程接线无关，属于装饰性断言（结案时勘误，见该格的 docstring）；
现在判的是生产组合根的注册面＋Go 的链表，两边都是真事实。
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
    REASON_SOURCE_OCCURRED_AT,
    REASON_SOURCE_REF_SHAPE,
    REASON_SOURCE_TYPE_NOT_ENABLED,
    REASON_TASK_TYPE_MISMATCH,
    ROUTING_KEY_TO_TASK_KIND,
    ROUTING_KEY_TO_TASK_TYPE,
    SOURCE_OCCURRED_AT_FIELD,
    SOURCE_REF_FIELD,
    TASK_TYPE_FIELD,
    Command,
    ContractError,
    parse_command,
)
from backend.services import ai_observation

ROOT = pathlib.Path(__file__).resolve().parents[2]
TASK_CHAIN_GO = ROOT / "go-core" / "internal" / "domain" / "task_chain.go"
COMMANDS_GO = ROOT / "go-core" / "internal" / "domain" / "commands.go"
CANDIDATE_GO = ROOT / "go-core" / "internal" / "domain" / "candidate.go"
PUBLISHER_GO = ROOT / "go-core" / "internal" / "messaging" / "amqp" / "publisher.go"
INBOUND_GO = ROOT / "go-core" / "internal" / "messaging" / "inbound" / "inbound.go"
INBOUND_FOLLOWUP_TEST_GO = (
    ROOT / "go-core" / "internal" / "messaging" / "inbound" / "inbound_followup_test.go"
)
#: 由 `topology.go` 的 Document() 生成；本文件最后一格拿它做「口径↔代码」的影子证人。
EVENTS_MD = ROOT / "contracts" / "events.md"

#: 与 `config.DEFAULT_MAX_BODY_BYTES` 同值；本闸只喂几十字节的体，取默认口径即可。
MAX_BODY = 64 * 1024

#: 与 `delivery.go` 的 `outboxSchemaVersion`（N7-5 默认 1）同值：publisher 把它写进 header。
GO_SCHEMA_VERSION = "1"

#: 命令体约定的键集（批 3 的三键 ＋ T2-01 的两个来源指针键，后者**恒写**）。
AGREED_BODY_FIELDS = (
    TASK_TYPE_FIELD,
    "sourceKind",
    "sourceMessageId",
    SOURCE_REF_FIELD,
    SOURCE_OCCURRED_AT_FIELD,
)

#: 一条合法的 uuid（36 字符，正好落在 `[0-9a-f-]{36}` 上）。
SAMPLE_UUID = "0f1e2d3c-4b5a-4968-8a7b-9c0d1e2f3a4b"


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


def _go_body_fields() -> dict[str, str]:
    """`TaskRequestBody` 的键 → 值的**表达式来源**（Go 源码逐字）。"""
    src = _read(TASK_CHAIN_GO)
    pairs = re.findall(
        r'"([A-Za-z]+)":\s*([A-Za-z][A-Za-z().]*),', _block(src, "func TaskRequestBody", None)
    )
    assert pairs, "TaskRequestBody 的写法变了 ⇒ 本闸的比对对象没了"
    return dict(pairs)


#: Go 侧值表达式 → 本镜像的取值槽。表里没有的表达式＝红，不许默默少写一个键。
GO_BODY_VALUE_SOURCES: dict[str, str] = {
    "stage.TaskType()": "task_type",
    "string(admitted)": "admitted",
    "sourceMessageID": "source_message_id",
    "sourceRef": "source_ref",
    "sourceOccurredAt": "source_occurred_at",
}


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


def go_command_body(stage_kind: str, admitted_kind: str, source_message_id: str,
                    source_ref: str = "", source_occurred_at: str = "") -> bytes:
    """Go `TaskRequestBody` 的**等价物**：同键、同值来源、同排序规则。

    Go 用 `json.Marshal(map[string]string{...})`，Go 的 map 序列化按键名升序 ⇒
    这里用 `sort_keys=True` 复现。这条等价性由 `TestTaskRequestBodyIsByteStable`
    （Go 侧）与下面的键名比对共同兜住：两侧任何一边换了写法，其中一条闸先红。

    键集不是抄在本文件里的常量，而是从 Go 源码现推（`_go_body_fields`）：Go 写了一个
    本镜像认不出的值来源时，这里直接 assert 红，而不是悄悄产出一个三键的假体。
    """
    task_types = _go_task_types()
    kind_value = _go_kind_values()[stage_kind]
    values = {
        "task_type": task_types[stage_kind],
        "admitted": admitted_kind,
        "source_message_id": source_message_id,
        "source_ref": source_ref,
        "source_occurred_at": source_occurred_at,
    }
    fields: dict[str, str] = {}
    for key, expr in _go_body_fields().items():
        slot = GO_BODY_VALUE_SOURCES.get(expr)
        assert slot is not None, (
            f"Go 的 TaskRequestBody 给 {key} 写的值来源是 {expr!r}，本镜像的表不认识它"
            " ⇒ 两侧对「体的形状」已经各写一套，把这条表达式补进 GO_BODY_VALUE_SOURCES 再放行"
        )
        fields[key] = values[slot]
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


def test_body_carries_exactly_the_agreed_fields() -> None:
    """体的形状：五个键，一个不多。

    三键是批 3 的链表（`taskType`/`sourceKind`/`sourceMessageId`），两键是 T2-01 的来源指针
    （`sourceRef`/`sourceOccurredAt`，**恒写**，空串＝这一段没有体外来源）。

    多了 `schemaVersion` ⇒ 与 header 形成第二个可能不一致的来源（`TaskRequestBody` 的
    注释就是为这个不写的）；多了任何身份键 ⇒ 被 `foreign_user_id` 永久拒绝。
    """
    assert set(_go_body_fields()) == set(AGREED_BODY_FIELDS), (
        "Go 的 `TaskRequestBody` 实际写出的键名不再是本批约定的五键 ⇒ 两侧对「体的形状」各写一套"
    )
    parsed = json.loads(go_command_body("Insight", "pattern", "cnd-1"))
    assert set(parsed) == set(AGREED_BODY_FIELDS), parsed
    assert parsed[SOURCE_REF_FIELD] == "" and parsed[SOURCE_OCCURRED_AT_FIELD] == "", parsed
    assert not any(k.lower().replace("_", "") == "userid" for k in parsed), parsed


# ── T2-01：来源指针两键——Go 写得出、对面读得懂、非法就永久拒 ─────────────────


def test_go_body_with_a_memory_source_ref_is_decoded_and_echoed() -> None:
    """`observation` 那一段带上 `memories:<uuid>` 时，真解码器要**原样**读出这两个键。

    断言取的是读出来的值而不是「没抛异常」：`body` 是 handler 唯一的输入面
    （`contract.py` 的口径是「原样透传，不裁剪不补全」），这里丢一个键
    就等于 T2-02 的 handler 拿不到来源行、静默产零候选。
    """
    ref = f"memories:{SAMPLE_UUID}"
    occurred = "2026-10-18T09:00:00Z"
    command = deliver(
        go_command_body("Observation", "fact", "cnd-1", ref, occurred),
        routing_key=go_routing_key("observation"),
        kind="observation",
        message_id="task-" + "5" * 40,
    )
    assert command.body[SOURCE_REF_FIELD] == ref, command.body
    assert command.body[SOURCE_OCCURRED_AT_FIELD] == occurred, command.body
    # 第一段用的还是同一张链表外的键：`sourceKind` 在 observation 这一格没有语义，
    # 但契约不许两侧各发明一套（Go 写什么 Python 就读什么），所以这里只钉「读得到」。
    assert command.task_type == ROUTING_KEY_TO_TASK_TYPE["ai.observation.requested"]


def test_body_without_the_two_new_keys_still_decodes() -> None:
    """老形状（两键根本不写）必须照旧读通。

    Go 今天恒写空串，但体是 JSON、生产者不止 `TaskRequestBody` 一处
    （取证驱动器 `tools/r5interop` 自己拼）。把「缺键」判成非法＝把批 3 那两条边
    现网正在发的字节打成红，而那条红与 T2-01 要证的件事无关。
    """
    legacy = json.dumps(
        {
            TASK_TYPE_FIELD: ROUTING_KEY_TO_TASK_TYPE["ai.pattern.requested"],
            "sourceKind": "fact",
            "sourceMessageId": "cnd-1",
        },
        sort_keys=True,
    ).encode()
    command = deliver(
        legacy, routing_key=go_routing_key("pattern"), kind="pattern",
        message_id="task-" + "6" * 40,
    )
    assert command.body.get(SOURCE_REF_FIELD, "") == "", command.body


@pytest.mark.parametrize(
    "ref,occurred,reason",
    [
        # 表名不在四表里：形状判据先拦住（这条若放到 enabled 表去判，会出现
        # 「拼错表名报的是未启用」，值班照着开关表找半天）。
        (f"orders:{SAMPLE_UUID}", "", REASON_SOURCE_REF_SHAPE),
        (f"memories:{SAMPLE_UUID[:35]}", "", REASON_SOURCE_REF_SHAPE),
        ("memories:" + SAMPLE_UUID.upper(), "", REASON_SOURCE_REF_SHAPE),
        # 形状合法、本期没开（ADR-003：只启用 memories）。
        (f"reflections:{SAMPLE_UUID}", "", REASON_SOURCE_TYPE_NOT_ENABLED),
        (f"mood_checkins:{SAMPLE_UUID}", "", REASON_SOURCE_TYPE_NOT_ENABLED),
        (f"chat_messages:{SAMPLE_UUID}", "", REASON_SOURCE_TYPE_NOT_ENABLED),
        # 发生时刻的两种错法：没有主体、写法不是 RFC3339。
        ("", "2026-10-18T09:00:00Z", REASON_SOURCE_OCCURRED_AT),
        (f"memories:{SAMPLE_UUID}", "2026年10月18日", REASON_SOURCE_OCCURRED_AT),
    ],
    ids=["unknown_table", "uuid_short", "uuid_uppercase", "reflections_off", "mood_checkins_off",
         "chat_messages_off", "occurred_without_ref", "occurred_not_rfc3339"],
)
def test_illegal_source_ref_is_permanently_rejected(ref: str, occurred: str, reason: str) -> None:
    """非法来源指针＝永久拒绝（进 DLQ 留 reason），不是 requeue、也不是「读成空」。

    这一格是工作单 §验收点名的变异靶（「把 Python 的白名单去掉 ⇒ 非法 ref 用例红」）：
    白名单一旦摘掉，`reflections:<uuid>` 会被 handler 当成 memories 去查库——
    查到的那一行确实属于这个用户，于是**跨表串语义**的候选能安静入库。
    """
    with pytest.raises(ContractError) as exc:
        deliver(
            go_command_body("Observation", "fact", "cnd-1", ref, occurred),
            routing_key=go_routing_key("observation"),
            kind="observation",
            message_id="task-" + "7" * 40,
        )
    assert exc.value.reason == reason, (ref, occurred, exc.value.reason)


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


def test_the_producer_side_is_still_zero_traffic_for_two_measurable_reasons() -> None:
    """口径「流量仍是零」的可判版本——**T2-02（2026-10-18）之后理由换了，这条测跟着换**。

    旧写法判的是「刚 ``Registry()`` 出来的空表」。那不是生产事实：它永远为空，
    注册接线删掉一半也不会红，是一条装饰性的闸（2026-10-18 结案时勘误）。
    现在判两格，各自指着 `contracts/events.md` 口径头里的一条：

    ① Python 的**生产注册面**恰好只有 ``observation``（组合根真跑了一遍），
       ``pattern``／``insight``／``proactive`` 仍按 ``handler_unregistered`` 永久拒绝 ⇒ 口径③；
    ② Go 侧的第一段没有发射点：链表里没有任何一条边指向 ``OBSERVATION``，
       而候选入库是生产侧唯一的 ``RequestAITask`` 调用点 ⇒ 口径②。

    R6/T2-03 把发射点接上那天，②会红——那是**要求改文档**的红，不是故障。
    """
    registered = ai_observation.build_registry(registry_module.Registry()).registered()
    assert registered == ("observation",), f"生产注册面变了：{registered}（同步改 events.md 口径②③）"
    assert registry_module.KNOWN_TASK_KINDS == tuple(sorted(ROUTING_KEY_TO_TASK_KIND.values()))
    for kind in ("pattern", "insight", "proactive"):
        with pytest.raises(ContractError) as exc:
            registry_module.Registry().handler_for(kind)  # 另三类至今没人接
        assert exc.value.reason == REASON_HANDLER_UNREGISTERED, kind

    # 链表：谁入库会请谁。判据取「下一段里没有 OBSERVATION」而不是「边数是 2」——
    # 加一条指向首段的边同样意味着第一段有了发射点，那种改动就该让这条红。
    edges = _go_follow_up_edges()
    assert set(edges.values()) == {"Pattern", "Insight"}, (
        f"候选入库后请的任务变了：{edges}（先改 events.md 的②，再改这条）"
    )
    # 文档侧证人：口径②③的措辞与上面两个代码事实指着同一件事
    # （没装 go 工具链的机器上，`contract_drift_test.go` 跑不到，这条是它在 pytest 侧的影子）。
    doc = _read(EVENTS_MD)
    assert "第一段命令没有发射点" in doc and "三个原因各自独立" in doc, (
        "events.md 还在写旧口径（「注册表仍为空」）⇒ 与本文件这两格脱钩了"
    )


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

"""R5 · 命令／候选两侧的**传输契约**（纯函数：不连 broker，不 import ``backend.gen``，不查库）。

三条分层口径，缺一不可：

* **只判形状，不判领域**。本文件管的是「这一条消息能不能成为一条合法投递」——内容类型、
  字节上限、幂等键（message_id）、归属（user_id）、``task_type``↔routing key 的一致性。
  Gate 七关与 per-kind 必填列都在 Go 的 ``domain.Service.Admit`` 里；Python 复述一遍就是
  第二套口径，而第二套口径一定会漂（V3 03 §5 ``:95-108`` 逐字「Final Evidence Strength is
  calculated by domain code」⇒ 提案方连自报算分都不采信，更不会由它来定义算分前置）。
* **上下文是有界黑盒**。V3 ``12:84-97`` 逐字「Go should construct bounded context. Python
  must not assume it can retrieve the user's entire life history」＋ ``14:154-159``
  「Keep messages bounded…not by embedding arbitrary large transcripts」⇒ body 原样透传给
  handler，消费者**不许**回库里补上下文（也补不了：``backend/tests/test_ai_boundary.py``
  禁止 ``backend/ai/`` import 仓储/模型）。
* **键名用 protojson 的 lowerCamelCase**（``schemaVersion``/``taskType``/``sourceRecordIds``），
  与 R1 既有的跨语言夹具 ``go-core/testdata/proto_contract_cases.json:111-127`` 同形。
  两侧各写一套命名（一边 snake、一边 camel）就是「R1 的逐字节闸管不到的那种耦合」。

⚠️ **protobuf 分支本批不落地**（工作单 D-R5-4 的这条就地偏离，理由如下，不是偷懒）：
对面今天发的是 JSON（``go-core/internal/messaging/amqp/publisher.go:33 ContentTypeJSON``，
D-R4-1 对 ``14:84`` 的偏离）。要让 Python 认 ``application/protobuf``，就得 import
``backend.gen.closenai_*_pb2``，而 ``test_ai_boundary.py:150 test_gen_import_is_confined_to_rpc``
明确只许 ``backend/ai/rpc/`` 碰生成物。放开那条禁令＝改分层裁决，不属于 R5 的点名范围；
用 ``importlib`` 绕过去＝「换个写法绕过禁令而不是被纠正」，正是那个闸自己的 docstring
（``test_clean_module_passes_detector``）点名的反模式。所以这里如实拒绝，并把拒绝写成
**可判的 reason**：O1 真切 protobuf 时，那条分支只需要在下方 ``_decode_body`` 加一个
decoder，同时连带改那条闸——两件事都会在那一批的测试里显式出现。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

# ── 内容类型（14 §5 要求 protobuf；今天线上是 JSON，见模块 docstring）──────────

CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_PROTOBUF = "application/protobuf"

# ── AMQP headers：四个串与 publisher.go:38-42 逐字相同 ────────────────────────
# 幂等键在 **property**（message_id）不在 header（``14:75-88``）；header 只带辅助元数据。

HEADER_SCHEMA_VERSION = "x-schema-version"
HEADER_USER_ID = "x-user-id"
HEADER_CAUSATION_ID = "x-causation-id"
HEADER_ATTEMPT_COUNT = "x-attempt-count"
# 批 T-B（工作单 T-5）：异步段续链的唯一载体；缺头是合法形态（后台 cron 起源），
# 读侧宽松、写侧非空才占位（与 Go amqp/publisher.go 的 HeaderTraceID 同一取舍）。
HEADER_TRACE_ID = "x-trace-id"

#: 认识的 payload schema_version。与 Go 侧 ``domain`` 的 ``Delivery.SchemaVersion`` 同值；
#: 不认识 ⇒ 永久拒绝（``14 §7``「拒收不支持的版本」），因为版本不认识的消息 requeue
#: 多少次都还是不认识（D-R5-11）。
SUPPORTED_SCHEMA_VERSIONS: frozenset[str] = frozenset({"1"})

# ── 永久拒绝的 reason 词表（进 DLQ 时逐字写进日志，R5-④ 的 rejects_by_reason 数它）──

REASON_CONTENT_TYPE = "unsupported_content_type"
REASON_TOO_LARGE = "body_too_large"
REASON_NOT_JSON = "payload_not_json"
REASON_UNKNOWN_ROUTING_KEY = "unknown_routing_key"
REASON_TASK_TYPE_MISMATCH = "task_type_mismatch"
REASON_MESSAGE_TYPE_MISMATCH = "message_type_mismatch"
REASON_SCHEMA_VERSION = "unsupported_schema_version"
REASON_MISSING_MESSAGE_ID = "missing_message_id"
REASON_MISSING_USER_ID = "missing_user_id"
REASON_FOREIGN_USER_ID = "foreign_user_id"
REASON_UNKNOWN_CANDIDATE_KIND = "unknown_candidate_kind"
REASON_CANDIDATE_KIND_MISMATCH = "candidate_kind_mismatch"
REASON_MISSING_SAFETY = "missing_safety_status"
#: 未注册 handler（D-R5-12：R5 交管道、R6 交生产者）
REASON_HANDLER_UNREGISTERED = "handler_unregistered"
#: 一条命令出了多条候选——D-R5-5 的派生规则下它们的 message_id 会全部相同，
#: 第二条起会被 Go 的 inbox 静默判成重复。宁可在这里拒（可诊断），不放行成静默丢失。
REASON_MULTI_CANDIDATE = "multi_candidate_unsupported"
#: 候选没拿到 broker 确认（17:91-112 的反面）：这不是契约错，是**瞬时**错，走 requeue。
REASON_PUBLISH_UNCONFIRMED = "candidate_unconfirmed"
#: T2-02 新增：候选体里出现了对应 proto 消息没有的顶层键。
#: 为什么 Python 侧要自己判：对面 `inbound/candidate_decode.go:17-20` 逐字
#: 「protojson 默认**拒绝未知字段**」，多一个键＝这条提案永远解码失败，
#: 而它落在 Go 侧的码是 `candidate_payload_invalid`——同一个错在对面留下一条
#: 「Python 发的字节不对」的 DLQ 记录，排查要从权威侧倒推回能力侧。在这里判，
#: reason 直接点出是哪个键、哪张表没有这一列。
REASON_CANDIDATE_FIELD_UNKNOWN = "candidate_field_unknown"


class ContractError(Exception):
    """契约级非法＝永久拒绝（reject 不 requeue，直入 DLX；D-R5-11）。

    ``reason`` 是机器读的口径，不是错误文本：它进 DLQ 日志、进 ``rejects_by_reason`` 计数，
    所以必须来自上面那张词表，不许随手拼一句新话——两边（Python 消费者与 Go 入站）要能对着
    同一串排查。
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


# ── 命令面（Go → Python）─────────────────────────────────────────────────────

#: routing key → protojson 的 ``TaskType`` 枚举名（``contracts/proto/closenai_ai.proto:155-160``）。
#:
#: 键串来自 ``domain.AITaskKind.RoutingKey()``（``commands.go:43``，键的唯一生成点），
#: 队列名来自 ``topology.go:74-81``；本表由 ``test_ai_mq_contract.py`` 与这两处逐字比对。
#: 为什么连 task_type 也要钉住：命令体里的 ``taskType`` 与投递用的 routing key 是**同一个事实
#: 的两处写法**，任一侧写错就会把 pattern 任务派给 insight handler——那是能跑通、也进了权威表、
#: 但语义串了台的静默故障。
ROUTING_KEY_TO_TASK_TYPE: Mapping[str, str] = {
    "ai.observation.requested": "TASK_TYPE_OBSERVATION_EXTRACT",
    "ai.pattern.requested": "TASK_TYPE_PATTERN_DETECT",
    "ai.insight.requested": "TASK_TYPE_INSIGHT_DRAFT",
    "ai.proactive.requested": "TASK_TYPE_PROACTIVE_DRAFT",
}

#: 消息体里承载 ``TaskType`` 的键（protojson 形态）。
TASK_TYPE_FIELD = "taskType"
SCHEMA_VERSION_FIELD = "schemaVersion"

# ── 命令体的来源指针（T2-01）───────────────────────────────────────────────────
#
# 为什么要新加一对键而不是复用 ``sourceRecordIds``／``sources``：那两样指的是**已经投影成
# ``source_records`` 的行**，而 ``ai.observation.requested`` 要表达的是投影**之前**的那一行原文。
# 今天体里只有 ``sourceMessageId``（＝「候选入库后请下一段」），第一段没有候选可指，
# 于是「记忆 → 证据」这条链的入口在契约上就写不出来（工作单 T2-01 §为什么）。
#
# 三条口径与 Go 侧 ``internal/domain/task_chain.go`` 逐字同源，由
# ``TestSourceRefVocabularyIsTheSameOnBothSides`` 比对：
#   1. 形状＝四张表之一 + ``:`` + 36 位 uuid；
#   2. 本期只启用 ``memories``（ADR-003），其余三张**永久拒绝**（重试不会让没开的来源变开）；
#   3. ``sourceOccurredAt`` 必须跟着 ``sourceRef`` 出现——单独一个发生时刻没有归属的主体，
#      下游只能拿消费时刻兜底，而「最近 30 天」的窗口会把重放的老消息算成新近证据。

SOURCE_REF_FIELD = "sourceRef"
SOURCE_OCCURRED_AT_FIELD = "sourceOccurredAt"

SOURCE_REF_PATTERN = re.compile(r"^(memories|reflections|mood_checkins|chat_messages):[0-9a-f-]{36}$")

#: 表名 → 本期是否启用。扩来源只改这一张表，``validate_source_ref`` 的判据不动。
SOURCE_REF_TABLES: Mapping[str, bool] = {
    "memories": True,
    "reflections": False,
    "mood_checkins": False,
    "chat_messages": False,
}

REASON_SOURCE_REF_SHAPE = "invalid_source_ref"
REASON_SOURCE_TYPE_NOT_ENABLED = "source_type_not_enabled"
REASON_SOURCE_OCCURRED_AT = "invalid_source_occurred_at"


def validate_source_ref(source_ref: str, source_occurred_at: str) -> None:
    """判命令体那两个来源键；两个都空＝合法（这一段没有体外来源，仍由 ``sourceMessageId`` 承载）。

    非法一律 ``ContractError``＝永久拒绝（进 DLQ 并留 reason），不 requeue。
    """
    if not source_ref:
        if source_occurred_at:
            raise ContractError(
                REASON_SOURCE_OCCURRED_AT,
                f"带了 {SOURCE_OCCURRED_AT_FIELD}={source_occurred_at!r} 却没带 {SOURCE_REF_FIELD}"
                "：发生时刻没有归属的主体，只能拿消费时刻兜底 ⇒ 重放的老消息会被算成新近证据",
            )
        return
    if not SOURCE_REF_PATTERN.match(source_ref):
        raise ContractError(
            REASON_SOURCE_REF_SHAPE,
            f"{SOURCE_REF_FIELD}={source_ref!r} 不是一条来源指针：形状必须是 "
            f"{SOURCE_REF_PATTERN.pattern}（四表之一 + 冒号 + 36 位 uuid）",
        )
    table = source_ref.split(":", 1)[0]
    if not SOURCE_REF_TABLES.get(table, False):
        raise ContractError(
            REASON_SOURCE_TYPE_NOT_ENABLED,
            f"{SOURCE_REF_FIELD} 指向 {table!r}，本期启用的来源是 "
            f"{sorted(k for k, v in SOURCE_REF_TABLES.items() if v)}；扩来源改 SOURCE_REF_TABLES",
        )
    if source_occurred_at:
        try:
            datetime.fromisoformat(source_occurred_at)
        except ValueError as exc:
            raise ContractError(
                REASON_SOURCE_OCCURRED_AT,
                f"{SOURCE_OCCURRED_AT_FIELD}={source_occurred_at!r} 不是 RFC3339：{exc}",
            ) from exc


def split_source_ref(source_ref: str) -> tuple[str, str]:
    """把 ``memories:<uuid>`` 拆成 ``(表名, 行 id)``。

    调用前必须已经过 ``validate_source_ref``（形状＋启用面都判过）：这里不再判，
    因为判一次由 ``parse_command`` 负责，handler 拿到的是已经进过门的 ``Command.body``。
    拆出来是给两条边用的——Python 侧按它读原文（T2-02），Go 侧按它落 ``source_records``
    的 ``(source_table, source_id)``（T2-03），两侧必须是同一次切分而不是各写一遍 ``split(":")``。
    """
    table, _, row_id = source_ref.partition(":")
    return table, row_id


@dataclass(frozen=True)
class Command:
    """一条已过传输契约的命令。

    ``body`` 是**原样解码后的 dict**，本层不裁剪、不补全；handler（R6）按 task_type 取用自己的那几列。
    """

    message_id: str
    user_id: str
    routing_key: str
    task_type: str
    message_type: str
    schema_version: str
    correlation_id: str
    causation_id: str
    attempt: int
    body: Mapping[str, Any]
    body_size: int
    # ''＝命令没带 trace 头（批 T-B）；放在最末带默认值，既不动既有构造的逐字形状，
    # 也不引入「默认值在非默认值前」的 dataclass 定义期错误。
    trace_id: str = ""

    @property
    def task_kind(self) -> str:
        """``observation``/``pattern``/``insight``/``proactive``——注册表的键。"""
        return ROUTING_KEY_TO_TASK_KIND[self.routing_key]


#: routing key → ``AITaskKind`` 值串（``commands.go:29-32``），注册表用它而不是枚举名：
#: handler 的注册名要跟 Go 侧 ``RequestAITask`` 的入参面读得通。
ROUTING_KEY_TO_TASK_KIND: Mapping[str, str] = {
    "ai.observation.requested": "observation",
    "ai.pattern.requested": "pattern",
    "ai.insight.requested": "insight",
    "ai.proactive.requested": "proactive",
}


def _as_str(raw: Any) -> str:
    """header／property 在 AMQP 表里可能是 bytes、int 或缺失，一律归一成 str。"""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    if isinstance(raw, bool):  # bool 是 int 的子类，先挡掉免得写出 "True"
        return "true" if raw else "false"
    return str(raw)


def _normalise_schema_version(raw: Any, *, where: str) -> str:
    """把 ``1``／``"1"``／``b"1"`` 归一成 ``"1"``；不认识就抛永久拒绝。

    两侧写法本来就不齐（Go 的 outbox 列是整数，protojson 的 ``schemaVersion`` 是 string 字段），
    消费端必须能读两种形态，否则 D-R4-1 那次「payload 今天还是 JSON」的偏离会在 header 上
    再撞一次同型问题。
    """
    text = _as_str(raw).strip()
    if text == "":
        raise ContractError(REASON_SCHEMA_VERSION, f"{where} 缺 schema_version")
    if not text.isdigit():
        raise ContractError(
            REASON_SCHEMA_VERSION, f"{where} schema_version={text!r} 不是正整数形态"
        )
    canonical = str(int(text))  # "01" → "1"，免得同一版本因补零被判成不认识
    if canonical not in SUPPORTED_SCHEMA_VERSIONS:
        raise ContractError(
            REASON_SCHEMA_VERSION,
            f"{where} schema_version={canonical} 不在认识的面 {sorted(SUPPORTED_SCHEMA_VERSIONS)}",
        )
    return canonical


def _decode_body(body: bytes, *, content_type: str, routing_key: str, max_body_bytes: int) -> dict:
    """内容类型分派（D-R5-4）：JSON→解析，protobuf→如实拒绝，其它→reject→DLQ。"""
    if len(body) > max_body_bytes:
        raise ContractError(
            REASON_TOO_LARGE,
            f"{routing_key} 载荷 {len(body)} 字节超过上限 {max_body_bytes}"
            "（14 §9「Keep messages bounded」：超限的上下文本该由 Go 截断，不是由 Python 硬吃）",
        )
    if content_type == CONTENT_TYPE_JSON:
        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError(REASON_NOT_JSON, f"{routing_key} 不是合法 JSON：{exc}") from exc
        if not isinstance(decoded, dict):
            raise ContractError(
                REASON_NOT_JSON, f"{routing_key} 顶层不是 JSON 对象而是 {type(decoded).__name__}"
            )
        return decoded
    if content_type == CONTENT_TYPE_PROTOBUF:
        raise ContractError(
            REASON_CONTENT_TYPE,
            f"{routing_key} 是 application/protobuf；本批消费者只解 JSON，protobuf 分支属 O1"
            "（那一批要同时在 _decode_body 加 decoder 并放开 test_ai_boundary 的生成物禁令）",
        )
    raise ContractError(
        REASON_CONTENT_TYPE, f"{routing_key} 的内容类型 {content_type!r} 不在分派表里"
    )


def parse_command(
    *,
    body: bytes,
    content_type: str,
    routing_key: str,
    message_id: str,
    message_type: str = "",
    headers: Mapping[Any, Any] | None = None,
    correlation_id: str = "",
    max_body_bytes: int,
) -> Command:
    """把一条 AMQP 投递校验成 ``Command``；任何一条不合就抛 ``ContractError``。

    次序是有意的：先判**幂等键**（缺了它，重复命令会重复生效，比失败更糟），再判路由归属，
    再判内容类型与形状，最后判版本。
    """
    table = headers or {}
    mid = _as_str(message_id).strip()
    if mid == "":
        raise ContractError(
            REASON_MISSING_MESSAGE_ID,
            f"{routing_key} 的 message_id 为空：Go 的 inbox 唯一键是 (message_id, consumer)"
            "（18:91-96），空 id 等于取消消费侧幂等",
        )
    expected_task_type = ROUTING_KEY_TO_TASK_TYPE.get(routing_key)
    if expected_task_type is None:
        raise ContractError(
            REASON_UNKNOWN_ROUTING_KEY,
            f"{routing_key} 不在命令面四条里 {sorted(ROUTING_KEY_TO_TASK_TYPE)}；"
            "按 D-R5-8 的纪律，表里没有的键一律拒，不许默认落到某个 handler",
        )

    parsed = _decode_body(
        body, content_type=content_type, routing_key=routing_key, max_body_bytes=max_body_bytes
    )

    # `type` 属性（publisher.go:125 用 MessageType()）与键必须同义：两条路都写得对，
    # 只判一条的话另一条写错就是「按 A 的键投了 B 的体」。
    if message_type and message_type != f"ai_{ROUTING_KEY_TO_TASK_KIND[routing_key]}_requested":
        raise ContractError(
            REASON_MESSAGE_TYPE_MISMATCH,
            f"键 {routing_key} 对应 type ai_{ROUTING_KEY_TO_TASK_KIND[routing_key]}_requested，"
            f"实收 {message_type!r}",
        )

    got_task_type = _as_str(parsed.get(TASK_TYPE_FIELD)).strip()
    if got_task_type != expected_task_type:
        raise ContractError(
            REASON_TASK_TYPE_MISMATCH,
            f"键 {routing_key} 要求 {TASK_TYPE_FIELD}={expected_task_type}，实收 {got_task_type!r}",
        )

    header_version = _as_str(table.get(HEADER_SCHEMA_VERSION))
    body_version = _as_str(parsed.get(SCHEMA_VERSION_FIELD))
    schema_version = _normalise_schema_version(header_version or body_version, where=routing_key)
    if header_version and body_version:
        if _normalise_schema_version(body_version, where=f"{routing_key} 体内") != schema_version:
            raise ContractError(
                REASON_SCHEMA_VERSION,
                f"{routing_key} 的 header 版本 {schema_version} 与体内版本 {body_version!r} 不一致",
            )

    user_id = _as_str(table.get(HEADER_USER_ID)).strip()
    if user_id == "":
        raise ContractError(
            REASON_MISSING_USER_ID,
            f"{routing_key} 缺 {HEADER_USER_ID}：Python 不查库（12:84-97），"
            "这个头就是这条任务唯一的归属来源，空值只能靠猜",
        )
    # 体内带 user 身份的形态一律拒：归属只认 header（15:49 把 user_id 归 Envelope；
    # Candidate/TaskRequested 两侧都把 user_id 标了 reserved），而候选的属性面由
    # encode_candidate 从**命令**继承。静默吃下一个不同的体内 id ＝「按 A 用户跑、
    # 产出记到 B 头上」的跨用户通路（变异靶 M5），所以两处同时出现就当冲突拒掉。
    foreign = {k: v for k, v in parsed.items() if k.lower().replace("_", "") == "userid"}
    if foreign:
        raise ContractError(
            REASON_FOREIGN_USER_ID,
            f"{routing_key} 的 body 里出现了 {sorted(foreign)}；归属只认 header "
            f"{HEADER_USER_ID}，两处不一致时宁可拒",
        )

    # T2-01：来源指针的判据排在**体内身份之后**——一条同时写了别人 user_id 和 sourceRef 的
    # 消息，先按跨用户拒掉才是该出现在日志里的那条 reason（`foreign_user_id` 比形状错更严重）。
    validate_source_ref(
        _as_str(parsed.get(SOURCE_REF_FIELD)).strip(),
        _as_str(parsed.get(SOURCE_OCCURRED_AT_FIELD)).strip(),
    )

    attempt_raw = _as_str(table.get(HEADER_ATTEMPT_COUNT))
    attempt = int(attempt_raw) if attempt_raw.isdigit() else 0
    return Command(
        message_id=mid,
        user_id=user_id,
        routing_key=routing_key,
        task_type=expected_task_type,
        message_type=_as_str(message_type),
        schema_version=schema_version,
        correlation_id=_as_str(correlation_id),
        causation_id=_as_str(table.get(HEADER_CAUSATION_ID)),
        attempt=attempt,
        body=parsed,
        body_size=len(body),
        trace_id=_as_str(table.get(HEADER_TRACE_ID)).strip(),
    )


# ── 回投面（Python → Go）─────────────────────────────────────────────────────

#: 五种候选 → 领域入口（D-R5-8，Go 侧 ``internal/messaging/inbound`` 用同一张表）。
#: 表里没有的 ``candidate.*`` 在**两侧**都是 reject→DLQ：默认落 ``Admit`` 会把「依据」与
#: 「主动消息门」两类语义塞进错的入口，写进权威表就收不回。
CANDIDATE_KIND_TO_ENTRY: Mapping[str, str] = {
    "fact": "Admit",
    "pattern": "Admit",
    "insight": "Admit",
    "evidence": "AdmitEvidence",
    "proactive": "AdmitProactive",
}

#: 五种候选 → 契约消息（``contracts/proto/closenai_ai.proto``）。
CANDIDATE_KIND_TO_MESSAGE: Mapping[str, str] = {
    "fact": "Candidate",
    "pattern": "Candidate",
    "insight": "Candidate",
    "evidence": "EvidenceCandidate",
    "proactive": "ProactiveDraft",
}

#: 三个走 ``Candidate`` 的 kind 必须在体内自报同一件事（``Kind`` 枚举，``closenai_ai.proto:52-57``）。
CANDIDATE_KIND_TO_PROTO_KIND: Mapping[str, str] = {
    "fact": "KIND_FACT",
    "pattern": "KIND_PATTERN",
    "insight": "KIND_INSIGHT",
}

CANDIDATE_MESSAGE_FIELD = "kind"

# ── 每种候选**体里允许的顶层键**（T2-02）───────────────────────────────────────
#
# 这张表是**对面 proto 消息的字段面**抄过来的一份，由
# `backend/tests/test_ai_mq_contract.py::test_candidate_body_keys_match_the_proto_message`
# 与 `contracts/proto/registry.json` 逐键比对（少一键／多一键都红）。
# 为什么要抄一份并且对着注册表判：Python 侧不许 import 生成物
# （`test_ai_boundary.py` 的③，生成物只能出现在 `rpc/`），于是这里没有类型可依赖；
# 而没有这张表时，「体里多写了 proto 没有的键」要到对面解码失败才看得见。
#
# ⚠️ **`evidence` 这一格没有 `safety`**，这不是漏写：
# `EvidenceCandidate`（`contracts/proto/closenai_ai.proto:243-283`）的字段面里没有这一列，
# 而 Go 的解码默认拒未知字段 ⇒ 「所有候选都必须显式带 safety」这条 D-8 口径套到 evidence 上，
# 会把**每一条**正常的证据提案打成永久拒绝。safety 的语义变更（不是删除）：
# evidence 侧的判定照样在 handler 里跑（`backend/ai/mq/handlers/observation.py` 的
# `safety_service.check_text_tri`），危机/越界 ⇒ **不产候选**，判定不上线是因为契约上没有承载它的列。
CANDIDATE_BODY_KEYS: Mapping[str, frozenset[str]] = {
    "fact": frozenset(
        {
            "schemaVersion", "kind", "origin", "subjectKey", "predicate", "objectValue",
            "confidence", "detectorVersion", "evidenceIds", "patternIds",
            "windowFrom", "windowTo", "generationRunId", "generationModel", "generationVer",
            "safety",
        }
    ),
    "pattern": frozenset(
        {
            "schemaVersion", "kind", "patternType", "scope", "description", "fingerprint",
            "confidence", "detectorVersion", "evidenceIds", "patternIds",
            "windowFrom", "windowTo", "generationRunId", "generationModel", "generationVer",
            "safety",
        }
    ),
    "insight": frozenset(
        {
            "schemaVersion", "kind", "statement", "claimType",
            "confidence", "detectorVersion", "evidenceIds", "patternIds",
            "windowFrom", "windowTo", "generationRunId", "generationModel", "generationVer",
            "safety",
        }
    ),
    "evidence": frozenset(
        {
            "schemaVersion", "sourceKind", "source", "evidenceType", "role",
            "observation", "excerpt", "metadataJson", "explicitness", "specificity",
            "generationRunId", "proposedIndependentIds",
        }
    ),
    "proactive": frozenset(
        {
            "schemaVersion", "candidateId", "triggerType", "triggerSource", "message",
            "confidence", "evidenceIds", "refIds", "anchorRefType", "anchorRefId",
            "journeyEventId", "scheduledAt", "expiresAt",
            "generationRunId", "generationModel", "generationVer", "safety",
        }
    ),
}

#: D-8「跨进程后 safety 不许省略」的**适用面**＝proto 里真有这一列的那四种候选。
KINDS_CARRYING_SAFETY: frozenset[str] = frozenset(
    kind for kind, keys in CANDIDATE_BODY_KEYS.items() if "safety" in keys
)


def candidate_message_id(command_message_id: str, item_key: str = "") -> str:
    """D-R5-5：由**命令的** message_id 确定性派生候选 id。

    ``"cand-" + sha256(命令 message_id)[:40]``；不含时间、不含随机。

    为什么必须这样：``18:137-144`` 逐字「stable message IDs…**do not generate a new message ID
    on every retry**」。带上时间或随机，同一命令的两次投递就会产出两条互不相干的候选，
    Go 的 ``inbox_messages`` 唯一键 ``(message_id, consumer)``（``18:91-96``）**永远撞不上**
    ⇒ 重复生效，而 inbox 那侧看起来一切正常。

    ``item_key``（T2-02 定的「同一次生成内的稳定序号」，`registry.Proposal.item_key`）：
    一条命令出**多条**候选时，全部拿同一个 id ＝ 第二条起被 inbox 判成重复而静默丢掉
    （这正是 D-R5-5 当初把多候选拒掉的原因）。这里的规则是
    ``"-" + sha256(命令 id + "\\n" + item_key)[:8]``——序号来自**这条观测自己的内容**
    （来源行 + 类型 + 摘录摘要），既不是输出顺序（重跑时模型换一下顺序就换号，同一条观测
    会被记成两条新证据），也不是 uuid（每次生成都新 id，幂等直接失效）。
    留空＝这条候选就是这次生成的全部产出，id 与 D-R5-5 首稿**逐字相同**，
    老字节与钉住它的断言都不受影响。
    """
    mid = _as_str(command_message_id).strip()
    if mid == "":
        raise ContractError(REASON_MISSING_MESSAGE_ID, "命令 message_id 为空，派生不出候选 id")
    digest = hashlib.sha256(mid.encode("utf-8")).hexdigest()
    base = f"cand-{digest[:40]}"
    key = _as_str(item_key).strip()
    if not key:
        return base
    tail = hashlib.sha256(f"{mid}\n{key}".encode("utf-8")).hexdigest()[:8]
    return f"{base}-{tail}"


@dataclass(frozen=True)
class CandidateMessage:
    """一条待投的候选：``publish()`` 需要的字段全在这里，MQ 层不再自己拼。"""

    exchange: str
    routing_key: str
    message_id: str
    message_type: str
    content_type: str
    body: bytes
    correlation_id: str
    causation_id: str
    headers: Mapping[str, Any]

    @property
    def size(self) -> int:
        return len(self.body)


def encode_candidate(
    *,
    command: Command,
    kind: str,
    body: Mapping[str, Any],
    exchange: str,
    max_body_bytes: int,
    item_key: str = "",
) -> CandidateMessage:
    """组装一条候选（校验＋确定性派生 id＋属性面），不碰连接。

    归属字段全部**从命令继承**：user 走 header（15:49 归 Envelope），causation 指向命令的
    message_id，correlation 沿用命令的。这样「A 的命令产出落到 B 的模型」在本函数里就无法
    表达——handler 拿不到覆盖归属的入口，而不是「拿到但我们信任它不用」。
    """
    entry = CANDIDATE_KIND_TO_ENTRY.get(kind)
    if entry is None:
        raise ContractError(
            REASON_UNKNOWN_CANDIDATE_KIND,
            f"候选 kind={kind!r} 不在映射表里 {sorted(CANDIDATE_KIND_TO_ENTRY)}；"
            "按 D-R5-8，表里没有的 candidate.* 两侧都拒",
        )
    allowed = CANDIDATE_BODY_KEYS[kind]
    extra = sorted(k for k in body if k not in allowed)
    if extra:
        raise ContractError(
            REASON_CANDIDATE_FIELD_UNKNOWN,
            f"candidate.{kind} 的体里有 {extra}，而 "
            f"{CANDIDATE_KIND_TO_MESSAGE[kind]} 的字段面是 {sorted(allowed)}；"
            "对面 protojson 默认拒未知字段（candidate_decode.go:17-20）⇒ 这一条永远进 DLQ。"
            "要加列先改契约（contracts/proto）并重导，别在这里补",
        )
    proto_kind = CANDIDATE_KIND_TO_PROTO_KIND.get(kind)
    if proto_kind is not None:
        got = _as_str(body.get(CANDIDATE_MESSAGE_FIELD)).strip()
        if got != proto_kind:
            raise ContractError(
                REASON_CANDIDATE_KIND_MISMATCH,
                f"routing key candidate.{kind} 要求 {CANDIDATE_MESSAGE_FIELD}={proto_kind}，"
                f"实收 {got!r}",
            )
    # D-8：跨进程后 safety 不许省略——省略＝UNSPECIFIED＝Go 侧 fail-closed
    # （candidate.go:65 逐字「nil 视为判定缺失 → fail-closed」）。
    # 在这里拦的理由不是「Go 会拒」，而是让 Go 拒会让这条 DLQ 看起来像权威侧的错。
    # ⚠️ 适用面是 KINDS_CARRYING_SAFETY：EvidenceCandidate 契约上就没有这一列
    # （见 CANDIDATE_BODY_KEYS 上方那段），要求它＝把每条证据提案变成解码失败。
    if kind in KINDS_CARRYING_SAFETY:
        safety = body.get("safety")
        status = _as_str(
            (safety or {}).get("status") if isinstance(safety, Mapping) else safety
        ).strip()
        if status in ("", "SAFETY_STATUS_UNSPECIFIED"):
            raise ContractError(
                REASON_MISSING_SAFETY,
                f"candidate.{kind} 的 safety.status 缺失或 UNSPECIFIED；"
                "没问题要显式写 SAFETY_STATUS_OK（contracts/proto/closenai_common.proto:84-101）",
            )

    serialised = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    raw = serialised.encode("utf-8")
    if len(raw) > max_body_bytes:
        raise ContractError(
            REASON_TOO_LARGE,
            f"candidate.{kind} 序列化后 {len(raw)} 字节超过上限 {max_body_bytes}"
            "（未超限才允许出网：14 §9）",
        )

    # message_type 的命名法与命令面同一条规则（点换下划线）：
    # `ai.pattern.requested`→`ai_pattern_requested`（commands.go:47），
    # `candidate.pattern`→`candidate_pattern`。
    # 空 trace 不占位（与 Go publisher 同一条口径）：把「没有链路」印成一条空链路，
    # 对面 grep 出来的是假证人。
    headers: dict[str, Any] = {
        HEADER_SCHEMA_VERSION: command.schema_version,
        HEADER_USER_ID: command.user_id,
        HEADER_CAUSATION_ID: command.message_id,
        HEADER_ATTEMPT_COUNT: command.attempt,
    }
    if command.trace_id:
        headers[HEADER_TRACE_ID] = command.trace_id

    return CandidateMessage(
        exchange=exchange,
        routing_key=f"candidate.{kind}",
        message_id=candidate_message_id(command.message_id, item_key),
        message_type=f"candidate_{kind}",
        content_type=CONTENT_TYPE_JSON,
        body=raw,
        correlation_id=command.correlation_id or command.message_id,
        causation_id=command.message_id,
        headers=headers,
    )


# ── 事件面（Python → Go，T2-06）──────────────────────────────────────────────
#
# 方向与候选面相反：候选是「Python 产了东西请 Go 入账」，这里是「Python 那一行业务数据
# 没了，请 Go 把已经入过的账改过来」。四个串的对侧是
# ``go-core/internal/messaging/inbound/source_deleted.go``，由
# ``test_ai_mq_contract.py::test_source_deleted_wire_constants_match_go`` 逐字比对。
#
# ⚠️ ``SOURCE_DELETED_CONSUMER`` 与候选面的 ``go.core.candidate`` **必须不同**：
# Go 的 inbox 幂等键是 ``(message_id, consumer)``，并成一个串就等于让
# 「候选面已经吃过这条 id」替事件面宣告「我也吃过了」⇒ 那条删除永远不传播。
# 这一格是变异靶（M3）。
SOURCE_DELETED_ROUTING_KEY = "source.deleted"
SOURCE_DELETED_MESSAGE_TYPE = "source_deleted"
SOURCE_DELETED_QUEUE = "go.source.deleted"
SOURCE_DELETED_CONSUMER = "go.core.source"

#: 事件体的四个键＝对侧 ``sourceDeletedBody`` 的 json tag（少一键多一键都会撞上
#: ``DisallowUnknownFields``，而那一撞是永久拒绝：字节进 DLQ，账面停在翻转前）。
SOURCE_DELETED_BODY_KEYS: frozenset[str] = frozenset(
    {"schemaVersion", "userId", "sourceTable", "sourceId"}
)

#: 本期唯一启用的事件来源表＝命令面 ``SOURCE_REF_TABLES`` 里启用的那张（ADR-003）。
#: 生产侧先判它，理由与候选面同一条：让 Go 拒会把「生产者发了一张没开的表」记成
#: 权威侧的拒绝码，排查要从能力侧倒推；在这里拦，reason 直接点出该改哪张表。
SOURCE_DELETED_TABLE = "memories"

#: 只有生产侧会有的码：指针少一半。对侧的同类拒绝是 Go 的
#: ``candidate_payload_invalid``（那里要解生成物，这里不解）。
REASON_SOURCE_POINTER_INCOMPLETE = "source_pointer_incomplete"


def source_deleted_message_id(user_id: str, source_table: str, source_id: str) -> str:
    """由**指针本身**确定性派生事件 id（同 D-R5-5 的纪律：不含时间、不含随机）。

    为什么这里能用「内容哈希」而候选面要用「命令 id 哈希」：候选的重复投递是
    「同一次生成的两次重试」，必须靠同一个上游 id 归一；删除事件的唯一身份就是那一行
    （谁的、哪张表的、哪一行），同一次删除投两次本来就该判成同一件事。
    ``\n`` 分隔而不是直接拼接：``("ab","c")`` 与 ``("a","bc")`` 拼起来同串，
    那是两条不同删除共享一个幂等键的形状。
    """
    joined = "\n".join(_as_str(x).strip() for x in (user_id, source_table, source_id))
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return f"srcdel-{digest[:40]}"


def encode_source_deleted(
    *,
    user_id: str,
    source_id: str,
    exchange: str,
    max_body_bytes: int,
    source_table: str = SOURCE_DELETED_TABLE,
    trace_id: str = "",
    causation_id: str = "",
) -> CandidateMessage:
    """组装一条 ``source.deleted``（校验＋确定性 id＋属性面），不碰连接。

    归属仍然只住在 header（``x-user-id``，15:49 的 Envelope 口径），体里那个 ``userId``
    只是给 DLQ 值班留的对账痕迹——两侧同值由下面这一行钉住，而对侧
    ``source_deleted.go:119`` 拿到之后判的是「头和体不一致就永久拒绝」。
    """
    uid = _as_str(user_id).strip()
    if uid == "":
        raise ContractError(
            REASON_MISSING_USER_ID,
            "source.deleted 没有 user_id：归属只有 header 一个来源，"
            "空值等于让 Go 去猜「这条删除是谁的」，而猜错就是替别人销账",
        )
    table, row = _as_str(source_table).strip(), _as_str(source_id).strip()
    if table == "" or row == "":
        raise ContractError(
            REASON_SOURCE_POINTER_INCOMPLETE,
            f"source.deleted 的指针少一半（source_table={table!r} source_id={row!r}）："
            "指针不全就回查不到登记行，而「把它当没发生过」与「传播错了别人的账」"
            "在这条消息上长得一样",
        )
    if table != SOURCE_DELETED_TABLE:
        raise ContractError(
            REASON_SOURCE_TYPE_NOT_ENABLED,
            f"事件面的来源表是 {sorted({SOURCE_DELETED_TABLE})}（ADR-003），收到 {table!r}；"
            "扩来源要同时改命令面 SOURCE_REF_TABLES、Go 的 AdmissibleSourceTables 与这一格",
        )

    body = {
        SCHEMA_VERSION_FIELD: next(iter(SUPPORTED_SCHEMA_VERSIONS)),
        "userId": uid,
        "sourceTable": table,
        "sourceId": row,
    }
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    if len(raw) > max_body_bytes:
        raise ContractError(
            REASON_TOO_LARGE,
            f"source.deleted 序列化后 {len(raw)} 字节超过上限 {max_body_bytes}"
            "（对侧 transportGate 同一条阈值：超限它在入站侧也是永久拒绝）",
        )
    headers: dict[str, Any] = {
        HEADER_SCHEMA_VERSION: next(iter(SUPPORTED_SCHEMA_VERSIONS)),
        HEADER_USER_ID: uid,
    }
    if causation_id:
        headers[HEADER_CAUSATION_ID] = causation_id
    if trace_id:
        headers[HEADER_TRACE_ID] = trace_id
    mid = source_deleted_message_id(uid, table, row)
    return CandidateMessage(
        exchange=exchange,
        routing_key=SOURCE_DELETED_ROUTING_KEY,
        message_id=mid,
        message_type=SOURCE_DELETED_MESSAGE_TYPE,
        content_type=CONTENT_TYPE_JSON,
        body=raw,
        correlation_id=mid,
        causation_id=causation_id,
        headers=headers,
    )

"""R5 · Python AI worker 的消费面闸（工作单 §四 R5-① 的第一条测）。

不需要 broker：V3 ``17:62-74`` 那八步里除了「连接」全是**决策**，决策在
``pipeline.run`` ＋ ``CommandConsumer._execute``，两边都用假消息／假 publisher 跑。
真 broker 上的端到端归 ``test_r5_interop_broker.py``（R5-③），这条不替代它。

每条测都注明它拦的是哪个事故（对应 R5-⑤ 的变异靶 M1–M7），因为「有测试」不等于
「测试会红」：这批准出口径是**每条纪律都能被打红一次**。
"""
from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from backend.ai.mq import pipeline, topology, worker
from backend.ai.mq.config import (
    DEFAULT_PREFETCH,
    ENV_PREFETCH,
    ENV_URL,
    ENV_WORKER,
    Switch,
    SwitchError,
    load_switch,
)
from backend.ai.mq.consumer import CommandConsumer, WorkerStats, escalation_target
from backend.ai.mq.contract import (
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_PROTOBUF,
    HEADER_USER_ID,
    REASON_MISSING_SAFETY,
    REASON_MULTI_CANDIDATE,
    CandidateMessage,
    Command,
    candidate_message_id,
)
from backend.ai.mq.pipeline import ACK, REJECT, REQUEUE, TransientError
from backend.ai.mq.publisher import TopologyMissing
from backend.ai.mq.registry import Proposal, Registry, RegistryError

QUEUE = topology.QUEUE_PATTERN
MAX_BODY = 64 * 1024


# ── 夹具：假投递／假发布／假声明通道 ─────────────────────────────────────────


class FakeMessage:
    """``aio_pika.AbstractIncomingMessage`` 的消费面（只实现本层真用到的那些成员）。"""

    def __init__(
        self,
        body: dict | bytes | str | None = None,
        *,
        routing_key: str = "ai.pattern.requested",
        exchange: str = topology.EXCHANGE_AI_TASKS,
        message_id: str = "cmd-0001",
        message_type: str = "ai_pattern_requested",
        user_id: str = "u-1",
        content_type: str = CONTENT_TYPE_JSON,
        headers: dict[str, Any] | None = None,
        correlation_id: str = "corr-1",
    ) -> None:
        if isinstance(body, bytes):
            raw = body
        elif isinstance(body, str):
            raw = body.encode()
        elif body is None:
            raw = json.dumps(
                {"schemaVersion": "1", "taskType": "TASK_TYPE_PATTERN_DETECT", "sourceRecordIds": ["sr-1"]}
            ).encode()
        else:
            raw = json.dumps(body).encode()
        self.body = raw
        self.routing_key = routing_key
        self.exchange = exchange
        self.message_id = message_id
        self.type = message_type
        self.correlation_id = correlation_id
        self.content_type = content_type
        merged: dict[str, Any] = {
            "x-schema-version": "1",
            HEADER_USER_ID: user_id,
            "x-causation-id": "evt-0",
            "x-attempt-count": 0,
        }
        merged.update(headers or {})
        self.headers = merged
        self.actions: list[str] = []
        # 与 dead_letters 共用的事件流：M1/M4 的判据是**次序**而不是「有没有 ack」——
        # 改投重试层之后再 ack 是对的，publish 之前 ack 才是错的。
        self.events: list[str] = []

    async def ack(self) -> None:
        self.actions.append("ack")
        self.events.append("ack")


class FakePublisher:
    """确认式发布的替身：``publish`` 被调用＝候选已拿到 ``Basic.Ack``。"""

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.exchange = topology.EXCHANGE_AI_CANDIDATES
        self.published: list[CandidateMessage] = []
        self.fail = fail

    async def publish(self, message: CandidateMessage) -> None:
        if self.fail is not None:
            raise self.fail
        self.published.append(message)


class FakeDeadLetters:
    def __init__(self) -> None:
        self.parked: list[tuple[str, str, str]] = []

    async def park(self, message: Any, *, routing_key: str, reason: str, detail: str = "") -> None:
        self.parked.append((routing_key, reason, detail))
        message.events.append(f"park:{routing_key}")


def build(
    registry: Registry | None = None,
    *,
    publisher: FakePublisher | None = None,
    dead: FakeDeadLetters | None = None,
    stats: WorkerStats | None = None,
    max_body_bytes: int = MAX_BODY,
    task_timeout: float = 5.0,
) -> tuple[CommandConsumer, FakePublisher, FakeDeadLetters, WorkerStats]:
    reg = registry if registry is not None else Registry()
    pub = publisher if publisher is not None else FakePublisher()
    dlq = dead if dead is not None else FakeDeadLetters()
    st = stats if stats is not None else WorkerStats()
    handler = CommandConsumer(
        registry=reg,
        publisher=pub,
        dead_letters=dlq,  # type: ignore[arg-type]
        stats=st,
        max_body_bytes=max_body_bytes,
        task_timeout_seconds=task_timeout,
    )
    return handler, pub, dlq, st


async def registered_handler(kind: str = "pattern", body: dict | None = None) -> Registry:
    """注册一个只出一条候选的 handler；``body`` **原样**用，不做合并。

    刻意不 merge：合并会永远带上 ``safety``，D-8 那条「跨进程不许省略 safety」就测不到了。
    """
    reg = Registry()
    payload = {"kind": "KIND_PATTERN", "safety": {"status": "SAFETY_STATUS_OK"}}
    if body is not None:
        payload = body

    async def handler(command: Command) -> Proposal:
        return Proposal(kind=kind, body=payload)

    reg.register("pattern", handler)
    return reg


# ── 八步的顺序（M1 靶：未 confirm 就 ACK）──────────────────────────────────


async def test_eight_steps_publish_before_ack() -> None:
    """``17:62-74`` 的第 7 步（publish candidate）必须严格早于第 8 步（ACK 原命令）。"""
    order: list[str] = []
    reg = Registry()

    async def handler(command: Command) -> Proposal:
        order.append("execute")
        return Proposal(kind="pattern", body={"kind": "KIND_PATTERN", "safety": {"status": "SAFETY_STATUS_OK"}})

    reg.register("pattern", handler)
    pub = FakePublisher()
    original = pub.publish

    async def tracked(message: CandidateMessage) -> None:
        await original(message)
        order.append("publish-confirmed")

    pub.publish = tracked  # type: ignore[method-assign]
    handler_obj, _, _, stats = build(reg, publisher=pub)

    message = FakeMessage()
    handler_obj_inner = handler_obj

    async def ack_tracked() -> None:
        order.append("ack")
        message.actions.append("ack")

    message.ack = ack_tracked  # type: ignore[method-assign]

    decision = await handler_obj_inner.handle(message, queue=QUEUE)
    assert order == ["execute", "publish-confirmed", "ack"], order
    assert decision.action == ACK and decision.reason == pipeline.REASON_PUBLISHED
    assert decision.candidate is not None and decision.candidate.message_id == candidate_message_id("cmd-0001")
    assert stats.snapshot() == {
        "consumed": 1,
        "produced": 1,
        "acked": 1,
        "requeued": 0,
        "rejected": 0,
        "parked": 0,
        "redials": 0,
        "rejects_by_reason": {},
    }


async def test_no_ack_when_broker_nacks_the_candidate() -> None:
    """M1：候选没拿到确认 ⇒ 原命令**不许** ACK（``17:91-112``），并走重试阶梯第一层。"""
    reg = await registered_handler()
    pub = FakePublisher(fail=RuntimeError("DeliveryError: broker nack"))
    handler, _, dlq, stats = build(reg, publisher=pub)
    message = FakeMessage()

    decision = await handler.handle(message, queue=QUEUE)
    assert decision.action == REQUEUE
    assert decision.reason == "candidate_unconfirmed"
    assert stats.produced == 0, "发布失败却记了产出一条候选＝假账"
    key, reason, detail = dlq.parked[0]
    assert (key, reason) == ("retry.30s.ai.pattern.detect", "candidate_unconfirmed→retry.30s")
    assert "broker nack" in detail, "死信副本要带上对面为什么没确认，否则 DLQ 里只有无头案"
    # 原件不许被直接 ack 掉：先有 retry 层的副本，再有 ack（一条消息只走一层）
    assert message.events == ["park:retry.30s.ai.pattern.detect", "ack"], message.events
    assert stats.requeued == 1 and stats.acked == 0


async def test_candidate_body_carries_command_inherited_identity() -> None:
    """归属只能从命令继承：候选的 id／causation／user 三样都由命令派生（D-R5-5＋15:49）。"""
    reg = await registered_handler()
    pub = FakePublisher()
    handler, _, _, _ = build(reg, publisher=pub)
    await handler.handle(FakeMessage(message_id="cmd-42", user_id="u-9"), queue=QUEUE)

    assert len(pub.published) == 1
    message = pub.published[0]
    assert message.message_id == candidate_message_id("cmd-42")
    assert message.routing_key == "candidate.pattern"
    assert message.message_type == "candidate_pattern"
    assert message.headers["x-causation-id"] == "cmd-42"
    assert message.headers[HEADER_USER_ID] == "u-9"
    assert message.content_type == CONTENT_TYPE_JSON


async def test_candidate_id_stable_across_two_deliveries_of_same_command() -> None:
    """同一命令投两次 ⇒ 候选 id 相同 ⇒ Go 的 inbox 唯一键会拒第二次（幂等在那一侧收口）。"""
    reg = await registered_handler()
    pub = FakePublisher()
    handler, _, _, _ = build(reg, publisher=pub)
    for _ in range(2):
        await handler.handle(FakeMessage(message_id="cmd-dup"), queue=QUEUE)
    assert [m.message_id for m in pub.published] == [
        candidate_message_id("cmd-dup"),
        candidate_message_id("cmd-dup"),
    ]


# ── 契约级非法 ⇒ reject 不 requeue（M4 靶）──────────────────────────────────


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"body": b"{not json"}, "payload_not_json"),
        ({"body": b"[1,2]"}, "payload_not_json"),
        ({"body": {"taskType": "TASK_TYPE_OBSERVATION_EXTRACT"}}, "task_type_mismatch"),
        ({"body": {"schemaVersion": "9", "taskType": "TASK_TYPE_PATTERN_DETECT"}}, "unsupported_schema_version"),
        ({"routing_key": "ai.unknown.requested"}, "unknown_routing_key"),
        ({"message_id": ""}, "missing_message_id"),
        ({"user_id": ""}, "missing_user_id"),
        ({"content_type": "text/plain"}, "unsupported_content_type"),
        ({"content_type": CONTENT_TYPE_PROTOBUF}, "unsupported_content_type"),
        ({"exchange": topology.EXCHANGE_EVENTS}, "unknown_routing_key"),
        ({"body": {"taskType": "TASK_TYPE_PATTERN_DETECT", "userId": "u-2"}}, "foreign_user_id"),
        ({"body": {"taskType": "TASK_TYPE_PATTERN_DETECT", "user_id": "u-2"}}, "foreign_user_id"),
    ],
)
async def test_illegal_command_is_parked_in_final_dlq(kwargs: dict, reason: str) -> None:
    """毒消息不许 requeue（D-R5-11）：跑阶梯就是让它无限撞库撞 LLM。"""
    reg = await registered_handler()
    pub = FakePublisher()
    handler, _, dlq, stats = build(reg, publisher=pub)
    message = FakeMessage(**kwargs)

    decision = await handler.handle(message, queue=QUEUE)
    assert decision.action == REJECT and decision.reason == reason, decision
    assert dlq.parked and dlq.parked[0][0] == topology.final_routing_key(QUEUE), dlq.parked
    assert dlq.parked[0][1] == reason
    assert message.actions == ["ack"], "改投终态后必须 ack 原件：一条消息只走一层"
    assert pub.published == [], "非法命令不许产出候选"
    assert stats.rejected == 1 and stats.rejects_by_reason == {reason: 1}


async def test_oversized_body_is_rejected_not_requeued() -> None:
    reg = await registered_handler()
    handler, _, dlq, _ = build(reg, max_body_bytes=32)
    big = FakeMessage(body={"taskType": "TASK_TYPE_PATTERN_DETECT", "sources": [{"a": "b" * 200}]})

    decision = await handler.handle(big, queue=QUEUE)
    assert decision.action == REJECT and decision.reason == "body_too_large"
    assert dlq.parked[0][0] == topology.final_routing_key(QUEUE)


async def test_protobuf_branch_names_the_batch_that_owns_it() -> None:
    """protobuf 载荷的拒绝理由必须**指向批次**，否则下一个人只会当成对面写错了。"""
    reg = await registered_handler()
    handler, _, _, _ = build(reg)
    decision = await handler.handle(
        FakeMessage(content_type=CONTENT_TYPE_PROTOBUF, body=b"\x08\x01"), queue=QUEUE
    )
    assert decision.action == REJECT and decision.reason == "unsupported_content_type"
    assert "O1" in decision.detail, decision.detail


# ── D-R5-12：注册表面（R5 交管道、R6 交生产者）──────────────────────────────


async def test_unregistered_task_type_is_permanent_not_backlog() -> None:
    """本批交付空注册表 ⇒ 任何命令都进终态 DLQ，日志点出归属，不留静默积压。"""
    handler, _, dlq, stats = build(Registry())
    decision = await handler.handle(FakeMessage(), queue=QUEUE)
    assert decision.action == REJECT and decision.reason == "handler_unregistered"
    assert "R5 交管道、R6 交生产者" in decision.detail
    assert dlq.parked[0][0] == topology.final_routing_key(QUEUE)
    assert stats.rejected == 1


def test_registry_rejects_unknown_and_duplicate() -> None:
    reg = Registry()

    async def handler(command: Command) -> None:
        return None

    with pytest.raises(Exception, match="未知任务种类"):
        reg.register("patternn", handler)  # 拼错的名字＝那条键永远不命中，最难查的哑故障
    reg.register("pattern", handler)
    with pytest.raises(RegistryError, match="已注册"):
        reg.register("pattern", handler)
    assert reg.registered() == ("pattern",) and len(reg) == 1


async def test_zero_candidates_acks_without_publishing() -> None:
    """「这次没的可提案」是合法答案：ACK 而不是 requeue，否则空答案会把队列堵死。"""
    reg = Registry()

    async def handler(command: Command) -> None:
        return None

    reg.register("pattern", handler)
    pub = FakePublisher()
    handler_obj, _, dlq, stats = build(reg, publisher=pub)
    decision = await handler_obj.handle(FakeMessage(), queue=QUEUE)
    assert decision.action == ACK and decision.reason == "no_candidate"
    assert pub.published == [] and dlq.parked == [] and stats.acked == 1


async def test_multiple_candidates_are_rejected_before_publish() -> None:
    """M2 的另一半：D-R5-5 的 id 派生不含序号 ⇒ 多条候选必然撞 inbox，出网前就拦。"""
    reg = Registry()

    async def handler(command: Command) -> list[Proposal]:
        return [
            Proposal(kind="pattern", body={"kind": "KIND_PATTERN", "safety": {"status": "SAFETY_STATUS_OK"}}),
            Proposal(kind="insight", body={"kind": "KIND_INSIGHT", "safety": {"status": "SAFETY_STATUS_OK"}}),
        ]

    reg.register("pattern", handler)
    pub = FakePublisher()
    handler_obj, _, dlq, _ = build(reg, publisher=pub)
    decision = await handler_obj.handle(FakeMessage(), queue=QUEUE)
    assert decision.action == REJECT and decision.reason == REASON_MULTI_CANDIDATE
    assert pub.published == [], "两条同 id 的候选出去后第二条会被静默判成重复"
    assert dlq.parked[0][0] == topology.final_routing_key(QUEUE)


async def test_missing_safety_status_is_rejected() -> None:
    """D-8：跨进程后 safety 不许省略（省略＝UNSPECIFIED＝对面 fail-closed）。"""
    for body in (
        {"kind": "KIND_PATTERN"},
        {"kind": "KIND_PATTERN", "safety": {"status": "SAFETY_STATUS_UNSPECIFIED"}},
    ):
        reg = await registered_handler(body=body)
        pub = FakePublisher()
        handler, _, dlq, _ = build(reg, publisher=pub)
        decision = await handler.handle(FakeMessage(), queue=QUEUE)
        assert decision.action == REJECT and decision.reason == REASON_MISSING_SAFETY
        assert pub.published == [] and dlq.parked[0][0] == topology.final_routing_key(QUEUE)


async def test_kind_mismatch_between_key_and_body_is_rejected() -> None:
    """M3（Python 侧那一半）：投 ``candidate.insight`` 却自报 ``KIND_PATTERN`` ⇒ 领域语义串台。"""
    reg = await registered_handler(kind="insight", body={"kind": "KIND_PATTERN", "statement": "x"})
    pub = FakePublisher()
    handler, _, _, _ = build(reg, publisher=pub)
    decision = await handler.handle(FakeMessage(), queue=QUEUE)
    assert decision.action == REJECT and decision.reason == "candidate_kind_mismatch"
    assert pub.published == []


# ── 瞬时失败 ⇒ requeue 走阶梯（M5/M6 靶的邻居）──────────────────────────────


async def test_transient_handler_error_requeues_to_first_tier() -> None:
    reg = Registry()

    async def handler(command: Command) -> None:
        raise TransientError("llm timeout")

    reg.register("pattern", handler)
    handler_obj, _, dlq, stats = build(reg)
    decision = await handler_obj.handle(FakeMessage(), queue=QUEUE)
    assert decision.action == REQUEUE and decision.reason == "handler_transient"
    assert dlq.parked[0][0] == "retry.30s.ai.pattern.detect"
    assert stats.requeued == 1 and stats.rejected == 0


async def test_buggy_handler_requeues_then_ladder_escalates() -> None:
    """未知异常按瞬时（让阶梯去收敛），且**按 x-death 换层**，不是一层循环。"""
    reg = Registry()

    async def handler(command: Command) -> None:
        raise ValueError("bug")

    reg.register("pattern", handler)
    handler_obj, _, dlq, _ = build(reg)
    seen: list[str] = []
    for tier in range(4):
        deaths = [
            {"queue": f"retry.{suffix}.ai.pattern.detect".encode(), "reason": "expired"}
            for suffix in topology.RETRY_SUFFIXES[:tier]
        ]
        message = FakeMessage(headers={"x-death": deaths} if deaths else {})
        decision = await handler_obj.handle(message, queue=QUEUE)
        assert decision.action == REQUEUE and decision.reason == "handler_failed"
        seen.append(dlq.parked[-1][0])
    assert seen == [
        "retry.30s.ai.pattern.detect",
        "retry.2m.ai.pattern.detect",
        "retry.10m.ai.pattern.detect",
        "dlq.ai.pattern.detect",
    ], "阶梯不升级＝毒 bug 在 30s 层无限循环；升过三层还不进终态＝永远堵着"


def test_escalation_target_pure_rule() -> None:
    assert escalation_target(QUEUE, None) == ("retry.30s.ai.pattern.detect", "retry.30s")
    assert escalation_target(QUEUE, {"x-death": [{"queue": "retry.30s.ai.pattern.detect"}]})[0] == (
        "retry.2m.ai.pattern.detect"
    )
    assert escalation_target(QUEUE, {"x-death": "不是列表"})[0] == "retry.30s.ai.pattern.detect"


async def test_task_timeout_requeues() -> None:
    reg = Registry()

    async def handler(command: Command) -> None:
        import asyncio

        await asyncio.sleep(1)

    reg.register("pattern", handler)
    handler_obj, _, dlq, _ = build(reg, task_timeout=0.01)
    decision = await handler_obj.handle(FakeMessage(), queue=QUEUE)
    assert decision.action == REQUEUE and decision.reason == "task_timeout"
    assert dlq.parked[0][0] == "retry.30s.ai.pattern.detect"


# ── D-R5-3 passive 声明（M6 靶）──────────────────────────────────────────────


class FakeChannel:
    def __init__(self, *, missing: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.missing = missing

    async def declare_exchange(self, name, type, **kwargs):  # noqa: ANN001, A002
        self.calls.append({"kind": "exchange", "name": name, **kwargs})
        if self.missing:
            import aio_pika

            raise aio_pika.exceptions.ChannelNotFoundEntity(f"NOT_FOUND - no exchange {name}")
        return FakeExchange(name)

    async def declare_queue(self, name=None, **kwargs):  # noqa: ANN001
        self.calls.append({"kind": "queue", "name": name, **kwargs})
        if self.missing:
            import aio_pika

            raise aio_pika.exceptions.ChannelNotFoundEntity(f"NOT_FOUND - no queue {name}")
        return object()


class FakeExchange:
    def __init__(self, name: str) -> None:
        self.name = name


async def test_topology_is_only_asserted_never_created() -> None:
    """M6：Python 必须 ``passive=True`` 且**不带任何 arguments／durable**（两份声明必撞 406）。"""
    from backend.ai.mq import publisher as mq_publisher

    channel = FakeChannel()
    await mq_publisher.declare_passive(channel, topology.EXCHANGE_AI_CANDIDATES, kind="候选回投面")
    await mq_publisher.declare_queue_passive(channel, QUEUE)
    assert [c["passive"] for c in channel.calls] == [True, True], channel.calls
    for call in channel.calls:
        assert call.get("arguments") in (None, {}), call
        assert "durable" not in call or call["durable"] is False, call


async def test_missing_topology_names_the_process_to_start_first() -> None:
    from backend.ai.mq import publisher as mq_publisher

    with pytest.raises(TopologyMissing, match="go-api"):
        await mq_publisher.declare_passive(FakeChannel(missing=True), topology.EXCHANGE_DLQ, kind="死信面")


# ── 开关面：默认关 ⇒ 零副作用（DoD 第 7 条）─────────────────────────────────


def test_switch_defaults_to_off_and_needs_url() -> None:
    sw = load_switch({})
    assert sw.enabled is False and sw.url == ""
    assert sw.prefetch == DEFAULT_PREFETCH == 1
    assert load_switch({ENV_WORKER: "on", ENV_URL: "amqp://broker"}).enabled is True
    with pytest.raises(SwitchError, match="拒绝启动"):
        load_switch({ENV_WORKER: "1"})
    with pytest.raises(SwitchError, match="必须为正整数"):
        load_switch({ENV_PREFETCH: "0"})


async def test_disabled_switch_dials_nobody(monkeypatch: pytest.MonkeyPatch) -> None:
    import aio_pika

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("开关关着还拨号＝灰度 0 时进程有副作用")

    monkeypatch.setattr(worker.aio_pika, "connect_robust", boom)
    monkeypatch.setattr(aio_pika, "connect_robust", boom)
    assert await worker.amain(load_switch({})) == 0


async def test_connect_failure_is_diagnosable(monkeypatch: pytest.MonkeyPatch) -> None:
    import aio_pika

    async def boom(*args: Any, **kwargs: Any) -> None:
        raise aio_pika.exceptions.AMQPConnectionError("connection refused")

    monkeypatch.setattr(worker.aio_pika, "connect_robust", boom)
    sw = load_switch({ENV_WORKER: "1", ENV_URL: "amqp://127.0.0.1:1"})
    assert await worker.amain(sw) == 4


# ── 接线本身不许悄悄消失（M7 靶）────────────────────────────────────────────


def test_worker_source_is_wired_to_the_four_queues() -> None:
    """摘掉 spawn／漏订阅一条队列，这种「看不见调用点」的缺陷只能由源码闸拦。"""
    src = pathlib.Path(worker.__file__).read_text(encoding="utf-8")
    assert "for name in topology.CONSUMED_QUEUES" in src, src
    assert "await queue.consume(" in src and "no_ack=False" in src, "no_ack=True＝自动丢消息"
    assert "set_qos(" in src and "sw.prefetch" in src, "prefetch 没落到通道上＝背压键是装饰品"
    assert "on_return_raises=True" in src, "不可路由必须抛错，不许静默丢弃"
    assert "__main__" in src and "raise SystemExit(main())" in src, "python -m 入口没了"
    assert "TopologyMissing" in src, "拓扑缺失必须退出非零而不是静默自建"


def test_mq_package_exposes_no_db_or_http_surface() -> None:
    """``backend/ai/mq`` 里不许出现 HTTP/DB 面（与 test_ai_boundary 同判据的窄版）。"""
    src_dir = pathlib.Path(worker.__file__).parent
    for path in sorted(src_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in ("SessionLocal", "APIRouter", "get_db"):
            assert token not in text or path.name == "__init__.py", (path.name, token)

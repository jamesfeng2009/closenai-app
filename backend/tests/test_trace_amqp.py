"""批 T-B 闸 G-B1：AMQP 面 trace 贯通（Python 侧的四跳）。

拦的事故形态：
- parse 缺头即拒 ⇒ 升级窗口内在途的老命令全军覆没（M-B3 打的正是这条）；
- encode 把空 trace 印成占位头 ⇒ grep 出假链路（M-B2）；
- 消费侧不恢复 contextvar ⇒ worker 日志格式对了、值永远是 `-`；
- DLX 改投丢头 ⇒ 死信断链，人工 replay 无从关联。

静态面（迁移件形状）与实库面（列反射）各自独立成测：一件漂移不该掩盖另一件。
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from backend.ai.mq import topology
from backend.ai.mq.contract import (
    CONTENT_TYPE_JSON,
    HEADER_TRACE_ID,
    HEADER_USER_ID,
    encode_candidate,
    parse_command,
)
from backend.core.logging import get_trace_id, set_trace_id

MAX_BODY = 64 * 1024
QUEUE = topology.QUEUE_PATTERN

_COMMAND_BODY = json.dumps({
    "schemaVersion": "1",
    "taskType": "TASK_TYPE_PATTERN_DETECT",
    "sourceRecordIds": ["sr-1"],
}).encode()


def _parse(headers: dict[str, Any]):
    return parse_command(
        body=_COMMAND_BODY,
        content_type=CONTENT_TYPE_JSON,
        routing_key="ai.pattern.requested",
        message_id="cmd-trace-1",
        message_type="ai_pattern_requested",
        headers={"x-schema-version": "1", HEADER_USER_ID: "u-1", **headers},
        correlation_id="corr-1",
        max_body_bytes=MAX_BODY,
    )


# ── 读侧：宽松（缺头是合法形态，不是契约违规）──────────────────────────────


def test_parse_command_reads_trace_header() -> None:
    command = _parse({HEADER_TRACE_ID: "T-42"})
    assert command.trace_id == "T-42", "命令头的值必须逐字进 Command，不许加修饰"


def test_parse_command_tolerates_missing_trace_header() -> None:
    """M-B3 靶：把「缺头」改成拒消息，这条先红——在途老命令不许因升级而死。"""
    command = _parse({})
    assert command.trace_id == ""


# ── 写侧：回投候选继承命令 trace；空值不占位 ────────────────────────────────


def _encode(command) -> dict[str, Any]:
    message = encode_candidate(
        command=command,
        kind="pattern",
        body={"kind": "KIND_PATTERN", "safety": {"status": "SAFETY_STATUS_OK"}},
        exchange=topology.EXCHANGE_AI_CANDIDATES,
        max_body_bytes=MAX_BODY,
    )
    return dict(message.headers)


def test_encode_candidate_carries_command_trace() -> None:
    headers = _encode(_parse({HEADER_TRACE_ID: "T-42"}))
    assert headers[HEADER_TRACE_ID] == "T-42", "候选必须把命令的链带下去，两侧一条 id 才可 grep"


def test_encode_candidate_omits_empty_trace() -> None:
    """M-B2 靶：空串照写＝把「没有链路」印成一条假链路。"""
    headers = _encode(_parse({}))
    assert HEADER_TRACE_ID not in headers


# ── 消费侧：处理该消息期间 contextvar 可见（与发布同 task，见 _decide）──────


class _TraceCapturingPublisher:
    def __init__(self) -> None:
        self.exchange = topology.EXCHANGE_AI_CANDIDATES
        self.captured: list[str] = []

    async def publish(self, message) -> None:  # noqa: ANN001
        self.captured.append(get_trace_id())


@pytest.fixture(autouse=True)
def _clean_trace_var():
    set_trace_id("")
    yield
    set_trace_id("")


async def test_handle_restores_trace_for_candidate_publish() -> None:
    from test_ai_mq_consumer import FakeMessage, build, registered_handler

    reg = await registered_handler()
    publisher = _TraceCapturingPublisher()
    consumer, _, _, _ = build(reg, publisher=publisher)  # type: ignore[arg-type]

    decision = await consumer.handle(
        FakeMessage(headers={HEADER_TRACE_ID: "T-77"}), queue=QUEUE
    )
    assert decision.acked
    assert publisher.captured == ["T-77"], (
        "回投 publish 发生在同一 task 上下文——这里读到空就是 _decide 没恢复 contextvar"
    )


async def test_handle_without_trace_leaves_contextvar_untouched() -> None:
    from test_ai_mq_consumer import FakeMessage, build, registered_handler

    reg = await registered_handler()
    publisher = _TraceCapturingPublisher()
    consumer, _, _, _ = build(reg, publisher=publisher)  # type: ignore[arg-type]

    await consumer.handle(FakeMessage(), queue=QUEUE)
    assert publisher.captured == [""], "缺头时不许残留上一条消息的 trace（串链即假证人）"


# ── DLX 面：改投保留 trace 头 ────────────────────────────────────────────────


async def test_dead_letter_park_preserves_trace_header() -> None:
    import aio_pika

    from backend.ai.mq.publisher import AioDeadLetterPublisher

    class _CapturingExchange:
        name = topology.EXCHANGE_DLQ

        def __init__(self) -> None:
            self.sent: list[aio_pika.Message] = []

        async def publish(self, message, routing_key, **kwargs) -> None:
            self.sent.append(message)

    incoming = aio_pika.Message(
        body=b"{}", headers={"x-schema-version": "1",
                             HEADER_USER_ID: "u-1", HEADER_TRACE_ID: "T-9"},
        message_id="m-1", correlation_id="c-1",
    )
    exchange = _CapturingExchange()
    await AioDeadLetterPublisher(exchange).park(  # type: ignore[arg-type]
        incoming, routing_key="retry.30s.ai.pattern.requested", reason="gate-test",
    )
    assert exchange.sent[0].headers[HEADER_TRACE_ID] == "T-9", (
        "死信/重试层断链＝replay 时找不到当初那次点击"
    )


# ── 静态与实库：trace 件的两面形状 ──────────────────────────────────────────


def test_trace_migration_declares_exact_column_shape() -> None:
    import pathlib

    src = (
        pathlib.Path(__file__).resolve().parents[2]
        / "backend" / "alembic" / "versions" / "e7a2c4b6d8f1_add_outbox_trace_id.py"
    ).read_text(encoding="utf-8")
    assert "sa.String(length=128)" in src and "nullable=False" in src, (
        "列形状漂移：VARCHAR(128) NOT NULL 是 N7-1 空串取舍的一部分"
    )
    assert 'server_default=""' in src, "存量行靠 server_default 兜住，否则加列即锁表失败"
    assert 'op.drop_column("outbox_messages", "trace_id")' in src, "回退件必须真能回退"


def test_live_outbox_trace_column_shape() -> None:
    from _pg_env import pg_test_url
    from sqlalchemy import create_engine, inspect

    engine = create_engine(pg_test_url(purpose="trace 列形状"))
    try:
        columns = {c["name"]: c for c in inspect(engine).get_columns("outbox_messages")}
    finally:
        engine.dispose()
    assert "trace_id" in columns, "库里没有该列——alembic upgrade head 跑过（e7a2c4b6d8f1）吗？"
    col = columns["trace_id"]
    assert col["nullable"] is False
    assert str(col["type"]) == "VARCHAR(128)"

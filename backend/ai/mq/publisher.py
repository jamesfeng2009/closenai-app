"""R5 · 发布面（confirm 模式）与 passive 声明（D-R5-3）。

两条地基：

* **publish 返回＝broker 已确认**。aio-pika 在 ``publisher_confirms=True``（``channel()``
  的默认值）下让 ``Exchange.publish()`` 等 ``Basic.Ack``，nack/reject 抛 ``DeliveryError``；
  再加 ``on_return_raises=True``＋库默认的 ``mandatory=True``，不可路由（没人绑定）就抛
  ``PublishError``。这两条合起来才是 ``17:91-112``「未确认不许 ACK」与 R4 的
  ``ErrNoConsumerBound``（无人认领不许记成已投递）在库层面的支撑——本层不需要再自己包一层
  DeferredConfirmation，也就不需要发明第二套等待语义。
* **只断言存在，绝不创建**（D-R5-3）。R4 在真 broker 上撞过
  ``PRECONDITION_FAILED - inequivalent arg 'x-message-ttl'``（406）：两份声明撞一张表。
  参数面（durable／x-dead-letter-*／x-message-ttl）的唯一事实源是
  ``go-core/internal/messaging/amqp/topology.go``。Python 手里只有**名字**，
  所以名字漂移由 ``test_ai_mq_contract.py`` 逐字比对拦住，参数漂移由 broker 当场拒。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractIncomingMessage, AbstractQueue

from backend.ai.mq import topology
from backend.ai.mq.contract import CandidateMessage

#: 本地补齐的 header（V3 ``14`` 全文没有 reject 留痕头）：终态死信要能被 R7 的 replay
#: 工具读出「为什么进来的」，否则 ``dlq.*`` 只是一堆无头案。
HEADER_REJECT_REASON = "x-reject-reason"
HEADER_REJECT_DETAIL = "x-reject-detail"


class TopologyMissing(RuntimeError):
    """passive 声明失败＝对面（Go worker）没先把拓扑建起来。

    这一条**必须**让进程退出而不是静默自建：静默建出来的第二套表参数一定与 Go 的不同，
    下一个 Go worker 启动时撞 406，而那时先起的进程已经跑了好几小时——故障现场与根因隔了
    一个进程。错误文本要能直接指出该起谁（D-R5-3）。
    """


async def declare_passive(channel: AbstractChannel, name: str, *, kind: str) -> AbstractExchange:
    """只断言 exchange 存在。缺失 ⇒ ``TopologyMissing``；参数不符 ⇒ 库抛 ``ChannelPreconditionFailed``。"""
    try:
        return await channel.declare_exchange(name, aio_pika.ExchangeType.TOPIC, passive=True)
    except aio_pika.exceptions.ChannelNotFoundEntity as exc:
        raise TopologyMissing(
            f"{kind} exchange {name!r} 在 broker 上不存在。拓扑由 Go 独占声明"
            f"（go-core/internal/messaging/amqp/topology.go 的 Declare），"
            f"先起带 CLOSEN_OUTBOX_WORKER 的 go-api，再起本进程。"
        ) from exc


async def declare_queue_passive(channel: AbstractChannel, name: str) -> AbstractQueue:
    """只断言队列存在（名字与参数都不碰），把它取回来供消费。理由同 ``declare_passive``。"""
    try:
        return await channel.declare_queue(name, passive=True)
    except aio_pika.exceptions.ChannelNotFoundEntity as exc:
        raise TopologyMissing(
            f"队列 {name!r} 在 broker 上不存在。拓扑由 Go 独占声明"
            f"（go-core/internal/messaging/amqp/topology.go 的 Declare），"
            f"先起带 CLOSEN_OUTBOX_WORKER 的 go-api，再起本进程。"
        ) from exc


class AioCandidatePublisher:
    """``pipeline.CandidatePublisher`` 的真实现：投 ``closenai.ai.candidates``。"""

    def __init__(self, exchange: AbstractExchange) -> None:
        self._exchange = exchange

    @property
    def exchange(self) -> str:  # 端口要求：名字由拓扑给，pipeline 不写死
        return self._exchange.name

    async def publish(self, message: CandidateMessage) -> None:
        # 没拿到 Basic.Ack 就走 aio_pika 的 DeliveryError/PublishError；
        # 异常照实往外抛，由 pipeline 转成 REQUEUE（变异靶 M1 就是摘掉这里的等待）。
        await self._exchange.publish(
            aio_pika.Message(
                body=message.body,
                headers=dict(message.headers),
                content_type=message.content_type,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=message.message_id,
                type=message.message_type,
                correlation_id=message.correlation_id,
                timestamp=datetime.now(timezone.utc),
            ),
            message.routing_key,
        )


class AioDeadLetterPublisher:
    """把一条消息**原样**改投 ``closenai.dlq`` 的某一格（重试层或终态面）。

    为什么用「改投＋ack」而不是「reject 不 requeue」：purpose queue 的
    ``x-dead-letter-routing-key`` 被 Go 写死成第一层（``topology.go:184-187``），
    所以 ``reject(requeue=False)`` 只能进 ``retry.30s.*``，永远到不了 2m/10m 与终态面；
    而升级到 ``retry.2m`` 之后再 reject 又会**再**进 30s 层，同一条消息被复制成两份。
    改投（自己算键）＋ ack 原件，才是「一条消息只走一层、且层号由 x-death 决定」。
    """

    def __init__(self, exchange: AbstractExchange) -> None:
        self._exchange = exchange

    async def park(
        self,
        message: AbstractIncomingMessage,
        *,
        routing_key: str,
        reason: str,
        detail: str = "",
    ) -> None:
        headers: dict[str, Any] = dict(message.headers or {})
        headers[HEADER_REJECT_REASON] = reason
        headers[HEADER_REJECT_DETAIL] = detail[:512]
        await self._exchange.publish(
            aio_pika.Message(
                body=message.body,
                headers=headers,
                content_type=message.content_type,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=message.message_id,
                type=message.type,
                correlation_id=message.correlation_id,
                # 归属头跟着消息走：终态面要能回答「这条卡在 DLQ 的是哪个用户的」，
                # 否则 purge（D-5）与人工 replay 都得先猜。
                timestamp=datetime.now(timezone.utc),
            ),
            routing_key,
        )


async def open_publishers(channel: AbstractChannel) -> tuple[AioCandidatePublisher, AioDeadLetterPublisher]:
    """两个 outbound exchange 一起做 passive 声明（候选面＋死信面）。"""
    candidates = await declare_passive(
        channel, topology.EXCHANGE_AI_CANDIDATES, kind="候选回投面"
    )
    dlq = await declare_passive(channel, topology.EXCHANGE_DLQ, kind="死信面")
    return AioCandidatePublisher(candidates), AioDeadLetterPublisher(dlq)


__all__ = [
    "AioCandidatePublisher",
    "AioDeadLetterPublisher",
    "HEADER_REJECT_DETAIL",
    "HEADER_REJECT_REASON",
    "TopologyMissing",
    "declare_passive",
    "declare_queue_passive",
    "open_publishers",
]

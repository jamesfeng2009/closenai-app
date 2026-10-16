"""R5 · Python AI worker 的进程入口：``python -m backend.ai.mq.worker``（D-R5-1）。

刻意**不**与 ``backend.ai.rpc.server`` 共进程（D-R5-1 的理由逐字）：gRPC 面被 Go 同步调用、
要低延迟，MQ 面会积压、要按队列重启，两者的重启节奏与背压不是一回事；共进程时
「因为积压要重启」会把在途 gRPC 调用一起打断。也不在 ``backend/main.py`` 的 lifespan 里
——那会把 HTTP 面与这条吞吐面绑成同一次部署。

开关关 ⇒ **零副作用**（DoD 第 7 条）：不拨号、不声明、不认领、不产候选，直接退出码 0。
所以清单（归 R7）要么带 ``CLOSEN_MQ_WORKER=1``，要么根本不建这个 Deployment。

停机次序（``17:59``「The worker must tolerate restarts」的可执行形式）：
先取消四个 consumer ⇒ 不再有新投递进回调；已进回调的在途任务给 ``drain_timeout`` 预算跑完
（候选已确认的会 ack，没跑完的**不 ack** ⇒ 断线后由 broker 重回队列，at-least-once）；
最后关连接。顺序反过来的话，正在 publish 的候选会撞上已关闭的 channel。
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from typing import Sequence

import aio_pika
from aio_pika.abc import AbstractRobustConnection, AbstractRobustQueue, ConsumerTag

from backend.ai.mq import topology
from backend.ai.mq.config import Switch, SwitchError, load_switch
from backend.ai.mq.consumer import CommandConsumer, WorkerStats
from backend.ai.mq.publisher import (
    TopologyMissing,
    declare_queue_passive,
    open_publishers,
)
from backend.ai.mq.registry import Registry

log = logging.getLogger("backend.ai.mq.worker")


async def _consume_one(
    message: object, queue_name: str, handler: CommandConsumer, inflight: set[asyncio.Task]
) -> None:
    """回调体：把这条投递变成一个**可被 drain 等到**的任务，然后就地等它。

    为什么不直接 ``await handler.handle(...)``：取消 consumer 只挡住新投递，在途那条要能被
    停机序找到——留在 ``inflight`` 里才谈得上「给 ``drain_timeout`` 预算跑完」。
    """
    task = asyncio.current_task()
    if task is not None:
        inflight.add(task)
        task.add_done_callback(inflight.discard)
    try:
        decision = await handler.handle(message, queue=queue_name)  # type: ignore[arg-type]
        # 每轮一条日志（工作单 §四 R5-④）：动作＋原因＋全量计数，
        # 让「管道通了但流量是零」这件事在日志里可核对，而不是靠一句口头结论。
        log.info(
            "queue=%s key=%s action=%s reason=%s stats=%s",
            queue_name,
            getattr(message, "routing_key", "?"),
            decision.action,
            decision.reason,
            decision.candidate.message_id if decision.candidate is not None else "-",
        )
    finally:
        if task is not None:
            inflight.discard(task)


async def serve(
    sw: Switch, registry: Registry, stats: WorkerStats, stop: asyncio.Event
) -> None:
    """连上 broker、passive 断言拓扑、起四个队列的消费循环，直到 ``stop``。"""
    conn: AbstractRobustConnection = await aio_pika.connect_robust(sw.url)
    try:
        # redials 只能从库的重连信号上数（``RobustConnection.reconnect_callbacks``）：
        # 自己包一层重连循环就能自己数，但那是第二套连接语义，不值。
        conn.reconnect_callbacks.add(lambda: setattr(stats, "redials", stats.redials + 1))

        # on_return_raises=True ＋库默认 mandatory=True ⇒ 不可路由就抛 PublishError：
        # 「没人认领的消息」不许被记成已投递（R4 的 ErrNoConsumerBound 同一方向）。
        channel = await conn.channel(on_return_raises=True)
        try:
            await channel.set_qos(prefetch_count=sw.prefetch)
            candidates, dead_letters = await open_publishers(channel)
            handler = CommandConsumer(
                registry=registry,
                publisher=candidates,
                dead_letters=dead_letters,
                stats=stats,
                max_body_bytes=sw.max_body_bytes,
                task_timeout_seconds=sw.task_timeout.total_seconds(),
            )

            inflight: set[asyncio.Task] = set()
            subscribed: list[tuple[AbstractRobustQueue, ConsumerTag]] = []
            try:
                for name in topology.CONSUMED_QUEUES:
                    queue = await declare_queue_passive(channel, name)
                    tag = await queue.consume(
                        lambda message, q=name: _consume_one(message, q, handler, inflight),
                        no_ack=False,
                    )
                    subscribed.append((queue, tag))
                    log.info("已认领队列 %s（prefetch=%d）", name, sw.prefetch)
                log.info(
                    "AI worker 就绪：注册 handler %s，未注册的任务种类按 permanent 进 DLQ",
                    list(registry.registered()) or "（空——R5 只交付管道）",
                )
                await stop.wait()
            finally:
                for queue, tag in subscribed:
                    with contextlib.suppress(Exception):
                        await queue.cancel(tag, nowait=True)
                if inflight:
                    drain = int(sw.drain_timeout.total_seconds())
                    log.info("等待 %d 条在途投递收尾（预算 %ds）", len(inflight), drain)
                    _, pending = await asyncio.wait(set(inflight), timeout=drain)
                    # 超预算的不许 ack（没跑完就是没确认候选），直接取消 ⇒ 留在 unacked，
                    # broker 在连接关闭时把它重回队列。
                    for task in pending:
                        task.cancel()
                    with contextlib.suppress(Exception):
                        await asyncio.gather(*pending, return_exceptions=True)
        finally:
            await channel.close()
    finally:
        if not conn.is_closed:
            await conn.close()


async def amain(sw: Switch, registry: Registry | None = None) -> int:
    """给测试与 ``main()`` 共用的协程入口：返回退出码。"""
    if not sw.enabled:
        log.info(
            "%s 未开启 ⇒ 进程零副作用退出（不拨号、不声明、不认领、不产候选）",
            "CLOSEN_MQ_WORKER",
        )
        return 0
    stats = WorkerStats()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # add_signal_handler 在非主线程/不支持的平台上抛错（Windows），这里允许它不起：
        # 集成测里由 stop 事件直接触发停机，不靠真信号。
        with contextlib.suppress(NotImplementedError, ValueError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)

    registry = registry or Registry()
    try:
        await serve(sw, registry, stats, stop)
    except TopologyMissing as exc:
        # D-R5-3 的失败形态：不静默自建，把「该起谁」逐字说清楚后退出非零。
        log.error("拓扑缺失：%s", exc)
        return 3
    except aio_pika.exceptions.AMQPConnectionError as exc:
        # connect_robust 的 fail_fast 默认 True ⇒ 首次拨号失败就地抛，不在这里空转重试。
        # 这条与拓扑缺失分开记：一个是「对面没建表」，一个是「对面根本没起来」。
        log.error("连不上 broker（%s）：先确认 %s 可达，再起本进程。", type(exc).__name__, sw.url)
        return 4
    finally:
        log.info("AI worker 退出，计数=%s", stats.snapshot())
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.ai.mq.worker")
    parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    try:
        sw = load_switch()
    except SwitchError as exc:
        log.error("开关配置非法：%s", exc)
        return 2
    return asyncio.run(amain(sw))


if __name__ == "__main__":  # pragma: no cover — 进程入口由集成测跑
    raise SystemExit(main())

"""R5 · 消费回调：把一条命令走完 ``17:62-74`` 的八步，并执行处置动作。

本模块只 import aio-pika 的**类型**（Protocol 用，测试里塞假对象即可跑），
真正的连接在 ``worker.py``。这样「八步的次序」与「ACK/REJECT/REQUEUE 的口径」
这两组纪律不需要 broker 就能被打红——变异靶 M1/M4/M5/M6 全打在这里。

三个口径落到动作上的映射（D-R5-11 ＋ ``topology.go:184-187`` 的实现约束）：

    ACK      → ``message.ack()``
    REJECT   → 原样改投 ``closenai.dlq`` 的 ``dlq.<queue>``，再 ``ack()`` 原件
    REQUEUE  → 按 x-death 算出的**下一层**改投 ``retry.<tier>.<queue>``，再 ``ack()`` 原件；
               阶梯走完（30s→2m→10m 都过了一遍）→ 进终态 ``dlq.<queue>``

为什么 REJECT/REQUEUE 都走「改投＋ack」而不是 ``reject(requeue=False)``：
purpose queue 的 ``x-dead-letter-routing-key`` 被 Go 写死成第一层，所以 reject 只能进
``retry.30s``——永久非法的毒消息会在 30s 后被送回 purpose queue 重跑，而升级换层
（``topology.go:28-33`` 逐字「阶梯升级靠**消费侧**读 x-death 后改投对应层级的 key（R5 的活）」）
只能靠改投表达。此时再 reject 会多复制一份，所以改投之后必须 ack 原件：**一条消息只走一层**。

失败即不 ack：改投本身抛错（连不上 broker）时让异常冒出回调，原件留在 unacked，
断线后由 broker 重回队列。这是有意的 at-least-once——宁可重投一次（幂等由 Go 的 inbox
兜住，``18:91-96``），不可把消息丢在两个地方都不存在。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from backend.ai.mq import pipeline, topology
from backend.ai.mq.contract import (
    REASON_UNKNOWN_ROUTING_KEY,
    Command,
    ContractError,
    parse_command,
)
from backend.ai.mq.pipeline import ACK, REQUEUE, Decision
from backend.ai.mq.publisher import AioDeadLetterPublisher
from backend.ai.mq.registry import Registry
from backend.core.logging import set_trace_id

#: 投递回调需要的消息面（aio_pika.AbstractIncomingMessage 逐字满足；测试用假对象）。
#: 刻意不含 ``queue``——队列名由 worker 在订阅时显式传入：broker 给的消息属性里没有队列名，
#: 而从 x-death 反推队列会让「哪个队列的消费者」这件事变成推断而不是事实。
class Incoming(Protocol):
    body: bytes
    routing_key: str
    exchange: str
    message_id: Any
    type: Any
    correlation_id: Any
    content_type: Any
    headers: Any

    async def ack(self) -> Any: ...


#: handler 超时／改投失败时写进日志的 reason（不是契约词表里的，因为这不是消息的错）
REASON_TASK_TIMEOUT = "task_timeout"


@dataclass
class WorkerStats:
    """R5-④ 的指标面：进程内计数，每轮一条日志（不新开 HTTP 端点，见工作单 §四 R5-④）。

    口径要说清，否则这四个数会互相打脸：``acked`` 数的是**判为不再重试**的条数
    （跑完／本来没的可提案），``requeued``/``rejected`` 数的是「改投到阶梯某层／终态面」的条数——
    后两条在 AMQP 层面同样要 ack 原件（一条消息只走一层），所以 ``acked`` **不是**
    「发过 basic.ack 的次数」。``produced`` 只在候选真正确认后才加，失败不记（不许记假账）。
    """

    consumed: int = 0
    produced: int = 0
    acked: int = 0
    requeued: int = 0
    rejected: int = 0
    parked: int = 0
    redials: int = 0
    rejects_by_reason: dict[str, int] = field(default_factory=dict)

    def note_reject(self, reason: str) -> None:
        self.rejects_by_reason[reason] = self.rejects_by_reason.get(reason, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "consumed": self.consumed,
            "produced": self.produced,
            "acked": self.acked,
            "requeued": self.requeued,
            "rejected": self.rejected,
            "parked": self.parked,
            "redials": self.redials,
            "rejects_by_reason": dict(sorted(self.rejects_by_reason.items())),
        }


def xdeath_tiers(queue: str, headers: Mapping[Any, Any] | None) -> int:
    """这条消息已经过过的**最深**重试层号（0＝还没进过阶梯）。

    判据取 ``x-death`` 里 ``queue`` 等于 ``retry.<suffix>.<queue>`` 的条目，
    层号用 ``topology.RETRY_SUFFIXES`` 的下标——与 Go 的 ``TierQueueName``（``topology.go:220``）
    同一条命名规则，由 ``test_ai_mq_contract.py`` 逐字比对钉住。
    """
    deepest = -1
    entries = (headers or {}).get("x-death") or []
    if not isinstance(entries, (list, tuple)):
        return 0
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        name = entry.get("queue")
        if isinstance(name, bytes):
            name = name.decode("utf-8", "replace")
        if not isinstance(name, str) or not name.startswith("retry.") or not name.endswith(f".{queue}"):
            continue
        suffix = name[len("retry.") : -len(f".{queue}")]
        if suffix in topology.RETRY_SUFFIXES:
            deepest = max(deepest, topology.RETRY_SUFFIXES.index(suffix))
    return deepest + 1


def escalation_target(queue: str, headers: Mapping[Any, Any] | None) -> tuple[str, str]:
    """瞬时失败的下一站：``(closenai.dlq 上的 routing key, 人读的层标签)``。

    阶梯走完就不再重试：``14 §8``「Prefer explicit retry queues over unlimited immediate requeue」。
    """
    tier = xdeath_tiers(queue, headers)
    if tier < len(topology.RETRY_SUFFIXES):
        return topology.tier_routing_key(queue, tier), f"retry.{topology.RETRY_SUFFIXES[tier]}"
    return topology.final_routing_key(queue), "dlq(final)"


class CommandConsumer:
    def __init__(
        self,
        *,
        registry: Registry,
        publisher: pipeline.CandidatePublisher,
        dead_letters: AioDeadLetterPublisher,
        stats: WorkerStats,
        max_body_bytes: int,
        task_timeout_seconds: float,
    ) -> None:
        self._registry = registry
        self._publisher = publisher
        self._dead_letters = dead_letters
        self._stats = stats
        self._max_body_bytes = max_body_bytes
        self._task_timeout = task_timeout_seconds

    async def handle(self, message: Incoming, *, queue: str) -> Decision:
        """一条命令的完整处置，含 ACK/改投动作本身。"""
        self._stats.consumed += 1
        try:
            decision = await asyncio.wait_for(
                self._decide(message, queue=queue), timeout=self._task_timeout
            )
        except asyncio.TimeoutError:
            decision = Decision(REQUEUE, REASON_TASK_TIMEOUT, f"单任务超过 {self._task_timeout}s")
        await self._execute(message, queue=queue, decision=decision)
        return decision

    async def _decide(self, message: Incoming, *, queue: str) -> Decision:
        # 17:62-74 的第 2–4 步（validate metadata → decode → validate task contract）
        if message.exchange != topology.EXCHANGE_AI_TASKS:
            return Decision(
                pipeline.REJECT,
                REASON_UNKNOWN_ROUTING_KEY,
                f"队列 {queue} 收到了 exchange={message.exchange!r} 的投递；"
                f"命令面只认 {topology.EXCHANGE_AI_TASKS!r}（键同义不代表同源）",
            )
        try:
            command: Command = parse_command(
                body=message.body,
                content_type=str(message.content_type or ""),
                routing_key=message.routing_key,
                message_id=str(message.message_id or ""),
                message_type=str(message.type or ""),
                headers=message.headers,
                correlation_id=str(message.correlation_id or ""),
                max_body_bytes=self._max_body_bytes,
            )
        except ContractError as exc:
            return Decision(pipeline.REJECT, exc.reason, exc.detail)
        # 批 T-B 消费侧：把发布方带来的 trace 恢复进本消息处理的 contextvar——
        # worker 进程装了 ContextFilter（批 T-A）后，这一跳起该消息的所有日志
        # （含回投 candidate 的 publish 行）都带同一条链；缺头不动作（后台起源合法）。
        if command.trace_id:
            set_trace_id(command.trace_id)
        return await pipeline.run(
            command,
            registry=self._registry,
            publisher=self._publisher,
            max_body_bytes=self._max_body_bytes,
        )

    async def _execute(self, message: Incoming, *, queue: str, decision: Decision) -> None:
        if decision.acked:
            # 变异靶 M1：把这一行挪到 publish 之前＝永久丢候选（17:91-112）。
            # 到这里的唯一路径是「候选已确认」或「本来就没的可提案」。
            self._stats.acked += 1
            if decision.candidates:
                # 一条命令出 N 条候选时逐条计数：`produced` 的口径是「真正确认过的候选条数」，
                # 把它按「命令条数」记会让「一次生成三条证据」在指标里少算两条。
                self._stats.produced += len(decision.candidates)
            await message.ack()
            return

        if decision.action == REQUEUE:
            key, tier = escalation_target(queue, message.headers)
            self._stats.requeued += 1
            await self._dead_letters.park(
                message,  # type: ignore[arg-type] — aio_pika 的消息逐字满足 Incoming
                routing_key=key,
                reason=f"{decision.reason}→{tier}",
                detail=decision.detail,
            )
            self._stats.parked += 1
            await message.ack()
            return

        self._stats.rejected += 1
        self._stats.note_reject(decision.reason)
        await self._dead_letters.park(
            message,  # type: ignore[arg-type]
            routing_key=topology.final_routing_key(queue),
            reason=decision.reason,
            detail=decision.detail,
        )
        self._stats.parked += 1
        await message.ack()

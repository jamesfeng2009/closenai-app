"""R5 · 消费一条命令的**决策**（V3 ``17:62-74`` 八步的可测形式）。

本模块不 import aio-pika，只做一件事：把「一条已解析的命令」走完整条链，然后回吐
``ACK``/``REJECT``/``REQUEUE`` 三个口径之一。执行动作（改投递状态）在 ``consumer.py``，
于是这条纪律可以脱离 broker 被打红：

    **候选拿到 broker 确认之后，才允许 ACK 原命令**（``17:91-112``）。

反面写法的后果不是「慢」，是**永久丢候选**：先 ACK 再发候选，中间进程一重启，命令已经没了、
候选还没出去，而 outbox 那侧早已 published（R4 的 ``MarkOutboxPublished``），
于是这条 AI 任务在两边都查不到。所以 ``publish`` 抛错 ⇒ 本函数回 ``REQUEUE``，
命令留在队列上走 ``14:101-122`` 的 30s/2m/10m 阶梯，最后一层进 DLQ。

三个口径的分工（D-R5-11，判据取自 ``17:148-164`` 与 ``14:101-122``）：

* ``REJECT``＝契约级非法（未知键、task_type 与键不同义、schema_version 不认识、缺归属、
  没注册 handler、候选越界）⇒ reject 不 requeue，直入 DLX。毒消息 requeue 就是让它
  无限撞库撞 LLM，R4 的变异靶 M3 打的正是这个形态。
* ``REQUEUE``＝瞬时失败（连不上、LLM 超时、候选未确认、handler 抛了非契约异常）。
* ``ACK``＝跑完了（出了**一条**候选并确认），**或**这次本来就没的可提案（0 条）。
  0 条必须 ACK：否则「模型认为这条输入不值得提案」会变成一次无限重试，把队列堵在
  一个合法的空答案上。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.ai.mq.contract import (
    REASON_MULTI_CANDIDATE,
    REASON_PUBLISH_UNCONFIRMED,
    CandidateMessage,
    Command,
    ContractError,
    encode_candidate,
)
from backend.ai.mq.registry import Proposal, Registry, proposals_of

ACK = "ack"
REJECT = "reject"
REQUEUE = "requeue"

#: handler 明确「这次没的可提案」时 Decision 带的 reason（进日志，不是错误）。
REASON_NO_CANDIDATE = "no_candidate"
#: 候选已确认、命令可以 ACK 的 reason。
REASON_PUBLISHED = "candidate_published"


class TransientError(RuntimeError):
    """handler 用它声明「换个时刻重试有可能成」（LLM 超时、上游 5xx、限流）。

    没有这个类型的话，所有异常都得按同一口径处置：要么把超时也 reject 进 DLQ（丢任务），
    要么把代码 bug 也 requeue 到阶梯尽头（拖很久才可见）。让 handler 自己标，两边都准。
    """


class CandidatePublisher(Protocol):
    """``publisher.py`` 满足的端口：``publish`` 只能在 broker 确认之后返回。"""

    exchange: str

    async def publish(self, message: CandidateMessage) -> None: ...


@dataclass(frozen=True)
class Decision:
    """本步的处置＋为什么。``reason`` 进 ``WorkerStats.rejects_by_reason`` 与日志。"""

    action: str
    reason: str
    detail: str = ""
    candidate: CandidateMessage | None = None

    @property
    def acked(self) -> bool:
        return self.action == ACK

    @property
    def requeued(self) -> bool:
        return self.action == REQUEUE


async def run(
    command: Command,
    *,
    registry: Registry,
    publisher: CandidatePublisher,
    max_body_bytes: int,
) -> Decision:
    """走 ``17:62-74`` 的第 4 步到第 8 步（1–3 步＝连接、属性校验、decode，在 consumer 里）。"""
    # 第 5 步 execute：取 handler。未注册按 permanent 处理（D-R5-12），不 requeue。
    try:
        handler = registry.handler_for(command.task_kind)
        outcome = await handler(command)
        proposals: tuple[Proposal, ...] = proposals_of(outcome)
    except ContractError as exc:
        return Decision(REJECT, exc.reason, exc.detail)
    except TransientError as exc:
        return Decision(REQUEUE, "handler_transient", f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 — 未知异常按瞬时：让阶梯去收敛，日志照实记
        return Decision(REQUEUE, "handler_failed", f"{type(exc).__name__}: {exc}")

    # 第 6 步 produce candidate
    if len(proposals) == 0:
        return Decision(ACK, REASON_NO_CANDIDATE)
    if len(proposals) > 1:
        # D-R5-5 的派生规则下多条候选会拿到同一个 message_id ⇒ 第二条起被 Go 的 inbox
        # 判成重复而**静默丢掉**。与其放行进那条静默路径，不如在这里就拒绝并说明归 R6。
        return Decision(
            REJECT,
            REASON_MULTI_CANDIDATE,
            f"一次命令出了 {len(proposals)} 条候选；candidate_message_id 由命令 id 派生"
            "（不含序号），多条必然同 id。要支持多候选，R6 得先定同一次生成内的稳定序号怎么来",
        )
    try:
        message = encode_candidate(
            command=command,
            kind=proposals[0].kind,
            body=proposals[0].body,
            exchange=publisher.exchange,
            max_body_bytes=max_body_bytes,
        )
    except ContractError as exc:
        return Decision(REJECT, exc.reason, exc.detail)

    # 第 7 步 publish——没确认就抛，本函数绝不往下走
    try:
        await publisher.publish(message)
    except Exception as exc:  # noqa: BLE001 — DeliveryError/PublishError/连接错同一处置
        return Decision(
            REQUEUE, REASON_PUBLISH_UNCONFIRMED, f"{type(exc).__name__}: {exc}"
        )

    # 第 8 步 ACK：只有上面那行正常返回才走到这里
    return Decision(ACK, REASON_PUBLISHED, f"{message.routing_key} {message.message_id}", message)

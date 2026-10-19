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
* ``ACK``＝跑完了（出了候选并逐条确认；一条或多条，多条的序号规则见 ``registry.Proposal``），
  **或**这次本来就没的可提案（0 条）。
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
    """本步的处置＋为什么。``reason`` 进 ``WorkerStats.rejects_by_reason`` 与日志。

    ``candidates`` 是这次跑完**已经拿到 broker 确认**的候选（0／1／N 条，N>1 只在每条都有
    ``item_key`` 时才可能存在，见 ``registry.Proposal``）；``candidate`` 留着是为了让
    「一条命令一条候选」那批既有断言逐字不变，它只是 ``candidates[0]``。
    """

    action: str
    reason: str
    detail: str = ""
    candidates: tuple[CandidateMessage, ...] = ()

    @property
    def acked(self) -> bool:
        return self.action == ACK

    @property
    def requeued(self) -> bool:
        return self.action == REQUEUE

    @property
    def candidate(self) -> CandidateMessage | None:
        return self.candidates[0] if self.candidates else None


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
        # D-R5-5 的派生规则下，没有 item_key 的多条候选会拿到**同一个** message_id
        # ⇒ 第二条起被 Go 的 inbox 判成重复而静默丢掉。T2-02 给出的稳定序号就是这一格的出口，
        # 但序号必须由 handler 自己声明：拿输出顺序当序号＝模型换一下顺序，同一条观测就换号，
        # inbox 拦不住重复而证据表多一行。
        missing = [i for i, p in enumerate(proposals) if not p.item_key.strip()]
        if missing:
            return Decision(
                REJECT,
                REASON_MULTI_CANDIDATE,
                f"一次命令出了 {len(proposals)} 条候选，其中第 {missing} 条没有 item_key；"
                "candidate_message_id 的尾巴由它派生（见 registry.Proposal），"
                "缺了就必然同 id、第二条起被 inbox 静默丢掉",
            )
        duplicated = _duplicated([p.item_key for p in proposals])
        if duplicated:
            return Decision(
                REJECT,
                REASON_MULTI_CANDIDATE,
                f"同一次生成里两条候选声明了同一个 item_key={duplicated}；"
                "同 id＝第二条起被 inbox 判成重复，要么它们本来就是同一条观测（应合并），"
                "要么序号没带上区分它们的内容",
            )
    messages: list[CandidateMessage] = []
    try:
        for proposal in proposals:
            messages.append(
                encode_candidate(
                    command=command,
                    kind=proposal.kind,
                    body=proposal.body,
                    exchange=publisher.exchange,
                    max_body_bytes=max_body_bytes,
                    item_key=proposal.item_key,
                )
            )
    except ContractError as exc:
        return Decision(REJECT, exc.reason, exc.detail)

    # 第 7 步 publish——没确认就抛，本函数绝不往下走
    try:
        for message in messages:
            await publisher.publish(message)
    except Exception as exc:  # noqa: BLE001 — DeliveryError/PublishError/连接错同一处置
        # ⚠️ 多候选时前面那几条可能已经发出去了。这里仍然回 REQUEUE 而不是 ACK：
        # 重试会把同样的几条候选（同 id）再发一遍，而 inbox 的唯一键正是为此而设——
        # 「至少一次＋确定性 id」是唯一能把这一步说圆的组合，「发一半就 ACK」反而把
        # 半条链留在两边都查不到的位置。
        return Decision(
            REQUEUE, REASON_PUBLISH_UNCONFIRMED, f"{type(exc).__name__}: {exc}"
        )

    # 第 8 步 ACK：只有上面那个循环全部正常返回才走到这里
    return Decision(
        ACK,
        REASON_PUBLISHED,
        f"{len(messages)} 条候选：{[m.routing_key for m in messages]}",
        tuple(messages),
    )


def _duplicated(values: list[str]) -> list[str]:
    """出现两次以上的 item_key（保持首次出现顺序，日志里要能指回是哪两条）。"""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen and value not in out:
            out.append(value)
        seen.add(value)
    return out

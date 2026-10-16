"""R5 · AI handler 的注册表（D-R5-12：本批**只交付注册表**，四个域的真候选生产归 R6）。

为什么 R5 不写四个 handler：
  ① R3 结案口径逐字是「handler 只做 decode/encode 不内嵌业务分支」；
  ② 现役那四段业务逻辑（``growth_service.py:412/467``、``memory_service.py:270``、
     ``cron_service.py``）**带 DB 写**。搬进 ``backend/ai/`` 要么复制第二份（ownership 表
     最容易漂的那格），要么把仓储 import 进来（``test_ai_boundary.py:28-33`` 禁的就是这一类）；
  ③ 用户口径「发现的缺陷不顺手修」＋灰度 0 时现网响应字节不许变。

所以本批的结案口径是**管道通了，流量仍是零**：空注册表 ⇒ 任何命令都按 permanent 拒绝、
进 DLQ、日志逐字点出「R5 交管道、R6 交生产者」。这条不是占位——它让「谁把命令投进来了但
没人处理」变成一条可观测的记录，而不是一个静默的积压。

handler 的签名（R6 要实现的）：

    async def handler(command: Command) -> Proposal | Sequence[Proposal] | None

返回 ``None``/空＝「这次没什么可提案」（ACK 掉，不 requeue）；返回**一条**候选；返回多条
按 D-R5-5 的 id 派生规则会撞 inbox，``pipeline`` 直接以 ``multi_candidate_unsupported`` 拒。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence

from backend.ai.mq.contract import (
    ROUTING_KEY_TO_TASK_KIND,
    ContractError,
    REASON_HANDLER_UNREGISTERED,
)

#: 一个 AI 任务的产出：routing key 用哪个 kind（fact/pattern/insight/evidence/proactive），
#: body 用 ``closenai.v1.*`` 的 protojson 形态。领域校验不在这里（见 contract 的 docstring）。
@dataclass(frozen=True)
class Proposal:
    kind: str
    body: Mapping[str, Any]


Handler = Callable[["Any"], Awaitable[object]]

#: 本表认得的四个任务种类。判据是 routing key 而不是队列名：
#: 键来自 ``domain.AITaskKind.RoutingKey()``（``commands.go:43`` 是唯一生成点），
#: 注册进一个拼错的名字＝那条队列有人消费但永远不命中，是最难查的那种哑故障。
KNOWN_TASK_KINDS: tuple[str, ...] = tuple(sorted(ROUTING_KEY_TO_TASK_KIND.values()))


class RegistryError(RuntimeError):
    """注册表本身被用错（未知任务名／重复注册）。"""


class Registry:
    """``task_kind → handler`` 的唯一一张表。

    刻意做成显式传入的对象而不是模块级全局：worker 与测试各拿一份，才不会出现
    「测试里注册过，生产进程里没注册」这种靠导入顺序才成立的接线。
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, task_kind: str, handler: Handler) -> None:
        if task_kind not in KNOWN_TASK_KINDS:
            raise RegistryError(
                f"未知任务种类 {task_kind!r}；可注册的面是 {list(KNOWN_TASK_KINDS)}"
                "（这个名字必须等于 routing key 的中间段，见 commands.go:43）"
            )
        if task_kind in self._handlers:
            raise RegistryError(
                f"{task_kind} 已注册过 handler；同进程两张表就是第二套口径"
            )
        if not callable(handler):
            raise RegistryError(f"{task_kind} 的 handler 不可调用：{handler!r}")
        self._handlers[task_kind] = handler

    def registered(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def __len__(self) -> int:
        return len(self._handlers)

    def handler_for(self, task_kind: str) -> Handler:
        """取 handler；没注册 ⇒ permanent 拒绝（D-R5-12）。"""
        handler = self._handlers.get(task_kind)
        if handler is None:
            raise ContractError(
                REASON_HANDLER_UNREGISTERED,
                f"task_type={task_kind} 没有 handler：**R5 交管道、R6 交生产者**"
                f"（当前注册面 {list(self._handlers) or '空'}）。"
                "命令被投进来了却没人处理，按永久拒绝进 DLQ，不许 requeue 成静默积压",
            )
        return handler


def proposals_of(result: object) -> tuple[Proposal, ...]:
    """把 handler 的返回值归一成候选元组（``None``/空 → 空元组）。"""
    if result is None:
        return ()
    if isinstance(result, Proposal):
        return (result,)
    if isinstance(result, Sequence) and not isinstance(result, (str, bytes)):
        out: list[Proposal] = []
        for item in result:
            if not isinstance(item, Proposal):
                raise TypeError(f"handler 返回列表里混了非 Proposal：{item!r}")
            out.append(item)
        return tuple(out)
    raise TypeError(f"handler 的返回类型不合法：{type(result).__name__}（要 Proposal／其序列／None）")

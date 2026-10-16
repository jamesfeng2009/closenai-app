"""R5 · 拓扑**名字**（Python 侧只读，不声明）。

这里只有字符串常量，没有任何 `x-message-ttl`／`arguments`／ durable 标志——那是 Go 侧
`internal/messaging/amqp/topology.go` 的专属事实源（D-R5-3）。写第二份参数就会撞出
R4 实测过的那条 `PRECONDITION_FAILED - inequivalent arg 'x-message-ttl'`（406）。

`backend/tests/test_ai_mq_contract.py` 拿本文件与 `topology.go` ＋ 生成物
`contracts/events.md` 逐字比对；那条闸红了只有一种含义：**有人在一侧改了名字**。
"""
from __future__ import annotations

# 交换机（四个，名字与 `topology.go:24-27` 同值）
EXCHANGE_AI_TASKS = "closenai.ai.tasks"
EXCHANGE_AI_CANDIDATES = "closenai.ai.candidates"
EXCHANGE_EVENTS = "closenai.events"
EXCHANGE_DLQ = "closenai.dlq"

# Python 要消费的四个 purpose queue（队列名 V3 `14:51-54` 逐字）
QUEUE_OBSERVATION = "ai.observation.extract"
QUEUE_PATTERN = "ai.pattern.detect"
QUEUE_INSIGHT = "ai.insight.draft"
QUEUE_PROACTIVE = "ai.proactive.draft"

CONSUMED_QUEUES = (QUEUE_OBSERVATION, QUEUE_PATTERN, QUEUE_INSIGHT, QUEUE_PROACTIVE)

# ── 阶梯与终态面的**命名规则**（D-R5-11 的实现依赖）────────────────────────────
#
# 为什么 Python 需要这两条规则：Go 的 purpose queue 把 `x-dead-letter-routing-key` 写死成
# **第一层**（`topology.go:184-187` declarePurposeQueue），AMQP 没有「按 x-death 计数换层」
# 这种参数——`topology.go:28-33` 逐字把这件事交给消费侧：「阶梯升级靠**消费侧**读 x-death 后
# 改投对应层级的 key（R5 的活）」。所以 Python 作为唯一的消费者必须自己算得出名与键；
# 算错不会报错，只会把重试打成静默丢弃（改投一个没人绑定的键，broker 直接扔）。
RETRY_SUFFIXES = ("30s", "2m", "10m")  # 值面（毫秒 TTL）在 Go 的 RetryTiers，这里只取名字


def tier_routing_key(queue: str, tier: int) -> str:
    """第 ``tier`` 层重试队列的**队列名＝投递键**（同值但不是一个东西，见 Go 的两个函数）。"""
    return f"retry.{RETRY_SUFFIXES[tier]}.{queue}"


def final_routing_key(queue: str) -> str:
    """终态死信面的键：到这儿就停（Go 声明的 `dlq.<queue>` 没有 DLX）。"""
    return f"dlq.{queue}"


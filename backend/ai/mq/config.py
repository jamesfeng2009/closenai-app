"""R5 · worker 的开关面（env-only，**默认关**）。

为什么不并进 `backend/config.py`：那个文件是 HTTP 面的配置，`Settings` 会被 routers/services
到处 import；把 broker 的键塞进去，等于诱导 AI 进程去读数据库配置（`test_ai_boundary.py`
禁的就是这一类连带）。代价在这里明说：`test_k8s_manifests.py:320` 那条跨语言 env 名闸只认
`config.go` 的 `envString/envInt` 与 `backend/config.py` 的字段，读在**本文件**里的键它看不见
⇒ 六个键今天不进 `deploy/k8s/10-config.yaml`（进了会让那条闸当场红），清单面整条归 R7。

默认档的取向与 Go 侧 `internal/messaging/dispatch` 同形：开关关 ⇒ 进程**零副作用**
（不拨号、不声明、不消费、不投递），这是「灰度 0 时现网行为一字不变」唯一可测的写法。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Mapping

ENV_WORKER = "CLOSEN_MQ_WORKER"
ENV_URL = "CLOSEN_AMQP_URL"
ENV_PREFETCH = "CLOSEN_MQ_PREFETCH"
ENV_TASK_TIMEOUT = "CLOSEN_MQ_TASK_TIMEOUT_SECONDS"
ENV_DRAIN = "CLOSEN_MQ_DRAIN_SECONDS"
ENV_MAX_BODY = "CLOSEN_MQ_MAX_BODY_BYTES"

# prefetch 默认 1：宁可慢，不要乱序与并发放大（17 §10 的背压真值要现网标定，归 R7）。
DEFAULT_PREFETCH = 1
# 一次任务的整体预算。120s 不是拍脑袋：它是「小于 broker 第一级 retry 的 TTL(30s) 之上、
# 又不至于让一条卡死的任务把整条队列堵住的量级」——真值同样待 R7 标定，这里只保证**有上限**。
DEFAULT_TASK_TIMEOUT_SECONDS = 120
DEFAULT_DRAIN_SECONDS = 30
# 单条消息体的上限。V3 `14:154-159` 只说「Keep messages bounded…not by embedding arbitrary
# large transcripts」，没给数字 ⇒ 64 KiB 是本地参数（已在 `contracts/events.md` 的口径外，
# 因为它不是拓扑事实），改它只需要改这一行。
DEFAULT_MAX_BODY_BYTES = 64 * 1024

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"", "0", "false", "no", "off"}


class SwitchError(ValueError):
    """配置本身不合法 ⇒ 进程拒绝启动（比带着坏配置跑起来便宜得多）。"""


@dataclass(frozen=True)
class Switch:
    enabled: bool
    url: str
    prefetch: int
    task_timeout: timedelta
    drain_timeout: timedelta
    max_body_bytes: int


def _flag(raw: str | None) -> bool:
    value = (raw or "").strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise SwitchError(f"布尔值只认 {sorted(_TRUE)}／{sorted(_FALSE)}，收到 {raw!r}")


def _positive_int(name: str, raw: str | None, default: int) -> int:
    text = (raw or "").strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError as exc:
        raise SwitchError(f"{name} 要的是整数秒/字节，收到 {raw!r}") from exc
    if value <= 0:
        # prefetch=0 在 AMQP 里是「不限」，那正是我们最不想在默认档看到的语义。
        raise SwitchError(f"{name} 必须为正整数（0 在 AMQP 里＝不限，不许用这一档）：{value}")
    return value


def load_switch(getenv: Callable[[str], str | None] | Mapping[str, str] = os.getenv) -> Switch:
    """读六个键并当场自证一致性；任何一条不合法都抛 `SwitchError`，不返回半成品。"""
    lookup = getenv.get if isinstance(getenv, Mapping) else getenv
    enabled = _flag(lookup(ENV_WORKER))
    url = (lookup(ENV_URL) or "").strip()
    if enabled and not url:
        # 与 Go 侧同一句话的形状：开了开关却没给地址，只会得到一个静默空转的 worker。
        raise SwitchError(f"{ENV_WORKER} 开了但 {ENV_URL} 为空 ⇒ worker 只会空转，拒绝启动")
    return Switch(
        enabled=enabled,
        url=url,
        prefetch=_positive_int(ENV_PREFETCH, lookup(ENV_PREFETCH), DEFAULT_PREFETCH),
        task_timeout=timedelta(
            seconds=_positive_int(ENV_TASK_TIMEOUT, lookup(ENV_TASK_TIMEOUT), DEFAULT_TASK_TIMEOUT_SECONDS)
        ),
        drain_timeout=timedelta(
            seconds=_positive_int(ENV_DRAIN, lookup(ENV_DRAIN), DEFAULT_DRAIN_SECONDS)
        ),
        max_body_bytes=_positive_int(ENV_MAX_BODY, lookup(ENV_MAX_BODY), DEFAULT_MAX_BODY_BYTES),
    )


# ── 事件生产者（T2-06，HTTP 进程侧，**默认关**）───────────────────────────────
#
# 为什么不并进上面的 `Switch`：那张表描述的是**一个 worker 进程**（预取、超时、排水），
# 而这里的三格描述的是「HTTP 进程要不要连 broker」。并成一个 dataclass 后，
# `CLOSEN_MQ_WORKER=1` 会让生产者跟着一起开——那正是「AI 进程与权威写面可分进程」
# 这层边界要避免的连带（`backend/tests/test_ai_boundary.py` 管的就是这类）。
# 两个开关共用 `CLOSEN_AMQP_URL` 与 `CLOSEN_MQ_MAX_BODY_BYTES`：地址与字节上限是
# 传输事实，进程角色才是开关该分开的东西。

ENV_SOURCE_EVENTS = "CLOSEN_SOURCE_EVENTS"


@dataclass(frozen=True)
class ProducerSwitch:
    enabled: bool
    url: str
    max_body_bytes: int


def load_producer_switch(
    getenv: Callable[[str], str | None] | Mapping[str, str] = os.getenv,
) -> ProducerSwitch:
    """读事件生产侧的两格；关⇒**零副作用**（不拨号、不声明、不投递）。

    默认关是本批唯一的灰度形状：现网没人接线时，删一条记忆的 HTTP 行为必须与今天
    逐字相同（多一个连接、多一次 await 都算行为变更）。
    """
    lookup = getenv.get if isinstance(getenv, Mapping) else getenv
    enabled = _flag(lookup(ENV_SOURCE_EVENTS))
    url = (lookup(ENV_URL) or "").strip()
    if enabled and not url:
        raise SwitchError(
            f"{ENV_SOURCE_EVENTS} 开了但 {ENV_URL} 为空 ⇒ 删除事件会全部落在发布失败那一支，"
            "拒绝启动这种配置"
        )
    return ProducerSwitch(
        enabled=enabled,
        url=url,
        max_body_bytes=_positive_int(
            ENV_MAX_BODY, lookup(ENV_MAX_BODY), DEFAULT_MAX_BODY_BYTES
        ),
    )

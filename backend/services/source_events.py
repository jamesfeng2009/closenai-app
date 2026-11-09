"""T2-06 · 事件生产者：删掉一行 `memories` 之后，把 `source.deleted` 投上 `closenai.events`。

这条面存在的理由（工作单 T2-06 §为什么）：`source_records` 是 D-2 的**薄指针**——Go 那边
只登记「哪一行指过谁的哪一行」，不复制内容。所以用户删掉一条记忆之后，已经入账的
依据不会自己失效，回链会指着一条已经不存在的行。Python 是那张行的唯一持有者，
因此这一段**只有 Python 能发射**；Go 侧的消费面（`internal/messaging/inbound/source_deleted.go`）
是它的第二个调用方，两边中间只有 broker。

四条口径，每条都对应一个会坏的具体形状：

1. **排在提交之后**。`live_support` 是按 `source_records.status <> 'deleted'` 算的，
   而事件本身说的是「这一行没了」；投在提交前，Go 读到的还是翻转前的自己。
   接线点在 `backend/core/db_route.py` 的 post-commit hooks（为什么不是
   `BackgroundTasks` 见那个文件的模块头）。
2. **默认关 ⇒ 零副作用**。`CLOSEN_SOURCE_EVENTS` 没开时不拨号、不声明、不投递，
   删一条记忆的 HTTP 行为与今天逐字相同——这是灰度 0 唯一可测的写法。
3. **失败不改响应，改留一条 pending 行**。数据已提交，500 只会让用户重试删除
   拿到 404。投递意图与删除写在**同一笔事务**里（`source_event_outbox`），提交之后
   的那次投递失败只把行留在 pending，由 `/cron/purge-deleted-users` 按退避补投
   （P2 #20；此前这里只有日志，事件可能永久丢失）。
4. **id 由指针确定性派生**（`contract.source_deleted_message_id`）：同一次删除投两次
   会被 Go 的 inbox `(message_id, consumer)` 判成同一件事；带时间或随机就会让
   「重复传播」看起来像两次不同的删除。这个 id 同时是本地投递台账的主键，重试收敛成一行。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.ai.mq.config import ProducerSwitch, SwitchError, load_producer_switch
from backend.ai.mq.contract import (
    CONTENT_TYPE_JSON,
    SOURCE_DELETED_CONSUMER,
    SOURCE_DELETED_MESSAGE_TYPE,
    SOURCE_DELETED_QUEUE,
    SOURCE_DELETED_ROUTING_KEY,
    SOURCE_DELETED_TABLE,
    ContractError,
    encode_source_deleted,
    source_deleted_message_id,
)
from backend.ai.mq.publisher import TopologyMissing, open_event_publisher
from backend.ai.mq.topology import EXCHANGE_EVENTS
from backend.core.db_route import add_post_commit_hook
from backend.core.logging import get_trace_id
from backend.models.source_event import SourceEventOutbox

log = logging.getLogger("backend.services.source_events")

#: 报告里 `reason` 的封闭取值面。多一个值＝运维查询多一个没人认领的桶，
#: 与 `handlers/observation.py:METRIC_RESULTS` 同一条理由。
REASON_SWITCH_OFF = "switch_off"
REASON_SWITCH_INVALID = "switch_invalid"
REASON_CONTRACT = "contract_invalid"
REASON_TOPOLOGY_MISSING = "topology_missing"
REASON_PUBLISH_FAILED = "publish_failed"
#: 正向那一条也是同一个取值面的一员：判据「published 的报告只有一个 reason」
#: 比「失败 reason 有四个」更容易写歪。
REASON_PUBLISHED = "published"
#: 补投时台账行已被删掉（账号注销并发删行）：这一支只该出现在日志里。
REASON_OUTBOX_MISSING = "outbox_missing"

STATUS_PENDING = "pending"
STATUS_PUBLISHED = "published"
STATUS_FAILED = "failed"

#: 补投退避阶梯（秒）：第 1 次失败 30s 后重试，之后逐级拉长，末级重复。
SOURCE_EVENT_RETRY_BACKOFF_SECONDS = (30, 120, 600, 3600)
#: 到顶标 failed 而不是无限重试：毒消息（契约错/拓扑长期缺失）要有终点，
#: 否则每小时一条永远失败的日志，运维就再也看不见新问题。
SOURCE_EVENT_MAX_ATTEMPTS = 5


def _retry_delay(attempt_count: int) -> timedelta:
    """第 `attempt_count` 次失败后的等待时长（超额取阶梯末级）。"""
    index = min(max(attempt_count, 1), len(SOURCE_EVENT_RETRY_BACKOFF_SECONDS)) - 1
    return timedelta(seconds=SOURCE_EVENT_RETRY_BACKOFF_SECONDS[index])


class SourceEventProducer:
    """连接面：懒开、复用、开关关时根本不实例化。

    为什么不在模块导入时建连接：`backend/main.py` 一 import 就会去拨号，而灰度 0 的
    现网里 broker 可能压根没起（拓扑由 Go 独占声明，D-R5-3）——那会把一次正常发布
    变成启动失败。
    """

    def __init__(self, switch: ProducerSwitch) -> None:
        self._switch = switch
        self._conn: Any = None
        self._channel: Any = None
        self._publisher: Any = None
        self._lock = asyncio.Lock()

    async def _ensure_publisher(self) -> Any:
        if self._publisher is not None:
            return self._publisher
        async with self._lock:
            if self._publisher is not None:
                return self._publisher
            import aio_pika

            conn = await aio_pika.connect_robust(self._switch.url)
            try:
                channel = await conn.channel()
                publisher = await open_event_publisher(channel)
            except BaseException:
                await conn.close()
                raise
            self._conn, self._channel, self._publisher = conn, channel, publisher
            return publisher

    async def publish(self, *, user_id: str, source_id: str, source_table: str,
                      trace_id: str = "") -> Any:
        """编码并投出去；拿到 broker 确认才返回消息（未确认由 aio_pika 抛）。"""
        message = encode_source_deleted(
            user_id=user_id,
            source_id=source_id,
            source_table=source_table,
            exchange=EXCHANGE_EVENTS,
            max_body_bytes=self._switch.max_body_bytes,
            trace_id=trace_id,
        )
        publisher = await self._ensure_publisher()
        try:
            await publisher.publish(message)
        except Exception:
            # 连接或 channel 已经坏掉时不许缓存着坏 publisher 继续用：下一次重新声明。
            self._publisher = None
            raise
        return message

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
        self._conn = self._channel = self._publisher = None


_producer: SourceEventProducer | None = None


def get_producer(switch: ProducerSwitch) -> SourceEventProducer:
    """进程内单例：一条 HTTP 长连接，而不是每次删除开一条。"""
    global _producer
    if _producer is None:
        _producer = SourceEventProducer(switch)
    return _producer


async def close_producer() -> None:
    """停机时关掉那条连接（`backend/main.py` 的 lifespan 用）。"""
    global _producer
    producer, _producer = _producer, None
    if producer is not None:
        await producer.close()


async def publish_source_deleted(*, user_id: str, source_id: str,
                                 source_table: str = SOURCE_DELETED_TABLE,
                                 trace_id: str = "") -> dict[str, Any]:
    """投一条 `source.deleted`，返回**报告**而不抛异常。

    返回形状是这条链唯一的可观测面（钩子把异常吞在 `db_route` 里），所以每一支都要
    能回答三件事：投没投、为什么没投、投的是哪条 id。
    """
    try:
        switch = load_producer_switch()
    except SwitchError as exc:
        log.warning("event=source_deleted_skipped reason=%s detail=%s", REASON_SWITCH_INVALID, exc)
        return {"published": False, "reason": REASON_SWITCH_INVALID, "message_id": ""}
    if not switch.enabled:
        return {"published": False, "reason": REASON_SWITCH_OFF, "message_id": ""}

    try:
        message = await get_producer(switch).publish(
            user_id=user_id, source_id=source_id, source_table=source_table, trace_id=trace_id
        )
    except ContractError as exc:
        log.warning(
            "event=source_deleted_failed reason=%s code=%s user_id=%s source_id=%s detail=%s",
            REASON_CONTRACT, exc.reason, user_id, source_id, exc.detail,
        )
        return {"published": False, "reason": REASON_CONTRACT, "code": exc.reason, "message_id": ""}
    except TopologyMissing as exc:
        log.warning(
            "event=source_deleted_failed reason=%s user_id=%s source_id=%s detail=%s",
            REASON_TOPOLOGY_MISSING, user_id, source_id, exc,
        )
        return {"published": False, "reason": REASON_TOPOLOGY_MISSING, "message_id": ""}
    except Exception as exc:  # noqa: BLE001 - 见模块头第 3 条：提交之后的失败不许改响应
        log.warning(
            "event=source_deleted_failed reason=%s user_id=%s source_id=%s detail=%s",
            REASON_PUBLISH_FAILED, user_id, source_id, exc,
        )
        return {"published": False, "reason": REASON_PUBLISH_FAILED, "message_id": ""}

    log.info(
        "event=source_deleted_published exchange=%s routing_key=%s queue=%s consumer=%s "
        "message_type=%s content_type=%s message_id=%s user_id=%s",
        EXCHANGE_EVENTS, SOURCE_DELETED_ROUTING_KEY, SOURCE_DELETED_QUEUE,
        SOURCE_DELETED_CONSUMER, SOURCE_DELETED_MESSAGE_TYPE, CONTENT_TYPE_JSON,
        message.message_id, user_id,
    )
    return {"published": True, "reason": REASON_PUBLISHED, "message_id": message.message_id}


async def deliver_source_event(
    db: AsyncSession,
    message_id: str,
    *,
    now: datetime | None = None,
    row: SourceEventOutbox | None = None,
) -> dict[str, Any]:
    """按台账行投一次，并把结局写回台账（成功 published / 失败 pending＋退避）。

    幂等：已 published 的行直接返回成功，不重复出网。失败只改行不动响应——
    调用方要么在 post-commit 钩子里（异常会被 `db_route` 吞掉），要么是 cron 补投。

    `row` 可由调用方传入（post-commit 钩子手里已经有那一行）：这样出网之前不碰库，
    「提交之后才投」那条判据（`db.in_transaction() is False`）才不会被台账读取污染。
    """
    now = now or datetime.now(timezone.utc)
    if row is None:
        row = await db.get(SourceEventOutbox, message_id)
    if row is None:
        log.warning("event=source_deleted_outbox_missing message_id=%s", message_id)
        return {"published": False, "reason": REASON_OUTBOX_MISSING, "message_id": message_id}
    if row.status == STATUS_PUBLISHED:
        return {"published": True, "reason": REASON_PUBLISHED, "message_id": message_id}

    report = await publish_source_deleted(
        user_id=row.user_id,
        source_id=row.source_id,
        source_table=row.source_table,
        trace_id=row.trace_id,
    )
    if report["published"]:
        row.status = STATUS_PUBLISHED
        row.published_at = now
        row.last_error = None
    else:
        reason = str(report.get("reason") or REASON_PUBLISH_FAILED)
        row.last_error = reason[:512]
        if reason in (REASON_SWITCH_OFF, REASON_SWITCH_INVALID):
            # 开关被事后关掉不是这一条事件的失败：不记次数，等开关打开再补投。
            row.available_at = now + _retry_delay(1)
        else:
            row.attempt_count = (row.attempt_count or 0) + 1
            if row.attempt_count >= SOURCE_EVENT_MAX_ATTEMPTS:
                row.status = STATUS_FAILED
                log.error(
                    "event=source_deleted_dead message_id=%s user_id=%s source_id=%s "
                    "attempts=%s last_error=%s",
                    message_id, row.user_id, row.source_id,
                    row.attempt_count, row.last_error,
                )
            else:
                row.available_at = now + _retry_delay(row.attempt_count)
    row.updated_at = now
    await db.commit()
    return report


async def retry_pending_source_events(
    db: AsyncSession, *, limit: int = 20, now: datetime | None = None
) -> dict[str, int]:
    """把到点的 pending 行补投一遍（`/cron/purge-deleted-users` 每小时调用）。

    重复投递由消费侧 `(message_id, consumer)` 兜住（18 §12：at-least-once）；
    两个 cron 同时扫到同一行也只多一条幂等消息，不会多销一次账。
    """
    now = now or datetime.now(timezone.utc)
    result = await db.execute(
        select(SourceEventOutbox.message_id)
        .where(
            SourceEventOutbox.status == STATUS_PENDING,
            SourceEventOutbox.available_at <= now,
        )
        .order_by(SourceEventOutbox.available_at.asc())
        .limit(limit)
    )
    message_ids = list(result.scalars().all())
    published = failed = 0
    for message_id in message_ids:
        report = await deliver_source_event(db, message_id, now=now)
        if report["published"]:
            published += 1
        else:
            failed += 1
    return {"due": len(message_ids), "published": published, "failed": failed}


async def schedule_source_deleted(
    request: Request,
    db: AsyncSession,
    *,
    user_id: str,
    source_id: str,
    source_table: str = SOURCE_DELETED_TABLE,
) -> dict[str, Any]:
    """把一次删除登记成「同一笔事务写台账 + 提交之后再投」；开关关时**什么都不发生**。

    trace_id 在**登记时**取而不是在钩子里取：contextvar 是任务局部的，钩子虽然同任务，
    但把「这一条事件属于哪次请求」钉成注册那一刻的值，才不会被后续复用同一 request
    对象的代码（SSE）悄悄换掉。
    """
    try:
        switch = load_producer_switch()
    except SwitchError:
        # 配置不合法时不在请求路径上抛：`publish_source_deleted` 会带着
        # `switch_invalid` 如实报告，而这里连钩子都不该挂——挂了也只会在日志里
        # 多一条与响应无关的警告。
        log.warning("event=source_deleted_skipped reason=%s", REASON_SWITCH_INVALID)
        return {"scheduled": False, "reason": REASON_SWITCH_INVALID}
    if not switch.enabled:
        return {"scheduled": False, "reason": REASON_SWITCH_OFF}
    trace_id = get_trace_id()
    message_id = source_deleted_message_id(user_id, source_table, source_id)

    # 台账行必须和删行走同一笔事务：提交失败时行随事务消失（没有删除就没有事件），
    # 提交成功而投递失败时行留下，补投才有东西可读。
    row = await db.get(SourceEventOutbox, message_id)
    if row is None:
        row = SourceEventOutbox(
            message_id=message_id,
            user_id=user_id,
            source_table=source_table,
            source_id=source_id,
            trace_id=trace_id or "",
        )
        db.add(row)
        await db.flush()

    async def hook() -> None:
        await deliver_source_event(db, message_id, row=row)

    add_post_commit_hook(request, hook)
    return {"scheduled": True, "reason": REASON_PUBLISHED, "message_id": message_id}

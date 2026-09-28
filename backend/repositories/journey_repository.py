"""
journey_events 数据访问层（架构方案 §二 M2/M3）。

职责：只与 journey_events 表交互，不含编排与文案逻辑。
幂等约定：预写前按 (user_id, event_kind, day_no) 查重，重复预写返回既有行——
onboarding 重放、cron 重试都不会产生第二条同类事件。
"""
import json
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.time_utils import utc_now
from backend.models.journey import JourneyEvent


class JourneyRepository:
    """journey_events 表数据访问。"""

    async def schedule(
        self,
        db: AsyncSession,
        user_id: str,
        event_kind: str,
        day_no: int,
        scheduled_at: datetime,
        payload: Optional[dict] = None,
        memory_refs: Optional[List[dict]] = None,
    ) -> JourneyEvent:
        """预写一条 pending 事件（幂等：同 user+kind+day 命中既有行则更新计划时刻与负载）。"""
        existing = await self.get_by_kind(db, user_id, event_kind, day_no)
        if existing is not None:
            existing.scheduled_at = scheduled_at
            if payload is not None:
                existing.payload = json.dumps(payload, ensure_ascii=False)
            if memory_refs is not None:
                existing.memory_refs = json.dumps(memory_refs, ensure_ascii=False)
            await db.flush()
            return existing
        event = JourneyEvent(
            id=str(uuid4()),
            user_id=user_id,
            event_kind=event_kind,
            day_no=day_no,
            status="pending",
            scheduled_at=scheduled_at,
            payload=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            memory_refs=(
                json.dumps(memory_refs, ensure_ascii=False) if memory_refs is not None else None
            ),
        )
        db.add(event)
        await db.flush()
        return event

    async def get_by_kind(
        self, db: AsyncSession, user_id: str, event_kind: str, day_no: int
    ) -> Optional[JourneyEvent]:
        result = await db.execute(
            select(JourneyEvent).where(
                JourneyEvent.user_id == user_id,
                JourneyEvent.event_kind == event_kind,
                JourneyEvent.day_no == day_no,
            )
        )
        return result.scalars().first()

    async def get(self, db: AsyncSession, event_id: str) -> Optional[JourneyEvent]:
        result = await db.execute(select(JourneyEvent).where(JourneyEvent.id == event_id))
        return result.scalar_one_or_none()

    async def list_due(
        self, db: AsyncSession, now: Optional[datetime] = None, limit: int = 200
    ) -> List[JourneyEvent]:
        """跨用户扫描到期未发事件（按 scheduled_at 正序，供 cron 分批处理）。"""
        moment = now or utc_now()
        result = await db.execute(
            select(JourneyEvent)
            .where(JourneyEvent.status == "pending", JourneyEvent.scheduled_at <= moment)
            .order_by(JourneyEvent.scheduled_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_for_user(
        self,
        db: AsyncSession,
        user_id: str,
        statuses: Optional[List[str]] = None,
        limit: int = 50,
    ) -> List[JourneyEvent]:
        """某用户全部剧本事件（按剧本日序正序）。"""
        stmt = select(JourneyEvent).where(JourneyEvent.user_id == user_id)
        if statuses:
            stmt = stmt.where(JourneyEvent.status.in_(statuses))
        stmt = stmt.order_by(JourneyEvent.day_no.asc(), JourneyEvent.scheduled_at.asc()).limit(
            limit
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def count_unseen(self, db: AsyncSession, user_id: str) -> int:
        """未消费事件数（App 内红点，§5.19.3 频控口径）。"""
        result = await db.execute(
            select(func.count())
            .select_from(JourneyEvent)
            .where(
                JourneyEvent.user_id == user_id,
                JourneyEvent.status == "sent",
                JourneyEvent.acknowledged.is_(False),
            )
        )
        return int(result.scalar() or 0)

    async def has_sent_on(
        self, db: AsyncSession, user_id: str, start: datetime, end: datetime
    ) -> bool:
        """本地今日是否已发过剧本事件（cron 重复触发不重发的去重依据）。"""
        result = await db.execute(
            select(func.count())
            .select_from(JourneyEvent)
            .where(
                JourneyEvent.user_id == user_id,
                JourneyEvent.status.in_(("sent", "seen")),
                JourneyEvent.sent_at >= start,
                JourneyEvent.sent_at < end,
            )
        )
        return int(result.scalar() or 0) > 0

    async def mark_sent(self, db: AsyncSession, event_id: str, sent_at: datetime) -> None:
        await db.execute(
            update(JourneyEvent)
            .where(JourneyEvent.id == event_id)
            .values(status="sent", sent_at=sent_at)
        )

    async def set_payload(
        self,
        db: AsyncSession,
        event_id: str,
        payload: Optional[dict] = None,
        memory_refs: Optional[List[dict]] = None,
    ) -> None:
        """投递时回填渲染结果与引用依据（memory_refs 为空表示本轮未产出引用）。"""
        values: dict = {}
        if payload is not None:
            values["payload"] = json.dumps(payload, ensure_ascii=False)
        if memory_refs is not None:
            values["memory_refs"] = json.dumps(memory_refs, ensure_ascii=False)
        if values:
            await db.execute(update(JourneyEvent).where(JourneyEvent.id == event_id).values(**values))

    async def acknowledge(self, db: AsyncSession, event_id: str) -> None:
        """用户已读（红点清除）。"""
        await db.execute(
            update(JourneyEvent)
            .where(JourneyEvent.id == event_id)
            .values(status="seen", acknowledged=True)
        )

    async def defer(self, db: AsyncSession, event_id: str, until: datetime) -> None:
        """沉默降频：顺延计划时刻（永不删行，§5.19.3 频控铁律）。"""
        await db.execute(
            update(JourneyEvent).where(JourneyEvent.id == event_id).values(scheduled_at=until)
        )

    async def skip(self, db: AsyncSession, event_id: str) -> None:
        """条件不满足（如 D7 无 2 条原话可引用）：置 skipped，不重排。"""
        await db.execute(
            update(JourneyEvent).where(JourneyEvent.id == event_id).values(status="skipped")
        )

    async def pending_kinds(self, db: AsyncSession, user_id: str) -> List[str]:
        result = await db.execute(
            select(JourneyEvent.event_kind).where(
                JourneyEvent.user_id == user_id, JourneyEvent.status == "pending"
            )
        )
        return [r[0] for r in result.all()]


journey_repository = JourneyRepository()


def parse_json(raw: Optional[str], default: object = None) -> object:
    """负载列反序列化（历史空值/坏数据回退默认值，不抛异常）。"""
    if not raw:
        return default if default is not None else {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}

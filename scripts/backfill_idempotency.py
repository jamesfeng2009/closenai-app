"""T5-03 幂等回填：给历史 proactive 行补 idempotency_key，并清 journey 同日重复行。

为什么需要脚本而不是全交给迁移件 c3e5a7b9d1f2：补键要先知道**每一行的用户本地日**
（users.timezone + created_at 折算），那是业务判定，不该塞进 DDL；而且迁移里的
journey 去重只跑一次，迁移之后仍可能因旧代码路径写进重复行，需要一个可重跑的入口。

口径（与迁移件、与运行时写入完全一致，否则回填出来的键和 cron 生成的键不是同一个）：
- 键 = `proactive:<user_id>:<用户本地日历日>:<slot>`，slot 恒为 0（cron 也只发 slot 0）；
- 同一用户同一本地日若有多行（回填正是要治的历史重复），**最早一条**拿到键，
  其余保持 NULL 并只登记不删——chat_messages 没有状态列，删用户看得见的历史
  不在这个脚本的授权范围内；留 NULL 的行不在部分唯一索引谓词内，不影响索引建立；
- journey 重复组保留最早一条，其余置 `skipped`（永不删行的既有口径），与迁移件同判据。

dry-run 是默认：只打印会改什么、改几行。写库要显式 `--apply`。
可重跑：写库时逐行再判一次「键仍为 NULL」「状态仍未 skipped」，第二遍跑出来改 0 行
——这条由 backend/tests/test_idempotency_indexes.py 打成红过。

用法：
    .venv/bin/python scripts/backfill_idempotency.py                 # dry-run
    .venv/bin/python scripts/backfill_idempotency.py --apply
    .venv/bin/python scripts/backfill_idempotency.py --database-url postgresql+psycopg://...
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from backend.core.time_utils import safe_zoneinfo  # noqa: E402
from backend.models.chat import ChatMessage  # noqa: E402
from backend.models.journey import JourneyEvent  # noqa: E402
from backend.models.user import User  # noqa: E402

SLOT = 0  # cron 每日本地 9:00 只发一条，slot 恒 0；键格式改了要和 cron_service 同步


def local_date_of(created_at: datetime, tz_name: str | None) -> date:
    """写入时刻 → 用户本地日历日。

    naive 按 UTC 处理：SQLite 的 DATETIME 回读不带偏移，PG 带。两处的**时刻**相同，
    只差 tzinfo 有无，不这样兜会让测试库和真库回填出两个不同的键。
    """
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at.astimezone(safe_zoneinfo(tz_name)).date()


def key_for(user_id: str, local_day: date) -> str:
    return f"proactive:{user_id}:{local_day.isoformat()}:{SLOT}"


@dataclass
class ChatPlan:
    set_key: list[tuple[str, str]] = field(default_factory=list)  # (行 id, 键)
    left_null: list[str] = field(default_factory=list)            # 同日本就多出的行


@dataclass
class JourneyPlan:
    skip: list[str] = field(default_factory=list)                 # 置 skipped 的行 id


async def plan_chat_keys(db: AsyncSession) -> ChatPlan:
    """按 (user, 本地日) 分组，组内最早一行补键。"""
    rows = (
        await db.execute(
            select(
                ChatMessage.id,
                ChatMessage.user_id,
                ChatMessage.created_at,
                User.timezone,
            )
            .join(User, User.id == ChatMessage.user_id)
            .where(
                ChatMessage.message_type == "proactive",
                ChatMessage.idempotency_key.is_(None),
            )
            .order_by(ChatMessage.user_id, ChatMessage.created_at, ChatMessage.id)
        )
    ).all()

    plan = ChatPlan()
    seen: set[tuple[str, date]] = set()
    for message_id, user_id, created_at, tz_name in rows:
        local_day = local_date_of(created_at, tz_name)
        group = (user_id, local_day)
        if group in seen:
            plan.left_null.append(message_id)
            continue
        seen.add(group)
        plan.set_key.append((message_id, key_for(user_id, local_day)))
    return plan


async def plan_journey_dupes(db: AsyncSession) -> JourneyPlan:
    """同 (user, kind, day) 且非 commitment_follow_up、非 skipped 的重复组：除最早一条外置 skipped。

    判据与迁移件 c3e5a7b9d1f2 逐字一致（「存在更早的兄弟行」），排除项也一致——
    承诺回访同一日多条不同承诺是合法写入，不能当重复。
    """
    rows = (
        await db.execute(
            select(
                JourneyEvent.id,
                JourneyEvent.user_id,
                JourneyEvent.event_kind,
                JourneyEvent.day_no,
                JourneyEvent.created_at,
            ).where(
                JourneyEvent.event_kind != "commitment_follow_up",
                JourneyEvent.status != "skipped",
            )
        )
    ).all()

    groups: dict[tuple[str, str, int], list[tuple[str, datetime]]] = {}
    for event_id, user_id, kind, day_no, created_at in rows:
        moment = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        groups.setdefault((user_id, kind, day_no), []).append((event_id, moment))

    plan = JourneyPlan()
    for members in groups.values():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda m: (m[1], m[0]))
        plan.skip.extend(event_id for event_id, _ in ordered[1:])
    return plan


async def apply(db: AsyncSession, chat: ChatPlan, journey: JourneyPlan) -> None:
    """写库。逐行只动仍是 NULL / 未 skipped 的行，所以重跑时这里改 0 行。"""
    for message_id, key in chat.set_key:
        row = await db.get(ChatMessage, message_id)
        if row is not None and row.idempotency_key is None:
            row.idempotency_key = key
    for event_id in journey.skip:
        row = await db.get(JourneyEvent, event_id)
        if row is not None and row.status != "skipped":
            row.status = "skipped"
    await db.commit()


def report(chat: ChatPlan, journey: JourneyPlan, applied: bool) -> None:
    verb = "已" if applied else "将"
    print(f"[chat_messages] {verb}补键 {len(chat.set_key)} 行；"
          f"同日本就多出的行保持 NULL {len(chat.left_null)} 行（不删，见脚本口径）")
    for message_id, key in chat.set_key[:10]:
        print(f"  {verb}设键 {message_id} -> {key}")
    print(f"[journey_events] {verb}置 skipped {len(journey.skip)} 行")
    for event_id in journey.skip[:10]:
        print(f"  {verb}skip {event_id}")
    if not applied and (chat.set_key or journey.skip):
        print("（dry-run：以上变更未写库，加 --apply 才执行）")


async def run(database_url: str, apply_changes: bool) -> tuple[ChatPlan, JourneyPlan]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(database_url)
    try:
        async with async_sessionmaker(bind=engine, class_=AsyncSession)() as db:
            chat = await plan_chat_keys(db)
            journey = await plan_journey_dupes(db)
            if apply_changes:
                await apply(db, chat, journey)
    finally:
        await engine.dispose()
    return chat, journey


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None,
                        help="缺省读 backend.config.settings.database_url")
    parser.add_argument("--apply", action="store_true",
                        help="真正写库；缺省是 dry-run")
    args = parser.parse_args()

    url = args.database_url
    if not url:
        from backend.config import settings

        url = settings.database_url

    chat, journey = asyncio.run(run(url, args.apply))
    report(chat, journey, args.apply)


if __name__ == "__main__":
    main()

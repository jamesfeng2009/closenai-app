"""T5-05 的实库面（2026-10-18）：三种关闭 + 对照组，跑的是 `dispatch_daily_messages` 本体。

sqlite 那批（test_delivery_off_e2e.py）证的是门的**位置与形状**；这条证的是真库上
这条路真的走得通——PG 的 TIME 列、部分唯一索引、`FOR UPDATE` 行锁都是 sqlite 没有的
东西，门只要在这些面前报错，process_batch 会把它吞成 `failed` 而不是 `skipped_*`，
「关掉就不发」在 sqlite 绿、在真库变成「谁都发不出去」，正是这条用例要拦的。

一条硬约束：`dispatch_daily_messages` 扫的是 `list_all` 里的**全部**用户。
指向共享库跑它，等于替所有到点用户各生成一条主动消息。所以本文件开局就查库里
有没有别人的数据，有则直接 fail（不是 skip——skip 会让"实库跑过"这句话失去取证）。

用法（一次性空库，跑完即删，不长占本机）：
    docker exec closenai-pg-dev psql -U postgres -c 'CREATE DATABASE w5_e2e'
    N2_MIGRATE_DATABASE_URL='postgresql+psycopg://postgres:***@127.0.0.1:55432/w5_e2e' \
        .venv/bin/alembic upgrade head
    TEST_DATABASE_URL='postgresql://postgres:***@127.0.0.1:55432/w5_e2e' \
        CLOSEN_REQUIRE_PG=1 .venv/bin/pytest backend/tests/test_delivery_off_pg.py -q
    docker exec closenai-pg-dev psql -U postgres -c 'DROP DATABASE w5_e2e'
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from _pg_env import pg_test_url
from backend.config import settings
from backend.models.chat import ChatMessage
from backend.models.user import User
from backend.repositories import chat_repository, user_repository
from backend.services import cron_service as cron_module
from backend.services.cron_service import cron_service
from backend.services.message_generator import message_generator

pytestmark = pytest.mark.asyncio


class PushSpy:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_to_user(self, db, user_id, title, body, data=None):
        self.sent.append((user_id, title))
        return {"sent": 1, "failed": 0}


def _window(inside: bool):
    now = datetime.now(ZoneInfo("UTC"))
    if inside:
        return (now - timedelta(hours=1)).time(), (now + timedelta(hours=1)).time()
    return (now + timedelta(hours=2)).time(), (now + timedelta(hours=3)).time()


@pytest.fixture
async def pg(monkeypatch):
    """真库会话 + 把外发动作全部就地截住（LLM、QStash、推送）。

    w5 前缀的行在**开局和收尾都清一遍**：这条用例要在同一个一次性库里可重跑
    （`processed == 4` 判的是本轮 due 用户数，上一遍留下的 w5conc 会把它变成 5）。
    """

    async def _purge(session: AsyncSession) -> None:
        await session.execute(delete(ChatMessage).where(ChatMessage.user_id.like("w5%")))
        await session.execute(delete(User).where(User.id.like("w5%")))
        await session.commit()

    url = pg_test_url(purpose="T5-05 真库 E2E，需 alembic upgrade head 后的一次性空库")
    engine = create_async_engine(url)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as probe:
        others = int(await probe.scalar(
            select(func.count(User.id)).where(User.id.not_like("w5%"))
        ) or 0)
    # 判据是「库里除了本用例的 w5 前缀行，一行用户都不许有」——写宽了就当空库误闯。
    assert others == 0, (
        f"库里已有 {others} 个别的用户：dispatch_daily_messages 会替所有到点用户生成消息，"
        "本用例只能在 alembic upgrade head 后的一次性空库上跑"
    )

    monkeypatch.setattr(settings, "qstash_token", "", raising=False)
    monkeypatch.setattr(settings, "backend_base_url", "", raising=False)
    monkeypatch.setattr(cron_service, "_jitter_minutes", lambda user, day, hour=None: 0)
    spy = PushSpy()
    monkeypatch.setattr(cron_module, "push_service", spy)

    calls: list[str] = []

    async def fake_generate(db, user_id, language):
        calls.append(user_id)
        return "早安，今天想聊点什么？"

    monkeypatch.setattr(message_generator, "generate_proactive_message", fake_generate)

    async with factory() as session:
        await _purge(session)
        yield SimpleNamespace(db=session, spy=spy, calls=calls, factory=factory)
        await _purge(session)

    await engine.dispose()


async def _make(db, user_id: str, **overrides):
    user = await user_repository.get_or_create_user(db, user_id)
    for key, value in overrides.items():
        setattr(user, key, value)
    await db.commit()
    return user


async def _rows(db, user_id: str) -> int:
    return int(await db.scalar(
        select(func.count(ChatMessage.id)).where(ChatMessage.user_id == user_id)
    ) or 0)


async def test_real_pg_quiet_off_and_control(pg):
    """三红一绿同库同轮：静默与 off 零落库，push_enabled=False 零推送但落库，对照组 1+1。

    末尾再跑一轮（重放）：`dispatch_daily_messages` 每小时被 crontab 打一次，
    同一本地日内第二次必须计成 `already_sent` 而不是第二行 proactive——
    sqlite 那条用例证不了 PG 上的当日去重查询（timestamptz 开区间 + 本地日折算）。
    """
    pg_ctx = pg
    db, spy, calls = pg_ctx.db, pg_ctx.spy, pg_ctx.calls
    due = datetime.now(ZoneInfo("UTC")).strftime("%H:00")
    q_start, q_end = _window(inside=True)
    out_start, out_end = _window(inside=False)

    await _make(db, "w5q", ai_consent=True, timezone="UTC", push_time=due,
               push_enabled=True, push_frequency="daily", quiet_start=q_start, quiet_end=q_end)
    await _make(db, "w5off", ai_consent=True, timezone="UTC", push_time=due,
               push_enabled=True, push_frequency="off", quiet_start=out_start, quiet_end=out_end)
    await _make(db, "w5nopush", ai_consent=True, timezone="UTC", push_time=due,
               push_enabled=False, push_frequency="daily",
               quiet_start=out_start, quiet_end=out_end)
    await _make(db, "w5on", ai_consent=True, timezone="UTC", push_time=due,
               push_enabled=True, push_frequency="daily",
               quiet_start=out_start, quiet_end=out_end)

    result = await cron_service.dispatch_daily_messages(db, "UTC")
    await db.commit()

    assert result["status"] == "completed", result
    assert result["processed"] == 4, f"四个用户没全进本轮（due 筛选在真库上没成立）：{result}"
    assert result["failed"] == 0, f"真库把门跑成了异常：{result}"
    assert result["skipped_quiet"] == 1
    assert result["skipped_off"] == 1
    assert result["success"] == 2, "对照组与 push_enabled=False 那档都该落库"

    assert await _rows(db, "w5q") == 0, "PG 上静默窗口内出现了 proactive 行"
    assert await _rows(db, "w5off") == 0, "PG 上 off 档出现了 proactive 行"
    assert await _rows(db, "w5nopush") == 1
    assert await _rows(db, "w5on") == 1
    assert sorted(calls) == ["w5nopush", "w5on"], "静默/off 两档不该调用生成（配额与成本）"
    assert [uid for uid, _ in spy.sent] == ["w5on"], "push_enabled=False 那档不该推送"

    spy.sent.clear()
    calls.clear()
    again = await cron_service.dispatch_daily_messages(db, "UTC")
    await db.commit()

    assert again["skipped_already_sent"] == 2, f"第二轮没认出当日已发：{again}"
    assert again["success"] == 0
    assert await _rows(db, "w5on") == 1, "重放同一天多写了一行 proactive"
    assert calls == [], "重放轮不该再花一次 LLM"
    assert spy.sent == [], "重放轮不该再推第二遍"


async def test_real_pg_concurrent_same_key_writes_one_row(pg):
    """T5-03 验收里 sqlite 给不出的那半句：真并发同一键 ⇒ 只有一行。

    两条独立 session 同时 INSERT 同一个 `idempotency_key`：PG 的部分唯一索引会让后到
    的那条**等**到先到者提交，再拿到 IntegrityError——`create_idempotent` 必须把它
    吞成 (既有行, created=False)，而不是冒 500（Baseline 不变量 9）。
    sqlite 是单写者，这条形状只能证「同键两次」，证不了「同时」。
    """
    db = pg.db
    await _make(db, "w5conc", ai_consent=True, timezone="UTC")

    key = "proactive:w5conc:2026-10-18:0"

    async def write_once(tag: str) -> bool:
        async with pg.factory() as session:
            _, created = await chat_repository.create_idempotent(
                session, "w5conc", "assistant", tag, "proactive", key
            )
            await session.commit()
            return created

    results = await asyncio.gather(write_once("A"), write_once("B"))

    assert sorted(results) == [False, True], (
        f"并发同键没走成「一新建 + 一幂等命中」：{results}（幂等成功必须是 200 语义，不是 500）"
    )
    assert await _rows(db, "w5conc") == 1, "真并发下写了两行同键 proactive"

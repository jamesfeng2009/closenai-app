"""T1-02 的实库面（2026-10-18）：CAS 认领与豁免删除顺序，sqlite 给不出的两半。

`test_posthog_erasure_retry.py`（sqlite）证的是状态流转、退避与告警时钟；只有真库能证的在这里：

① 认领是 CAS（`UPDATE … WHERE next_attempt_at <= :now` 的 rowcount）。sqlite 是单写者，
   两条 session 永远排不成「同时」，所以「两个 worker 不许把同一个人擦两遍」这句话
   在 sqlite 侧只是注释。这里两条真 session 各跑一轮完整重试（判批次结果），再加一条
   确定性的跨 session 认领（判谓词本身——变异打掉 `<= now` 时红的是它）。
② `PURGE_EXEMPT_TABLES` 的豁免在**迁移出来的库**上是真的：`purge_user` 的删除循环跑完
   41 张表之后，它刚写的重试台账必须还在。sqlite 用 `create_all`、生产用 alembic DDL，
   两边形状本就不同（唯一约束与部分索引的落法），豁免只在其中一边成立过，
   生产就是那个没成立的边。

一次性空库，跑完即删（本机是工作机，不长占）：
    docker exec closenai-pg-dev psql -U postgres -c 'CREATE DATABASE t102_e2e'
    DATABASE_URL='postgresql+psycopg://postgres:***@127.0.0.1:55432/t102_e2e' \
        .venv/bin/python -m alembic -c backend/alembic.ini upgrade head
    TEST_DATABASE_URL='postgresql://postgres:***@127.0.0.1:55432/t102_e2e' \
        CLOSEN_REQUIRE_PG=1 .venv/bin/pytest backend/tests/test_posthog_erasure_retry_pg.py -q
    docker exec closenai-pg-dev psql -U postgres -c 'DROP DATABASE t102_e2e'
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from _pg_env import pg_test_url
from backend.config import settings
from backend.core.time_utils import utc_now
from backend.models.chat import ChatMessage
from backend.models.erasure import PostHogErasureRetry
from backend.models.user import User
from backend.repositories import erasure_repository, user_repository
from backend.services.user_service import user_service

pytestmark = pytest.mark.asyncio

PREFIX = "t102"
HEAD = "f6b8d0e2a4c6"  # 当前 alembic head（T4-01 件挂在 T1-02 件之上）


@pytest.fixture
async def pg(monkeypatch):
    """真库会话工厂；开局与收尾都清 `t102` 前缀，同一库可重跑。

    闸门判的是「这库除了本用例的前缀行以外一行用户都不许有」：`purge_user` 按 user_id 删，
    指错库就等于替别人删干净数据——所以缺 env 是 fail（`CLOSEN_REQUIRE_PG`），
    库里有别人也是 fail，不是 skip。
    """
    url = pg_test_url(purpose="T1-02 真库 CAS/豁免取证，需 alembic upgrade head 后的一次性空库")
    engine = create_async_engine(url)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def _purge(session: AsyncSession) -> None:
        like = f"{PREFIX}%"
        await session.execute(delete(PostHogErasureRetry).where(
            PostHogErasureRetry.user_id.like(like)))
        await session.execute(delete(ChatMessage).where(ChatMessage.user_id.like(like)))
        await session.execute(delete(User).where(User.id.like(like)))
        await session.commit()

    async with factory() as probe:
        head = await probe.scalar(text("select version_num from alembic_version"))
        others = int(await probe.scalar(
            select(func.count(User.id)).where(User.id.not_like(f"{PREFIX}%"))
        ) or 0)
    assert head == HEAD, f"库的 alembic head={head}，不是 T1-02 的 posthog_erasure_retries"
    assert others == 0, f"库里已有 {others} 个别的用户：本用例会在真库上删数据，只能跑在一空库"

    async with factory() as session:
        await _purge(session)
        yield SimpleNamespace(db=session, factory=factory)
        await _purge(session)

    await engine.dispose()


async def _make_user(db: AsyncSession, user_id: str) -> None:
    await user_repository.get_or_create_user(db, user_id)
    await db.commit()


def _stub_erase(monkeypatch, *, erased: bool, reason: str = "http_500") -> list[str]:
    seen: list[str] = []

    async def fake(distinct_id: str) -> dict:
        seen.append(distinct_id)
        # 让出一次事件循环：两个 worker 的「同时」就体现在这里——不让的话
        # gather 出来的两条协程会各自跑完，CAS 根本没机会互相撞。
        await asyncio.sleep(0)
        return {"erased": True, "status": 202} if erased else {"erased": False, "reason": reason}

    monkeypatch.setattr("backend.services.user_service.erase_person_data", fake)
    return seen


async def _seed_due(pg) -> tuple[str, str]:
    """写一行到点的重试台账，返回 (row_id, user_id)。"""
    user_id = f"{PREFIX}-u{uuid4().hex[:8]}"
    now = utc_now()
    async with pg.factory() as session:
        await _make_user(session, user_id)
        row = await erasure_repository.record_failure(
            session, row_id=str(uuid4()), user_id=user_id, reason="http_500",
            now=now - timedelta(minutes=5), next_attempt_at=now - timedelta(minutes=1),
        )
        await session.commit()
        return row.id, user_id


async def _t102_left(session: AsyncSession) -> list[str]:
    """本用例前缀下还欠着外部擦除的用户。

    按前缀而不是整表计数：同一张一次性库会被 T1-01 的 `test_data_export_pg.py` 用过，
    而它每次注销都会写一行台账（`purge_user` 的 T1-02 新行为）。整表计数会把别人的残行
    算成我们这边的失败，那条红到不了自己的靶。
    """
    return list((await session.execute(
        select(PostHogErasureRetry.user_id).where(PostHogErasureRetry.user_id.like(f"{PREFIX}%"))
    )).scalars().all())


async def test_real_pg_two_workers_erase_the_same_person_once(pg, monkeypatch):
    """①：两条真 session 各跑一轮完整重试，同一个人只被处理一次、台账行消失。

    这条判的是**批次结果**（对外调用一次、只有一方做过动作），不单独承担 CAS 的证明责任——
    两个 worker 在单事件循环上的交错顺序不固定，抢没抢到都可能出现。CAS 谓词本身由下一条
    确定性的跨 session 认领用例打红。

    2026-10-18 变异实测补一句边界：把 `claim()` 里「认领同时推 `next_attempt_at`」摘掉，
    打不红这条——stub 走的是擦成功路径，第一个 worker 成功后把台账行删掉了，行没了本身就是
    第二道闸。所以这条只对「同一个人不会被处理两遍」负责，谓词与推时刻都由下一条判。
    """
    seen = _stub_erase(monkeypatch, erased=True)
    await _seed_due(pg)

    async def run() -> dict:
        async with pg.factory() as session:
            out = await user_service.retry_pending_erasures(session)
            await session.commit()
            return out

    outs = await asyncio.gather(run(), run())

    assert len(seen) == 1, f"同一个人被擦了 {len(seen)} 次（PostHog 被重复打）"
    actions = sum(o["erased"] + o["pending"] + o["alerted"] for o in outs)
    assert actions == 1, f"两轮重试做了 {actions} 次动作，应当只有一次：{outs}"
    async with pg.factory() as check:
        assert await _t102_left(check) == [], "擦成功后台账行必须消失"


async def test_real_pg_claim_is_cross_session_single_use(pg, monkeypatch):
    """①的确定性版本：A 提交后再让 B 认领同一行，B 必须拿不到。"""
    _stub_erase(monkeypatch, erased=True)
    row_id, _ = await _seed_due(pg)
    now = utc_now()

    async with pg.factory() as a:
        first = await erasure_repository.claim(a, row_id, now=now, next_attempt_at=now + timedelta(hours=1))
        await a.commit()
    async with pg.factory() as b:
        second = await erasure_repository.claim(b, row_id, now=now, next_attempt_at=now + timedelta(hours=1))
        await b.commit()

    assert first is True and second is False, (
        "跨 session 的 CAS 认出来了两次：谓词 next_attempt_at<=now 没起作用")


async def test_real_pg_purge_keeps_the_retry_row_it_just_wrote(pg, monkeypatch):
    """②：豁免在迁移出来的库上是真的——删除循环跑完 41 张表后台账行还在。"""
    _stub_erase(monkeypatch, erased=False, reason="request_failed")
    user_id = f"{PREFIX}-p{uuid4().hex[:8]}"
    async with pg.factory() as session:
        await _make_user(session, user_id)
        session.add(ChatMessage(id=f"{PREFIX}-c1", user_id=user_id, role="user", content="今天很难受"))
        await session.commit()

        counts = await user_service.purge_user(session, user_id)
        await session.commit()

        assert counts["users"] == 1 and counts["chat_messages"] == 1
        assert await session.scalar(
            select(User.id).where(User.id == user_id)
        ) is None, "users 行没删掉"
        left = await _t102_left(session)
        assert left == [user_id], (
            "purge_user 把自己刚写的重试行删了：PURGE_EXEMPT_TABLES 的豁免在真库上不成立")

    async with pg.factory() as cleanup:
        await erasure_repository.delete(cleanup, (await cleanup.execute(
            select(PostHogErasureRetry.id).where(PostHogErasureRetry.user_id == user_id)
        )).scalar_one())
        await cleanup.commit()


async def test_real_pg_unique_guard_rejects_a_second_row_for_same_user(pg):
    """唯一约束不许只活在注释里：同一 user 叠第二行必须被库拒绝。"""
    user_id = f"{PREFIX}-q{uuid4().hex[:8]}"
    now = utc_now()
    async with pg.factory() as session:
        for _ in range(2):
            session.add(PostHogErasureRetry(
                id=str(uuid4()), user_id=user_id, last_reason="http_500", attempts=1,
                first_failed_at=now, next_attempt_at=now, last_attempt_at=now,
            ))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

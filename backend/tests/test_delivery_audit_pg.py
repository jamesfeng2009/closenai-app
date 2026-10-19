"""T1-04 的实库面（2026-10-18）：交付审计的那句「付费之后」在真库 DDL 上算不算得对。

sqlite 侧（`test_delivery_audit.py`）证的是判据形状、宽限期、阈值与告警；只有真库能证的有两件：

① **跨表时间戳比较**。审计的交付谓词是相关子查询里的
   `share_exports.created_at >= user_entitlements.purchase_date`。sqlite 把
   `DateTime(timezone=True)` 存成字符串，比较是**字典序**——同一 UTC 偏移下恰好等于时间序，
   所以 sqlite 永远看不出这条写错；生产库是 `timestamptz`，比较的是真实瞬间。
   只在其中一边成立的判据，生产就是那个没成立的边。
② **计数跑在迁移出来的 DDL 上**。`distinct(user_id)` × 两笔同用户买断 × `revoked_at IS NULL`
   这三件事在 `create_all` 的 sqlite 表上没有唯一约束与真索引参与，形状本就与 alembic
   落出来的库不同（T1-02 同一理由）。

一次性空库，跑完即删（本机是工作机，不长占）：
    docker exec closenai-pg-dev psql -U postgres -c 'CREATE DATABASE t104_e2e'
    DATABASE_URL='postgresql+psycopg://postgres:***@127.0.0.1:55432/t104_e2e' \\
        .venv/bin/python -m alembic -c backend/alembic.ini upgrade head
    TEST_DATABASE_URL='postgresql://postgres:***@127.0.0.1:55432/t104_e2e' \\
        CLOSEN_REQUIRE_PG=1 ./.venv/bin/pytest backend/tests/test_delivery_audit_pg.py -q
    docker exec closenai-pg-dev psql -U postgres -c 'DROP DATABASE t104_e2e'
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from _pg_env import pg_test_url
from backend.config import settings
from backend.core.time_utils import utc_now
from backend.models.journey import UserEntitlement
from backend.models.share import ShareExport
from backend.models.user import User
from backend.repositories import entitlement_repository, user_repository
from backend.services.delivery_service import METRIC_MARKER, audit_paid_delivery

pytestmark = pytest.mark.asyncio

PREFIX = "t104"
HEAD = "f6b8d0e2a4c6"  # 当前 alembic head（T4-01 件挂在 T1-02 件之上）


@pytest.fixture
async def pg(monkeypatch):
    """真库会话工厂。

    审计数的是**全表**（`select count(distinct user_id) from user_entitlements …`），
    所以别的用例留下的行会直接混进分母——这里开局就要求两张靶表在前缀之外一行都没有，
    有就是 fail 而不是 skip：那条计数到不了自己的靶（[[feedback-mutation-anchor-must-reach-target]]）。
    """
    url = pg_test_url(purpose="T1-04 真库交付审计取证，需 alembic upgrade head 后的一次性空库")
    engine = create_async_engine(url)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def _purge(session: AsyncSession) -> None:
        like = f"{PREFIX}%"
        await session.execute(delete(ShareExport).where(ShareExport.user_id.like(like)))
        await session.execute(delete(UserEntitlement).where(UserEntitlement.user_id.like(like)))
        await session.execute(delete(User).where(User.id.like(like)))
        await session.commit()

    async with factory() as probe:
        head = await probe.scalar(text("select version_num from alembic_version"))
        stray_users = int(await probe.scalar(
            select(func.count(User.id)).where(User.id.not_like(f"{PREFIX}%"))
        ) or 0)
        stray_paid = int(await probe.scalar(
            select(func.count(UserEntitlement.id)).where(
                UserEntitlement.user_id.not_like(f"{PREFIX}%")
            )
        ) or 0)
    assert head == HEAD, f"库的 alembic head={head}，不是 T1-04 审计依赖的那张权益表"
    assert stray_users == 0 and stray_paid == 0, (
        f"库里有别人的行（用户 {stray_users}、权益 {stray_paid}）："
        "审计按全表计数，混库的数就不是这条判据的数"
    )

    async with factory() as session:
        await _purge(session)
        yield SimpleNamespace(db=session, factory=factory)
        await _purge(session)

    await engine.dispose()


async def _pay(
    db: AsyncSession, user_id: str, *, hours_ago: float,
    scope: str = "all", tx: str | None = None, revoked_hours_ago: float | None = None,
) -> None:
    await user_repository.get_or_create_user(db, user_id)
    row = await entitlement_repository.grant(
        db,
        user_id,
        product_id=f"life_archive_{scope}",
        scope=scope,
        transaction_id=tx or f"tx-{user_id}",
        purchase_date=utc_now() - timedelta(hours=hours_ago),
        original_transaction_id=tx or f"tx-{user_id}",
        environment="Production",
    )
    if revoked_hours_ago is not None:
        row.revoked_at = utc_now() - timedelta(hours=revoked_hours_ago)
    await db.commit()


async def _export(db: AsyncSession, user_id: str, *, hours_ago: float) -> None:
    db.add(
        ShareExport(
            id=str(uuid4()),
            user_id=user_id,
            period="life_archive",
            period_key="all",
            payload="{}",
            created_at=utc_now() - timedelta(hours=hours_ago),
        )
    )
    await db.commit()


async def test_real_pg_export_after_purchase_clears_but_before_does_not(pg):
    """①：同一种「一行 life_archive 导出」，付费之后的算交付、付费之前的不算。

    sqlite 里这条比较是字符串字典序，恰好与时间序同向，所以永远看不出写错；
    `timestamptz` 才是生产真正执行的那次比较。
    """
    ok_user = f"{PREFIX}-ok-{uuid4().hex[:6]}"
    stale_user = f"{PREFIX}-stale-{uuid4().hex[:6]}"

    await _pay(pg.db, stale_user, hours_ago=48, tx=f"tx-{stale_user}")
    await _export(pg.db, stale_user, hours_ago=720)  # 付费**之前**的订阅期免费导出

    async with pg.factory() as session:
        await _pay(session, ok_user, hours_ago=72, tx=f"tx-{ok_user}")
        await _export(session, ok_user, hours_ago=24)  # 付费**之后**的交付

    result = await audit_paid_delivery(pg.db)
    assert result["paid_users"] == 2, result
    assert result["undelivered_users"] == 1, (
        f"真库上的未交付数={result['undelivered_users']}：「付费之后」这条比较没算对 — {result}"
    )
    assert result["alert"] is (1 > settings.delivery_audit_alert_threshold), result


async def test_real_pg_distinct_counts_users_not_rows_and_revoked_leaves(pg, caplog):
    """②：两笔买断算一个人；退款撤销的人出人群、进 revoked 分子。

    跑在迁移出来的 DDL 上：`distinct(user_id)` 与 `revoked_at IS NULL` 这两半
    在 sqlite 的 `create_all` 表上没有真约束与索引参与。
    """
    two = f"{PREFIX}-two-{uuid4().hex[:6]}"
    refunded = f"{PREFIX}-refund-{uuid4().hex[:6]}"

    await _pay(pg.db, two, hours_ago=72, scope="all", tx=f"tx-{two}-1")
    await _pay(pg.db, two, hours_ago=72, scope="1y", tx=f"tx-{two}-2")
    async with pg.factory() as session:
        await _pay(session, refunded, hours_ago=72, tx=f"tx-{refunded}", revoked_hours_ago=10)

    caplog.set_level(logging.INFO, logger="closenai.delivery")
    result = await audit_paid_delivery(pg.db)
    assert result["paid_users"] == 1, f"两笔买断被数成 {result['paid_users']} 个人：{result}"
    assert result["undelivered_users"] == 1, result
    assert result["revoked_users"] == 1, f"撤销的那行没进退款分子：{result}"

    line = [
        r.getMessage() for r in caplog.records
        if r.getMessage().startswith(METRIC_MARKER)
    ][0]
    fields = dict(re.findall(r"(\w+)=(\S+)", line[len(METRIC_MARKER):]))
    assert fields["paid_users"] == "1" and fields["undelivered_users"] == "1", fields
    assert fields["revoked_users"] == "1", fields
    assert fields["grace_hours"] == str(settings.delivery_audit_grace_hours), fields

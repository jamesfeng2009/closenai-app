"""进程内 purge 兜底循环（main.py J1）回归测试。

覆盖：
- _purge_sweep_once：扫描并提交物理删除（与 /cron/purge-deleted-users 同口径）
- lifespan：purge_loop_enabled=True 启动循环 / False 不启动
- 退出 lifespan 必须取消循环（否则测试/热重载留下悬挂 task）
- 单轮异常不终止循环（循环必须活着）
"""
import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.main as main_mod
import backend.models  # noqa: F401  注册全部表到 Base.metadata
from backend.core.time_utils import utc_now
from backend.database import Base
from backend.models.user import User
from backend.repositories import user_repository


@pytest.fixture
async def sweep_session_factory(tmp_path):
    """独立 sqlite 文件库的 session 工厂（_purge_sweep_once 内部自建会话）。

    factory.commits 记录显式 commit 次数：sqlite 下 begin_nested 释放 SAVEPOINT
    会被 aiosqlite 驱动隐式提交（SQLAlchemy 已知行为），所以"漏 commit"在
    sqlite 数据层面看不出差异，只能直接断言提交确实发生（PG 上必须显式提交）。
    """
    commits: list[int] = []

    class _CommitSpy(AsyncSession):
        async def commit(self):
            commits.append(1)
            return await super().commit()

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'purge.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        bind=engine, class_=_CommitSpy, expire_on_commit=False, autoflush=False
    )
    factory.commits = commits  # type: ignore[attr-defined]
    yield factory
    await engine.dispose()


async def _seed_stale_deleted(factory, hours_ago: int) -> str:
    """建一个用户并软删除，deleted_at 前推 hours_ago 小时（成为 stale 候选）。"""
    async with factory() as session:
        user = await user_repository.create_user(session, user_id=str(uuid4()))
        await user_repository.soft_delete(session, user.id)
        await session.execute(
            update(User)
            .where(User.id == user.id)
            .values(deleted_at=utc_now() - timedelta(hours=hours_ago))
        )
        await session.commit()
        return user.id


async def test_purge_sweep_once_removes_stale_users(
    sweep_session_factory, monkeypatch
):
    monkeypatch.setattr("backend.database.SessionLocal", sweep_session_factory)
    user_id = await _seed_stale_deleted(sweep_session_factory, hours_ago=2)
    sweep_session_factory.commits.clear()  # 只统计本轮扫描的提交

    count = await main_mod._purge_sweep_once()

    assert count == 1
    assert sweep_session_factory.commits, "扫描结果必须显式 commit（PG 不会隐式提交）"
    async with sweep_session_factory() as session:
        assert await user_repository.get_user_any_status(session, user_id) is None


async def test_purge_sweep_once_ignores_fresh_deletion(sweep_session_factory, monkeypatch):
    """未超 stale_hours 的软删用户不被进程内循环抢先删除（留给 QStash 重试窗口）。"""
    monkeypatch.setattr("backend.database.SessionLocal", sweep_session_factory)
    user_id = await _seed_stale_deleted(sweep_session_factory, hours_ago=0)

    assert await main_mod._purge_sweep_once() == 0
    async with sweep_session_factory() as session:
        assert await user_repository.get_user_any_status(session, user_id) is not None


async def test_lifespan_starts_and_cancels_purge_loop(monkeypatch):
    started = asyncio.Event()

    async def fake_sweep() -> int:
        started.set()
        await asyncio.sleep(30)  # 模拟长轮次，验证退出时被取消
        return 0

    monkeypatch.setattr(main_mod, "_purge_sweep_once", fake_sweep)
    monkeypatch.setattr(main_mod.settings, "purge_loop_enabled", True)
    monkeypatch.setattr(
        main_mod.cron_service, "register_follow_up_schedule", _noop_schedule
    )

    async with main_mod.lifespan(main_mod.app):
        await asyncio.wait_for(started.wait(), timeout=2)
        task = next(
            (t for t in asyncio.all_tasks() if t.get_name() == "purge-sweeper"), None
        )
        assert task is not None and not task.done()

    assert task.done() and task.cancelled()


async def test_lifespan_skips_purge_loop_when_disabled(monkeypatch):
    calls = []

    async def fake_sweep() -> int:
        calls.append(1)
        await asyncio.sleep(30)
        return 0

    monkeypatch.setattr(main_mod, "_purge_sweep_once", fake_sweep)
    monkeypatch.setattr(main_mod.settings, "purge_loop_enabled", False)
    monkeypatch.setattr(
        main_mod.cron_service, "register_follow_up_schedule", _noop_schedule
    )

    async with main_mod.lifespan(main_mod.app):
        await asyncio.sleep(0)

    assert calls == []
    assert not [t for t in asyncio.all_tasks() if t.get_name() == "purge-sweeper"]


async def test_sweeper_keeps_running_after_round_failure(monkeypatch):
    rounds = []
    give_up = asyncio.Event()

    async def failing_sweep() -> int:
        rounds.append(1)
        if len(rounds) < 3:
            raise RuntimeError("db down")
        give_up.set()
        await asyncio.sleep(30)
        return 0

    monkeypatch.setattr(main_mod, "_purge_sweep_once", failing_sweep)
    monkeypatch.setattr(main_mod, "PURGE_SWEEP_INTERVAL_SECONDS", 0)

    task = asyncio.create_task(main_mod._purge_sweeper())
    try:
        await asyncio.wait_for(give_up.wait(), timeout=2)
        assert len(rounds) == 3  # 前两轮失败后循环仍然活着
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def _noop_schedule():
    return None

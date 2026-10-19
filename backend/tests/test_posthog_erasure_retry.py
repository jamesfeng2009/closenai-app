"""T1-02 第 3 件：PostHog 侧擦除失败后的重试台账与重试路径。

工作单的验收原话是「PostHog 抛错 ⇒ 用户删除仍成功且留下可重试标记」，本文件把这句话拆成
四条能各自打红的判据：

1. 失败要留下**行**（不是留下日志）——没有行就没有第二次机会，因为 `users` 行已在同一段
   事务里删除，`/cron/purge-deleted-users` 的 `status='deleted'` 谓词再也扫不到这个用户；
2. 成功不许留行（这张表只表示「还有活要干」，留终态就变成 J4 否掉的墓碑表）；
3. 到点才重试、认领用 CAS（两个 worker 不许把同一个人擦两遍）；
4. 超过 `posthog_erasure_retry_max_days` 要**告警后删行**——既不永久遗留外部残留，也不
   永久遗留「谁注销了」的旁证。

CAS 的真并发证人不在这里（sqlite 同会话测不出两个事务抢同一行），见伴生实库闸
`backend/tests/test_posthog_erasure_retry_pg.py`。
"""
from datetime import timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.config import settings
from backend.core.middleware import register_exception_handlers
from backend.core.time_utils import utc_now
from backend.database import get_db
from backend.models.erasure import PostHogErasureRetry
from backend.repositories import erasure_repository, user_repository
from backend.routers import cron as cron_router
from backend.services.user_service import user_service

ROOT = Path(__file__).resolve().parents[2]
INTERVAL = timedelta(minutes=1)


@pytest.fixture(autouse=True)
def _short_retry_interval(monkeypatch):
    """把退避间隔压到 1 分钟：默认 60 分钟会让「推后了没有」这条判据要造两个时刻才能验。"""
    monkeypatch.setattr(settings, "posthog_erasure_retry_interval_minutes", 1)


async def _make_user(db) -> str:
    user = await user_repository.create_user(db, user_id=str(uuid4()))
    await db.flush()
    return user.id


def _stub_erase(monkeypatch, *, erased: bool, reason: str = "http_500") -> list[str]:
    """替换 user_service 里的 erase_person_data，返回它被调用过哪些 distinct_id。"""
    seen: list[str] = []

    async def fake(distinct_id: str) -> dict:
        seen.append(distinct_id)
        return {"erased": erased, "reason": reason} if not erased else {"erased": True, "status": 202}

    monkeypatch.setattr("backend.services.user_service.erase_person_data", fake)
    return seen


async def _pending(db, user_id: str) -> PostHogErasureRetry | None:
    result = await db.execute(
        select(PostHogErasureRetry).where(PostHogErasureRetry.user_id == user_id)
    )
    return result.scalars().first()


def _aware(value) -> object:
    """SQLite 把 DateTime(timezone=True) 读成 naive，naive 一律按 UTC 解释后再比。"""
    return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)


# ── 1／2：purge_user 侧留不留行 ──


async def test_purge_failure_leaves_retry_row(db, monkeypatch):
    _stub_erase(monkeypatch, erased=False, reason="http_500")
    user_id = await _make_user(db)

    counts = await user_service.purge_user(db, user_id)

    # 验收的前半句：用户删除仍然成功（本地该删的都删了）
    assert counts["users"] == 1
    assert await user_repository.get_user_any_status(db, user_id) is None
    # 验收的后半句：留下可重试标记
    row = await _pending(db, user_id)
    assert row is not None, "erase 失败却没写重试台账：外部残留将永久无人认领"
    assert row.last_reason == "http_500"
    assert row.attempts == 1
    assert _aware(row.next_attempt_at) > utc_now()  # 不是「立刻再来一次」，那一分钟由退避决定


async def test_purge_success_leaves_no_row(db, monkeypatch):
    _stub_erase(monkeypatch, erased=True)
    user_id = await _make_user(db)

    await user_service.purge_user(db, user_id)

    assert await erasure_repository.count(db) == 0, "擦成功还留行＝把重试台账写成了墓碑表"


async def test_repeat_failure_counts_not_stacks(db, monkeypatch):
    """同一用户被登记两次不许叠成两行（唯一约束＋upsert），但要留下次数。

    `first_id`／`first_failed` 取的是**值的快照**而不是 ORM 对象：同一个 session 里两次
    `_pending` 拿到的是身份映射的同一个对象，`row.x == first.x` 会退化成对象跟自己比、
    恒等，改坏了也读不出来。
    """
    _stub_erase(monkeypatch, erased=False, reason="request_failed")
    user_id = await _make_user(db)
    await user_service.record_erasure_retry(db, user_id=user_id, reason="request_failed")
    first = await _pending(db, user_id)
    first_id, first_failed = first.id, first.first_failed_at
    await user_service.record_erasure_retry(db, user_id=user_id, reason="http_503")

    row = await _pending(db, user_id)
    assert row is not None and row.id == first_id
    assert await erasure_repository.count(db) == 1
    assert row.attempts == 2
    assert row.last_reason == "http_503"
    # first_failed_at 是 7 天告警时钟的起点，重复登记不许把它推到未来
    assert row.first_failed_at == first_failed


# ── 3：重试路径 ──


async def test_retry_drops_row_when_erase_succeeds(db, monkeypatch):
    seen = _stub_erase(monkeypatch, erased=True)
    user_id = await _make_user(db)
    now = utc_now()
    await erasure_repository.record_failure(
        db, row_id=str(uuid4()), user_id=user_id, reason="http_500",
        now=now - timedelta(minutes=5), next_attempt_at=now - timedelta(minutes=1),
    )

    out = await user_service.retry_pending_erasures(db)

    assert out == {"checked": 1, "erased": 1, "pending": 0, "alerted": 0, "skipped": 0}
    assert seen == [user_id]
    assert await erasure_repository.count(db) == 0


async def test_retry_reschedules_when_erase_fails_again(db, monkeypatch):
    _stub_erase(monkeypatch, erased=False, reason="http_403")
    user_id = await _make_user(db)
    now = utc_now()
    await erasure_repository.record_failure(
        db, row_id=str(uuid4()), user_id=user_id, reason="http_500",
        now=now - timedelta(minutes=5), next_attempt_at=now - timedelta(minutes=1),
    )

    out = await user_service.retry_pending_erasures(db)

    assert out["pending"] == 1 and out["erased"] == 0
    row = await _pending(db, user_id)
    assert row.attempts == 2 and row.last_reason == "http_403"
    assert _aware(row.next_attempt_at) > utc_now(), "失败后没推后时刻＝下一轮立刻又撞同一堵墙"


async def test_retry_skips_rows_not_due_yet(db, monkeypatch):
    seen = _stub_erase(monkeypatch, erased=True)
    user_id = await _make_user(db)
    now = utc_now()
    await erasure_repository.record_failure(
        db, row_id=str(uuid4()), user_id=user_id, reason="not_configured",
        now=now, next_attempt_at=now + timedelta(minutes=30),
    )

    out = await user_service.retry_pending_erasures(db)

    assert out["checked"] == 0 and seen == []
    assert await erasure_repository.count(db) == 1


async def test_retry_is_claimed_once_per_pass(db, monkeypatch):
    """CAS 的 sqlite 面：同一轮里第二次认领同一行必须拿不到，也不许再打 PostHog。

    真并发（两条 session 同时抢）由 test_posthog_erasure_retry_pg.py 在实库上作证。
    """
    seen = _stub_erase(monkeypatch, erased=False, reason="http_500")
    user_id = await _make_user(db)
    now = utc_now()
    row = await erasure_repository.record_failure(
        db, row_id=str(uuid4()), user_id=user_id, reason="http_500",
        now=now - timedelta(minutes=5), next_attempt_at=now - timedelta(minutes=1),
    )

    # 手工认领一次（模拟另一个 worker 先到），再跑重试：这一行应当被跳过
    assert await erasure_repository.claim(
        db, row.id, now=now, next_attempt_at=now + INTERVAL
    ) is True
    assert await erasure_repository.claim(
        db, row.id, now=now, next_attempt_at=now + INTERVAL
    ) is False

    out = await user_service.retry_pending_erasures(db)
    assert out == {"checked": 0, "erased": 0, "pending": 0, "alerted": 0, "skipped": 0}
    assert seen == []


# ── 4：超龄告警 ──


async def test_retry_alerts_then_drops_row_past_max_days(db, monkeypatch, caplog):
    seen = _stub_erase(monkeypatch, erased=True)
    monkeypatch.setattr(settings, "posthog_erasure_retry_max_days", 7)
    user_id = await _make_user(db)
    now = utc_now()
    await erasure_repository.record_failure(
        db, row_id=str(uuid4()), user_id=user_id, reason="request_failed",
        now=now - timedelta(days=8), next_attempt_at=now - timedelta(minutes=1),
    )

    with caplog.at_level("ERROR"):
        out = await user_service.retry_pending_erasures(db)

    assert out == {"checked": 1, "erased": 0, "pending": 0, "alerted": 1, "skipped": 0}
    assert seen == [], "超龄行不该再去打 PostHog，它要的是人来处理"
    stuck = [r for r in caplog.records if "POSTHOG-ERASURE-STUCK" in r.getMessage()]
    assert len(stuck) == 1, "超过 7 天必须有一条可告警的 ERROR"
    assert user_id in stuck[0].getMessage()
    assert await erasure_repository.count(db) == 0, "告警后仍留行＝永久留存「谁注销了」的旁证"


async def test_retry_within_window_does_not_alert(db, monkeypatch, caplog):
    """6 天 23 小时还不该告警：告警时钟的起点是 first_failed_at，不是每次重试都续期。"""
    _stub_erase(monkeypatch, erased=False, reason="request_failed")
    monkeypatch.setattr(settings, "posthog_erasure_retry_max_days", 7)
    user_id = await _make_user(db)
    now = utc_now()
    await erasure_repository.record_failure(
        db, row_id=str(uuid4()), user_id=user_id, reason="request_failed",
        now=now - timedelta(days=6, hours=23), next_attempt_at=now - timedelta(minutes=1),
    )

    with caplog.at_level("ERROR"):
        out = await user_service.retry_pending_erasures(db)

    assert out["pending"] == 1 and out["alerted"] == 0
    assert "POSTHOG-ERASURE-STUCK" not in caplog.text
    assert await erasure_repository.count(db) == 1


# ── 排程可达：/cron/purge-deleted-users 真的会驱动重试 ──


def _cron_app(db):
    app = FastAPI()
    app.include_router(cron_router.router, prefix="/api/v1", tags=["cron"])
    register_exception_handlers(app)

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    return app


async def test_cron_endpoint_drives_erasure_retry(db, monkeypatch):
    """工作单第 3 条把重试挂在 `/cron/purge-deleted-users` 上，这条就是那句话的证人。

    只测 service 方法是不够的：端点里漏一行 `await service.retry_pending_erasures(db)`
    不会有任何测试变红，而那条排程每小时真的在跑，跑出来的结果与「没跑」完全一样。
    """
    _stub_erase(monkeypatch, erased=True)
    monkeypatch.setattr(settings, "cron_secret", "s3cret")
    user_id = await _make_user(db)
    now = utc_now()
    await erasure_repository.record_failure(
        db, row_id=str(uuid4()), user_id=user_id, reason="http_500",
        now=now - timedelta(minutes=5), next_attempt_at=now - timedelta(minutes=1),
    )

    transport = ASGITransport(app=_cron_app(db))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        no_secret = await client.get("/api/v1/cron/purge-deleted-users")
        res = await client.get(
            "/api/v1/cron/purge-deleted-users", headers={"Authorization": "Bearer s3cret"}
        )

    assert no_secret.status_code == 401, "这条端点能删数据，必须验密钥"
    assert res.status_code == 200, res.text
    assert res.json()["data"]["erasure_retry"]["erased"] == 1
    assert await erasure_repository.count(db) == 0


def test_purged_users_cron_line_is_scheduled():
    """正向证人：这条排程在宿主清单里真的被排了（重试不可达＝台账永远等着，同 T1-01 口径）。"""
    lines = (ROOT / "deploy" / "schedule" / "crontab").read_text(encoding="utf-8").splitlines()
    hits = [ln for ln in lines if "/api/v1/cron/purge-deleted-users" in ln and not ln.startswith("#")]
    assert len(hits) == 1, hits


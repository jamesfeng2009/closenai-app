"""T1-04 付费交付审计（工作单 §怎么做.3/§怎么做.4 ＋ §验收）。

判据形状：人群 = `user_entitlements` 里未撤销的买断用户；宽限期
`delivery_audit_grace_hours` 内的新付费不计；付费之后 `share_exports` 里没有
`period='life_archive'` 的成功行 ⇒ 计入 `undelivered_users`；超过
`delivery_audit_alert_threshold` 打一行 `DELIVERY-ALERT` ERROR。

为什么告警的靶是库而不是事件流（这是本批最要紧的一条口径）：交付事件有四种
正当理由落不到分析平台——SDK 没装、key 没配、用户没给分析同意、中继之前进程崩了。
把「付款成功但交付失败」建在事件计数上，等于让「平台没配」长得像「交付一切正常」。
权益行与导出行不会因为这三件事消失，所以 `test_counts_survive_a_dead_analytics_channel`
专门证「事件全丢，计数照样数得到」。

已知边界（写在 `delivery_service.audit_paid_delivery` 的 docstring 里，这里配证人）：
分母是**生成成功**落库的行；PDF 排版失败发生在落库之后，库里已有成功行，
审计数不到那一格，由 `test_delivery_observability.py` 的告警行补位。
"""
import logging
import re
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.config import settings
from backend.core.auth import get_current_user
from backend.core.middleware import register_exception_handlers
from backend.core.time_utils import utc_now
from backend.database import get_db
from backend.models.share import ShareExport
from backend.repositories import entitlement_repository, user_repository
from backend.routers import cron as cron_router
from backend.services import analytics_service, delivery_service
from backend.services.delivery_service import (
    ALERT_MARKER,
    METRIC_MARKER,
    audit_paid_delivery,
)

ROOT = Path(__file__).resolve().parents[2]
CRONTAB = ROOT / "deploy" / "schedule" / "crontab"


async def _user(db, user_id: str) -> None:
    await user_repository.get_or_create_user(db, user_id)


async def _pay(
    db,
    user_id: str,
    *,
    hours_ago: float,
    scope: str = "all",
    tx: str | None = None,
    revoked_hours_ago: float | None = None,
) -> None:
    """造一笔买断：`hours_ago` 小时前付费，可选在 `revoked_hours_ago` 小时前退款撤销。"""
    await _user(db, user_id)
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
    await db.flush()


async def _export(db, user_id: str, *, hours_ago: float, row_id: str) -> None:
    """造一行档案导出（审计眼里的"交付成功"）。created_at 显式给，不吃 server_default。"""
    db.add(
        ShareExport(
            id=row_id,
            user_id=user_id,
            period="life_archive",
            period_key="all",
            payload="{}",
            created_at=utc_now() - timedelta(hours=hours_ago),
        )
    )
    await db.flush()


async def _other_period_export(db, user_id: str, *, hours_ago: float, row_id: str) -> None:
    """同表不同 period 的行：审计只认 life_archive，别把周报当成档案交付。"""
    db.add(
        ShareExport(
            id=row_id,
            user_id=user_id,
            period="weekly",
            period_key="2026-W32",
            payload="{}",
            created_at=utc_now() - timedelta(hours=hours_ago),
        )
    )
    await db.flush()


# ── 1. 工作单点名的那条判据 ──

async def test_delivery_audit_counts_paid_without_export(db):
    """§怎么做.4 的原话：构造 1 个买断用户 + 0 导出 ⇒ 计数 1。"""
    await _pay(db, "u-paid", hours_ago=72)
    result = await audit_paid_delivery(db)
    assert result["undelivered_users"] == 1, result
    assert result["paid_users"] == 1, result
    assert result["alert"] is True, "默认阈值 0 ⇒ 一个人付费没拿到交付就该告警"


# ── 2. 什么算"已交付" ──

async def test_export_after_purchase_clears_the_count(db):
    await _pay(db, "u-ok", hours_ago=72)
    await _export(db, "u-ok", hours_ago=24, row_id="exp-1")
    result = await audit_paid_delivery(db)
    assert result["undelivered_users"] == 0, result
    assert result["paid_users"] == 1, result
    assert result["alert"] is False


async def test_export_before_purchase_is_not_a_delivery_witness(db):
    """付费前的导出行不算交付证人。

    订阅期内每月可免费生成一档，那种历史行在付费之前就有了；把它当成交付，
    「买断后一次都没出货」的人就会被统计成已交付——那正是本指标要抓的形状。
    """
    await _export(db, "u-stale", hours_ago=720, row_id="exp-old")
    await _pay(db, "u-stale", hours_ago=48)
    result = await audit_paid_delivery(db)
    assert result["undelivered_users"] == 1, result


async def test_other_period_export_does_not_count(db):
    await _pay(db, "u-weekly", hours_ago=72)
    await _other_period_export(db, "u-weekly", hours_ago=24, row_id="exp-w")
    assert (await audit_paid_delivery(db))["undelivered_users"] == 1


# ── 3. 人群边界 ──

async def test_revoked_entitlement_leaves_the_paid_population(db):
    """退款回收的行不进人群：审计管的是"现在还持有权益"的人。"""
    await _pay(db, "u-refunded", hours_ago=72, revoked_hours_ago=10)
    result = await audit_paid_delivery(db)
    assert result["paid_users"] == 0, result
    assert result["undelivered_users"] == 0, result
    # 退款侧分子另有其人：撤销数单独报，否则"退款率"这一格还是算不出来
    assert result["revoked_users"] == 1, result


async def test_purchase_inside_grace_is_not_a_failure(db):
    """刚买 1 小时没点导出 ≠ 交付失败：宽限期把这种从计数里摘出去。"""
    await _pay(db, "u-new", hours_ago=1)
    result = await audit_paid_delivery(db)
    assert result["undelivered_users"] == 0, result
    assert result["paid_users"] == 1, result
    assert result["grace_hours"] == settings.delivery_audit_grace_hours


async def test_grace_and_threshold_come_from_settings(db, monkeypatch):
    """两个数字必须是旋钮而不是硬编码：换 env 就换行为，否则生产调不动。

    断言写的是**字面量**（48／1），不是 `settings.xxx`：拿响应去等于同一个 settings 是恒真等式
    （默认 24 时，哪怕代码把 24 硬编码在返回体里也照样绿）——变异证人 A2 就是打在这里的。
    """
    await _pay(db, "u-knob", hours_ago=30)
    monkeypatch.setattr(settings, "delivery_audit_grace_hours", 48)
    wide = await audit_paid_delivery(db)
    assert wide["undelivered_users"] == 0, wide
    assert wide["grace_hours"] == 48, "返回体里的宽限期必须跟着旋钮走，硬编码 24 在这里要红"
    monkeypatch.setattr(settings, "delivery_audit_grace_hours", 24)
    monkeypatch.setattr(settings, "delivery_audit_alert_threshold", 1)
    result = await audit_paid_delivery(db)
    assert result["undelivered_users"] == 1 and result["alert"] is False, result
    assert result["threshold"] == 1, "阈值同理：写死 0 的话这条指标永远在告警"


# ── 4. 告警与计数口径 ──

async def test_alert_line_is_emitted_only_over_threshold(db, caplog, monkeypatch):
    await _pay(db, "u-a", hours_ago=72)
    await _pay(db, "u-b", hours_ago=72)
    monkeypatch.setattr(settings, "delivery_audit_alert_threshold", 1)
    caplog.set_level(logging.INFO, logger="closenai.delivery")
    result = await audit_paid_delivery(db)
    assert result["undelivered_users"] == 2 and result["alert"] is True

    lines = [r.getMessage() for r in caplog.records if r.name == "closenai.delivery"]
    alerts = [ln for ln in lines if ln.startswith(ALERT_MARKER)]
    assert len(alerts) == 1, lines
    assert any(ln.startswith(METRIC_MARKER) and "event=delivery_audit" in ln for ln in lines)
    # 阈值边界：等于阈值不告警（「超过」阈值才响，不是「达到」）
    monkeypatch.setattr(settings, "delivery_audit_alert_threshold", 2)
    assert (await audit_paid_delivery(db))["alert"] is False


async def test_metric_line_is_parseable_and_counts_users(db, caplog):
    await _pay(db, "u-m", hours_ago=72)
    await _pay(db, "u-n", hours_ago=72, tx="tx-n")
    await _export(db, "u-n", hours_ago=24, row_id="exp-n")
    caplog.set_level(logging.INFO, logger="closenai.delivery")
    await audit_paid_delivery(db)
    line = [
        r.getMessage() for r in caplog.records
        if r.getMessage().startswith(METRIC_MARKER)
    ][0]
    fields = dict(re.findall(r"(\w+)=(\S+)", line[len(METRIC_MARKER):]))
    assert fields == {
        "event": "delivery_audit",
        "paid_users": "2",
        "undelivered_users": "1",
        "revoked_users": "0",
        "grace_hours": str(settings.delivery_audit_grace_hours),
        "threshold": "0",
        "alert": "True",
    }, fields


async def test_counts_survive_a_dead_analytics_channel(db, monkeypatch):
    """立项理由的证人：事件全部中继失败，审计计数照样数得到。

    把告警建在事件流上的话，这一格会变成「PostHog 没配 ⇒ 交付一切正常」——
    所以这里打桩让中继恒失败，再证明库侧计数与事件通道无关。
    """
    monkeypatch.setattr(analytics_service, "_relay_to_posthog", lambda *a, **k: False)
    await _pay(db, "u-blind", hours_ago=72)
    assert (await audit_paid_delivery(db))["undelivered_users"] == 1


async def test_same_user_two_products_counts_once(db):
    """一个用户买两档算一个人：指标要的是"多少人没拿到东西"，不是多少笔订单。"""
    await _pay(db, "u-two", hours_ago=72, scope="all", tx="tx-two-1")
    await _pay(db, "u-two", hours_ago=72, scope="1y", tx="tx-two-2")
    result = await audit_paid_delivery(db)
    assert result["paid_users"] == 1, result
    assert result["undelivered_users"] == 1, result


async def test_one_product_delivered_clears_the_other_scope(db):
    """同用户两笔买断、任一档已出货 ⇒ 不再计入未交付（本指标数人，不数 SKU）。

    这是有意的口径：`share_exports` 的行按 period_key 分档，而审计问的是
    「这个付费用户拿到过东西没有」。要按 SKU 粒度告警需要另立指标，不在本批四步里。
    """
    await _pay(db, "u-mix", hours_ago=72, scope="all", tx="tx-mix-1")
    await _pay(db, "u-mix", hours_ago=72, scope="1y", tx="tx-mix-2")
    await _export(db, "u-mix", hours_ago=24, row_id="exp-mix")
    result = await audit_paid_delivery(db)
    assert result["paid_users"] == 1 and result["undelivered_users"] == 0, result


# ── 5. 调度面与端点形状 ──

@pytest.fixture
def api(db):
    """只挂 cron router：审计端点的鉴权依赖 `verify_cron_secret`，不需要当前用户。"""
    app = FastAPI()
    app.include_router(cron_router.router, prefix="/api/v1", tags=["cron"])
    register_exception_handlers(app)

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: "u-any"
    return app


async def _request(app, path, headers=None):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request("GET", path, headers=headers)


async def test_cron_endpoint_is_fail_closed_on_secret(db, api, monkeypatch):
    await _pay(db, "u-cron", hours_ago=72)
    monkeypatch.setattr(settings, "cron_secret", "")
    assert (await _request(api, "/api/v1/cron/delivery-audit")).status_code == 401
    monkeypatch.setattr(settings, "cron_secret", "s3cret")
    assert (await _request(api, "/api/v1/cron/delivery-audit")).status_code == 401
    ok = await _request(
        api, "/api/v1/cron/delivery-audit", headers={"Authorization": "Bearer s3cret"}
    )
    assert ok.status_code == 200, ok.text
    data = ok.json()["data"]
    assert data["undelivered_users"] == 1 and data["alert"] is True, data


async def test_endpoint_body_has_no_user_ids(db, api, monkeypatch):
    """响应里只有计数：把付费用户名单写进一条 HTTP 响应是新造的泄漏面。"""
    await _pay(db, "u-list", hours_ago=72)
    monkeypatch.setattr(settings, "cron_secret", "s3cret")
    ok = await _request(
        api, "/api/v1/cron/delivery-audit", headers={"Authorization": "Bearer s3cret"}
    )
    body = ok.text
    assert "u-list" not in body, body
    assert set(ok.json()["data"]) == {
        "paid_users", "undelivered_users", "revoked_users",
        "grace_hours", "threshold", "alert",
    }


def test_delivery_audit_is_scheduled_daily():
    """正向证人：这条端点在宿主清单里真的排了，而且是每日一次（不可达＝指标永远不出数）。"""
    lines = CRONTAB.read_text(encoding="utf-8").splitlines()
    hits = [ln for ln in lines if "/api/v1/cron/delivery-audit" in ln and not ln.startswith("#")]
    assert len(hits) == 1, hits
    assert hits[0].split()[:5] == ["0", "4", "*", "*", "*"], hits[0]


def test_audit_query_targets_the_two_named_tables():
    """口径护栏：审计只读 `user_entitlements` 与 `share_exports`，不读事件、不读别的表。

    这条是"改判要留痕"的证人——哪天有人把判据换成读 PostHog 或读 subscriptions，
    这段源码文本会先变红，逼那次改动过一次讨论。
    """
    source = (ROOT / "backend" / "services" / "delivery_service.py").read_text(
        encoding="utf-8"
    )
    # 判据从查询构造器起算：`audit_paid_delivery` 的谓词写在 `_delivered_exists()` 里，
    # 只切函数体就会把两张靶表漏在切片之外（那是一条会因为重构而假红的闸）。
    body = source[source.index("def _delivered_exists()"):]
    assert "UserEntitlement" in body and "ShareExport" in body
    assert "period" in body and "revoked_at" in body, "人群与交付谓词被摘掉后，计数会变成「所有人都未交付」"
    assert "posthog" not in body.lower(), "审计改读事件流＝平台没配时看着像一切正常"
    assert "Subscription" not in body, "订阅权益不是本指标的人群（工作单点名 user_entitlements）"


async def test_entitlement_rows_are_still_purged_by_deletion(db, make_user):
    """审计人群依赖的表本来就是注销清理对象：这条不重测 T1-02，只钉"表名没漂"。

    万一 `user_entitlements` 从 PURGE_TARGETS 漂出去，审计的人群会留下已注销用户，
    未交付计数会永久性虚高——所以在这里留一行便宜的证人。
    """
    from backend.services.user_service import PURGE_TARGETS

    keys = {key for _model, key in PURGE_TARGETS}
    assert "user_entitlements" in keys and "share_exports" in keys
    await make_user("u-purge")
    await _pay(db, "u-purge", hours_ago=72)
    await _export(db, "u-purge", hours_ago=24, row_id="exp-purge")
    await db.commit()
    counts = await delivery_service.audit_paid_delivery(db)
    assert counts["paid_users"] == 1

"""T1-04 付费交付可观测·事件面（Baseline §4.3「线上监控能发现付款成功但交付失败」）。

工作单 §怎么做.1 点名七个服务端事件，§验收 要求「沙盒购买 → 生成 → 下载三步各有事件；
故意让 PDF 生成抛错，能数到」。这个文件只管**事件**，不管审计计数（那条在
`test_delivery_audit.py`），分四组：

A 通道形状：日志行的机器可解析形状、白名单同时管着日志面与出网面、consent 与
  「没中继」的四种机器码各不相混。
B IAP 链路：started/succeeded/failed 三个出口，含异常出口也要数得到。
C 档案链路：生成 ok/缓存/被拒/抛错 四态 ＋ 下载请求 ＋ 排版失败告警。
D 白名单：新注册的 `reason` 键在前端、Python、Go 三侧都在（反向归零闸的正向证人）。

判据口径的两条硬约束，测试专门盯着：
- `reason` 只能是机器码。异常 message 可能带外部原文或用户内容，进了事件就等于
  绕过匿名化往外推，所以第 7 条用哨兵字符串证明文案不会出现在任何一侧。
- 「没送出去」不许说成「送出去了」（D-G18 同源）：未配 key、没同意、没有用户行，
  三种情况各有一个自己的码，且日志照写。
"""
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.config import settings
from backend.core.errors import AppException, ErrorCode
from backend.models.journey import UserEntitlement
from backend.repositories import entitlement_repository
from backend.services import analytics_service
from backend.services import delivery_service
from backend.services.delivery_service import (
    ALERT_MARKER,
    DELIVERY_EVENTS,
    METRIC_MARKER,
    RELAY_DROPPED,
    RELAY_NO_CONSENT,
    RELAY_NO_USER,
    RELAY_RELAYED,
    DeliveryEvent,
    emit,
)
from backend.services.iap_service import IAPService
from backend.services.share_service import share_service

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "backend" / "services"
IAP_PY = SERVICES / "iap_service.py"
SHARE_PY = SERVICES / "share_service.py"
ANALYTICS_TS = ROOT / "frontend" / "src" / "services" / "analytics.ts"
ANALYTICS_GO = ROOT / "go-core" / "internal" / "domain" / "analytics.go"

DELIMITER = "DELIVERY-TEST-SENTINEL-DO-NOT-LEAK"
_MS = 1000

pytestmark = pytest.mark.asyncio


# ── 取证工具 ──

@pytest.fixture
def metrics(caplog):
    """抓 `closenai.delivery` 的行，返回 [事件名, {键: 值}]；同时保留 ERROR 面。

    解析形状本身就是判据：`DELIVERY-METRIC event=… user=… relay=… k=v` 是 Loki 侧
    切指标的依据，写法一改（比如换成 JSON），这里就取不到行——不是测试跟着改写法，
    是写法要经过这条闸。
    """
    caplog.set_level(logging.INFO, logger="closenai.delivery")

    def _take() -> list[tuple[str, dict[str, str]]]:
        out = []
        for rec in caplog.records:
            if rec.name != "closenai.delivery":
                continue
            text = rec.getMessage()
            if not text.startswith(METRIC_MARKER):
                continue
            fields = dict(re.findall(r"(\w+)=(\S+)", text[len(METRIC_MARKER):]))
            out.append((fields.get("event", ""), fields))
        return out

    return _take


@pytest.fixture
def relay_spy(monkeypatch):
    """替身 `_relay_to_posthog`：记下每一次中继并回报「送出去了」。

    打的是 `_relay_to_posthog` 而不是 `relay_server_event`：后者是本次新加的公开入口，
    桩掉它就测不到「服务端事件与前端埋点走同一条出网路」这条结构。
    """
    calls: list[tuple[str, str, dict]] = []

    def _stub(distinct_id, event, properties, occurred_at):
        calls.append((distinct_id, event, dict(properties)))
        return True

    monkeypatch.setattr(analytics_service, "_relay_to_posthog", _stub)
    return calls


def _fake_app_store(monkeypatch, *, status: int = 0, in_app: list | None = None) -> None:
    """替身 verifyReceipt：订阅交易一笔，走真实校验分支（配了共享密钥）。"""
    monkeypatch.setattr(settings, "app_store_shared_secret", "test-secret")
    tx = {
        "product_id": "closenai_pro_monthly",
        "transaction_id": "tx-metric-1",
        "original_transaction_id": "tx-metric-1",
        "expires_date_ms": str(int(datetime(2026, 12, 1, tzinfo=timezone.utc).timestamp() * _MS)),
        "is_trial_period": "false",
        "environment": "Production",
    }
    body = {
        "status": status,
        "latest_receipt_info": in_app if in_app is not None else [tx],
        "receipt": {"in_app": in_app if in_app is not None else [tx]},
    }

    class _Resp:
        def json(self):
            return body

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            return _Resp()

    monkeypatch.setattr(
        "backend.services.iap_service.httpx.AsyncClient", lambda timeout=None: _Client()
    )


async def _grant(db, user_id: str, scope: str = "all", tx_id: str = "tx-metric-la") -> None:
    await entitlement_repository.grant(
        db,
        user_id,
        product_id=f"life_archive_{scope}",
        scope=scope,
        transaction_id=tx_id,
        purchase_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
        original_transaction_id=tx_id,
        environment="Production",
    )


def _events(rows: list[tuple[str, dict[str, str]]]) -> list[str]:
    return [event for event, _ in rows]


# ── A 通道形状 ──

async def test_seven_named_events_all_have_call_sites():
    """§怎么做.1 点名的七个事件必须各有发射点，且发射点不在 delivery_service 自己里。

    反向归零闸（「源码里不再出现某个字符串」）单独用会扫空集永远绿，所以这条同时是
    正向证人：登记表非空（`len(DELIVERY_EVENTS) == 7`）＋逐条数得到调用点。
    """
    assert len(DELIVERY_EVENTS) == 7
    sources = [IAP_PY.read_text(encoding="utf-8"), SHARE_PY.read_text(encoding="utf-8")]
    members = {
        "iap_verify_started": "DeliveryEvent.IAP_VERIFY_STARTED",
        "iap_verify_succeeded": "DeliveryEvent.IAP_VERIFY_SUCCEEDED",
        "iap_verify_failed": "DeliveryEvent.IAP_VERIFY_FAILED",
        "archive_generate_started": "DeliveryEvent.ARCHIVE_GENERATE_STARTED",
        "archive_generate_succeeded": "DeliveryEvent.ARCHIVE_GENERATE_SUCCEEDED",
        "archive_generate_failed": "DeliveryEvent.ARCHIVE_GENERATE_FAILED",
        "archive_download_requested": "DeliveryEvent.ARCHIVE_DOWNLOAD_REQUESTED",
    }
    for event, member in members.items():
        hits = sum(text.count(member) for text in sources)
        assert hits >= 1, f"{event} 没有任何发射点（登记表里写着，代码里没人调用）"


async def test_metric_line_shape_is_parseable(metrics, relay_spy, make_user):
    user = await make_user("u-shape", analytics_consent=True)
    emit(DeliveryEvent.ARCHIVE_DOWNLOAD_REQUESTED, user=user,
         source="life_archive", feature="all", action="download")
    rows = metrics()
    assert len(rows) == 1
    event, fields = rows[0]
    assert event == "archive_download_requested"
    assert fields["user"] == "u-shape"
    assert fields["relay"] == RELAY_RELAYED
    assert fields["feature"] == "all"


async def test_content_key_never_enters_log_or_relay(metrics, relay_spy, make_user):
    """日志面与出网面共用同一张白名单：非白名单键在两侧都不落地。"""
    user = await make_user("u-leak", analytics_consent=True)
    emit(DeliveryEvent.ARCHIVE_GENERATE_FAILED, user=user,
         result="failed", reason="generate_error_ValueError",
         memory_content=DELIMITER, reflection_text=DELIMITER)
    event, fields = metrics()[0]
    assert "memory_content" not in fields and "reflection_text" not in fields
    assert DELIMITER not in str(fields)
    assert DELIMITER not in relay_spy[0][2]
    assert fields["reason"] == "generate_error_ValueError"


async def test_consent_off_is_no_consent_and_does_not_relay(metrics, relay_spy, make_user):
    user = await make_user("u-noconsent", analytics_consent=False)
    emit(DeliveryEvent.IAP_VERIFY_SUCCEEDED, user=user, result="ok")
    assert relay_spy == [], "没给分析同意却中继了 = 违反 analytics 红线"
    assert metrics()[0][1]["relay"] == RELAY_NO_CONSENT


async def test_relay_distinguishes_relayed_from_dropped(metrics, monkeypatch, make_user):
    """SDK 没装/没配 key 时「没送出去」要说成 dropped，不许冒充 relayed（D-G18 同源）。"""
    user = await make_user("u-drop", analytics_consent=True)
    monkeypatch.setattr(analytics_service, "_relay_to_posthog",
                        lambda *a, **k: False)
    emit(DeliveryEvent.IAP_VERIFY_STARTED, user=user)
    assert metrics()[0][1]["relay"] == RELAY_DROPPED


async def test_missing_user_row_is_no_user_but_still_logged(metrics, relay_spy):
    """收据属于一个不存在的账号：日志要有（这是唯一还能说话的一格），中继不能有。"""
    emit(DeliveryEvent.IAP_VERIFY_FAILED, user_id="u-ghost", result="failed",
         reason="no_transactions")
    assert relay_spy == []
    event, fields = metrics()[0]
    assert fields["relay"] == RELAY_NO_USER
    assert fields["user"] == "u-ghost"


# ── B IAP 链路 ──

async def test_verify_started_and_succeeded(db, make_user, metrics, relay_spy, monkeypatch):
    await make_user("u-buy", tier="free", analytics_consent=True)
    _fake_app_store(monkeypatch, status=0)
    result = await IAPService().verify(db, "u-buy", "RECEIPT-BYTES", "closenai_pro_monthly")
    assert result["valid"] is True
    events = _events(metrics())
    assert events == ["iap_verify_started", "iap_verify_succeeded"]
    assert metrics()[1][1]["result"] == "ok"


async def test_verify_not_configured_emits_failed_reason(db, make_user, metrics, monkeypatch):
    """开发态关掉、密钥没配：这是「付了钱验不了」的一种，必须数得到。"""
    await make_user("u-nosecret", analytics_consent=True)
    monkeypatch.setattr(settings, "app_store_shared_secret", "")
    monkeypatch.setattr(settings, "debug", False)
    result = await IAPService().verify(db, "u-nosecret", "RECEIPT", "closenai_pro_monthly")
    assert result["valid"] is False
    event, fields = metrics()[-1]
    assert event == "iap_verify_failed"
    assert fields["reason"] == "not_configured"


async def test_verify_apple_status_is_machine_reason(db, make_user, metrics, monkeypatch):
    _fake_app_store(monkeypatch, status=21003)  # 收据无效（Apple 状态码）
    result = await IAPService().verify(db, "u-u", "RECEIPT", "closenai_pro_monthly")
    assert result["valid"] is False
    fields = metrics()[-1][1]
    assert fields["reason"] == "apple_status_21003"
    # 正向证人：message 里的自由文本没有被当成 reason 用
    assert "App" not in fields["reason"]


async def test_verify_exception_still_emits_failed(
    db, make_user, metrics, relay_spy, monkeypatch
):
    """超时走的是抛异常，不是返回 valid=False：只在 return 分支埋点就会漏掉这一格。"""
    await make_user("u-timeout", analytics_consent=True)
    monkeypatch.setattr(settings, "app_store_shared_secret", "test-secret")

    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            import asyncio

            raise asyncio.TimeoutError()

    monkeypatch.setattr(
        "backend.services.iap_service.httpx.AsyncClient", lambda timeout=None: _Boom()
    )
    with pytest.raises(AppException) as exc_info:
        await IAPService().verify(db, "u-timeout", "RECEIPT", "closenai_pro_monthly")
    assert exc_info.value.code == ErrorCode.IAP_VERIFY_TIMEOUT
    event, fields = metrics()[-1]
    assert event == "iap_verify_failed"
    assert fields["reason"] == f"app_error_{ErrorCode.IAP_VERIFY_TIMEOUT}"
    assert fields["relay"] == RELAY_RELAYED


async def test_verify_reason_never_carries_message_text(db, make_user, metrics, monkeypatch):
    """reason 是枚举机器码：异常文案（可能含外部原文）不许顺着事件漂出去。"""
    await make_user("u-text", analytics_consent=True)
    monkeypatch.setattr(settings, "app_store_shared_secret", "test-secret")

    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            raise RuntimeError(DELIMITER)

    monkeypatch.setattr(
        "backend.services.iap_service.httpx.AsyncClient", lambda timeout=None: _Boom()
    )
    with pytest.raises(RuntimeError):
        await IAPService().verify(db, "u-text", "RECEIPT", "closenai_pro_monthly")
    rendered = str(metrics())
    assert DELIMITER not in rendered
    assert metrics()[-1][1]["reason"] == "unexpected_RuntimeError"


# ── C 档案链路 ──

async def test_generate_and_cache_emit_ok_then_cache(db, make_user, metrics):
    await make_user("u-gen", tier="free")
    await _grant(db, "u-gen")
    await share_service.export_life_archive(db, "u-gen", "all", "en")
    await share_service.export_life_archive(db, "u-gen", "all", "en")
    events = _events(metrics())
    assert events == [
        "archive_generate_started", "archive_generate_succeeded",
        "archive_generate_started", "archive_generate_succeeded",
    ]
    assert metrics()[1][1]["result"] == "ok"
    assert metrics()[3][1]["result"] == "cache"


async def test_generate_denied_emits_rejected_code(db, make_user, metrics):
    await make_user("u-deny", tier="free")
    with pytest.raises(AppException) as exc_info:
        await share_service.export_life_archive(db, "u-deny", "all", "en")
    assert exc_info.value.code == ErrorCode.ENTITLEMENT_REQUIRED
    event, fields = metrics()[-1]
    assert event == "archive_generate_failed"
    assert fields["reason"] == f"rejected_{ErrorCode.ENTITLEMENT_REQUIRED}"


async def test_generate_build_error_emits_exception_name(db, make_user, metrics, monkeypatch):
    await make_user("u-boom", tier="free")
    await _grant(db, "u-boom")

    async def _raise(*a, **k):
        raise ValueError("排版前的组装炸了")

    monkeypatch.setattr(share_service, "_build_life_archive", _raise)
    with pytest.raises(ValueError):
        await share_service.export_life_archive(db, "u-boom", "all", "en")
    event, fields = metrics()[-1]
    assert event == "archive_generate_failed"
    assert fields["reason"] == "generate_error_ValueError"


async def test_pdf_download_requested_and_render_failure_alerts(
    db, make_user, metrics, caplog, monkeypatch
):
    """§验收 后半句：故意让 PDF 生成抛错——事件要数到，且要有一条能叫醒人的 ERROR 行。

    为什么审计数不到这一格：`share_exports` 的成功行在排版**之前**就落库了，
    所以"库里已交付、用户手上没有文件"只有日志面看得见。这条测试就是那个补位证人。
    """
    await make_user("u-pdf", tier="free")
    await _grant(db, "u-pdf")

    def _boom(*a, **k):
        raise RuntimeError("字体没装上")

    monkeypatch.setattr(share_service, "_render_pdf", _boom)
    with pytest.raises(RuntimeError):
        await share_service.export_life_archive_pdf(db, "u-pdf", "all", "en")

    events = _events(metrics())
    assert "archive_download_requested" in events
    failed = [f for e, f in metrics() if e == "archive_generate_failed"]
    assert failed and failed[0]["reason"] == "pdf_render_failed_RuntimeError"
    assert failed[0]["action"] == "download"
    alerts = [
        rec.getMessage() for rec in caplog.records
        if rec.levelno == logging.ERROR and rec.getMessage().startswith(ALERT_MARKER)
    ]
    assert len(alerts) == 1, "排版炸了要有一条 ERROR 告警行，而不是只有一行 INFO"
    assert "event=archive_pdf_render_failed" in alerts[0]


async def test_pdf_denied_by_gate_is_counted_once(
    db, make_user, metrics, caplog
):
    """被权益门拦下：只数 generate_failed 一条，不再补一条 pdf 侧失败（同一件事算两遍）。"""
    await make_user("u-once", tier="free")
    caplog.set_level(logging.INFO, logger="closenai.delivery")
    with pytest.raises(AppException):
        await share_service.export_life_archive_pdf(db, "u-once", "all", "en")
    rows = metrics()
    assert _events(rows).count("archive_generate_failed") == 1
    assert _events(rows).count("archive_download_requested") == 1


# ── D 白名单三侧 ──

async def test_reason_key_registered_on_all_three_sides():
    """新增的 `reason` 键必须在三侧同时注册（前端、Python、Go）。

    只改 Python 的后果是静默丢属性：前端入队成功、后端匿名化时整条被剔空，
    指标永远显示 reason 为空——那种红不会自己出现，所以要一条正向证人。
    """
    from backend.services.analytics_service import ALLOWED_PROPERTY_KEYS

    assert "reason" in ALLOWED_PROPERTY_KEYS
    front = ANALYTICS_TS.read_text(encoding="utf-8")
    go = ANALYTICS_GO.read_text(encoding="utf-8")
    assert re.search(r"'reason'", front), "前端 analytics.ts 白名单没有 reason"
    assert '"reason"' in go, "Go domain/analytics.go 白名单没有 reason"


async def test_delivery_metric_marker_and_events_are_stable_strings():
    """事件名是运营看板/告警规则的依据，改名等于把历史数据作废：逐字钉住。"""
    assert set(DELIVERY_EVENTS) == {
        "iap_verify_started", "iap_verify_succeeded", "iap_verify_failed",
        "archive_generate_started", "archive_generate_succeeded",
        "archive_generate_failed", "archive_download_requested",
    }
    assert METRIC_MARKER == "DELIVERY-METRIC"
    assert ALERT_MARKER == "DELIVERY-ALERT"


async def test_entitlement_grant_still_lands_in_user_entitlements(db, make_user):
    """审计的靶表证人：`_grant` 写的确实是 `user_entitlements`，否则审计测的是空集。"""
    await make_user("u-target")
    await _grant(db, "u-target")
    rows = (await db.execute(
        select(UserEntitlement).where(UserEntitlement.user_id == "u-target")
    )).scalars().all()
    assert len(rows) == 1 and rows[0].revoked_at is None

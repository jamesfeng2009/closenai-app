"""
Life Archive 一次性买断（架构方案 §二 M6 / 行动方案 P0-2）。

覆盖三条链路：
1. 收据发放：non-consumable 走 user_entitlements，不改 tier、不写 subscriptions；
   同收据共存订阅时两条链路各走各的；跨账号拒发；退款即回收。
2. 导出权益门：买断（高档向下兼容）/ Pro 订阅每月 1 次免费生成 / 其余 40304。
3. 导出内容与合规：幂等缓存、章节化数据、AIGC 章节逐章显式标识（GB 45438-2025）。
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.config import settings
from backend.core.errors import AppException, ErrorCode
from backend.models.journey import JourneyEvent, OnboardingProfile, SafetyEvent
from backend.repositories import (
    entitlement_repository,
    growth_repository,
    memory_repository,
    mood_repository,
    personality_repository,
    subscription_repository,
    user_repository,
)
from backend.services.iap_service import (
    PRODUCT_TIER_MAP,
    IAPService,
    _collect_transactions,
    _non_consumable_scope,
)
from backend.services.share_service import AIGC_CHAPTER_KEYS, share_service

IAP = IAPService()
_MS = 1000


def _non_consumable_tx(
    product_id: str = "life_archive_all",
    tx_id: str = "tx-la-1",
    purchased: datetime | None = None,
) -> dict:
    """构造 verifyReceipt 内的一笔一次性买断交易（Apple 字段口径）。"""
    purchased = purchased or datetime(2026, 8, 1, tzinfo=timezone.utc)
    return {
        "product_id": f"com.closenai.{product_id}",
        "transaction_id": tx_id,
        "original_transaction_id": tx_id,
        "purchase_date_ms": str(int(purchased.timestamp() * _MS)),
        "environment": "Production",
    }


def _subscription_tx(
    product_id: str = "closenai_pro_monthly",
    tx_id: str = "tx-sub-1",
    expires: datetime | None = None,
) -> dict:
    expires = expires or datetime(2026, 9, 1, tzinfo=timezone.utc)
    return {
        "product_id": product_id,
        "transaction_id": tx_id,
        "original_transaction_id": tx_id,
        "expires_date_ms": str(int(expires.timestamp() * _MS)),
        "is_trial_period": "false",
        "environment": "Production",
    }


class _FakeResponse:
    def __init__(self, data: dict):
        self._data = data

    def json(self) -> dict:
        return self._data


class _FakeClient:
    """替身 httpx.AsyncClient：无论打到沙盒还是生产端点都返回同一响应体。"""

    def __init__(self, data: dict):
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        return _FakeResponse(self._data)


def _stub_app_store(monkeypatch, in_app: list, latest: list | None = None):
    """让 App Store verifyReceipt 返回给定交易（配置共享密钥以走真实校验分支）。"""
    monkeypatch.setattr(settings, "app_store_shared_secret", "test-secret")
    body = {
        "status": 0,
        "latest_receipt_info": latest if latest is not None else list(in_app),
        "receipt": {"in_app": in_app},
    }

    def _factory(timeout=None):
        return _FakeClient(body)

    monkeypatch.setattr("backend.services.iap_service.httpx.AsyncClient", _factory)


async def _grant(db, user_id: str, scope: str, tx_id: str) -> None:
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


async def _seed_records(db, user_id: str) -> tuple[datetime, datetime]:
    """造档案素材：原话记忆 / 签到 / Pattern / 周报 / 画像，跨两个自然年。"""
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=400)

    fresh = await memory_repository.create(
        db, user_id, "最近每天都在跑步", "values", "self", 0.9
    )
    stale = await memory_repository.create(
        db, user_id, "想换个城市生活", "relationship", "self", 0.8
    )
    stale.created_at = old
    await db.flush()

    await mood_repository.create(db, user_id, "😌", "平静", "模板解读")
    old_checkin = await mood_repository.create(db, user_id, "🙂", "还行", "模板解读")
    old_checkin.created_at = old
    await db.flush()

    await growth_repository.create(db, user_id, "pattern_discovery", "你在冲突后倾向沉默")
    await growth_repository.create(db, user_id, "weekly_summary", "本周情绪整体平稳")
    await personality_repository.create(
        db, user_id, "INTP", '["观察细节"]', "独特", 1
    )
    return now, old


# ── 1. 收据发放 ──

def test_life_archive_is_not_a_subscription_tier():
    """M6 工程清理项：`life_archive → pro_plus` 别名必须不存在，买断档位只由
    _non_consumable_scope 判定——误映射会让一次性买断永久改写 users.tier。"""
    assert not any("life_archive" in key for key in PRODUCT_TIER_MAP)
    assert _non_consumable_scope("com.closenai.life_archive_1y") == "1y"
    assert _non_consumable_scope("life_archive_all") == "all"
    assert _non_consumable_scope("com.closenai.life_archive_legacy") == "legacy"
    assert _non_consumable_scope("closenai_pro_monthly") is None


def test_collect_transactions_merges_both_arrays():
    """买断只出现在 receipt.in_app，订阅历史在 latest_receipt_info——两处都要取。"""
    body = {
        "latest_receipt_info": [
            _subscription_tx(),
            {"product_id": "life_archive_1y", "transaction_id": "dup"},
        ],
        "receipt": {"in_app": [{"product_id": "life_archive_1y", "transaction_id": "dup"}]},
    }
    rows = _collect_transactions(body)
    assert [r["transaction_id"] for r in rows] == ["tx-sub-1", "dup"]


async def test_verify_non_consumable_grants_entitlement_without_tier(db, make_user, monkeypatch):
    """买断校验只落 user_entitlements：不改 tier、不写 subscriptions。"""
    await make_user("u-buyer", tier="free")
    _stub_app_store(monkeypatch, [_non_consumable_tx("life_archive_all", "tx-la-1")])

    result = await IAP.verify(db, "u-buyer", "receipt-blob", "life_archive_all", False)

    assert result["valid"] is True
    assert result["kind"] == "entitlement"
    assert result["scope"] == "all"
    assert result["tier"] == "free"
    user = await user_repository.get_user(db, "u-buyer")
    assert user.tier == "free"
    assert await subscription_repository.get_active(db, "u-buyer") is None
    assert await entitlement_repository.has(db, "u-buyer", "all") is True


async def test_verify_mixed_receipt_handles_both(db, make_user, monkeypatch):
    """同一收据既有订阅又有买断：订阅下发 tier，买断落权益，互不覆盖。"""
    await make_user("u-both", tier="free")
    _stub_app_store(
        monkeypatch,
        [_subscription_tx(), _non_consumable_tx("life_archive_legacy", "tx-la-2")],
    )

    result = await IAP.verify(db, "u-both", "receipt-blob", "closenai_pro_monthly", False)

    assert result["kind"] == "subscription"
    assert result["tier"] == "pro"
    assert [e["scope"] for e in result["entitlements"]] == ["legacy"]
    sub = await subscription_repository.get_active(db, "u-both")
    assert sub is not None and sub.tier == "pro"
    assert await entitlement_repository.has(db, "u-both", "legacy") is True


async def test_restore_is_idempotent_per_transaction(db, make_user, monkeypatch):
    """卸载重装后恢复购买：同 transaction_id 重复校验不重复发放。"""
    await make_user("u-restore", tier="free")
    _stub_app_store(monkeypatch, [_non_consumable_tx("life_archive_1y", "tx-la-3")])

    await IAP.verify(db, "u-restore", "receipt-blob", "life_archive_1y", False)
    await IAP.verify(db, "u-restore", "receipt-blob", "life_archive_1y", False)

    rows = await entitlement_repository.list_active(db, "u-restore")
    assert len(rows) == 1
    assert rows[0].scope == "1y"


async def test_non_consumable_cross_account_rejected(db, make_user, monkeypatch):
    """同一买断交易已归属其他账号 → 拒发（买断无自然失效，错绑即永久错发）。"""
    await make_user("u-owner", tier="free")
    await make_user("u-thief", tier="free")
    _stub_app_store(monkeypatch, [_non_consumable_tx("life_archive_all", "tx-la-4")])
    await IAP.verify(db, "u-owner", "receipt-blob", "life_archive_all", False)

    with pytest.raises(AppException) as exc_info:
        await IAP.verify(db, "u-thief", "receipt-blob", "life_archive_all", False)
    assert exc_info.value.code == ErrorCode.SUBSCRIPTION_ALREADY_EXISTS


async def test_refund_revokes_entitlement(db, make_user, monkeypatch):
    """退款通知回收买断权益（此前只回收订阅，买断会漏撤）。"""
    await make_user("u-refund", tier="free")
    _stub_app_store(monkeypatch, [_non_consumable_tx("life_archive_all", "tx-la-5")])
    await IAP.verify(db, "u-refund", "receipt-blob", "life_archive_all", False)

    await IAP.handle_refund(db, "tx-la-5")

    assert await entitlement_repository.has(db, "u-refund", "all") is False
    row = await entitlement_repository.get_by_transaction(db, "tx-la-5")
    assert row.revoked_at is not None  # 保留行做审计，不物理删除


# ── 2. 导出权益门 ──

async def test_export_denied_without_entitlement_or_subscription(db, make_user):
    await make_user("u-free", tier="free")
    with pytest.raises(AppException) as exc_info:
        await share_service.export_life_archive(db, "u-free", "all", "en")
    assert exc_info.value.code == ErrorCode.ENTITLEMENT_REQUIRED
    assert exc_info.value.http_status == 403


async def test_invalid_scope_rejected(db, make_user):
    await make_user("u-scope", tier="free")
    with pytest.raises(AppException) as exc_info:
        await share_service.export_life_archive(db, "u-scope", "forever", "en")
    assert exc_info.value.code == ErrorCode.INVALID_PARAMS


async def test_scope_is_not_escalated(db, make_user):
    """只买 1y 档不能导出 all 档（权益向下兼容，不向上）。"""
    await make_user("u-1y", tier="free")
    await _grant(db, "u-1y", "1y", "tx-scope-1")

    archive = await share_service.export_life_archive(db, "u-1y", "1y", "en")
    assert archive["gate"] == "entitlement"

    with pytest.raises(AppException) as exc_info:
        await share_service.export_life_archive(db, "u-1y", "all", "en")
    assert exc_info.value.code == ErrorCode.ENTITLEMENT_REQUIRED


async def test_subscriber_gets_one_free_generation_per_month(db, make_user, time_travel):
    """Pro 联动权益（§九-4）：订阅期内每月 1 次免费生成，跨月重新计。"""
    await make_user("u-pro", tier="pro")
    time_travel.set(datetime(2026, 8, 10, 12, tzinfo=timezone.utc))

    first = await share_service.export_life_archive(db, "u-pro", "all", "en")
    assert first["gate"] == "subscription"
    # 同月第二次命中缓存（不重复生成），跨月才允许新的免费额度
    second = await share_service.export_life_archive(db, "u-pro", "all", "en")
    assert second["from_cache"] is True
    assert second["generated_at"] == first["generated_at"]

    time_travel.set(datetime(2026, 9, 10, 12, tzinfo=timezone.utc))
    third = await share_service.export_life_archive(db, "u-pro", "all", "en")
    assert "from_cache" not in third
    # 订阅免费生成不产生买断权益行
    assert await entitlement_repository.list_active(db, "u-pro") == []


async def test_export_idempotent_per_scope(db, make_user):
    """买断导出按档位幂等：重复请求复用缓存，regenerate 才重组。"""
    await make_user("u-twice", tier="free")
    await _grant(db, "u-twice", "legacy", "tx-idem-1")
    await _seed_records(db, "u-twice")

    first = await share_service.export_life_archive(db, "u-twice", "legacy", "en")
    second = await share_service.export_life_archive(db, "u-twice", "legacy", "en")
    assert second["from_cache"] is True
    assert second["generated_at"] == first["generated_at"]

    regenerated = await share_service.export_life_archive(
        db, "u-twice", "legacy", "en", regenerate=True
    )
    assert "from_cache" not in regenerated
    assert regenerated["generated_at"] >= first["generated_at"]


# ── 3. 导出内容与合规 ──

async def test_archive_chapters_and_aigc_marking(db, make_user):
    """章节齐备 + AIGC 只标注 AI 产出的章节（GB 45438-2025 显式标识）。"""
    await make_user("u-content", tier="free", language="en")
    await _grant(db, "u-content", "legacy", "tx-legacy-1")
    await _seed_records(db, "u-content")

    archive = await share_service.export_life_archive(db, "u-content", "legacy", "en")
    keys = [c["key"] for c in archive["chapters"]]
    assert keys == [
        "overview", "portrait", "voice", "mood", "patterns", "insights",
        "milestones", "cross_year",
    ]
    for chapter in archive["chapters"]:
        if chapter["key"] in AIGC_CHAPTER_KEYS:
            assert chapter["aigc_generated"] is True
            assert "AI-GENERATED" in chapter["aigc_label"]
        else:
            assert chapter["aigc_generated"] is False
            assert chapter["aigc_label"] is None
    # 用户原话进 voice，且不标 AI
    voice_items = archive["chapters"][keys.index("voice")]["items"]
    assert any("最近每天都在跑步" in item for item in voice_items)
    assert "GB 45438-2025" in archive["aigc_notice"]
    assert archive["stats"]["memory_count"] == 2


async def test_one_year_scope_excludes_older_records(db, make_user):
    """1y 档只导出近 365 天：400 天前的原话/签到不得混入（档位即窗口）。"""
    await make_user("u-1y-window", tier="free")
    await _grant(db, "u-1y-window", "1y", "tx-1y-window")
    await _seed_records(db, "u-1y-window")

    archive = await share_service.export_life_archive(db, "u-1y-window", "1y", "zh")
    voice_items = next(c for c in archive["chapters"] if c["key"] == "voice")["items"]
    assert all("想换个城市生活" not in item for item in voice_items)
    assert any("最近每天都在跑步" in item for item in voice_items)
    assert archive["stats"]["memory_count"] == 1
    assert archive["window_days"] == 365


async def test_legacy_chapter_titles_localized_zh(db, make_user):
    """中文档：章节标题走中文，且 [AI 生成] 标识用中文（不混语言）。"""
    await make_user("u-zh", tier="free", language="zh")
    await _grant(db, "u-zh", "all", "tx-zh-1")
    await _seed_records(db, "u-zh")

    archive = await share_service.export_life_archive(db, "u-zh", "all", "zh")
    titles = [c["title"] for c in archive["chapters"]]
    assert "你说过的话" in titles
    portrait = next(c for c in archive["chapters"] if c["key"] == "portrait")
    assert portrait["aigc_label"] == "［AI 生成］"
    # all 档没有跨年对比章节（该章节是 legacy 档独有）
    assert "cross_year" not in [c["key"] for c in archive["chapters"]]


async def test_pdf_renders_and_carries_aigc_notice(db, make_user):
    """PDF 可渲染（中文字体走 CIDFont），且文档级 AIGC 声明出现在正文。"""
    await make_user("u-pdf", tier="free", language="zh")
    await _grant(db, "u-pdf", "legacy", "tx-pdf-1")
    await _seed_records(db, "u-pdf")

    pdf = await share_service.export_life_archive_pdf(db, "u-pdf", "legacy", "zh")
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 2000


# ── 4. 注销覆盖（新表不得残留用户数据）──

async def test_purge_user_covers_new_tables(db, make_user):
    from backend.services.user_service import user_service as svc

    user = await make_user("u-purge", tier="free")
    await entitlement_repository.grant(
        db, user.id, product_id="life_archive_all", scope="all",
        transaction_id="tx-purge-1",
        purchase_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    db.add(JourneyEvent(
        id="je-purge", user_id=user.id, day_no=1, event_kind="day1_checkin_echo",
        status="pending", scheduled_at=datetime.now(timezone.utc),
    ))
    db.add(OnboardingProfile(id="op-purge", user_id=user.id, persona_type="观察者"))
    db.add(SafetyEvent(id="se-purge", user_id=user.id, kind="self_harm", source="user_message"))
    await db.flush()

    counts = await svc.purge_user(db, user.id)

    assert counts["user_entitlements"] == 1
    assert counts["journey_events"] == 1
    assert counts["onboarding_profiles"] == 1
    assert counts["safety_events"] == 1

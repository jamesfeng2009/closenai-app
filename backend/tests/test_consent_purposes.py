"""T5-01 同意台账闸（Baseline §14.2）。

四件事，每件都能被打红：
1. **单一来源**：六种用途只许在 models/consent.py 定义一次；代码里出现的 purpose 形状
   字面量必须是枚举成员（typo 如 `ai_procesing` 会在这里红，而不是在库里静默长出一行垃圾）。
2. **每个枚举都有 API 可写**：逐值 POST /user/consents 并回读台账行（正向证人，
   不是「搜不到字符串就算过」）。
3. **双写不脱钩**：旧端点/新端点/设置 PATCH 三条写路径都要留下台账——只写布尔位
   等于台账永远缺页。
4. **注销删得净**：user_consents 进 PURGE_TARGETS，purge 后无残留。
"""
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.core.auth import get_current_user
from backend.core.middleware import register_exception_handlers
from backend.database import get_db
from backend.models.consent import (
    CONSENT_PURPOSES,
    CONSENT_TEXT_VERSION,
    PURPOSE_LINKED_FLAGS,
    ConsentPurpose,
)
from backend.repositories import consent_repository, user_repository
from backend.routers import user as user_router
from backend.services.consent_service import consent_service
from backend.services.user_service import PURGE_TARGETS, user_service

ROOT = Path(__file__).resolve().parents[2]

# Baseline §14.2 点名的六种用途（测试里硬写一份＝独立证人：枚举单方面增删会在这里红）
BASELINE_PURPOSES = {
    "core_service",
    "ai_processing",
    "proactive_push",
    "analytics",
    "data_export",
    "model_training",
}

# 与 purpose 同形的**非用途**字面量（模块名/函数名撞上了形状启发式）。
# 每条都要写理由，空着比留着好——加一条就等于放宽一次闸。
NON_PURPOSE_TOKENS = {
    "push_service": "backend/services/push/__init__.py 的服务变量名，不是用途",
    "discovery_service": "backend/tests/test_discovery_fusion.py 里的模块名，不是用途",
}

PURPOSE_SHAPE = re.compile(r'"([a-z][a-z0-9_]*_(?:service|processing|push|export|training))"')


@pytest.fixture
def app(db):
    app = FastAPI()
    app.include_router(user_router.router, prefix="/api/v1", tags=["user"])
    register_exception_handlers(app)

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: "u1"
    return app


async def _request(app, method, path, payload=None):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, json=payload)


# ── 1. 单一来源 ──

def test_enum_matches_baseline_section_14_2():
    assert set(CONSENT_PURPOSES) == BASELINE_PURPOSES, (
        f"用途集合与 Baseline §14.2 不一致：多 {set(CONSENT_PURPOSES) - BASELINE_PURPOSES} / "
        f"少 {BASELINE_PURPOSES - set(CONSENT_PURPOSES)}"
    )
    assert len(CONSENT_PURPOSES) == len(set(CONSENT_PURPOSES)) == 6


def test_linked_flags_covers_every_purpose():
    """每个用途都要明确表态「有没有兼容布尔位」——漏登记＝双写漏一半而没人报警。"""
    assert set(PURPOSE_LINKED_FLAGS) == set(CONSENT_PURPOSES)


def test_purpose_shaped_literals_are_all_declared():
    """代码里出现的 purpose 形状字面量必须命中枚举（typo  tripwire）。"""
    offenders = []
    for path in ROOT.joinpath("backend").rglob("*.py"):
        text = path.read_text()
        if "__pycache__" in str(path) or f"consent.py" == path.name:
            continue
        for token in PURPOSE_SHAPE.findall(text):
            if token in CONSENT_PURPOSES or token in NON_PURPOSE_TOKENS:
                continue
            offenders.append(f"{token}（{path.relative_to(ROOT)}）")
    assert not offenders, f"未登记的 purpose 形状字面量：{sorted(set(offenders))}"


def test_non_purpose_allowlist_is_not_stale():
    """放宽清单也要有人核对：清单里的 token 若已不在代码里出现，就该删掉。"""
    corpus = "\n".join(
        p.read_text()
        for p in ROOT.joinpath("backend").rglob("*.py")
        if "__pycache__" not in str(p)
    )
    stale = [t for t in NON_PURPOSE_TOKENS if f'"{t}"' not in corpus]
    assert not stale, f"NON_PURPOSE_TOKENS 过期：{stale}"


# ── 2. 每个枚举都有 API 可写 ──

@pytest.mark.parametrize("purpose", CONSENT_PURPOSES)
async def test_every_purpose_writable_via_api(app, db, make_user, purpose):
    await make_user("u1")
    resp = await _request(
        app, "POST", "/api/v1/user/consents", {"purpose": purpose, "granted": True}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["purpose"] == purpose
    row = await consent_repository.get(db, "u1", purpose, CONSENT_TEXT_VERSION)
    assert row is not None and row.granted_at is not None and row.revoked_at is None

    got = await _request(app, "GET", "/api/v1/user/consents")
    item = {i["purpose"]: i for i in got.json()["data"]["items"]}[purpose]
    assert item["granted"] is True


async def test_unknown_purpose_is_rejected(app, db, make_user):
    await make_user("u1")
    resp = await _request(
        app, "POST", "/api/v1/user/consents", {"purpose": "ai_procesing", "granted": True}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == 40001


async def test_revoke_then_regrant_keeps_one_row_and_clears_revoked(app, db, make_user):
    """同 (user,purpose,version) 只留一行：撤回写 revoked_at，再授予清空它。"""
    await make_user("u1")
    await _request(
        app, "POST", "/api/v1/user/consents", {"purpose": "analytics", "granted": True}
    )
    await _request(
        app, "POST", "/api/v1/user/consents", {"purpose": "analytics", "granted": False}
    )
    await _request(
        app, "POST", "/api/v1/user/consents", {"purpose": "analytics", "granted": True}
    )
    rows = await consent_repository.list_for_user(db, "u1")
    analytics = [r for r in rows if r.purpose == "analytics"]
    assert len(analytics) == 1
    assert analytics[0].granted_at is not None and analytics[0].revoked_at is None
    item = {i["purpose"]: i for i in (await _request(app, "GET", "/api/v1/user/consents")).json()["data"]["items"]}["analytics"]
    assert item["granted"] is True


async def test_revoke_writes_revoked_at(app, db, make_user):
    await make_user("u1", ai_consent=True)
    await _request(
        app, "POST", "/api/v1/user/consents", {"purpose": "ai_processing", "granted": True}
    )
    await _request(
        app, "POST", "/api/v1/user/consents", {"purpose": "ai_processing", "granted": False}
    )
    row = await consent_repository.get(db, "u1", "ai_processing", CONSENT_TEXT_VERSION)
    assert row.granted_at is not None and row.revoked_at is not None


# ── 3. 双写：三条写路径都要留台账 ──

async def test_ai_consent_legacy_endpoint_double_writes_ledger(app, db, make_user):
    await make_user("u1")
    resp = await _request(app, "POST", "/api/v1/user/ai-consent", {"consent": True})
    assert resp.json()["data"]["ai_consent"] is True
    assert await consent_repository.get(db, "u1", "ai_processing") is not None
    user = await user_repository.get_user(db, "u1")
    assert user.ai_consent is True


async def test_ledger_write_syncs_ai_flag(app, db, make_user):
    """反向也要同步：写台账 ⇒ 布尔位跟着动，老 App 读 users.ai_consent 才不会读到过期值。"""
    await make_user("u1")
    await _request(
        app, "POST", "/api/v1/user/consents", {"purpose": "ai_processing", "granted": True}
    )
    user = await user_repository.get_user(db, "u1")
    assert user.ai_consent is True


async def test_analytics_revocation_via_ledger_erases_and_syncs_flag(app, db, make_user, monkeypatch):
    calls = []

    async def fake_erase(user_id):
        calls.append(user_id)
        return {"erased": True}

    monkeypatch.setattr(
        "backend.services.consent_service.erase_person_data", fake_erase
    )
    await make_user("u1", analytics_consent=True)
    await _request(
        app, "POST", "/api/v1/user/consents", {"purpose": "analytics", "granted": False}
    )
    assert calls == ["u1"]
    user = await user_repository.get_user(db, "u1")
    assert user.analytics_consent is False


async def test_push_enabled_patch_records_proactive_push_consent(app, db, make_user):
    """PATCH /user/settings 关掉推送也必须进台账：否则「用户自己关的」在合规记录里查无此事。"""
    await make_user("u1")
    await _request(app, "PATCH", "/api/v1/user/settings", {"push_enabled": True})
    row = await consent_repository.get(db, "u1", "proactive_push")
    assert row is not None and row.granted_at is not None
    assert (await user_repository.get_user(db, "u1")).push_enabled is True


# ── 4. 注册默认值与注销清理 ──

async def test_registration_grants_only_core_service(db):
    from backend.services.auth_service import auth_service

    resp = await auth_service.register_with_password(
        db, "u-new@example.com", "newbie", "Passw0rd!", "en"
    )
    rows = await consent_repository.list_for_user(db, resp.user.id)
    assert [r.purpose for r in rows] == ["core_service"]
    assert rows[0].granted_at is not None


async def test_consent_table_is_in_purge_targets():
    assert "user_consents" in {key for _, key in PURGE_TARGETS}


async def test_purge_deletes_consent_rows(db, make_user):
    from sqlalchemy import select
    from backend.models.consent import UserConsent

    await make_user("u1")
    await consent_service.set_consent(db, "u1", "ai_processing", True)
    rows = (await db.execute(select(UserConsent))).scalars().all()
    assert len(rows) == 1
    counts = await user_service.purge_user(db, "u1")
    assert counts["user_consents"] == 1
    assert (await db.execute(select(UserConsent))).scalars().all() == []


# ── 5. 用途枚举的 Python 侧形状（值即落库串，Enum 改名不改值的护栏）──

def test_enum_values_are_the_stored_strings():
    assert [p.value for p in ConsentPurpose] == list(CONSENT_PURPOSES)
    assert ConsentPurpose.AI_PROCESSING == "ai_processing"  # str-Enum：可直接进 SQL

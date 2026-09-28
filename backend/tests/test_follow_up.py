"""事件后 3-7 天主动跟进（P0-F07）。

覆盖：跟进窗口/类型/防重复标记过滤、消息生成（LLM lite + language + 降级模板）、
cron 分发（发送/幂等/合规跳过）、/cron/follow-up 端点 QStash 验签。
"""
import base64
import hashlib
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.config import settings
from backend.core.middleware import register_exception_handlers
from backend.database import get_db
from backend.repositories import chat_repository, growth_repository
from backend.routers import cron as cron_router
from backend.services.cron_service import cron_service
from backend.services.growth_service import growth_service
from backend.services.message_generator import message_generator

FROZEN_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


async def _make_record(db, user_id, record_type, content, days_ago, follow_up_sent=False, hours=0):
    """造一条 created_at 为 N 天前的成长记录。"""
    record = await growth_repository.create(db, user_id, record_type, content)
    record.created_at = FROZEN_NOW - timedelta(days=days_ago, hours=hours)
    record.follow_up_sent = follow_up_sent
    await db.flush()
    return record


# ── growth_service.get_pending_follow_ups：窗口 / 类型 / 标记过滤 ──


async def test_get_pending_follow_ups_filters_window_type_and_flag(db, make_user, time_travel):
    """仅返回 3-7 天前、reflection/milestone 类型、未标记跟进的记录。"""
    time_travel.set(FROZEN_NOW)
    await make_user("u1", timezone="UTC")
    pending_reflection = await _make_record(db, "u1", "reflection", "四天前的反思", 4)
    pending_milestone = await _make_record(db, "u1", "milestone", "五天前的重要事件", 5)
    await _make_record(db, "u1", "reflection", "一天前的反思（太新）", 1)
    await _make_record(db, "u1", "reflection", "十天前的反思（太旧）", 10)
    await _make_record(db, "u1", "daily_insight", "四天前的每日洞察（类型不符）", 4)
    await _make_record(db, "u1", "reflection", "五天前已跟进", 5, follow_up_sent=True)

    records = await growth_service.get_pending_follow_ups(db)

    assert [r.id for r in records] == [pending_milestone.id, pending_reflection.id]


async def test_get_pending_follow_ups_respects_configurable_window(
    db, make_user, time_travel, monkeypatch
):
    """窗口天数走配置项：改为 1-2 天后只命中 1.5 天前的记录。"""
    time_travel.set(FROZEN_NOW)
    monkeypatch.setattr(settings, "follow_up_window_start_days", 1)
    monkeypatch.setattr(settings, "follow_up_window_end_days", 2)
    await make_user("u1", timezone="UTC")
    recent = await _make_record(db, "u1", "reflection", "一天半前的反思", 1, hours=12)
    await _make_record(db, "u1", "reflection", "四天前的反思", 4)

    records = await growth_service.get_pending_follow_ups(db)

    assert [r.id for r in records] == [recent.id]


# ── message_generator.generate_follow_up：lite + language + 降级 ──


async def test_generate_follow_up_uses_lite_and_language(mock_llm):
    """LLM 调用必须 lite=True 且透传用户语言。"""
    text = await message_generator.generate_follow_up("我因为同事不配合很生气", "zh")

    assert text == "MOCK_LLM_REPLY"
    call = mock_llm.calls["complete"][0]
    assert call["lite"] is True
    assert call["language"] == "zh"
    assert "我因为同事不配合很生气" in call["prompt"]


async def test_generate_follow_up_fallback_on_llm_failure(mock_llm):
    """LLM 异常 → 降级模板（中英按语言），不抛异常。"""

    def _raise(prompt, **kwargs):
        raise RuntimeError("llm down")

    mock_llm.state["complete_fn"] = _raise

    zh_text = await message_generator.generate_follow_up("面试没发挥好", "zh")
    assert zh_text == "上次你提到「面试没发挥好」，现在怎么样了？想聊聊吗？"

    en_text = await message_generator.generate_follow_up("I bombed the interview", "en")
    assert en_text == (
        'Last time you mentioned "I bombed the interview" — '
        "how's it going now? I'd love to hear."
    )


async def test_generate_follow_up_empty_content_returns_fallback(mock_llm):
    """空内容不调用 LLM，直接返回降级模板。"""
    text = await message_generator.generate_follow_up("", "zh")

    assert "现在怎么样了" in text
    assert mock_llm.calls["complete"] == []


# ── cron_service.dispatch_follow_ups：发送 / 幂等 / 合规跳过 ──


async def test_dispatch_follow_ups_sends_message_and_marks(db, make_user, time_travel):
    """窗口内记录 → 写入 follow_up 消息并标记已发送。"""
    time_travel.set(FROZEN_NOW)
    await make_user("u1", ai_consent=True, language="zh", timezone="UTC")
    record = await _make_record(db, "u1", "reflection", "我因为同事不配合很生气", 4)

    result = await cron_service.dispatch_follow_ups(db)

    assert result["sent"] == 1
    assert result["failed"] == 0
    history = await chat_repository.get_history(db, "u1")
    assert len(history) == 1
    assert history[0].message_type == "follow_up"
    assert history[0].role == "assistant"
    assert history[0].ai_generated is True
    assert history[0].content == "MOCK_LLM_REPLY"
    await db.refresh(record)
    assert record.follow_up_sent is True


async def test_dispatch_follow_ups_idempotent_second_run(db, make_user, time_travel):
    """防重复：第二轮扫描不再发送（follow_up_sent 标记生效）。"""
    time_travel.set(FROZEN_NOW)
    await make_user("u1", ai_consent=True, language="zh", timezone="UTC")
    await _make_record(db, "u1", "reflection", "四天前的反思", 4)

    first = await cron_service.dispatch_follow_ups(db)
    second = await cron_service.dispatch_follow_ups(db)

    assert first["sent"] == 1
    assert second["pending"] == 0
    assert second["sent"] == 0
    assert len(await chat_repository.get_history(db, "u1")) == 1


async def test_dispatch_follow_ups_skips_without_ai_consent(db, make_user, time_travel):
    """合规：未同意 AI 数据共享 → 不生成消息，但标记已处理避免重复扫描。"""
    time_travel.set(FROZEN_NOW)
    await make_user("u1", ai_consent=False, language="zh", timezone="UTC")
    record = await _make_record(db, "u1", "reflection", "四天前的反思", 4)

    result = await cron_service.dispatch_follow_ups(db)

    assert result["sent"] == 0
    assert result["skipped"] == 1
    assert await chat_repository.get_history(db, "u1") == []
    await db.refresh(record)
    assert record.follow_up_sent is True


# ── POST /cron/follow-up：QStash 验签 ──

_SIGNING_KEY = "test-signing-key-0123456789abcdef"


@pytest.fixture
def app(db, monkeypatch):
    monkeypatch.setattr(settings, "qstash_current_signing_key", _SIGNING_KEY)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(cron_router.router, prefix="/api/v1", tags=["cron"])

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    return app


def _qstash_signature(body: bytes) -> str:
    """按 Upstash 签名机制构造 JWT：payload.body = 请求体 SHA-256 的 base64url（无填充）。

    exp 必须基于真实时钟：PyJWT 用自己的 utcnow 校验 exp，不受 time_travel 补丁影响，
    用 FROZEN_NOW 造 exp 会在冻结日之后把签名判成过期。
    """
    digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()
    return jwt.encode(
        {"body": digest, "exp": int(datetime.now(timezone.utc).timestamp()) + 3600},
        _SIGNING_KEY,
        algorithm="HS256",
    )


async def _post_follow_up(app, headers=None):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/v1/cron/follow-up", content=b"", headers=headers)


async def test_follow_up_endpoint_rejects_missing_signature(app):
    """无 Upstash-Signature 头 → 401（fail-closed）。"""
    resp = await _post_follow_up(app)

    assert resp.status_code == 401


async def test_follow_up_endpoint_rejects_bad_signature(app):
    """签名错误 → 401。"""
    resp = await _post_follow_up(app, headers={"Upstash-Signature": "bad-token"})

    assert resp.status_code == 401


async def test_follow_up_endpoint_dispatches_with_valid_signature(
    app, db, make_user, time_travel
):
    """验签通过 → 执行扫描分发，返回统一成功包装。"""
    time_travel.set(FROZEN_NOW)
    await make_user("u1", ai_consent=True, language="zh", timezone="UTC")
    await _make_record(db, "u1", "reflection", "四天前的反思", 4)

    resp = await _post_follow_up(
        app, headers={"Upstash-Signature": _qstash_signature(b"")}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200
    assert body["data"]["sent"] == 1
    assert body["data"]["status"] == "completed"

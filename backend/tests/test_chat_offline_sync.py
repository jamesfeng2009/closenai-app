"""P2 #28 · 离线消息补投的判据（POST /chat/messages/batch + 增量历史）。

缺陷原状：离线时用户消息只写在本机 SQLite，联网后没有任何上传路径；在线发送又由
SSE 在服务端另生成一个 uuid ⇒ 同一条消息在本地与远端各一份，刷新后靠 id 合并也认不出。
本文件判三件事：

1. **幂等**：同一个 (user_id, client_message_id) 无论补投几次、补投与在线流谁先到，
   库里只有一行；第二次以后的客户端 id 归 `existing`；
2. **不越权**：未同意 AI 数据共享时正文不上送（403），别的用户拿同一个客户端 id
   不受影响（唯一性按用户）；
3. **水位可用**：`GET /chat/history?since=` 真的只返回新消息，且每条回显
   `client_message_id`——前端靠它把本地缓存行与远端行认成同一条。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import backend.routers.chat as chat_router
from backend.core.auth import get_current_user
from backend.core.errors import ErrorCode
from backend.core.middleware import register_exception_handlers
from backend.database import get_db
from backend.models.chat import ChatMessage
from backend.repositories import chat_repository, user_repository


@pytest.fixture
def app(db):
    app = FastAPI()
    app.include_router(chat_router.router, prefix="/api/v1", tags=["chat"])
    register_exception_handlers(app)

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: "u-sync"
    return app


async def _post_batch(app, messages: list[dict]):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/v1/chat/messages/batch", json={"messages": messages})


async def _get_history(app, query: str = ""):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(f"/api/v1/chat/history{query}")


async def _rows(db, user_id: str = "u-sync") -> list[ChatMessage]:
    result = await db.execute(select(ChatMessage).where(ChatMessage.user_id == user_id))
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_batch_inserts_user_messages_once(app, db, make_user):
    await make_user("u-sync", ai_consent=True)

    resp = await _post_batch(app, [
        {"client_message_id": "u-1", "content": "第一条"},
        {"client_message_id": "u-2", "content": "第二条"},
    ])

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"created": ["u-1", "u-2"], "existing": []}
    rows = await _rows(db)
    assert {r.client_message_id for r in rows} == {"u-1", "u-2"}
    assert all(r.role == "user" for r in rows)
    assert (await user_repository.get_user(db, "u-sync")).chat_count == 2


@pytest.mark.asyncio
async def test_repeat_upload_is_idempotent_and_does_not_double_count(app, db, make_user):
    await make_user("u-sync", ai_consent=True)
    payload = [{"client_message_id": "u-1", "content": "只说一次"}]

    first = await _post_batch(app, payload)
    second = await _post_batch(app, payload)

    assert first.json()["data"]["created"] == ["u-1"]
    assert second.json()["data"] == {"created": [], "existing": ["u-1"]}
    assert len(await _rows(db)) == 1
    assert (await user_repository.get_user(db, "u-sync")).chat_count == 1


@pytest.mark.asyncio
async def test_consent_gate_blocks_the_body(app, db, make_user):
    await make_user("u-sync", ai_consent=False)

    resp = await _post_batch(app, [{"client_message_id": "u-1", "content": "不同意就别收"}])

    assert resp.status_code == 403
    assert resp.json()["code"] == int(ErrorCode.AI_CONSENT_REQUIRED)
    assert await _rows(db) == []


@pytest.mark.asyncio
async def test_client_id_is_unique_per_user_not_globally(db, make_user):
    """两个用户各自的本地 id 可能都叫 u-1：唯一性只能是 (user_id, client_message_id)。"""
    await make_user("u-sync")
    await make_user("u-other")

    _, a_new = await chat_repository.create_from_client(db, "u-sync", "u-1", "A")
    _, b_new = await chat_repository.create_from_client(db, "u-other", "u-1", "B")

    assert a_new and b_new
    assert len(await _rows(db, "u-sync")) == 1
    assert len(await _rows(db, "u-other")) == 1


@pytest.mark.asyncio
async def test_history_echoes_client_id_and_since_filters(app, db, make_user):
    await make_user("u-sync", ai_consent=True)
    t1 = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
    t2 = t1 + timedelta(hours=1)

    await _post_batch(app, [
        {"client_message_id": "u-old", "content": "昨天", "created_at": t1.isoformat()},
        {"client_message_id": "u-new", "content": "今天", "created_at": t2.isoformat()},
    ])

    full = await _get_history(app)
    assert full.status_code == 200
    assert {m["client_message_id"] for m in full.json()["data"]} == {"u-old", "u-new"}

    # `+00:00` 里的 `+` 在 query string 里会被解成空格，必须转义
    incremental = await _get_history(app, f"?since={quote(t1.isoformat())}")
    assert incremental.status_code == 200
    assert [m["client_message_id"] for m in incremental.json()["data"]] == ["u-new"]


@pytest.mark.asyncio
async def test_incremental_pull_is_ascending_so_watermark_cannot_skip(app, db, make_user):
    """新消息多于 limit 时，增量批次必须从旧到新发：水位推进一步也不漏。

    若按首屏的倒序发，第一批拿最新的 limit 条、水位就跳到最新，
    中间那批（比第一批旧）以后再也取不到。
    """
    await make_user("u-sync", ai_consent=True)
    base = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
    await _post_batch(app, [
        {"client_message_id": f"u-{i}", "content": f"第{i}条",
         "created_at": (base + timedelta(minutes=i)).isoformat()}
        for i in range(3)
    ])

    first = await _get_history(
        app, f"?since={quote((base - timedelta(minutes=1)).isoformat())}&limit=2"
    )
    assert [m["client_message_id"] for m in first.json()["data"]] == ["u-0", "u-1"]

    watermark = first.json()["data"][-1]["created_at"]
    second = await _get_history(app, f"?since={quote(watermark)}&limit=2")
    assert [m["client_message_id"] for m in second.json()["data"]] == ["u-2"]

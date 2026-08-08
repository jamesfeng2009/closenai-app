"""对话风格（PRD P1-F04）：设置更新、非法值 400、prompt 注入断言。"""
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from backend.core.auth import get_current_user
from backend.core.middleware import register_exception_handlers
from backend.database import get_db
from backend.models.schemas import UserSettingsUpdate
from backend.routers import user as user_router
from backend.services.message_generator import (
    conversation_style_prompt,
    message_generator,
)
from backend.services.push.copy import push_copywriter
from backend.services.user_service import user_service


# ── schema 校验 ──

def test_conversation_style_valid_values():
    """三种合法风格均可通过校验。"""
    for style in ("warm", "rational", "philosophical"):
        assert UserSettingsUpdate(conversation_style=style).conversation_style == style


def test_conversation_style_invalid_rejected():
    """非法风格值 → ValidationError（与 language 校验同路径，API 层映射 400）。"""
    with pytest.raises(ValidationError):
        UserSettingsUpdate(conversation_style="aggressive")


def test_conversation_style_prompt_bilingual_and_fallback():
    """风格 prompt 按语言返回，未知值回退默认 warm；均含安全不变约束。"""
    zh = conversation_style_prompt("rational", "zh")
    assert "理性分析" in zh and "不改变任何安全与边界规则" in zh
    en = conversation_style_prompt("philosophical", "en")
    assert "Philosophical" in en and "never overrides any safety" in en
    assert conversation_style_prompt("unknown", "zh") == conversation_style_prompt("warm", "zh")
    assert conversation_style_prompt(None, "en") == conversation_style_prompt("warm", "en")


# ── 设置更新 + 画像透传 ──

async def test_update_settings_persists_conversation_style(db, make_user):
    """PATCH 设置持久化 conversation_style，get_profile 透传给前端选择器。"""
    await make_user("u1")
    profile = await user_service.get_profile(db, "u1")
    assert profile.conversation_style == "warm"  # 模型默认值

    profile = await user_service.update_settings(
        db, "u1", UserSettingsUpdate(conversation_style="rational")
    )
    assert profile.conversation_style == "rational"

    # 未传字段不覆盖（部分更新语义）
    profile = await user_service.update_settings(db, "u1", UserSettingsUpdate(name="新名字"))
    assert profile.conversation_style == "rational"


# ── API 层：非法值 400 ──

@pytest.fixture
def app(db):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(user_router.router, prefix="/api/v1", tags=["user"])

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: "u1"
    return app


async def test_patch_settings_invalid_style_returns_400(app, db, make_user):
    """PATCH /user/settings 非法 conversation_style → 400 INVALID_PARAMS。"""
    await make_user("u1")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/user/settings", json={"conversation_style": "aggressive"}
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == 40001

        # 合法值 → 200 且画像带出该字段
        resp = await client.patch(
            "/api/v1/user/settings", json={"conversation_style": "philosophical"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["conversation_style"] == "philosophical"

        resp = await client.get("/api/v1/user/profile")
        assert resp.json()["data"]["conversation_style"] == "philosophical"


# ── prompt 注入 ──

async def test_chat_stream_injects_conversation_style(db, make_user, mock_llm):
    """流式对话 system prompt 含风格描述 + 安全不变约束（zh / en 各一）。"""
    await make_user("u-zh", language="zh", conversation_style="rational")
    async for _ in message_generator.generate_chat_stream(db, "u-zh", "你好", language="zh"):
        pass
    prompt = mock_llm.calls["stream"][0]["system_prompt"]
    assert "理性分析" in prompt
    assert "不改变任何安全与边界规则" in prompt

    await make_user("u-en", language="en", conversation_style="philosophical")
    async for _ in message_generator.generate_chat_stream(db, "u-en", "hello", language="en"):
        pass
    prompt = mock_llm.calls["stream"][1]["system_prompt"]
    assert "Philosophical" in prompt
    assert "never overrides any safety" in prompt


async def test_chat_stream_default_style_warm(db, make_user, mock_llm):
    """未设置风格的用户 → 默认 warm 注入。"""
    await make_user("u1", language="zh")
    async for _ in message_generator.generate_chat_stream(db, "u1", "你好", language="zh"):
        pass
    assert "温暖知心" in mock_llm.calls["stream"][0]["system_prompt"]


async def test_push_copy_aware_of_conversation_style(db, make_user, mock_llm):
    """推送文案 LLM 链路（Pro）prompt 含风格描述，安全措辞约束保留。"""
    user = await make_user("u-pro", tier="pro", language="zh", conversation_style="rational")
    title, body = await push_copywriter.build_copy(db, user, "proactive", message="来聊聊吧")
    assert title and body
    push_prompt = mock_llm.calls["complete"][0]["prompt"]
    assert "理性分析" in push_prompt
    assert "占星" in push_prompt  # 原有安全措辞约束不变

"""P2 #24 · 主要性格框架的持久化闸（users.primary_framework）。

缺陷形状：前端「选择框架」确实发了 `primary_framework`，后端 `UserSettingsUpdate`
里没有这个字段 ⇒ pydantic 静默丢弃；界面再自己拼一个本地 state 顶上，看起来能用，
但一刷新/换设备就退回默认。所以本文件判的不只是「PATCH 能写」，而是：

1. 写侧接受且只接受两个合法值（mbti / enneagram）——非法值挡在 422，不进库；
2. 写后**回读**：响应与下一次 GET 都来自库里，不是请求体的回声；
3. 只改别的设置不许把它顶掉（仓储「None 不动」的语义）；
4. 库里的历史脏值不许把整个个人页打成 500（回显为 None）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

import backend.routers.user as user_router
from backend.core.auth import get_current_user
from backend.core.middleware import register_exception_handlers
from backend.database import get_db
from backend.models.schemas import UserProfileResponse, UserSettingsUpdate
from backend.services.user_service import user_service


async def test_new_user_has_no_framework(db, make_user):
    """没选过的用户回 None：不替用户猜一个框架（前端据此显示默认项但不冒充已选）。"""
    await make_user("u-framework-default")
    profile = await user_service.get_profile(db, "u-framework-default")
    assert profile.primary_framework is None


async def test_patch_persists_and_echoes_the_framework(db, make_user):
    """PATCH 的响应体里必须就是刚写进去的值（get_profile 读库，不回显请求体）。"""
    await make_user("u-framework-echo")
    updated = await user_service.update_settings(
        db, "u-framework-echo", UserSettingsUpdate(primary_framework="enneagram")
    )
    assert updated.primary_framework == "enneagram"
    assert (await user_service.get_profile(db, "u-framework-echo")).primary_framework == "enneagram"


async def test_framework_survives_a_fresh_read_and_can_be_switched_back(db, make_user):
    """先写一个值再换一个：证明读回来的来自库里，而不是某处缓存或默认值。"""
    await make_user("u-framework-switch")
    await user_service.update_settings(
        db, "u-framework-switch", UserSettingsUpdate(primary_framework="enneagram")
    )
    assert (await user_service.get_profile(db, "u-framework-switch")).primary_framework == "enneagram"
    await user_service.update_settings(
        db, "u-framework-switch", UserSettingsUpdate(primary_framework="mbti")
    )
    assert (await user_service.get_profile(db, "u-framework-switch")).primary_framework == "mbti"


async def test_unrelated_settings_update_does_not_clobber_the_framework(db, make_user):
    """只改推送时间不许把框架顶掉：仓储按「None 就不动」写库。"""
    await make_user("u-framework-partial")
    await user_service.update_settings(
        db, "u-framework-partial", UserSettingsUpdate(primary_framework="mbti")
    )
    after = await user_service.update_settings(
        db, "u-framework-partial", UserSettingsUpdate(push_time="22:30")
    )
    assert after.primary_framework == "mbti"
    assert after.push_time == "22:30"


def test_write_side_rejects_unknown_frameworks():
    """合法集合只有两个值：写侧是 Literal，第三个名字必须在入参处就被挡掉。"""
    with pytest.raises(ValidationError):
        UserSettingsUpdate(primary_framework="bigfive")
    assert UserSettingsUpdate(primary_framework="enneagram").primary_framework == "enneagram"


async def test_profile_read_falls_back_to_none_for_a_dirty_stored_value(db, make_user):
    """存量/手工写入的非法值不许把个人页打成 500：回显 None 而不是抛 Literal 校验。"""
    user = await make_user("u-framework-dirty")
    user.primary_framework = "bigfive"
    await db.flush()
    profile = await user_service.get_profile(db, "u-framework-dirty")
    assert profile.primary_framework is None


def test_both_sides_declare_the_same_field():
    """读写两侧都得有这一格：只加写侧＝继续「写得进读不出」。"""
    assert "primary_framework" in UserProfileResponse.model_fields
    assert "primary_framework" in UserSettingsUpdate.model_fields


# ── HTTP 面：PATCH/GET 真的走通（路由层不许把字段漏掉）──

@pytest.fixture
def app(db):
    app = FastAPI()
    app.include_router(user_router.router, prefix="/api/v1", tags=["user"])
    register_exception_handlers(app)

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: "u-framework-http"
    return app


async def test_http_patch_then_get_round_trips_the_framework(app, make_user):
    await make_user("u-framework-http")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        patched = await client.patch(
            "/api/v1/user/settings", json={"primary_framework": "enneagram"}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["data"]["primary_framework"] == "enneagram"

        got = await client.get("/api/v1/user/profile")
        assert got.status_code == 200, got.text
        assert got.json()["data"]["primary_framework"] == "enneagram"


async def test_http_rejects_unknown_framework_before_touching_the_db(app, make_user):
    user = await make_user("u-framework-http-bad")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/user/settings", json={"primary_framework": "bigfive"}
        )
    # 本仓把请求体校验失败统一折成 400（middleware 的参数校验分支），不是 FastAPI 默认 422
    assert resp.status_code == 400, resp.text
    assert user.primary_framework is None

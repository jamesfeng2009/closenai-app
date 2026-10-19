"""T5-02 免打扰时段：纯函数、API 校验、cron 投递门、推送门。

判据分四层，缺一层就有「设了免打扰还是被叫醒」的漏洞：
1. `in_quiet_hours` 纯函数——跨零点、边界含头不含尾、NULL＝关闭；
2. `PATCH /user/settings` 的输入校验——非法 HH:MM、start==end、半开窗口都进不了库；
3. cron 的投递门**先于配额与写库**——命中静默当日不生成消息、不吃当日配额；
4. push 的投递门在 `send_to_user` 里兜住九处投递点（剧本、里程碑、关心……）。
"""
from datetime import datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.core.auth import get_current_user
from backend.core.middleware import register_exception_handlers
from backend.core.time_utils import in_quiet_hours, user_in_quiet_hours
from backend.database import get_db
from backend.repositories import chat_repository, user_repository
from backend.routers import user as user_router
from backend.services.cron_service import cron_service
from backend.services.message_generator import message_generator
from backend.services.push import push_service


# ── 1. 纯函数 ──

def test_inside_and_outside_window():
    assert in_quiet_hours(dt_time(23, 0), dt_time(22, 0), dt_time(8, 0)) is True
    assert in_quiet_hours(dt_time(12, 0), dt_time(22, 0), dt_time(8, 0)) is False


def test_cross_midnight_window():
    """22:00–08:00 是同一个窗口，不是两段互斥的窗口。"""
    for moment in (dt_time(23, 30), dt_time(2, 0), dt_time(7, 59)):
        assert in_quiet_hours(moment, dt_time(22, 0), dt_time(8, 0)) is True


def test_boundaries_include_start_exclude_end():
    start, end = dt_time(22, 0), dt_time(8, 0)
    assert in_quiet_hours(start, start, end) is True  # 起点本身静默
    assert in_quiet_hours(end, start, end) is False  # 终点即恢复


def test_null_window_means_off():
    """任一列为 NULL 即关闭（成对写、成对清，见 users.quiet_start 注释）。"""
    assert in_quiet_hours(dt_time(3, 0), None, dt_time(8, 0)) is False
    assert in_quiet_hours(dt_time(3, 0), dt_time(22, 0), None) is False
    assert in_quiet_hours(dt_time(3, 0), None, None) is False


def test_same_start_and_end_is_not_silently_all_day():
    """start==end 由 API 层拒绝；这里兜第二道：万一进了库，按「不静默」处理而不是全天静默。"""
    assert in_quiet_hours(dt_time(5, 0), dt_time(4, 0), dt_time(4, 0)) is False


def test_dst_shift_follows_local_wall_clock():
    """同一个本地墙钟时刻，DST 前后判定一致（免打扰按用户的钟，不按 UTC 时刻）。

    America/New_York：2026-03-08 02:00 春季跳秒（EST−5 → EDT−4）。
    两个 UTC 时刻都换算是本地 01:30——窗口 01:00–02:00 内都必须判静默。
    """
    tz = ZoneInfo("America/New_York")
    winter = datetime(2026, 3, 6, 6, 30, tzinfo=timezone.utc).astimezone(tz)  # 01:30 EST
    summer = datetime(2026, 7, 1, 5, 30, tzinfo=timezone.utc).astimezone(tz)  # 01:30 EDT
    assert winter.timetz().replace(tzinfo=None) == summer.timetz().replace(tzinfo=None) == dt_time(1, 30)
    assert in_quiet_hours(winter.time(), dt_time(1, 0), dt_time(2, 0)) is True
    assert in_quiet_hours(summer.time(), dt_time(1, 0), dt_time(2, 0)) is True


def test_user_wrapper_uses_users_timezone():
    class _U:
        timezone = "Asia/Shanghai"
        quiet_start = dt_time(23, 0)
        quiet_end = dt_time(7, 0)

    now_utc = datetime(2026, 8, 1, 15, 30, tzinfo=timezone.utc)  # 本地 23:30
    assert user_in_quiet_hours(_U(), now_utc) is True
    assert user_in_quiet_hours(_U(), datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)) is False


# ── 2. PATCH 输入校验与回显 ──

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


async def test_patch_accepts_and_echoes_window(app, db, make_user):
    await make_user("u1")
    resp = await _request(
        app, "PATCH", "/api/v1/user/settings", {"quiet_start": "22:00", "quiet_end": "08:00"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert (data["quiet_start"], data["quiet_end"]) == ("22:00", "08:00")
    user = await user_repository.get_user(db, "u1")
    assert (user.quiet_start, user.quiet_end) == (dt_time(22, 0), dt_time(8, 0))


async def test_patch_rejects_malformed_and_degenerate(app, db, make_user):
    await make_user("u1")
    bad = [
        {"quiet_start": "22:00", "quiet_end": "8:00"},  # 不是 HH:MM
        {"quiet_start": "24:00", "quiet_end": "08:00"},  # 时越界
        {"quiet_start": "08:00", "quiet_end": "08:00"},  # start==end：全天或全不，歧义
        {"quiet_start": "22:00"},  # 半开窗口
    ]
    for payload in bad:
        resp = await _request(app, "PATCH", "/api/v1/user/settings", payload)
        assert resp.status_code == 400, f"{payload} 应被拒，实得 {resp.status_code}"
        assert resp.json()["code"] == 40001, f"{payload} 应走 40001 参数错误"


async def test_disable_clears_both_columns(app, db, make_user):
    """Optional 字段分不清「未提供」和「置空」，所以关闭走显式开关。"""
    await make_user("u1")
    await _request(
        app, "PATCH", "/api/v1/user/settings", {"quiet_start": "22:00", "quiet_end": "08:00"}
    )
    resp = await _request(app, "PATCH", "/api/v1/user/settings", {"quiet_hours_enabled": False})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["quiet_start"] is None and data["quiet_end"] is None


async def test_enable_without_both_moments_rejected(app, db, make_user):
    await make_user("u1")
    resp = await _request(app, "PATCH", "/api/v1/user/settings", {"quiet_hours_enabled": True})
    # 本仓把 pydantic 校验失败统一映射为 400/40001（见 middleware.register_exception_handlers），
    # 不是 FastAPI 默认的 422；断错码会漏掉「校验绕过全局 handler 直接 500」这类回归。
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == 40001


# ── 3. cron 投递门：窗口内不生成、不吃配额；窗口外照常 ──

def _windows_around_now(tz: str = "UTC"):
    """按**真实此刻**推窗口，而不是写死日期。

    本仓已有教训：主动消息的今日去重查询是开区间（只有下界），把冻结时钟推到过去
    会让日历一天天腐烂——今天过的用例明天照样绿。用真实此刻推就不存在过期日。
    """
    now = datetime.now(ZoneInfo(tz))
    inside = ((now - timedelta(hours=1)).time(), (now + timedelta(hours=1)).time())
    outside = ((now + timedelta(hours=2)).time(), (now + timedelta(hours=3)).time())
    return inside, outside


async def test_cron_skips_inside_quiet_window_and_keeps_quota(db, make_user, monkeypatch):
    inside, _ = _windows_around_now()

    async def fail_generate(db, user_id, language):
        raise AssertionError("免打扰窗口内不该调用 LLM 生成（配额与成本都不该被吃掉）")

    monkeypatch.setattr(message_generator, "generate_proactive_message", fail_generate)
    await make_user(
        "u1", ai_consent=True, timezone="UTC",
        quiet_start=inside[0], quiet_end=inside[1],
    )

    result = await cron_service.process_batch(db, ["u1"])

    assert result["skipped_quiet"] == 1
    assert result["success"] == 0
    assert await chat_repository.get_history(db, "u1") == []


async def test_cron_delivers_outside_quiet_window(db, make_user, monkeypatch):
    """对照组：窗口外照常投递——否则「永远不发」也能让上一条用例绿。"""
    _, outside = _windows_around_now()

    async def fake_generate(db, user_id, language):
        return "早安。"

    monkeypatch.setattr(message_generator, "generate_proactive_message", fake_generate)
    await make_user(
        "u1", ai_consent=True, timezone="UTC",
        quiet_start=outside[0], quiet_end=outside[1],
    )

    result = await cron_service.process_batch(db, ["u1"])

    assert result["success"] == 1
    history = await chat_repository.get_history(db, "u1")
    assert len(history) == 1


# ── 4. 推送门：剧本等九处投递点也受同一窗口约束 ──

async def test_push_gate_blocks_devices_inside_window(db, make_user, monkeypatch):
    from backend.repositories import push_token_repository

    inside, _ = _windows_around_now()
    await make_user("u1", timezone="UTC", quiet_start=inside[0], quiet_end=inside[1])

    async def spy_list(db, user_id):
        raise AssertionError("免打扰窗口内不该去查设备 token")

    monkeypatch.setattr(push_token_repository, "list_for_user", spy_list)
    out = await push_service.send_to_user(db, "u1", "标题", "正文", {"type": "journey"})
    assert out == {"sent": 0, "failed": 0}


async def test_push_gate_allows_devices_outside_window(db, make_user, monkeypatch):
    """正向证人：窗口外照旧查到 token 并推送——证明上一条拦的是静默，不是整条链路坏了。"""
    from backend.repositories import push_token_repository

    _, outside = _windows_around_now()
    await make_user("u1", timezone="UTC", quiet_start=outside[0], quiet_end=outside[1])
    seen = []

    async def fake_list(db, user_id):
        seen.append(user_id)
        return []

    monkeypatch.setattr(push_token_repository, "list_for_user", fake_list)
    await push_service.send_to_user(db, "u1", "标题", "正文", {"type": "journey"})
    assert seen == ["u1"]

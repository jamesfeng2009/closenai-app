"""注销抹除 PostHog 侧数据（J3-②）+ purge_user 串联。

为什么按 Personal API 而不是 capture SDK（实测证据，不是猜测）：
- 本机 pip download posthog 得到 7.60.1（requirements 写的是 >=3.0.0，新装即此版本），
  解包后 grep 整个包：模块级与 CapturedClient **都没有** delete_person / delete_data，
  所以"用 SDK 删人"这条路根本不存在。
- 服务端 posthog/api/person.py 的 PersonBulkDeleteRequestSerializer 接受
  distinct_ids 直传（不必先查 person uuid），POST /api/person/bulk_delete/；
  posthog/auth.py 的 PersonalAPIKeyAuthentication.keyword == "Bearer"
  → 鉴权头为 Authorization: Bearer <personal key>。
"""
import pytest

from backend.config import settings
from backend.repositories import user_repository
from backend.services import analytics_service as analytics_module
from backend.services.analytics_service import erase_person_data
from backend.services.user_service import user_service

PERSONAL_KEY = "phx_personal_test_key"


async def _make_user(db) -> str:
    from uuid import uuid4

    user = await user_repository.create_user(db, user_id=str(uuid4()))
    await db.flush()
    return user.id


class _Resp:
    def __init__(self, code: int):
        self.status_code = code
        self.text = "stub-body"


def _stub_httpx(monkeypatch, status_code=202, exc: Exception | None = None):
    """替换 httpx.AsyncClient，记录 url / 头 / body / 超时，不发真实请求。"""
    calls: list[dict] = []

    class _Client:
        def __init__(self, **kwargs):
            self.init_kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def post(self, url, headers=None, json=None):
            calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "json": json,
                    "timeout": self.init_kwargs.get("timeout"),
                }
            )
            if exc is not None:
                raise exc
            return _Resp(status_code)

    monkeypatch.setattr(analytics_module.httpx, "AsyncClient", _Client)
    return calls


# ── 未配置即 no-op ──


async def test_erase_noop_without_personal_key(monkeypatch):
    calls = _stub_httpx(monkeypatch)
    monkeypatch.setattr(settings, "posthog_personal_api_key", "")

    result = await erase_person_data("u1")

    assert result == {"erased": False, "reason": "not_configured"}
    assert calls == []  # 未配置绝不能发请求（防把内部地址当 PostHog 打）


# ── 请求形态（URL / 鉴权 / body / 超时）──


async def test_erase_posts_bulk_delete_with_bearer(monkeypatch):
    calls = _stub_httpx(monkeypatch, status_code=202)
    monkeypatch.setattr(settings, "posthog_personal_api_key", PERSONAL_KEY)
    monkeypatch.setattr(settings, "posthog_host", "https://eu.i.posthog.com/")

    result = await erase_person_data("u-abc")

    assert result["erased"] is True and result["status"] == 202
    assert len(calls) == 1
    call = calls[0]
    # 尾斜杠拼接不能产出 https://host//api/...
    assert call["url"] == "https://eu.i.posthog.com/api/person/bulk_delete/"
    assert call["headers"] == {"Authorization": f"Bearer {PERSONAL_KEY}"}
    assert call["json"] == {
        "distinct_ids": ["u-abc"],
        "delete_events": True,
        "delete_recordings": True,
    }
    assert call["timeout"] == analytics_module.POSTHOG_ERASE_TIMEOUT_SECONDS


async def test_erase_uses_configured_host(monkeypatch):
    calls = _stub_httpx(monkeypatch)
    monkeypatch.setattr(settings, "posthog_personal_api_key", PERSONAL_KEY)
    monkeypatch.setattr(settings, "posthog_host", "https://us.i.posthog.com")

    await erase_person_data("u1")

    assert calls[0]["url"].startswith("https://us.i.posthog.com")


# ── 失败必须不抛（分析平台抖动不能把注销卡在半途）──


async def test_erase_non_2xx_returns_false_without_raising(monkeypatch):
    calls = _stub_httpx(monkeypatch, status_code=403)
    monkeypatch.setattr(settings, "posthog_personal_api_key", PERSONAL_KEY)

    result = await erase_person_data("u1")

    assert result == {"erased": False, "reason": "http_403"}
    assert len(calls) == 1  # 单次尝试：不在注销链路里做退避重试


async def test_erase_network_error_returns_false_without_raising(monkeypatch):
    _stub_httpx(monkeypatch, exc=RuntimeError("conn reset"))
    monkeypatch.setattr(settings, "posthog_personal_api_key", PERSONAL_KEY)

    result = await erase_person_data("u1")

    assert result == {"erased": False, "reason": "request_failed"}


# ── purge_user 串联 ──


async def test_purge_user_calls_posthog_erase(db, monkeypatch):
    seen: list[str] = []

    async def fake_erase(distinct_id: str) -> dict:
        seen.append(distinct_id)
        return {"erased": True, "status": 202}

    monkeypatch.setattr("backend.services.user_service.erase_person_data", fake_erase)
    user_id = await _make_user(db)
    counts = await user_service.purge_user(db, user_id)

    assert seen == [user_id]
    # counts 口径不变：全是各表 rowcount（幂等测试按 all(v == 0) 断言）
    assert counts["users"] == 1
    assert all(isinstance(v, int) for v in counts.values())


async def test_purge_user_survives_erase_failure(db, monkeypatch):
    async def failing_erase(distinct_id: str) -> dict:
        return {"erased": False, "reason": "http_500"}

    monkeypatch.setattr("backend.services.user_service.erase_person_data", failing_erase)
    user_id = await _make_user(db)
    counts = await user_service.purge_user(db, user_id)

    assert counts["users"] == 1  # PostHog 删除失败不影响本地物理删除结果

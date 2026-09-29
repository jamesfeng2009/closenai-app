"""
#38 分享归因链路「代码就绪」的三件：/share/resolve 限流 + 客户端 IP 口径 + AASA 参数化。

限流测的是**门闸本身**而不是业务：
1. 固定窗口数学：窗口内放行 limit 次、超了拒、窗口翻篇恢复，且拒绝的那次不续命。
2. IP 取值口径：CF-Connecting-IP 可信、X-Forwarded-For 要显式开关——默认采信调用方
   可控的头等于让绕过限流只需要改一个请求头。
3. 端点接线：只有 resolve 挂了这个依赖（其余 share 端点是登录态，不该被匿名 IP 计数误伤）。
"""
import json
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.config import settings
from backend.core.auth import get_current_user
from backend.core.errors import ErrorCode
from backend.core.middleware import register_exception_handlers
from backend.core.rate_limit import MAX_BUCKETS, FixedWindowLimiter, client_ip
from backend.database import get_db
from backend.routers import share as share_router

REPO_ROOT = Path(__file__).resolve().parents[2]
AASA_SCRIPT = REPO_ROOT / "site" / "scripts" / "build-aasa.mjs"


# ── 1. 窗口数学 ──

def test_window_admits_limit_then_blocks_until_it_rolls_over():
    limiter = FixedWindowLimiter()
    for _ in range(3):
        assert limiter.hit("k", 3, 60, now=1000.0) is True
    assert limiter.hit("k", 3, 60, now=1030.0) is False
    # 窗口按起点算：60s 后（>=1060）重置，与"最后一次访问"无关
    assert limiter.hit("k", 3, 60, now=1059.0) is False
    assert limiter.hit("k", 3, 60, now=1060.0) is True


def test_blocked_hit_does_not_extend_the_counter():
    """被拒的请求不累加：否则刚恢复的用户会被自己超限期间的重试再堵回去。"""
    limiter = FixedWindowLimiter()
    for _ in range(2):
        limiter.hit("k", 2, 60, now=0.0)
    for _ in range(10):
        assert limiter.hit("k", 2, 60, now=10.0) is False
    # 窗口未变，恢复后仍有 2 次额度（而不是被 10 次重试吃光）
    assert limiter.hit("k", 2, 60, now=60.0) is True
    assert limiter.hit("k", 2, 60, now=60.0) is True
    assert limiter.hit("k", 2, 60, now=60.0) is False


def test_window_starts_at_the_first_admitted_hit_not_the_last_one():
    """固定窗口按窗口起点算：每次放行都把起点往后推，等于持续流量的 IP 永久被封。"""
    limiter = FixedWindowLimiter()
    assert limiter.hit("k", 3, 60, now=0.0) is True
    assert limiter.hit("k", 3, 60, now=20.0) is True
    assert limiter.hit("k", 3, 60, now=40.0) is True
    assert limiter.hit("k", 3, 60, now=50.0) is False
    assert limiter.hit("k", 3, 60, now=60.0) is True


def test_keys_are_isolated_per_ip():
    limiter = FixedWindowLimiter()
    assert limiter.hit("a", 1, 60, now=0.0) is True
    assert limiter.hit("a", 1, 60, now=0.0) is False
    assert limiter.hit("b", 1, 60, now=0.0) is True


def test_bucket_count_is_capped_so_the_limiter_cannot_leak_memory():
    """海量源地址不能把计数器变成内存泄漏。"""
    limiter = FixedWindowLimiter()
    for i in range(MAX_BUCKETS + 500):
        limiter.hit(f"k{i}", 10, 1, now=float(i))  # 每次都比上次晚 → 旧桶全部过期
    assert len(limiter._buckets) <= MAX_BUCKETS


def test_bucket_cap_is_a_real_bound_not_just_a_symbol():
    """把上限写成天文数字 = 没有上限，而测试若只引用常量就照样绿。"""
    assert MAX_BUCKETS <= 100_000


def test_zero_limit_blocks_everything():
    """limit=0 是合法的"关掉这个端点"配置，不能变成放行。"""
    limiter = FixedWindowLimiter()
    assert limiter.hit("k", 0, 60, now=0.0) is False


# ── 2. 客户端 IP 口径 ──
# client_ip 只读 request.headers / request.client，用一个够用的假对象即可，
# 不引 Starlette 的 TestRequest（那会把测试绑到框架内部 API 上）。


class _Req:
    def __init__(self, headers=None, host=None):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.client = type("C", (), {"host": host})() if host else None


def test_cf_connecting_ip_wins_and_is_not_strippable():
    assert client_ip(_Req({"CF-Connecting-IP": "1.2.3.4", "X-Forwarded-For": "9.9.9.9"})) == "1.2.3.4"


def test_forwarded_for_is_only_trusted_when_explicitly_enabled():
    req = _Req({"X-Forwarded-For": "5.6.7.8, 10.0.0.1"}, host="127.0.0.1")
    # 默认：调用方可伪造的头不参与限流
    assert client_ip(req, trust_forwarded_for=False) == "127.0.0.1"
    # 显式声明"我在可信代理后面"才取首跳
    assert client_ip(req, trust_forwarded_for=True) == "5.6.7.8"


def test_default_argument_is_the_untrusting_side():
    """默认值本身要判：签名改了但没人显式传参 = 全站默认按可伪造的头限流。"""
    assert client_ip(_Req({"X-Forwarded-For": "5.6.7.8"}, host="127.0.0.1")) == "127.0.0.1"


def test_peer_host_is_the_last_resort_and_missing_client_does_not_crash():
    assert client_ip(_Req({}, host="127.0.0.1")) == "127.0.0.1"
    assert client_ip(_Req({})) == "unknown"


def test_whitespace_only_cf_header_falls_through():
    """空/全空白的 CF 头不能成为一个"所有人共享同一个空 key"的后门。"""
    assert client_ip(_Req({"CF-Connecting-IP": "  "}, host="127.0.0.1")) == "127.0.0.1"


# ── 3. 端点接线 ──


@pytest.fixture
def app(db, monkeypatch):
    share_router._resolve_limiter.reset()
    monkeypatch.setattr(settings, "share_resolve_limit", 3)
    monkeypatch.setattr(settings, "share_resolve_window_seconds", 60)

    app = FastAPI()
    app.include_router(share_router.router, prefix="/api/v1", tags=["share"])
    register_exception_handlers(app)

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: "u1"
    return app


async def _get(app, url, headers=None):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers or {}
    ) as client:
        return await client.get(url)


async def test_resolve_endpoint_enforces_per_ip_limit(app):
    ip = {"CF-Connecting-IP": "203.0.113.7"}
    for _ in range(3):
        resp = await _get(app, "/api/v1/share/resolve/ZZZZZZZZ", ip)
        assert resp.status_code == 200
    blocked = await _get(app, "/api/v1/share/resolve/ZZZZZZZZ", ip)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == ErrorCode.SHARE_RESOLVE_RATE_LIMITED


async def test_limit_is_per_ip_not_global(app):
    for i in range(3):
        await _get(app, "/api/v1/share/resolve/ZZZZZZZZ", {"CF-Connecting-IP": f"203.0.113.{i}"})
    # 换地址就该通：否则一个用户刷几下就把整站落地页关了
    other = await _get(app, "/api/v1/share/resolve/ZZZZZZZZ", {"CF-Connecting-IP": "198.51.100.9"})
    assert other.status_code == 200


async def test_rate_limit_precedes_the_lookup(app, make_user, db):
    """超限后连合法码也不给解析：限流要保护的就是那次 DB 查询。"""
    from backend.repositories import user_repository

    user = await make_user("u1")
    await user_repository.ensure_referral_code(db, user)
    await db.commit()
    code = user.referral_code
    ip = {"CF-Connecting-IP": "203.0.113.30"}
    for _ in range(3):
        await _get(app, f"/api/v1/share/resolve/{code}", ip)
    blocked = await _get(app, f"/api/v1/share/resolve/{code}", ip)
    assert blocked.status_code == 429


async def test_invalid_code_still_counts_against_the_limit(app):
    """先判长度非法就返回、不计数的写法会让字典攻击免费——非法码也要占额度。"""
    ip = {"CF-Connecting-IP": "203.0.113.31"}
    for _ in range(3):
        await _get(app, "/api/v1/share/resolve/bad-code!", ip)
    blocked = await _get(app, "/api/v1/share/resolve/bad-code!", ip)
    assert blocked.json()["code"] == ErrorCode.SHARE_RESOLVE_RATE_LIMITED


async def test_other_share_endpoints_are_not_ip_limited(app, make_user):
    """挂限流的只有匿名端点：登录端点本就有鉴权，误伤的是真实用户。"""
    await make_user("u1")
    ip = {"CF-Connecting-IP": "203.0.113.40"}
    for _ in range(6):
        assert (await _get(app, "/api/v1/share/link", ip)).status_code == 200


# ── 4. AASA 参数化脚本 ──


def _run_aasa(env, *argv):
    proc = subprocess.run(
        ["node", str(AASA_SCRIPT), *argv],
        capture_output=True,
        text=True,
        env={**{k: v for k, v in env.items()}, "PATH": __import__("os").environ["PATH"]},
    )
    return proc


def test_aasa_script_refuses_missing_or_malformed_team_id(tmp_path):
    out = str(tmp_path / "aasa.json")
    missing = _run_aasa({}, "--out", out)
    assert missing.returncode != 0 and "APPLE_TEAM_ID" in missing.stderr
    bad = _run_aasa({"APPLE_TEAM_ID": "short"}, "--out", out)
    assert bad.returncode != 0
    # 长度对但字符集不对：Apple 的 Team ID 是 10 位大写字母数字，
    # 小写串照样匹配的正则等于没校验（发布出去就是静默不唤起）
    lowercase = _run_aasa({"APPLE_TEAM_ID": "abcdefghij"}, "--out", out)
    assert lowercase.returncode != 0
    assert not Path(out).exists()  # 失败不许留半个文件


def test_aasa_script_renders_real_team_id_into_applinks(tmp_path):
    out = tmp_path / "aasa.json"
    proc = _run_aasa({"APPLE_TEAM_ID": "AB12CD34EF"}, "--out", str(out))
    assert proc.returncode == 0, proc.stderr

    parsed = json.loads(out.read_text())
    detail = parsed["applinks"]["details"][0]
    assert detail["appIDs"] == ["AB12CD34EF.com.closenai.app"]
    # 只接管 /r/*：写 "/" 会把品牌整站变成唤起 App，落地页反而打不开
    assert detail["components"][0]["/"] == "/r/*"


def test_aasa_check_mode_fails_on_the_committed_placeholder(tmp_path):
    """--check 是给部署流水线用的：占位没换成真 Team ID 就红，而不是上线后静默不唤起。"""
    out = tmp_path / "aasa.json"
    out.write_text(
        json.dumps({"applinks": {"details": [{"appIDs": ["TEAMID.com.closenai.app"], "components": []}]}})
    )
    proc = _run_aasa({}, "--check", "--out", str(out))
    assert proc.returncode != 0 and "占位" in proc.stderr

    _run_aasa({"APPLE_TEAM_ID": "AB12CD34EF"}, "--out", str(out))
    ok = _run_aasa({}, "--check", "--out", str(out))
    assert ok.returncode == 0


def test_aasa_bundle_id_override_is_validated(tmp_path):
    out = str(tmp_path / "aasa.json")
    proc = _run_aasa(
        {"APPLE_TEAM_ID": "AB12CD34EF", "APPLE_BUNDLE_ID": "not a bundle id"}, "--out", out
    )
    assert proc.returncode != 0 and "APPLE_BUNDLE_ID" in proc.stderr


def test_app_json_bundle_identifier_matches_the_default():
    """脚本默认 bundleId 与 App 侧真实 bundle 不一致时，AASA 会指向一个不存在的 App。"""
    app_json = json.loads((REPO_ROOT / "frontend" / "app.json").read_text())
    assert app_json["expo"]["ios"]["bundleIdentifier"] == "com.closenai.app"
    assert app_json["expo"]["ios"]["associatedDomains"] == ["applinks:closenai.app"]

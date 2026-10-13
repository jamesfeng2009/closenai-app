"""请求级事务边界回归（缺陷 2026-10-13 登记，同日修）。

要证的性质只有一条：**响应首字节交给 ASGI server 之前，本请求的写必须已提交**。

为什么原写法会破：FastAPI ≥0.106 起，`yield` 依赖的后置代码由
`AsyncExitStackMiddleware` 在 `await self.app(...)` **之后**关闭，也就是响应
已发出之后。`backend/database.py` 的 `get_db` 把 `await session.commit()` 写在
那里，于是 `POST /auth/register` 返回 200 时那一行可能还没提交。2026-10-13 实测
（本机两容器共享同一 PG）：register 于 +61.0ms 返回 200，另一进程立刻
`GET /auth/me` 在 +64.3ms 判 404/40401、+121.1ms 才 200；**同进程自读同样复现**
（+54.7ms 返回 200 → +64.8ms 判 404 → +90.6ms 才 200），与 Go 无关。

测法：不用 TestClient/httpx——它们把整个 ASGI 调用（含依赖 teardown）跑完才返回，
区分不了「提交在响应之前」和「提交在响应之后」。这里手工驱动一次裸 ASGI 调用，在
`send()` 收到 `http.response.start` 的那一刻用**另一条连接**读那一行：PG 的 MVCC
保证未提交的行对外不可见，所以那一刻能读到 = 提交确实发生在首字节之前。

前两条闸门无需 PG、常驻 CI；后两条缺 `TEST_DATABASE_URL` 时 skip，宿主无库也不会红。

    TEST_DATABASE_URL='postgresql+asyncpg://postgres:***@127.0.0.1:55432/closenai' \
        .venv/bin/pytest backend/tests/test_db_commit_boundary.py -q
"""
import inspect
import os
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import APIRouter, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.db_route import CommitBeforeResponseRoute

ROUTER_STEMS = sorted(
    p.stem
    for p in Path(__file__).resolve().parents[1].joinpath("routers").glob("*.py")
    if p.stem != "__init__"
)


# ── 闸门 1：每个 router 都必须挂上提交边界路由类 ──


def test_every_router_declares_commit_route_class():
    """新增 router 忘了挂 route_class 时，此条必须红。

    route_class 只在 APIRouter 构造期生效（Starlette 的 APIRoute.__init__ 当场把
    handler 烘进 self.app），事后改属性无效——所以漏挂不报错，只会静默退回
    「提交跑在响应之后」，正是本文件要防的回归。
    """
    checked = 0
    missing = []
    for stem in ROUTER_STEMS:
        module = import_module(f"backend.routers.{stem}")
        for name, obj in vars(module).items():
            if not isinstance(obj, APIRouter):
                continue
            checked += 1
            if obj.route_class is not CommitBeforeResponseRoute:
                missing.append(f"backend/routers/{stem}.py::{name}")
    assert checked >= len(ROUTER_STEMS), "router 模块里没取到 APIRouter 实例，闸门本身失效"
    assert not missing, f"以下 router 未声明 route_class=CommitBeforeResponseRoute: {missing}"


# ── 闸门 2：get_db 与路由层的交接面不许漂移 ──


def test_get_db_hands_session_to_route_layer():
    src = inspect.getsource(import_module("backend.database"))
    assert "request.state.db_session" in src, "get_db 未把 session 挂到 request.state"
    # teardown 的提交要留给 SSE（响应体流式期间才写库），但必须条件提交，
    # 否则每个请求在路由层提交之后再多一次空 BEGIN/COMMIT 往返
    assert "in_transaction()" in src, "get_db 收尾提交未做条件判断"


def _pg_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL 未设置（需 alembic upgrade head 后的隔离库）")
    return url


def _build_app() -> FastAPI:
    """只挂真实 auth 路由的最小 app；顺带验 route_class 能否穿过 include_router。"""
    from backend.routers import auth

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1")
    return app


async def _post_raw(app: FastAPI, path: str, payload: bytes, on_response_start) -> tuple[int, bytes]:
    """手工驱动一次 ASGI 调用，在首字节交给 server 的那一刻回调。"""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "state": {},
    }
    consumed = {"done": False}
    messages: list[dict] = []

    async def receive():
        if consumed["done"]:
            return {"type": "http.disconnect"}
        consumed["done"] = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message: dict):
        if message["type"] == "http.response.start" and not any(
            m["type"] == "http.response.start" for m in messages
        ):
            # 响应头即将交给 server：客户端马上就会拿到 200
            await on_response_start()
        messages.append(message)

    await app(scope, receive, send)

    start = next(m for m in messages if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start["status"], raw


async def _row_visible_on_other_connection(probe_engine, email: str) -> bool:
    async with probe_engine.connect() as conn:
        row = (
            await conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email})
        ).first()
        return row is not None


async def _purge(url: str, email: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM refresh_tokens WHERE user_id IN "
                    "(SELECT id FROM users WHERE email = :e)"
                ),
                {"e": email},
            )
            await conn.execute(text("DELETE FROM users WHERE email = :e"), {"e": email})
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_register_row_is_committed_before_response_first_byte(monkeypatch):
    """核心断言：注册响应首字节交出时那一行已提交——不是靠重试读到。"""
    from backend import database

    url = _pg_url()
    # 被测写路径的 session 工厂指向隔离库（get_db 在调用处取 SessionLocal，替换即生效）
    app_engine = create_async_engine(url)
    monkeypatch.setattr(
        database,
        "SessionLocal",
        async_sessionmaker(
            bind=app_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        ),
    )
    # 探针走另一条连接：与被请求同一连接时，读到的只是自己未提交的尾巴，证不了 MVCC 可见性
    probe_engine = create_async_engine(url)
    tag = uuid4().hex[:12]
    email = f"boundary_{tag}@example.test"
    seen: dict[str, bool] = {}

    async def on_first_byte():
        seen["visible"] = await _row_visible_on_other_connection(probe_engine, email)

    try:
        payload = (
            '{"email":"' + email + '","username":"bd_' + tag + '","password":"passw0rd"}'
        ).encode()
        status, raw = await _post_raw(
            _build_app(), "/api/v1/auth/register", payload, on_first_byte
        )
        assert status == 200, f"注册未返回 200：HTTP {status} {raw[:300]!r}"
        assert b'"code":200' in raw, raw[:300]
        assert seen.get("visible") is True, (
            "响应首字节交给 server 时那一行仍不可见 —— commit 又跑到响应之后了，"
            "『写后立刻读』会间歇性 404"
        )
    finally:
        for engine in (app_engine, probe_engine):
            await engine.dispose()
        await _purge(url, email)


@pytest.mark.asyncio
async def test_real_app_commits_before_bytes_leave_the_middleware_stack(monkeypatch):
    """同一断言打在 `backend.main.app` 上——那才是生产的路由装配。

    最小 app 只证明 route_class 能穿过一次 include_router；生产 app 有 17 次
    include_router，还叠着 CORSMiddleware + TraceIdMiddleware（后者是
    BaseHTTPMiddleware，会拷贝 scope，正是 `request.state` 交接最容易断的地方）。
    这里的 send() 收到的是整套中间件**出口**的消息，等价于交给 uvicorn 的首字节。
    """
    from backend import database
    from backend.main import app as real_app

    url = _pg_url()
    app_engine = create_async_engine(url)
    monkeypatch.setattr(
        database,
        "SessionLocal",
        async_sessionmaker(
            bind=app_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        ),
    )
    probe_engine = create_async_engine(url)
    tag = uuid4().hex[:12]
    email = f"boundary_{tag}@example.test"
    seen: dict[str, bool] = {}

    async def on_first_byte():
        seen["visible"] = await _row_visible_on_other_connection(probe_engine, email)

    try:
        payload = (
            '{"email":"' + email + '","username":"bx_' + tag + '","password":"passw0rd"}'
        ).encode()
        status, raw = await _post_raw(real_app, "/api/v1/auth/register", payload, on_first_byte)
        assert status == 200, f"注册未返回 200：HTTP {status} {raw[:300]!r}"
        assert seen.get("visible") is True, (
            "生产 app 的响应出口拿到首字节时那一行仍不可见 —— "
            "中间件栈或 17 次 include_router 里有一处把提交边界弄丢了"
        )
    finally:
        for engine in (app_engine, probe_engine):
            await engine.dispose()
        await _purge(url, email)

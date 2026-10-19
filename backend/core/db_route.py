"""请求级事务边界：把提交挪到响应字节离开进程之前。

缺陷（2026-10-13 登记同日修）：FastAPI ≥0.106 起 `yield` 依赖的后置代码由
`AsyncExitStackMiddleware` 在 `await self.app(...)` 之后关闭，即响应已发出之后。
`backend/database.py:get_db` 的 `await session.commit()` 落在那里，于是注册接口
返回 200 时那一行可能尚未提交，紧接着的读会间歇性 404（实测窗口约 3–60ms，
同进程与跨进程都复现，与被读方是不是 Go 无关）。

为什么不在 handler 里逐个显式 commit：项目约定是 repository 只 flush、提交由请求
边界完成（见 `backend/services/auth_service.py` 模块头），80+ 端点都依赖它。

为什么不用 ASGI 中间件拦 `http.response.start`：`APIRoute` 的 handler 返回 Response
对象时字节还没发，而 `Route.handle` 是在 handler **返回之后**才 `await response(...)`
的——所以官方扩展点 `route_class` 就能把提交放在发送前，不必手写 send 拦截去碰
StreamingResponse 的首包时机。

`get_db` 的后置提交仍然保留：SSE（chat）在响应体流式期间才写库，那次提交是它唯一的
提交点。两者靠 `in_transaction()` 去重。

── 提交之后的那一段（T2-06）──────────────────────────────────────────────

`post-commit hooks` 是给「**已经提交**这件事」留的出口，不是第二个事务。存在的理由
只有一条：`source.deleted` 必须排在翻转之后，否则 Go 收到的删除事件对应一行还没存在
（或已经不存在）的账。为什么不能用 FastAPI 的 `BackgroundTasks`：`APIRoute` 生成的
handler 是「`await response(...)` → 跑 background tasks」，两者都在 `await original(request)`
里面，也就是**早于**下面这一行的 `session.commit()`——用它就等于把事件投在提交前，
正是本文件要修的那个缺陷换了个方向再犯一次。

钩子失败**不**改变响应：数据已经提交，把 500 还给用户只会让他重试删除并拿到 404
（`MEMORY_NOT_FOUND`），而那比「事件没投出去」更难解释。代价如实登记：这一格只有
日志，没有本地补投表（归 T2-05 的重放面）。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute

log = logging.getLogger("backend.core.db_route")

STATE_ATTR = "post_commit_hooks"


def add_post_commit_hook(request: Request, hook: Callable[[], Awaitable[None]]) -> None:
    """登记一个「本请求提交成功之后」要跑的协程。"""
    hooks: list[Any] = getattr(request.state, STATE_ATTR, None)
    if hooks is None:
        hooks = []
        setattr(request.state, STATE_ATTR, hooks)
    hooks.append(hook)


async def run_post_commit_hooks(request: Request) -> None:
    """按登记顺序跑完并清空；每条各自吞异常，一条坏钩子不许连累另一条与响应。"""
    hooks: list[Any] = getattr(request.state, STATE_ATTR, None)
    if not hooks:
        return
    setattr(request.state, STATE_ATTR, [])
    for hook in hooks:
        try:
            await hook()
        except Exception as exc:  # noqa: BLE001 - 见模块头：提交之后的失败不许改响应
            log.warning(
                "event=post_commit_hook_failed hook=%s error=%s",
                getattr(hook, "__qualname__", type(hook).__name__),
                exc,
            )


class CommitBeforeResponseRoute(APIRoute):
    """路由层提交：handler 产出 Response 后、该 Response 发送前提交本请求的事务。"""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            response = await original(request)
            session = getattr(request.state, "db_session", None)
            if session is not None and session.in_transaction():
                await session.commit()
            await run_post_commit_hooks(request)
            return response

        return handler

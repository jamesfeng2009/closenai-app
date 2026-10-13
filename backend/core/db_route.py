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
"""
from fastapi import Request, Response
from fastapi.routing import APIRoute


class CommitBeforeResponseRoute(APIRoute):
    """路由层提交：handler 产出 Response 后、该 Response 发送前提交本请求的事务。"""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            response = await original(request)
            session = getattr(request.state, "db_session", None)
            if session is not None and session.in_transaction():
                await session.commit()
            return response

        return handler

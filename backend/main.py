"""
ClosenAI FastAPI 应用入口。

个人成长追踪工具后端 API。
启动命令：
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import chat, memories, mood, growth, personality, user, discovery, subscription, cron, reflection, share
from backend.config import settings
from backend.core.logging import setup_logging
from backend.core.middleware import TraceIdMiddleware, register_exception_handlers

# 初始化日志系统（统一格式：trace_id / user_id 透传）
setup_logging("DEBUG" if settings.debug else "INFO")

app = FastAPI(
    title=settings.app_name,
    description=settings.app_tagline,
    version="1.0.0",
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id"],
)

# TraceId 中间件（最外层，保证所有日志都有 trace_id）
app.add_middleware(TraceIdMiddleware)

# 全局异常处理（统一响应格式）
register_exception_handlers(app)

# 注册路由
app.include_router(chat.router, prefix="/api/v1", tags=["chat"])
app.include_router(memories.router, prefix="/api/v1", tags=["memories"])
app.include_router(mood.router, prefix="/api/v1", tags=["mood"])
app.include_router(growth.router, prefix="/api/v1", tags=["growth"])
app.include_router(personality.router, prefix="/api/v1", tags=["personality"])
app.include_router(user.router, prefix="/api/v1", tags=["user"])
app.include_router(discovery.router, prefix="/api/v1", tags=["discovery"])
app.include_router(subscription.router, prefix="/api/v1", tags=["subscription"])
app.include_router(reflection.router, prefix="/api/v1", tags=["reflections"])
app.include_router(cron.router, prefix="/api/v1", tags=["cron"])
app.include_router(share.router, prefix="/api/v1", tags=["share"])


@app.get("/health")
async def health_check():
    """健康检查端点（Vercel Cron 每 5 分钟保热）。"""
    from backend.core.response import success

    return success({
        "status": "ok",
        "app": settings.app_name,
        "version": "1.0.0",
    })
"""
ClosenAI FastAPI 应用入口。

个人成长追踪工具后端 API。
启动命令：
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import auth, chat, memories, mood, growth, personality, user, discovery, subscription, cron, reflection, share, analytics
from backend.config import settings
from backend.core.logging import get_logger, setup_logging
from backend.core.middleware import TraceIdMiddleware, register_exception_handlers
from backend.services.cron_service import cron_service

# 初始化日志系统（统一格式：trace_id / user_id 透传）
setup_logging("DEBUG" if settings.debug else "INFO")
logger = get_logger("closenai.main")


def _validate_startup_config() -> None:
    """启动配置校验。

    production 环境：JWT_SECRET / CRON_SECRET 缺失或 DATABASE_URL 仍为 sqlite 默认
    → fail-fast（启动即报错，避免带病上线后认证/定时任务静默失效）。
    development 环境仅告警，不阻断开发。
    推荐配置（Redis / QStash / Resend / Apple）缺失任何环境均仅告警（功能降级可用）。
    """
    prod = settings.environment.lower() in ("production", "prod")
    missing_required: list[str] = []
    if not settings.jwt_secret:
        missing_required.append("JWT_SECRET")
    if not settings.cron_secret:
        missing_required.append("CRON_SECRET")
    if settings.database_url.startswith("sqlite"):
        missing_required.append("DATABASE_URL(生产禁止 sqlite 默认库)")

    recommended = {
        "REDIS_URL/REDIS_TOKEN": bool(settings.redis_url and settings.redis_token),
        "QSTASH_TOKEN": bool(settings.qstash_token),
        "QSTASH_CURRENT_SIGNING_KEY": bool(settings.qstash_current_signing_key),
        "RESEND_API_KEY": bool(settings.resend_api_key),
        "APPLE_CLIENT_ID": bool(settings.apple_client_id),
    }
    for name, configured in recommended.items():
        if not configured:
            logger.warning("启动配置校验：%s 未配置，对应功能降级/不可用", name)

    if missing_required:
        msg = f"启动配置校验失败，缺失必需配置: {', '.join(missing_required)}"
        if prod:
            raise RuntimeError(msg)
        logger.warning("%s（development 仅告警）", msg)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _validate_startup_config()
    try:
        schedule_id = await cron_service.register_follow_up_schedule()
        if schedule_id:
            logger.info("lifespan 已注册 follow-up 调度 schedule=%s", schedule_id)
    except Exception as exc:  # noqa: BLE001 - 调度注册失败不影响应用启动
        logger.warning("lifespan follow-up 调度注册失败（可后续手动注册）: %s", exc)
    yield


app = FastAPI(
    title=settings.app_name,
    description=settings.app_tagline,
    version="1.0.0",
    lifespan=lifespan,
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
app.include_router(auth.router, prefix="/api/v1", tags=["auth"])
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
app.include_router(analytics.router, prefix="/api/v1", tags=["analytics"])


@app.get("/health")
async def health_check():
    """健康检查端点（Vercel Cron 每 5 分钟保热）。"""
    from backend.core.response import success

    return success({
        "status": "ok",
        "app": settings.app_name,
        "version": "1.0.0",
    })
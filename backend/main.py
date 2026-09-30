"""
ClosenAI FastAPI 应用入口。

个人成长追踪工具后端 API。
启动命令：
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
"""
import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import auth, onboarding, journey, chat, memories, mood, growth, personality, user, discovery, subscription, cron, reflection, share, analytics, bedtime, curriculum
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


# 进程内 purge 兜底：扫描间隔 10 分钟，stale_hours=1 与外部 cron 口径一致
PURGE_SWEEP_INTERVAL_SECONDS = 600
PURGE_STALE_HOURS = 1


async def _purge_sweep_once() -> int:
    """跑一轮物理删除兜底扫描，返回本次删除的用户数。

    独立成函数：lifespan 循环只做调度，扫描/提交口径与
    /cron/purge-deleted-users 一致（同一 service 方法，逐用户 SAVEPOINT）。
    """
    from backend.database import SessionLocal
    from backend.services.user_service import user_service

    async with SessionLocal() as session:
        purged = await user_service.purge_deleted_users(
            session, stale_hours=PURGE_STALE_HOURS
        )
        await session.commit()
    return len(purged)


async def _purge_sweeper() -> None:
    """进程内注销数据物理删除兜底循环。

    注销链路是「软删除即时失效 + 异步 purge」：QStash 投递失败 / 未配置 /
    Vercel Cron 未生效时，软删记录会永久残留（个人数据留存 = 合规风险）。
    本循环每 10 分钟扫一次 stale 软删用户，与 /cron/purge-deleted-users 复用
    同一实现（已删表 rowcount=0 天然幂等），多 worker 并发扫描无副作用，
    外部 cron 保留作第二道。

    单轮异常只告警不退出（循环必须活着）；CancelledError 必须透传以便停机。
    """
    while True:
        try:
            count = await _purge_sweep_once()
            if count:
                logger.info("进程内 purge 兜底：物理删除 %d 个用户", count)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 兜底循环不能因单轮失败终止
            logger.warning("进程内 purge 兜底失败（下一轮重试）: %s", exc)
        await asyncio.sleep(PURGE_SWEEP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _validate_startup_config()
    purge_task: asyncio.Task | None = None
    if settings.purge_loop_enabled:
        purge_task = asyncio.create_task(_purge_sweeper(), name="purge-sweeper")
    try:
        schedule_id = await cron_service.register_follow_up_schedule()
        if schedule_id:
            logger.info("lifespan 已注册 follow-up 调度 schedule=%s", schedule_id)
    except Exception as exc:  # noqa: BLE001 - 调度注册失败不影响应用启动
        logger.warning("lifespan follow-up 调度注册失败（可后续手动注册）: %s", exc)
    try:
        yield
    finally:
        if purge_task:
            purge_task.cancel()
            with suppress(asyncio.CancelledError):
                await purge_task
        logger.info("lifespan 已停止进程内 purge 兜底循环")


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
app.include_router(onboarding.router, prefix="/api/v1", tags=["onboarding"])
app.include_router(journey.router, prefix="/api/v1", tags=["journey"])
app.include_router(chat.router, prefix="/api/v1", tags=["chat"])
app.include_router(memories.router, prefix="/api/v1", tags=["memories"])
app.include_router(mood.router, prefix="/api/v1", tags=["mood"])
app.include_router(growth.router, prefix="/api/v1", tags=["growth"])
app.include_router(bedtime.router, prefix="/api/v1", tags=["bedtime"])
app.include_router(curriculum.router, prefix="/api/v1", tags=["curriculum"])
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
"""
定时任务路由 (P0-F01/P0-F07)。

通过 Vercel Cron 每小时 GET 触发，QStash 分块分发。

端点：
    GET /api/v1/cron/daily-message   — 主动消息生成（按时区分组）
    GET /api/v1/cron/daily-insight   — 每日洞察生成
    GET /api/v1/cron/weekly-insight  — 周洞察生成
    GET /api/v1/cron/health          — 保热端点
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.errors import AppException, ErrorCode
from backend.core.response import success
from backend.database import get_db
from backend.repositories import user_repository
from backend.services.cron_service import CronService, cron_service
from backend.services.growth_service import growth_service

router = APIRouter()


def get_cron_service() -> CronService:
    return cron_service


def verify_cron_secret(request: Request):
    """验证 Cron Job 调用密钥。"""
    secret = request.headers.get("Authorization", "").replace("Bearer ", "")
    if secret != settings.cron_secret and settings.cron_secret:
        raise AppException(ErrorCode.UNAUTHORIZED, "Unauthorized")
    return True


@router.get("/cron/daily-message")
async def daily_message(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: CronService = Depends(get_cron_service),
):
    """
    每日主动消息生成（Vercel Cron 触发）。

    1. 按用户时区分组（UTC+8, UTC+7, UTC+0）
    2. 对每组用户，通过 QStash 分块分发（每块 50 用户）
    3. 每个分块独立调用 LLM 生成主动消息 + 推送通知
    """
    verify_cron_secret(request)
    timezone_group = request.query_params.get("tz", "UTC+8")
    result = await service.dispatch_daily_messages(db, timezone_group)
    return success(result)


@router.get("/cron/daily-insight")
async def daily_insight(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """每日洞察生成（为所有活跃用户生成当日洞察）。"""
    verify_cron_secret(request)
    users = await user_repository.list_all(db)
    processed = 0
    for user in users:
        # 合规：未同意 AI 数据共享的用户跳过（5.1.2(i)）
        if not user.ai_consent:
            continue
        try:
            await growth_service.generate_daily_insight(db, user.id, user.language or "en")
            processed += 1
        except Exception:  # noqa: BLE001
            continue
    return success({"users_processed": processed, "status": "completed"})


@router.get("/cron/weekly-insight")
async def weekly_insight(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: CronService = Depends(get_cron_service),
):
    """周洞察生成（每周日凌晨触发）。"""
    verify_cron_secret(request)
    result = await service.generate_weekly_insights(db)
    return success(result)


@router.get("/cron/health")
async def health():
    """保热端点（Vercel Cron 每 5 分钟调用）。"""
    return success({"status": "ok", "warmed": True})
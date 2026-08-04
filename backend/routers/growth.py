"""
成长仪表盘路由 (P0-F07)。

端点：
    GET /api/v1/growth/dashboard  — 成长仪表盘
    GET /api/v1/growth/insights   — 本周洞察
    GET /api/v1/growth/timeline   — 365 天热力图
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.response import success
from backend.database import get_db
from backend.services.growth_service import GrowthService, growth_service

router = APIRouter()


def get_growth_service() -> GrowthService:
    return growth_service


@router.get("/growth/dashboard")
async def dashboard(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: GrowthService = Depends(get_growth_service),
):
    """
    成长仪表盘。

    返回：日期、Streak、等级、今日心情、6 维度指标、14 天热力图、
    本周洞察、昨日记录延续。
    新用户（无数据）返回空状态。
    """
    return success(await service.get_dashboard(db, user_id))


@router.get("/growth/insights")
async def insights(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: GrowthService = Depends(get_growth_service),
):
    """本周洞察（来自 P0-F07 成长记录）。"""
    return success(await service.get_weekly_insight(db, user_id))


@router.get("/growth/timeline")
async def timeline(
    days: int = Query(365, ge=7, le=365, description="天数"),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: GrowthService = Depends(get_growth_service),
):
    """365 天成长热力图数据。"""
    return success(await service.get_timeline(db, user_id, days))


@router.get("/growth/reports/{period}")
async def report(
    period: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: GrowthService = Depends(get_growth_service),
):
    """周期成长报告（P1-F03）：月 / 季 / 年报告，含情绪趋势、模式发现、洞察等级。"""
    return success(await service.generate_report(db, user_id, period))


@router.get("/growth/patterns")
async def patterns(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: GrowthService = Depends(get_growth_service),
):
    """跨维度模式发现（P1-F03）：AI 基于真实数据发现关联模式。"""
    content = await service.generate_pattern_discovery(db, user_id)
    return success({"content": content})
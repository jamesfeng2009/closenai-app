"""
成长仪表盘路由 (P0-F07)。

端点：
    GET /api/v1/growth/dashboard  — 成长仪表盘
    GET /api/v1/growth/insights   — 本周洞察
    GET /api/v1/growth/timeline   — 365 天热力图
"""
from fastapi import APIRouter, Depends, Query

from backend.models.schemas import (
    GrowthDashboardResponse, WeeklyInsightResponse,
)
from backend.services.growth_service import GrowthService, growth_service

router = APIRouter()


def get_growth_service() -> GrowthService:
    return growth_service


@router.get("/growth/dashboard", response_model=GrowthDashboardResponse)
async def dashboard(
    service: GrowthService = Depends(get_growth_service),
):
    """
    成长仪表盘。

    返回：日期、Streak、等级、今日心情、6 维度指标、14 天热力图、
    本周洞察、昨日记录延续。
    新用户（无数据）返回空状态。
    """
    user_id = "demo-user"
    return await service.get_dashboard(user_id)


@router.get("/growth/insights", response_model=WeeklyInsightResponse)
async def insights(
    service: GrowthService = Depends(get_growth_service),
):
    """本周洞察（来自 P0-F07 成长记录）。"""
    user_id = "demo-user"
    return await service.get_weekly_insight(user_id)


@router.get("/growth/timeline")
async def timeline(
    days: int = Query(365, ge=7, le=365, description="天数"),
    service: GrowthService = Depends(get_growth_service),
):
    """365 天成长热力图数据。"""
    user_id = "demo-user"
    return await service.get_timeline(user_id, days)
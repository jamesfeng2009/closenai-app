"""
Analytics 路由（PostHog 中继）。

端点：
    POST /api/v1/analytics/batch  — 接收前端本地 analytics_events 队列批量上报
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.db_route import CommitBeforeResponseRoute
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import AnalyticsBatchRequest
from backend.services.analytics_service import AnalyticsService, analytics_service

router = APIRouter(route_class=CommitBeforeResponseRoute)


def get_analytics_service() -> AnalyticsService:
    return analytics_service


@router.post("/analytics/batch")
async def analytics_batch(
    request: AnalyticsBatchRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
):
    """批量上报前端本地分析事件队列。

    后端校验用户 analytics_consent（未同意则丢弃），匿名化后按真实
    occurred_at 中继到 PostHog。返回 {relayed, dropped}，前端据此将
    已中继事件标记 synced=1 并清理。
    """
    result = await service.relay_batch(db, user_id, request.events)
    return success(result)

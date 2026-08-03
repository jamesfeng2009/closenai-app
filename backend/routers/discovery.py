"""
每日发现路由 (P0-F10)。

端点：
    GET /api/v1/discovery/daily  — 当日发现
"""
from fastapi import APIRouter, Depends

from backend.models.schemas import DailyDiscoveryResponse
from backend.services.discovery_service import DiscoveryService, discovery_service

router = APIRouter()


def get_discovery_service() -> DiscoveryService:
    return discovery_service


@router.get("/discovery/daily", response_model=DailyDiscoveryResponse)
async def daily(
    service: DiscoveryService = Depends(get_discovery_service),
):
    """
    每日发现。

    基于 Personal Dataset 对比生成当日数据洞察。
    内容作为 AI 主动消息（P0-F01）四层公式的第②层发出。
    数据对比无显著变化时返回 has_discovery=false。
    """
    user_id = "demo-user"
    return await service.get_daily_discovery(user_id)
"""
每日发现路由 (P0-F10)。

端点：
    GET /api/v1/discovery/daily  — 当日发现
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.db_route import CommitBeforeResponseRoute
from backend.core.language import get_request_language
from backend.core.response import success
from backend.database import get_db
from backend.services.discovery_service import DiscoveryService, discovery_service

router = APIRouter(route_class=CommitBeforeResponseRoute)


def get_discovery_service() -> DiscoveryService:
    return discovery_service


# deprecated=True：N8 每域四步的第④步，同 backend/routers/analytics.py 的注释口径
# （Go 侧实现在 go-core/internal/httpapi/discovery_daily.go）。
@router.get("/discovery/daily", deprecated=True)
async def daily(
    db: AsyncSession = Depends(get_db),
    language: str = Depends(get_request_language),
    user_id: str = Depends(get_current_user),
    service: DiscoveryService = Depends(get_discovery_service),
):
    """
    每日发现。

    基于 Personal Dataset 对比生成当日数据洞察。
    内容作为 AI 主动消息（P0-F01）四层公式的第②层发出。
    数据对比无显著变化时返回 has_discovery=false。
    """
    return success(await service.get_daily_discovery(db, user_id, language))
"""
第一周剧本路由（P1-B02 · 架构方案 §二 M2 / §四 API 增量）。

端点：
    GET  /api/v1/journey/events              — 全剧本状态 + 未读数（红点与 Week1ReportScreen 数据源）
    POST /api/v1/journey/events/acknowledge  — 标记某条已读（红点消费）

正文由 cron 侧（/api/v1/cron/journey）写入对话流，本路由只读状态、不生成内容。
架构方案 §四 只列了 GET；红点必须能被消费，acknowledge 属实现必要增量。
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.db_route import CommitBeforeResponseRoute
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import JourneyAckRequest
from backend.services.journey_service import journey_service

router = APIRouter(route_class=CommitBeforeResponseRoute)


@router.get("/journey/events")
async def list_journey_events(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """某用户 D0–D7 剧本状态（含每条事件的 payload 与 memory_refs 引用依据）。"""
    return success(await journey_service.list_events(db, user_id))


@router.post("/journey/events/acknowledge")
async def acknowledge_journey_event(
    request: JourneyAckRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """用户已读某条剧本事件（清除该条红点）。"""
    await journey_service.acknowledge(db, user_id, request.event_id)
    return success({"acknowledged": request.event_id})

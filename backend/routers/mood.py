"""
情绪签到路由 (P0-F03)。

端点：
    POST /api/v1/mood/checkin  — 情绪签到 + AI 解读
    GET  /api/v1/mood/history  — 签到历史
    GET  /api/v1/mood/trend    — 情绪趋势
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.db_route import CommitBeforeResponseRoute
from backend.core.language import get_request_language
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import MoodCheckinRequest
from backend.services.mood_service import MoodService, mood_service

router = APIRouter(route_class=CommitBeforeResponseRoute)


def get_mood_service() -> MoodService:
    return mood_service


@router.post("/mood/checkin")
async def checkin(
    request: MoodCheckinRequest,
    db: AsyncSession = Depends(get_db),
    language: str = Depends(get_request_language),
    user_id: str = Depends(get_current_user),
    service: MoodService = Depends(get_mood_service),
):
    """
    情绪签到。

    记录当日情绪，生成 AI 解读，更新 Streak。
    签到每日可多次：同一本地日重复签到走更新（不新增记录）；
    AI 解读计入统一免费配额，超额返回 QUOTA_EXCEEDED。
    连续 3 天同情绪 / 连续 2 天负面时响应附带 deep_interpretation。
    """
    return success(await service.checkin(db, user_id, request.emoji, request.note, language))


@router.get("/mood/history")
async def history(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: MoodService = Depends(get_mood_service),
):
    """查询签到历史。"""
    return success(await service.get_history(db, user_id))


@router.get("/mood/trend")
async def trend(
    days: int = Query(14, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    language: str = Depends(get_request_language),
    user_id: str = Depends(get_current_user),
    service: MoodService = Depends(get_mood_service),
):
    """查询情绪趋势。"""
    return success(await service.get_trend(db, user_id, days, language))
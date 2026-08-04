"""
情绪签到路由 (P0-F03)。

端点：
    POST /api/v1/mood/checkin  — 情绪签到 + AI 解读
    GET  /api/v1/mood/history  — 签到历史
    GET  /api/v1/mood/trend    — 情绪趋势
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.language import get_request_language
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import MoodCheckinRequest
from backend.services.mood_service import MoodService, mood_service

router = APIRouter()


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
    今日已签到则返回 ALREADY_CHECKED_IN。
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
    days: int = 14,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: MoodService = Depends(get_mood_service),
):
    """查询情绪趋势。"""
    return success(await service.get_trend(db, user_id, days))
"""
情绪签到路由 (P0-F03)。

端点：
    POST /api/v1/mood/checkin  — 情绪签到 + AI 解读
    GET  /api/v1/mood/history  — 签到历史
    GET  /api/v1/mood/trend    — 情绪趋势
"""
from fastapi import APIRouter, Depends, HTTPException

from backend.models.schemas import (
    MoodCheckinRequest, MoodCheckinResponse, MoodHistoryResponse, MoodTrendResponse,
)
from backend.services.mood_service import MoodService, mood_service

router = APIRouter()


def get_mood_service() -> MoodService:
    return mood_service


@router.post("/mood/checkin", response_model=MoodCheckinResponse)
async def checkin(
    request: MoodCheckinRequest,
    service: MoodService = Depends(get_mood_service),
):
    """
    情绪签到。

    记录当日情绪，生成 AI 解读，更新 Streak。
    免费用户：Rule Engine 基础版解读。
    Pro/Pro+ 用户：LLM 深度解读。
    """
    # TODO: 从 JWT 获取 user_id
    user_id = "demo-user"
    result = await service.checkin(user_id, request.emoji, request.note)
    if not result:
        raise HTTPException(status_code=400, detail="签到失败")
    return result


@router.get("/mood/history", response_model=MoodHistoryResponse)
async def history(
    days: int = 30,
    service: MoodService = Depends(get_mood_service),
):
    """查询签到历史。"""
    user_id = "demo-user"
    checkins = await service.get_history(user_id, days)
    return MoodHistoryResponse(checkins=checkins)


@router.get("/mood/trend", response_model=MoodTrendResponse)
async def trend(
    weeks: int = 4,
    service: MoodService = Depends(get_mood_service),
):
    """查询情绪趋势。"""
    user_id = "demo-user"
    return await service.get_trend(user_id, weeks)
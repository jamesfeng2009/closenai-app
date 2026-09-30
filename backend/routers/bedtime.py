"""
睡前复盘路由（PRD §5.16 S1 / tasks C1）。

端点：
    GET  /api/v1/bedtime/today — 今日三步状态 + 摘要参考 + 昨日归档
    POST /api/v1/bedtime/step  — 逐步提交复盘（step 1|2|3；step1 危机转介挂载）

需 AI 数据共享同意（step2/step3 为 AI 生成内容，合规 5.1.2(i)）。
"""
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import BedtimeStepRequest
from backend.repositories import user_repository
from backend.services.bedtime_service import BedtimeService, bedtime_service
from backend.services.user_service import user_service

router = APIRouter()


def get_bedtime_service() -> BedtimeService:
    return bedtime_service


@router.get("/bedtime/today")
async def bedtime_today(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: BedtimeService = Depends(get_bedtime_service),
):
    """今日复盘状态：completed / 三步内容 / 当日摘要参考（零 LLM）/ 昨日归档。"""
    await user_service.require_ai_consent(db, user_id)
    return success(await service.get_today(db, user_id))


@router.post("/bedtime/step")
async def bedtime_step(
    request: BedtimeStepRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: BedtimeService = Depends(get_bedtime_service),
):
    """逐步提交复盘（step 1|2|3）。step1 命中危机触发词时返回转介载荷（不落复盘）。"""
    await user_service.require_ai_consent(db, user_id)
    user = await user_repository.require_user(db, user_id)
    return success(await service.submit_step(db, user, request.step, request.text))

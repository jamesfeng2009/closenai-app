"""
Growth Curriculum 路由（PRD §5.17 / tasks C2）。

端点：
    GET  /api/v1/curriculum/week          — 当周 7 张主题卡（revealed + 未读）
    POST /api/v1/curriculum/{id}/read     — 点击卡片标记已读（payload.read_at）

生成在 cron（周日 21:00 本地窗口），本路由只读 + 已读标记；均需 AI 数据共享同意。
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.response import success
from backend.database import get_db
from backend.repositories import user_repository
from backend.services.curriculum_service import CurriculumService, curriculum_service
from backend.services.user_service import user_service

router = APIRouter()


def get_curriculum_service() -> CurriculumService:
    return curriculum_service


@router.get("/curriculum/week")
async def curriculum_week(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: CurriculumService = Depends(get_curriculum_service),
):
    """当周主题旅程：7 张卡（未生成的天为空位）、揭晓状态、未读数。"""
    await user_service.require_ai_consent(db, user_id)
    user = await user_repository.require_user(db, user_id)
    return success(await service.get_week(db, user))


@router.post("/curriculum/{event_id}/read")
async def curriculum_read(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: CurriculumService = Depends(get_curriculum_service),
):
    """点击主题卡 → 已读标记（越权 40403；幂等）。"""
    await user_service.require_ai_consent(db, user_id)
    user = await user_repository.require_user(db, user_id)
    return success(await service.mark_read(db, user, event_id))

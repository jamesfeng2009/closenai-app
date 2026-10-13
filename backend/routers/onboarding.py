"""
性格初读卡路由（P1-B01 · 架构方案 §二 M1 / §四 API 增量）。

端点：
    POST /api/v1/onboarding/first-read — 生成并落库（幂等 upsert）
    GET  /api/v1/onboarding/first-read  — 重进 App 恢复（按生成时语言复现）

静态直出、零 LLM：生成过程只是模板槽位填充，因此本路由不检查 ai_consent。
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.db_route import CommitBeforeResponseRoute
from backend.core.errors import AppException, ErrorCode
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import FirstReadRequest
from backend.services.first_read_service import first_read_service

router = APIRouter(route_class=CommitBeforeResponseRoute)


@router.post("/onboarding/first-read")
async def create_first_read(
    request: FirstReadRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """生成初读卡并落库（每用户一行，重复提交以最后一次为准）。"""
    card = await first_read_service.submit(
        db, user_id, request.persona_type, [a.model_dump() for a in request.answers],
        request.language,
    )
    return success(card)


@router.get("/onboarding/first-read")
async def get_first_read(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """恢复已生成的初读卡；未生成 → 40402（前端据此回到引导快问步）。"""
    card = await first_read_service.restore(db, user_id)
    if card is None:
        raise AppException(
            ErrorCode.RESOURCE_NOT_FOUND, "尚未生成初读卡 / First-read card not generated yet"
        )
    return success(card)

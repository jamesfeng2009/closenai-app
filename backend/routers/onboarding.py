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


# deprecated=True 是 N8 每域四步的第④步，同 backend/routers/journey.py:24-29 的口径：
# 这两条路的对外形状已由 Go Core 接管（go-core/internal/httpapi/onboarding_submit.go 与
# onboarding_read.go），但**路由本体留着**——灰度回滚＝把 CLOSEN_CUTOVER_ONBOARDING 改回 0，
# 前提是 Python 还答得出。
# 标废弃只描述**对外契约**的归属：初读卡的**内容**还有两个不走 HTTP 的消费者——
# share_service.py:245 直接 await first_read_service.restore() 取卡做分享文案，
# journey_service.py:574 读同一列 profile.first_read 做洞察证据。
# 它们各自归最后一批与批 3，本文件与本域的服务本体因此不能删。
@router.post("/onboarding/first-read", deprecated=True)
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


@router.get("/onboarding/first-read", deprecated=True)
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

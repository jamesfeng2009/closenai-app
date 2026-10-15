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


# 下面两条 GET 的 deprecated=True 是 N8 每域四步的第④步，口径同
# backend/routers/onboarding.py:24-31：对外形状已由 Go Core 接管
# （go-core/internal/httpapi/mood_history.go 与 mood_trend.go），但**路由本体留着**——
# 灰度回滚＝把 CLOSEN_CUTOVER_MOOD 改回 0，前提是 Python 还答得出。
# 上面那条 POST 与 GET /api/v1/reflections **都不标**：前者调 LLM、计配额、写库，属最后一批；
# 后者的 Go 实现与逐字节回放都已就绪，但 Ingress 的 path 匹配**不看方法**，
# `/api/v1/reflections` 这一个 path 上挂着 GET 与 POST 两个操作，path 还没上（决策点 D-10），
# 此刻标废弃＝告诉客户端一条其实仍由 Python 全量回答的线。
# 标废弃只描述**对外契约**的归属：`mood_repository.get_recent`/`count_by_day` 还有大量
# 不走 HTTP 的消费者（growth_service.py:254/:879、personality_service.py:245、
# cron_service.py:265、discovery_service.py:47-48），本域的服务与仓储因此不能删。
@router.get("/mood/history", deprecated=True)
async def history(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: MoodService = Depends(get_mood_service),
):
    """查询签到历史。"""
    return success(await service.get_history(db, user_id))


@router.get("/mood/trend", deprecated=True)
async def trend(
    days: int = Query(14, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    language: str = Depends(get_request_language),
    user_id: str = Depends(get_current_user),
    service: MoodService = Depends(get_mood_service),
):
    """查询情绪趋势。"""
    return success(await service.get_trend(db, user_id, days, language))
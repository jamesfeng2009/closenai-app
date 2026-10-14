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


# deprecated=True 是 N8 每域四步的第④步，同 backend/routers/curriculum.py:28-30 的口径：
# 这两条路的对外形状已由 Go Core 接管（go-core/internal/httpapi/journey_events.go 与
# journey_ack.go），但**路由本体留着**——灰度回滚＝把 CLOSEN_CUTOVER_JOURNEY 改回 0，
# 前提是 Python 还答得出。
# 标废弃只描述**对外契约**的归属：本域还有 cron 侧的写入面（/api/v1/cron/journey，
# 见本文件 docstring 第 8 行），那一条不在批 3 的切流范围里，仍由 Python 生成正文。
@router.get("/journey/events", deprecated=True)
async def list_journey_events(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """某用户 D0–D7 剧本状态（含每条事件的 payload 与 memory_refs 引用依据）。"""
    return success(await journey_service.list_events(db, user_id))


@router.post("/journey/events/acknowledge", deprecated=True)
async def acknowledge_journey_event(
    request: JourneyAckRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """用户已读某条剧本事件（清除该条红点）。"""
    await journey_service.acknowledge(db, user_id, request.event_id)
    return success({"acknowledged": request.event_id})

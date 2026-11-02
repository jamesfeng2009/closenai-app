"""
情绪签到路由 (P0-F03)。

端点：
    POST /api/v1/mood/checkin   — 情绪签到 + AI 解读
    GET  /api/v1/mood/history   — 签到历史
    GET  /api/v1/mood/trend     — 情绪趋势
    POST /api/v1/mood/analysis  — 三层次情绪分析（KG-28，受开关控制）
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.auth import get_current_user
from backend.core.db_route import CommitBeforeResponseRoute
from backend.core.errors import AppException, ErrorCode
from backend.core.language import get_request_language
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import MoodAnalysisRequest, MoodCheckinRequest
from backend.services.mood_analysis_service import (
    MoodAnalysisService,
    mood_analysis_service,
)
from backend.services.mood_service import MoodService, mood_service
from backend.services.user_service import user_service

router = APIRouter(route_class=CommitBeforeResponseRoute)


def get_mood_service() -> MoodService:
    return mood_service


def get_mood_analysis_service() -> MoodAnalysisService:
    return mood_analysis_service


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


# KG-28 三层次分析（方案 §6.2.1）。**这条不标 deprecated**，也不在 N8 的切流面里：
# mood 域切给 Go 的是两条 Exact path（/mood/history、/mood/trend，见
# deploy/k8s/50-ingress.yaml 批 5 那段），Ingress 的 Exact 只搬这一条路径本身，
# 新端点自动留在 py-api 一侧——所以这里加端点**不需要**改 Ingress、也不需要 Go 注册。
# （反例是 KG-26 踩过的 `/reflections` 前缀：那一批用 Prefix 切整个域，
# 新端点会跟着搬去 Go 然后当场 404。判据由 backend/tests/test_mood_analysis.py 的
# 「按 (方法,路径) 模拟最长前缀路由后落到 go-api 的端点必须都在 server.go 注册」那条守着。）
#
# 开关判在 require_ai_consent **之前**：关着的时候这个功能对外不存在，
# 一条 40303 就已经把「这里藏了一个功能」泄露出去了。
@router.post("/mood/analysis")
async def analysis(
    request: MoodAnalysisRequest,
    db: AsyncSession = Depends(get_db),
    language: str = Depends(get_request_language),
    user_id: str = Depends(get_current_user),
    service: MoodAnalysisService = Depends(get_mood_analysis_service),
):
    """三层次情绪分析（轻松／探索／深潜）。

    - 开关 `mood_analysis_depths_enabled` 关 ⇒ 40402（不暴露功能存在）；
    - 未同意 AI 数据共享 ⇒ 40303；今天没签到 ⇒ 40402；分析池耗尽 ⇒ 40301；
    - 危机词命中 ⇒ 直接下发分地区热线文案，**排在配额之前**，不花额度；
    - 引用未通过逐字校验 ⇒ `safety_status="degraded"` 且 `pattern=null`。
    """
    if not settings.mood_analysis_depths_enabled:
        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, "资源不存在")
    await user_service.require_ai_consent(db, user_id)
    return success(await service.analyze(db, user_id, request, language))


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
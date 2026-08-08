"""
本命星盘路由（PRD 探索 Tab 可选入口）。

端点：
    POST /api/v1/natal-chart        — 生成/更新本命星盘（出生信息录入 + 计算 + 首次 AI 解读）
    GET  /api/v1/natal-chart        — 查询星盘（无星盘返回 data=null）
    GET  /api/v1/natal-chart/daily  — 今日参考（综合/事业/财运 + 幸运色/数字/时刻，无星盘 data=null）

PRD 禁止项：圆形星盘轮盘图、爱情运势、合盘/匹配 —— 不提供对应端点。
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.language import get_request_language
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import NatalChartRequest
from backend.services.natal_service import NatalService, natal_service
from backend.services.user_service import user_service

router = APIRouter()


def get_natal_service() -> NatalService:
    return natal_service


@router.post("/natal-chart")
async def upsert_natal_chart(
    request: NatalChartRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    language: str = Depends(get_request_language),
    service: NatalService = Depends(get_natal_service),
):
    """
    生成/更新本命星盘。需 AI 数据共享同意（5.1.2(i)，AI 解读喂出生数据给 LLM）。

    body: birth_date + birth_time（可选，缺省按 12:00、上升不可用）+ birth_place（城市）。
    时区由后端从城市推导，用户不填时区；城市库未命中回退用户时区并标 tz_fallback。
    """
    await user_service.require_ai_consent(db, user_id)
    return success(await service.upsert_chart(db, user_id, request, language))


@router.get("/natal-chart")
async def get_natal_chart(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    language: str = Depends(get_request_language),
    service: NatalService = Depends(get_natal_service),
):
    """
    查询本命星盘（含按请求语言的 AI 解读缓存；缓存未命中惰性生成）。

    无星盘时返回 data=null（前端据此引导录入，优于 40403 异常路径）。
    """
    return success(await service.get_chart(db, user_id, language))


@router.get("/natal-chart/daily")
async def get_natal_daily(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    language: str = Depends(get_request_language),
    service: NatalService = Depends(get_natal_service),
):
    """
    今日参考：综合/事业/财运评分(1-5) + 幸运色/数字/时刻 + 模板短评。

    按 user_id + 用户本地日历日确定性伪随机生成，零 LLM 成本；PRD 禁止爱情维度。
    无星盘时返回 data=null。
    """
    return success(await service.get_daily(db, user_id, language))

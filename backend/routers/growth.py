"""
成长仪表盘路由 (P0-F07)。

端点：
    GET /api/v1/growth/dashboard    — 成长仪表盘
    GET /api/v1/growth/insights     — 本周洞察
    GET /api/v1/growth/timeline     — 365 天热力图
    GET /api/v1/growth/records      — 成长记录列表（支持标签筛选）
    GET /api/v1/growth/topics       — 推荐话题（基于近期记录高频标签）
    GET /api/v1/growth/memory-card  — 回忆卡片（N 天前今日）
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.language import get_request_language
from backend.core.response import success
from backend.database import get_db
from backend.services.growth_service import GrowthService, growth_service
from backend.services.user_service import user_service

router = APIRouter()


def get_growth_service() -> GrowthService:
    return growth_service


@router.get("/growth/dashboard")
async def dashboard(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: GrowthService = Depends(get_growth_service),
):
    """
    成长仪表盘。

    返回：日期、Streak、等级、今日心情、6 维度指标、14 天热力图、
    本周洞察、昨日记录延续。
    新用户（无数据）返回空状态。
    """
    return success(await service.get_dashboard(db, user_id))


@router.get("/growth/insights")
async def insights(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: GrowthService = Depends(get_growth_service),
):
    """本周洞察（来自 P0-F07 成长记录）。只读已生成周报，无记录返回空内容（生成由 cron 触发）。需 AI 数据共享同意。"""
    await user_service.require_ai_consent(db, user_id)
    return success(await service.get_weekly_insight(db, user_id))


@router.get("/growth/timeline")
async def timeline(
    days: int = Query(365, ge=7, le=365, description="天数"),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: GrowthService = Depends(get_growth_service),
):
    """365 天成长热力图数据。"""
    return success(await service.get_timeline(db, user_id, days))


@router.get("/growth/reports/{period}")
async def report(
    period: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: GrowthService = Depends(get_growth_service),
):
    """周期成长报告（P1-F03）：月 / 季 / 年报告，含情绪趋势、模式发现、洞察等级。需 AI 数据共享同意。"""
    await user_service.require_ai_consent(db, user_id)
    return success(await service.generate_report(db, user_id, period))


@router.get("/growth/patterns")
async def patterns(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: GrowthService = Depends(get_growth_service),
):
    """跨维度模式发现（P1-F03）：AI 基于真实数据发现关联模式。需 AI 数据共享同意。"""
    await user_service.require_ai_consent(db, user_id)
    content = await service.generate_pattern_discovery(db, user_id)
    return success({"content": content})


@router.get("/growth/records")
async def records(
    tag: Optional[str] = Query(None, description="按标签筛选（如 情绪/关系/工作/目标/沟通/成长）"),
    days: int = Query(30, ge=1, le=365, description="查询最近 N 天"),
    limit: int = Query(20, ge=1, le=100, description="返回条数上限"),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: GrowthService = Depends(get_growth_service),
):
    """成长记录列表（P0-F07 §6 成长记录页）：按日期倒序，支持标签筛选。"""
    items = await service.list_records(db, user_id, days=days, limit=limit, tag=tag)
    return success({"records": items, "total": len(items)})


@router.get("/growth/topics")
async def topics(
    days: int = Query(30, ge=1, le=365, description="统计最近 N 天记录"),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    language: str = Depends(get_request_language),
    service: GrowthService = Depends(get_growth_service),
):
    """推荐话题（P0-F07 §4：洞察标签 → 话题推荐）。规则实现，零 LLM 成本。"""
    items = await service.get_recommended_topics(db, user_id, language, days=days)
    return success({"topics": items})


@router.get("/growth/memory-card")
async def memory_card(
    days_ago: int = Query(30, ge=1, le=365, description="取 N 天前今日的数据"),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    language: str = Depends(get_request_language),
    service: GrowthService = Depends(get_growth_service),
):
    """回忆卡片（P0-F07 §7）：取 N 天前今日的记录/签到/记忆，生成"去年今日"式卡片。需 AI 数据共享同意。"""
    await user_service.require_ai_consent(db, user_id)
    return success(await service.get_memory_card(db, user_id, days_ago, language))
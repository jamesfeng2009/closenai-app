"""
性格画像路由 (P0-F06)。

端点：
    GET  /api/v1/personality/profile          — 查看性格画像
    POST /api/v1/personality/refresh          — 刷新画像
    GET  /api/v1/personality/assessment       — 查询对话式评估状态
    POST /api/v1/personality/assessment/start — 开始/续答评估
    POST /api/v1/personality/assessment/answer— 提交当前题答案
    POST /api/v1/personality/combo-report     — Pro 组合报告（16 型 × 星座 × 九型）
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import AssessmentAnswerRequest
from backend.services.personality_service import PersonalityService, personality_service
from backend.services.user_service import user_service

router = APIRouter()


def get_personality_service() -> PersonalityService:
    return personality_service


@router.get("/personality/profile")
async def get_profile(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: PersonalityService = Depends(get_personality_service),
):
    """
    查看性格画像。

    冷启动阶段（< 7 天）：返回进度条和阶段信息。
    成熟阶段（≥ 7 天）：返回 MBTI 类型 + 观察 + 独特之处。
    """
    return success(await service.get_profile(db, user_id))


@router.post("/personality/refresh")
async def refresh(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: PersonalityService = Depends(get_personality_service),
):
    """
    刷新性格画像。需 AI 数据共享同意（5.1.2(i)）。

    免费用户：总计 3 次刷新配额。
    Pro 用户：每月 5 次刷新配额。
    Pro+ 用户：不限次数。
    """
    await user_service.require_ai_consent(db, user_id)
    return success(await service.refresh(db, user_id))


@router.get("/personality/assessment")
async def get_assessment(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: PersonalityService = Depends(get_personality_service),
):
    """查询对话式评估状态（not_started / in_progress / completed + 下一题）。"""
    return success(await service.get_assessment(db, user_id))


@router.post("/personality/assessment/start")
async def start_assessment(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: PersonalityService = Depends(get_personality_service),
):
    """开始 8 题对话式评估；已有进行中评估则续答，返回当前题。"""
    return success(await service.start_assessment(db, user_id))


@router.post("/personality/assessment/answer")
async def answer_assessment(
    request: AssessmentAnswerRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: PersonalityService = Depends(get_personality_service),
):
    """提交当前题答案（快捷选项或自由输入）；答满 8 题后生成画像（首次免费）。"""
    return success(await service.answer_assessment(db, user_id, request))


@router.post("/personality/combo-report")
async def combo_report(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: PersonalityService = Depends(get_personality_service),
):
    """生成 Pro 组合报告（16 型人格 × 星座 × 九型交叉解读；每月 1 份，本月内命中缓存）。"""
    await user_service.require_ai_consent(db, user_id)
    return success(await service.generate_combo_report(db, user_id))
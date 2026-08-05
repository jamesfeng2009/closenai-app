"""
社交分享导出路由 (P1-F04)。

端点：
    GET  /api/v1/share/personality-card    — 性格名片导出（JSON）
    GET  /api/v1/share/report/{period}      — 周期报告导出（JSON）
    GET  /api/v1/share/report/{period}/pdf  — 周期报告导出（PDF）
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.response import success
from backend.database import get_db
from backend.services.share_service import ShareService, share_service

router = APIRouter()


def get_share_service() -> ShareService:
    return share_service


@router.get("/share/personality-card")
async def personality_card(
    language: str = Query("en", description="语言"),
    variant: str = Query("classic", description="名片变体：classic / minimal / combo(Pro)"),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ShareService = Depends(get_share_service),
):
    """导出性格名片（JSON 结构 + 分享文案），用于社交分享卡片。"""
    return success(await service.export_personality_card(db, user_id, language, variant))


@router.get("/share/report/{period}")
async def report_json(
    period: str,
    language: str = Query("en", description="语言"),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ShareService = Depends(get_share_service),
):
    """导出周期成长报告（JSON）。"""
    return success(await service.export_report(db, user_id, period, language))


@router.get("/share/report/{period}/pdf")
async def report_pdf(
    period: str,
    language: str = Query("en", description="语言"),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ShareService = Depends(get_share_service),
):
    """导出周期成长报告（PDF，中文正常渲染）。"""
    pdf = await service.export_report_pdf(db, user_id, period, language)
    filename = f"closenai_{period}_report.pdf"
    return StreamingResponse(
        iter([pdf]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
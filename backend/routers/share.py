"""
社交分享导出路由 (P1-F04) + Life Archive 导出 (P0-2 / M6)。

端点：
    GET  /api/v1/share/personality-card    — 性格名片导出（JSON）
    POST /api/v1/share/first-read-card     — 初读卡分享（PDF，P1-B01）
    POST /api/v1/share/memory-card         — 记忆卡分享（PDF，M4）
    GET  /api/v1/share/on-this-day         — 记忆考古候选（§10.1 ①「N 年前的今天」）
    GET  /api/v1/share/year-in-review      — 年度报告卡（JSON，自然年统计 + 深链）
    GET  /api/v1/share/year-in-review/pdf  — 年度报告卡（PDF）
    GET  /api/v1/share/link                — 本人深链与推荐码（M4 归因）
    GET  /api/v1/share/resolve/{code}      — 推荐码 → 落地信息（公开，落地页调用）
    GET  /api/v1/share/report/{period}     — 周期报告导出（JSON）
    GET  /api/v1/share/report/{period}/pdf — 周期报告导出（PDF）
    POST /api/v1/share/life-archive        — Life Archive 导出（JSON，权益校验 + 幂等）
    GET  /api/v1/share/life-archive/pdf    — Life Archive 导出（PDF，含 AIGC 标识）
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.auth import get_current_user
from backend.core.db_route import CommitBeforeResponseRoute
from backend.core.errors import AppException, ErrorCode
from backend.core.rate_limit import FixedWindowLimiter, client_ip
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import LifeArchiveExportRequest, MemoryCardRequest
from backend.services.share_service import ShareService, share_service

router = APIRouter(route_class=CommitBeforeResponseRoute)

# 进程内共享单例： limiter 的寿命 = 进程寿命，重启即清零（对反滥用计数无所谓）
_resolve_limiter = FixedWindowLimiter()


def get_share_service() -> ShareService:
    return share_service


async def guard_resolve_rate(request: Request) -> None:
    """`/share/resolve/{code}` 的单 IP 限流（#38）。

    前置在依赖里而不是服务里：限流判的是"这个来源刚才打了太多次"，与推荐码本身
    是否有效无关——写在服务里就得先查一次库，而那正是我们要保护的开销。
    """
    ip = client_ip(request, trust_forwarded_for=settings.trust_forwarded_for)
    key = f"share_resolve:{ip}"
    if not _resolve_limiter.hit(
        key, settings.share_resolve_limit, settings.share_resolve_window_seconds
    ):
        raise AppException(
            ErrorCode.SHARE_RESOLVE_RATE_LIMITED,
            "解析请求过于频繁，请稍后再试 / Too many resolve requests, try again later",
        )


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


@router.post("/share/first-read-card")
async def first_read_card_pdf(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ShareService = Depends(get_share_service),
):
    """初读卡分享 PDF（P1-B01 / 架构方案 §二 M1）。

    内容 = 已落库的静态初读卡，不新增 LLM 调用；未生成 → 40402。
    """
    pdf = await service.export_first_read_card_pdf(db, user_id)
    return StreamingResponse(
        iter([pdf]),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="closenai_first_read.pdf"'},
    )


@router.post("/share/memory-card")
async def memory_card_pdf(
    req: MemoryCardRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ShareService = Depends(get_share_service),
):
    """记忆卡分享 PDF：某条记忆的原话 + 日期 + 带推荐码的深链（架构方案 §二 M4）。

    memory_id 必须属于当前用户，否则 40403（归属校验在 repository 内强制）。
    """
    pdf = await service.export_memory_card_pdf(db, user_id, req.memory_id)
    return StreamingResponse(
        iter([pdf]),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="closenai_memory_card.pdf"'},
    )


@router.get("/share/on-this-day")
async def on_this_day(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ShareService = Depends(get_share_service),
):
    """记忆考古候选（§10.1 ①）：「N 年前的今天」原话列表，首页每日主位用。

    按用户本地时区逐往年窗口查询（近 3 年），零 LLM；无周年记忆 → 空候选。
    选中某条后走 POST /share/memory-card 导出 PDF（周年标题由该端点自动判定）。
    """
    return success(await service.on_this_day(db, user_id))


@router.get("/share/year-in-review")
async def year_in_review(
    year: Optional[int] = Query(None, description="回顾哪一年；缺省为上一个完整自然年"),
    language: str = Query("en", description="语言"),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ShareService = Depends(get_share_service),
):
    """年度报告卡（JSON）：一个自然年的确定性统计 + 带推荐码的分享深链。

    整年没有任何记录 → 40402 空态；请求早于注册年或未来年 → 400。
    """
    return success(await service.export_year_in_review(db, user_id, year, language))


@router.get("/share/year-in-review/pdf")
async def year_in_review_pdf(
    year: Optional[int] = Query(None, description="回顾哪一年；缺省为上一个完整自然年"),
    language: str = Query("en", description="语言"),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ShareService = Depends(get_share_service),
):
    """年度报告卡（PDF）：统计行 + 情绪 + 里程碑 + 品牌水印与二维码。

    内容零 LLM 生成，故不打 AIGC 元数据标识（与初读卡同口径）。
    """
    pdf = await service.export_year_in_review_pdf(db, user_id, year, language)
    return StreamingResponse(
        iter([pdf]),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="closenai_year_in_review.pdf"'},
    )


@router.get("/share/link")
async def share_link(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ShareService = Depends(get_share_service),
):
    """本人分享深链（App 内所有分享文案统一取这里，不各自拼 URL）。"""
    return success(await service.get_share_link(db, user_id))


@router.get("/share/resolve/{code}", dependencies=[Depends(guard_resolve_rate)])
async def resolve_share_code(
    code: str,
    db: AsyncSession = Depends(get_db),
    service: ShareService = Depends(get_share_service),
):
    """推荐码 → 落地信息（公开端点，CF Pages 落地页与 App 冷启动共用）。

    只回"该跳 App Store 还是打开 App"，不回分享者的任何资料——陌生码不该变成
    一个探测他人是否在用这个 App 的入口。
    单 IP 超窗口次数 → 42903（`guard_resolve_rate`），因为每次解析都带一次 DB 查询。
    """
    return success(await service.resolve_referral(db, code))


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


@router.post("/share/life-archive")
async def life_archive(
    request: LifeArchiveExportRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ShareService = Depends(get_share_service),
):
    """Life Archive 导出（JSON 章节数据）。

    权益判定：一次性买断（user_entitlements）或 Pro/Pro+ 订阅期内的每月 1 次
    免费生成；都不满足 → 40304。幂等：同档位（订阅口径为同档位同自然月）
    命中 share_exports 缓存直接复用，`regenerate=true` 才重新组装。
    """
    return success(
        await service.export_life_archive(
            db, user_id, request.scope, request.language, request.regenerate
        )
    )


@router.get("/share/life-archive/pdf")
async def life_archive_pdf(
    scope: str = Query("all", description="导出档位：1y / all / legacy"),
    language: str = Query("en", description="语言"),
    regenerate: bool = Query(False, description="忽略缓存重新生成"),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ShareService = Depends(get_share_service),
):
    """Life Archive 导出（PDF）。

    v1 在请求内同步排版：档案只编排已落库的用户记录与既有 AI 产出，
    不新增 LLM 调用（架构方案 §二 M6 合规前提），排版成本为本地 CPU。
    E3（arq 队列）落地后可切换为后台渲染 + 完成推送。
    """
    pdf = await service.export_life_archive_pdf(db, user_id, scope, language, regenerate)
    filename = f"closenai_life_archive_{scope}.pdf"
    return StreamingResponse(
        iter([pdf]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
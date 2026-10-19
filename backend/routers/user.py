"""
用户设置路由 (P0-F04)。

端点：
    GET    /api/v1/user/profile    — 用户信息
    PATCH  /api/v1/user/settings   — 更新设置
    GET    /api/v1/user/ai-consent — 查询 AI 数据共享同意状态
    POST   /api/v1/user/ai-consent — 设置 AI 数据共享同意（5.1.2(i)）
    GET    /api/v1/user/consents   — 同意台账（T5-01：六用途 × 文案版本 × 授予/撤回时刻）
    POST   /api/v1/user/consents   — 记一次单项表态（granted=true/false）
    POST   /api/v1/user/push-token — 注册推送 Token
    DELETE /api/v1/user/push-token — 清除推送 Token（登出时调用）
    DELETE /api/v1/user/account    — 注销账号（级联删除全部数据）
    POST   /api/v1/user/export     — 申请通用数据导出（T1-01，异步 job，1 次/24h）
    GET    /api/v1/user/export     — 最近导出任务列表（换设备后仍能找回 ready 产物）
    GET    /api/v1/user/export/{job_id} — 导出 job 状态（ready 时带下载路径）
    GET    /api/v1/user/export/{job_id}/download — 流式取回 zip（NDJSON 分表）
"""
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.db_route import CommitBeforeResponseRoute
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import AIConsentUpdate, ConsentUpdate, ExportRequest, UserSettingsUpdate, PushTokenRequest
from backend.services.auth_service import AuthService, auth_service
from backend.services.consent_service import ConsentService, consent_service
from backend.services.export_service import ExportService, export_service
from backend.services.user_service import UserService, user_service

router = APIRouter(route_class=CommitBeforeResponseRoute)


def get_user_service() -> UserService:
    return user_service


def get_consent_service() -> ConsentService:
    return consent_service


def get_auth_service() -> AuthService:
    return auth_service


def get_export_service() -> ExportService:
    return export_service


@router.get("/user/profile")
async def get_profile(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    """获取用户信息（Streak、等级、配额等）。"""
    return success(await service.get_profile(db, user_id))


@router.patch("/user/settings")
async def update_settings(
    settings_update: UserSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    """更新用户设置（昵称、对话风格、推送时间、时区）。"""
    return success(await service.update_settings(db, user_id, settings_update))


@router.get("/user/ai-consent")
async def get_ai_consent(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    """查询 AI 数据共享同意状态。"""
    profile = await service.get_profile(db, user_id)
    return success({"ai_consent": profile.ai_consent})


@router.post("/user/ai-consent")
async def set_ai_consent(
    request: AIConsentUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    """设置 AI 数据共享同意（5.1.2(i)：同意后弹窗不再显示，撤回后 AI 功能停用）。"""
    consent = await service.set_ai_consent(db, user_id, request.consent)
    return success({"ai_consent": consent})


@router.get("/user/consents")
async def get_consents(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ConsentService = Depends(get_consent_service),
):
    """同意台账（T5-01 / Baseline §14.2）：六种用途在当前文案版本上的表态与时刻。

    users 上的两个布尔位只答「现在开没开」，答不了「哪个版本、何时同意、何时撤回」。
    """
    return success(await service.list_consents(db, user_id))


@router.post("/user/consents")
async def set_consent(
    request: ConsentUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ConsentService = Depends(get_consent_service),
):
    """记一次单项表态（granted=true 授予 / false 撤回）。

    兼容期双写：ai_processing ⇒ users.ai_consent，analytics ⇒ users.analytics_consent，
    proactive_push ⇒ users.push_enabled；analytics 撤回顺带抹除 PostHog 人物数据。
    """
    return success(
        await service.set_consent(db, user_id, request.purpose, request.granted)
    )


@router.post("/user/analytics-consent")
async def set_analytics_consent(
    request: AIConsentUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    """设置匿名行为分析同意（独立于 AI 共享的第二项同意，可随时撤回）。

    撤回时顺带抹除 PostHog 侧人物数据：只改标志位会让「撤回」停留在纸面上。
    读取走 GET /users/profile 的 analytics_consent 字段，不另开只读端点。
    """
    return success(await service.set_analytics_consent(db, user_id, request.consent))


@router.post("/user/push-token")
async def register_push_token(
    request: PushTokenRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    """注册设备推送 Token（登录时调用）。"""
    await service.register_push_token(db, user_id, request.token, request.platform)
    return success({"status": "ok"})


@router.delete("/user/push-token")
async def clear_push_token(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
):
    """清除用户全部推送 Token（登出时调用，登出后不再收到推送）。"""
    await service.clear_push_tokens(db, user_id)
    return success({"status": "ok"})


@router.delete("/user/account")
async def delete_account(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    auth_svc: AuthService = Depends(get_auth_service),
):
    """注销账号：软删除即时失效 + QStash 异步物理删除（不可恢复）。

    即时返回（毫秒级）：status=deleted + 吊销 refresh + 清 push token；
    物理删除由 QStash 回调 /cron/purge-user 异步执行。
    """
    result = await auth_svc.delete_account(db, user_id)
    return success(result)


@router.post("/user/export")
async def request_export(
    request: ExportRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ExportService = Depends(get_export_service),
):
    """申请通用数据导出（T1-01 / Baseline §2.2-6「带走」、§3 P0）。

    只建 job 并返回状态，不在请求里生成 zip：一个老用户的全表拷贝可以跑到几十秒，
    把它放进一次 HTTP 请求就等于让 Vercel/k3s 的网关超时替用户决定「带走」成不成。
    生成由 GET /cron/export-jobs 认领。
    """
    return success(
        await service.request_export(db, user_id, request.scope or "all")
    )


@router.get("/user/export")
async def list_exports(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ExportService = Depends(get_export_service),
):
    """最近导出任务列表（倒序）。

    有这一条，「数据导出入口」在重装 App／换设备后仍然可用：否则前端只能记住上一次的
    job_id 才能回到已生成的档案，而 ready 产物只保留 7 天，那条 job_id 一丢就找不到。
    """
    return success(await service.list_jobs(db, user_id))


@router.get("/user/export/{job_id}")
async def get_export_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ExportService = Depends(get_export_service),
):
    """查询导出 job 状态；status=ready 时响应里带 download_path。"""
    return success(await service.get_job(db, user_id, job_id))


@router.get("/user/export/{job_id}/download")
async def download_export(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: ExportService = Depends(get_export_service),
):
    """鉴权流式取回 zip（media_type="application/zip"，与 5 条 PDF 端点同一条口径）。

    不发签名 URL：本部署没有接通对象存储，公开可转发的 bearer 链接会把「导出」变成
    第二次泄露。路径解析与越界拒绝在 export_service.resolve_download 里。
    """
    path, filename = await service.resolve_download(db, user_id, job_id)
    return FileResponse(path, filename=filename, media_type="application/zip")
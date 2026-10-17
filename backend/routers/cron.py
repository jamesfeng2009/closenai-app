"""
定时任务路由 (P0-F01/P0-F03/P0-F07/P1-F01)。

通过 Vercel Cron 每小时 GET 触发，QStash 分块分发。

端点：
    GET  /api/v1/cron/daily-message   — 主动消息生成（按时区分组）
    GET  /api/v1/cron/journey         — 第一周剧本 D0–D7 到期事件投递（P1-B02）
    POST /api/v1/cron/process-user    — QStash 回调：单用户主动消息处理（验签）
    GET  /api/v1/cron/mood-reminder   — 签到轻提醒（偏好时间后 2 小时未签到则推送）
    GET  /api/v1/cron/bedtime         — 睡前复盘提醒（PRD §5.16 S1，本地 22:00–23:30 窗口）
    GET  /api/v1/cron/care-push       — 连续负面 3 天关心推送（P1-F01，特殊事件 ≤4 次/月）
    GET  /api/v1/cron/trial-reminder  — 试用到期 24h 提醒（P1-F02）
    GET  /api/v1/cron/daily-insight   — 每日洞察生成
    GET  /api/v1/cron/weekly-insight  — 周洞察生成
    GET  /api/v1/cron/weekly-drift    — 渐变漂移周检测（记忆层）
    POST /api/v1/cron/follow-up       — QStash 调度：事件后 3-7 天主动跟进（验签）
    POST /api/v1/cron/purge-user      — QStash 回调：注销用户物理删除（验签，幂等）
    GET  /api/v1/cron/purge-deleted-users — 兜底清理：软删除超 1h 用户批量物理删除
    GET  /api/v1/cron/cleanup-tokens  — 清理过期 refresh token（防表无限增长）
    GET  /api/v1/cron/health          — 保热端点
"""
import base64
import hashlib
import json

import jwt
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.db_route import CommitBeforeResponseRoute
from backend.core.errors import AppException, ErrorCode
from backend.core.response import success
from backend.database import get_db
from backend.repositories import auth_repository
from backend.services.cron_service import CronService, cron_service
from backend.services.user_service import UserService, user_service

router = APIRouter(route_class=CommitBeforeResponseRoute)


def get_cron_service() -> CronService:
    return cron_service


def get_user_service() -> UserService:
    return user_service


def verify_cron_secret(request: Request):
    """验证 Cron Job 调用密钥（fail-closed：未配置 CRON_SECRET 时拒绝所有调用）。"""
    secret = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not settings.cron_secret or secret != settings.cron_secret:
        raise AppException(ErrorCode.UNAUTHORIZED, "Unauthorized")
    return True


async def verify_qstash_signature(request: Request) -> bytes:
    """验证 Upstash QStash 回调签名（fail-closed），返回原始请求体。

    Upstash 签名机制：Upstash-Signature 头为 HS256 JWT（用 Signing Key 签名），
    payload.body 为请求体 SHA-256 的 base64url（无填充）编码；exp 由 jwt.decode 自动校验。
    密钥轮换：current 验不过再试 next。
    """
    signature = request.headers.get("Upstash-Signature", "")
    keys = [
        k for k in (settings.qstash_current_signing_key, settings.qstash_next_signing_key) if k
    ]
    if not signature or not keys:
        raise AppException(ErrorCode.UNAUTHORIZED, "Unauthorized")
    body = await request.body()
    digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()
    for key in keys:
        try:
            payload = jwt.decode(signature, key, algorithms=["HS256"])
        except jwt.PyJWTError:
            continue
        if payload.get("body") == digest:
            return body
    raise AppException(ErrorCode.UNAUTHORIZED, "Unauthorized")


@router.get("/cron/daily-message")
async def daily_message(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: CronService = Depends(get_cron_service),
):
    """
    每日主动消息生成（Vercel Cron 触发）。

    1. 按用户时区分组（UTC+8, UTC+7, UTC+0）
    2. 对每组用户，通过 QStash 分块分发（每块 50 用户）
    3. 每个分块独立调用 LLM 生成主动消息 + 推送通知
    """
    verify_cron_secret(request)
    timezone_group = request.query_params.get("tz", "UTC+8")
    result = await service.dispatch_daily_messages(db, timezone_group)
    return success(result)


@router.get("/cron/journey")
async def journey_dispatch(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: CronService = Depends(get_cron_service),
):
    """第一周剧本调度（PRD §5.19.3 P1-B02 / 架构方案 §二 M2，Vercel Cron 每小时触发）。

    扫描 scheduled_at<=now 且 status=pending 的剧本事件并按日序投递：D0–D5 与 D7 报告
    全部模板直出（零 LLM），仅 D2 走推送，其余只进 App 内红点。
    """
    verify_cron_secret(request)
    result = await service.dispatch_journey_events(db)
    return success(result)


@router.post("/cron/process-user")
async def process_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: CronService = Depends(get_cron_service),
):
    """QStash 回调：处理单个用户的主动消息任务。

    验签（Upstash-Signature JWT）通过后按 body.user_id 处理；
    处理异常返回 500，由 QStash 重试机制兜底。
    """
    body = await verify_qstash_signature(request)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise AppException(ErrorCode.BAD_REQUEST, "Invalid JSON body")
    user_id = payload.get("user_id")
    if not user_id:
        raise AppException(ErrorCode.BAD_REQUEST, "user_id required")
    status = await service.process_user_task(db, user_id)
    return success({"user_id": user_id, "status": status})


@router.get("/cron/mood-reminder")
async def mood_reminder(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: CronService = Depends(get_cron_service),
):
    """签到轻提醒（PRD P0-F03 §7，Vercel Cron 每小时触发）。

    对用户本地「push_time + 2 小时」仍未签到的用户推送轻提醒；
    纯推送（非 LLM 内容），不写对话流、不计配额。
    """
    verify_cron_secret(request)
    result = await service.dispatch_mood_reminders(db)
    return success(result)


@router.get("/cron/bedtime")
async def bedtime_reminder(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: CronService = Depends(get_cron_service),
):
    """睡前复盘提醒（PRD §5.16 S1，Vercel Cron 每小时触发）。

    用户本地 22:00–23:30 固定窗口推送复盘入口；危机命中 24h 内停推、
    当日已完成复盘不打扰。纯推送（模板文案），不写对话流、不计配额。
    """
    verify_cron_secret(request)
    result = await service.dispatch_bedtime_reminders(db)
    return success(result)


@router.get("/cron/curriculum")
async def curriculum_generation(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: CronService = Depends(get_cron_service),
):
    """Growth Curriculum 周规划（PRD §5.17，Vercel Cron 每小时触发）。

    用户本地周日 21 点桶预生成下周 7 张主题卡（Rule 优先 + LLM 周规划）。
    拉取式消费：只落 journey_events 首页池，不推送、不计配额。
    """
    verify_cron_secret(request)
    result = await service.dispatch_curriculum_generation(db)
    return success(result)


@router.get("/cron/trial-reminder")
async def trial_reminder(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: CronService = Depends(get_cron_service),
):
    """试用到期提醒（PRD P1-F02 §4，Vercel Cron 每日触发）。

    对 24 小时内试用到期的用户推送提醒（按用户语言），每条订阅只提醒一次。
    """
    verify_cron_secret(request)
    result = await service.dispatch_trial_reminders(db)
    return success(result)


@router.get("/cron/care-push")
async def care_push(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: CronService = Depends(get_cron_service),
):
    """连续负面 3 天关心推送（PRD P1-F01 §6，Vercel Cron 每小时触发）。

    对最近连续 3 天负面签到的用户在最佳发送小时推送关心文案；
    属特殊事件，每用户每月 ≤4 次，超限静默跳过。
    纯推送（非 LLM 内容），不写对话流、不计配额。
    """
    verify_cron_secret(request)
    result = await service.dispatch_care_pushes(db)
    return success(result)


@router.get("/cron/daily-insight")
async def daily_insight(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: CronService = Depends(get_cron_service),
):
    """每日洞察生成（P0-F07 §1）：仅为当日活跃用户（当日有签到或对话）生成，prompt 喂入当日对话。"""
    verify_cron_secret(request)
    result = await service.generate_daily_insights(db)
    return success(result)


@router.get("/cron/weekly-insight")
async def weekly_insight(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: CronService = Depends(get_cron_service),
):
    """周洞察生成（每周日凌晨触发）。"""
    verify_cron_secret(request)
    result = await service.generate_weekly_insights(db)
    return success(result)


@router.get("/cron/weekly-drift")
async def weekly_drift(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: CronService = Depends(get_cron_service),
):
    """渐变漂移周检测（spec memory-layer §3，每周日凌晨触发）。

    对每用户 current 且 conviction≥0.4 的记忆做近 7 天 vs 前 7 天消息
    embedding 趋势分析，相似度下降 > 0.15 时写 drift_score 并生成 drift_alert。
    """
    verify_cron_secret(request)
    result = await service.run_weekly_drift_detection(db)
    return success(result)


@router.post("/cron/follow-up")
async def follow_up(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: CronService = Depends(get_cron_service),
):
    """事件后 3-7 天主动跟进（PRD P0-F07，QStash 每日调度触发）。

    验签（Upstash-Signature JWT）通过后扫描跟进窗口内未跟进的
    reflection/milestone 成长记录，生成「上次你提到...现在怎么样了？」式
    跟进消息写入对话流（message_type='follow_up'）并标记防重复。
    调度注册见 cron_service.register_follow_up_schedule（幂等）。
    """
    await verify_qstash_signature(request)
    result = await service.dispatch_follow_ups(db)
    return success(result)


@router.post("/cron/purge-user")
async def purge_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: UserService = Depends(get_user_service),
):
    """QStash 回调：物理删除已软删除用户的全量数据（幂等）。

    verify_qstash_signature 验签通过后按 body.user_id 物理删 13 表；
    已删表 rowcount=0，天然幂等（QStash 重试安全）。
    异常返回 500 由 QStash 重试机制兜底。
    """
    body = await verify_qstash_signature(request)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise AppException(ErrorCode.BAD_REQUEST, "Invalid JSON body")
    user_id = payload.get("user_id")
    if not user_id:
        raise AppException(ErrorCode.BAD_REQUEST, "user_id required")
    counts = await service.purge_user(db, user_id)
    return success({"user_id": user_id, "counts": counts})


@router.get("/cron/purge-deleted-users")
async def purge_deleted_users(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: UserService = Depends(get_user_service),
):
    """兜底清理：物理删除软删除超 1 小时的用户（QStash 投递失败/未配置时）。

    Vercel Cron 定时触发（建议每小时）；逐用户独立 SAVEPOINT，单用户失败不影响批次。
    stale_hours=1 给 QStash 正常重试留窗口，避免与异步任务重复删除。
    """
    verify_cron_secret(request)
    results = await service.purge_deleted_users(db, stale_hours=1)
    return success({"purged": len(results), "users": results})


@router.get("/cron/cleanup-tokens")
async def cleanup_tokens(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """清理过期 refresh token（Vercel Cron 每日触发）。

    过期 token 已无认证价值，物理删除防 refresh_tokens 表无限增长；
    已吊销未过期 token 保留（重放检测依赖吊销记录）。
    """
    verify_cron_secret(request)
    deleted = await auth_repository.delete_expired(db)
    return success({"deleted": deleted})


@router.get("/cron/health")
async def health():
    """保热端点（供外部探活/手动调用）。

    调度清单（vercel.json）并未排程本路径，`settings.health_check_interval`
    也没有消费方——这两条口径由 tests/test_cron_schedule_coverage.py 与
    tasks.md 的 T 桶看住，别在这里写成「已定时」。
    """
    return success({"status": "ok", "warmed": True})
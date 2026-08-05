"""
定时任务服务 (P0-F01/P0-F03/P0-F07/P1-F01)。

管理 Vercel Cron 触发的定时任务：主动消息生成、签到轻提醒、周洞察生成、
连续负面关心推送。

触发模型（PRD P0-F01）：
- Vercel Cron 每小时触发一次
- 对每个用户，仅当其「本地时区当前小时 == push_time（默认 9:00）+ 每用户确定性抖动
  （hash(user_id+日期) % 31 - 15 分钟）所在小时」才生成，±15 分钟抖动把全量用户
  打散到不同 cron 批次；QStash 模式下再用 Upstash-Delay 把分钟级落点精确到目标时刻
- 分发路径：配置 qstash_token + backend_base_url 时逐用户 publish 到 QStash，
  由 QStash 回调 /api/v1/cron/process-user 单用户处理；未配置时回退 inline 批处理
- 连续未回复降级：≥2 天简短关心 / ≥5 天降频为每周一一次 / ≥14 天停止

推送频率档位（PRD P1-F01 §6）：
- daily（默认）：全部推送
- important：仅重要事件——里程碑、连续负面 3 天关心、周报
- off：不推送（消息仍存库）
- 特殊事件（里程碑/关心类）每月每用户 ≤4 次，超限静默跳过并记日志
- 活跃时段优化：近 7 天活跃中位时段明显偏离 push_time 时，改用活跃中位小时发送
"""
import hashlib
import logging
import math
from datetime import date, timedelta, timezone
from statistics import median
from typing import List, Dict, Any, Optional, Tuple

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.llm import llm
from backend.core.time_utils import (
    local_day_start_utc,
    local_now,
    local_today,
    safe_zoneinfo,
    utc_now,
)
from backend.models.user import User
from backend.repositories import (
    chat_repository,
    growth_repository,
    memory_repository,
    mood_repository,
    subscription_repository,
    user_repository,
)
from backend.services.growth_service import growth_service
from backend.services.message_generator import message_generator
from backend.services.mood_service import MOOD_MAP
from backend.services.push import push_copywriter, push_service
from backend.services.quota_service import quota_service

logger = logging.getLogger("closenai.cron")

BATCH_SIZE = 50

# 渐变漂移检测（spec §3）：近 7 天 vs 前 7 天相似度差超过该阈值触发渐变提醒
DRIFT_SCORE_THRESHOLD = 0.15
# 漂移检测每用户参与的记忆 / 每窗口消息数上限（成本控制）
DRIFT_MEMORY_LIMIT = 20
DRIFT_WINDOW_MSG_LIMIT = 50

# QStash 默认端点（settings.qstash_url 未配置时使用）
QSTASH_DEFAULT_URL = "https://qstash.upstash.io"

# 连续未回复简短关心模板（按语言，缺省回退 en）
REENGAGE_TEMPLATES = {
    "zh": "好久没聊了，一切都好吗？",
    "en": "Haven't heard from you in a while — how's everything going?",
}

# 未回复降级阈值（PRD P0-F01 §6）
SILENT_SHORT_DAYS = 2    # 连续 2 天未回复 → 简短关心
SILENT_WEEKLY_DAYS = 5   # 连续 5 天未回复 → 降频每周一
SILENT_STOP_DAYS = 14    # 连续 14 天未回复 → 停止主动消息

# 特殊事件推送（里程碑/关心类）每月每用户上限（PRD P1-F01 §6）
SPECIAL_PUSH_MONTHLY_LIMIT = 4
# 连续负面签到触发关心推送的天数（恰达该天数的当天触发一次，episode 内不重复）
CARE_NEGATIVE_DAYS = 3
# 里程碑天数（与 message_generator._milestone_message 同口径：streak/累计活跃恰达即触发）
MILESTONE_DAYS = (7, 30, 100, 365)
# 活跃时段优化（PRD P1-F01 §5，零 LLM 规则统计）：
# 近 7 天活跃事件数 / 活跃天数门槛；中位活跃小时与 push_time 偏离 ≥3h 才改用活跃中位时段
ACTIVITY_WINDOW_DAYS = 7
ACTIVITY_MIN_EVENTS = 5
ACTIVITY_MIN_DAYS = 3
ACTIVITY_DEVIATION_HOURS = 3


def _reengage_template(language: str) -> str:
    return REENGAGE_TEMPLATES.get((language or "en")[:2], REENGAGE_TEMPLATES["en"])


def _cosine_similarity(a, b) -> float:
    """余弦相似度（纯 Python，pgvector 返回 list/numpy 均可）。"""
    va, vb = list(a), list(b)
    dot = sum(x * y for x, y in zip(va, vb))
    na = math.sqrt(sum(x * x for x in va))
    nb = math.sqrt(sum(x * x for x in vb))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _drift_alert_text(memory_content: str, language: str) -> str:
    """渐变提醒文案（spec §3.3 模板，中英按 language）。"""
    if (language or "").startswith("zh"):
        return f"我注意到你最近越来越多地提到不同的话题，你对「{memory_content}」的想法是不是在变化？"
    return (
        f"I've noticed you've been mentioning different topics lately — "
        f"is your view on \"{memory_content}\" changing?"
    )


# 签到轻提醒文案已迁移至 push_copywriter 场景模板池（P1-F01 人格化推送文案）

# 试用到期提醒模板（PRD P1-F02 §4，按语言，缺省回退 en）
TRIAL_REMINDER_TEMPLATES = {
    "zh": "你的 ClosenAI 免费试用将在 24 小时内到期，续订即可继续享受完整权益。",
    "en": "Your ClosenAI free trial ends within 24 hours — subscribe to keep your full benefits.",
}


def _trial_reminder_template(language: str) -> str:
    return TRIAL_REMINDER_TEMPLATES.get((language or "en")[:2], TRIAL_REMINDER_TEMPLATES["en"])


def _push_hour(user: User) -> int:
    """用户偏好推送小时（push_time "HH:MM"，非法/缺省回退 9）。"""
    try:
        return int((user.push_time or "09:00").split(":")[0])
    except (ValueError, IndexError):
        return 9


class CronService:
    """定时任务管理服务。"""

    async def dispatch_daily_messages(self, db: AsyncSession, timezone_group: str) -> Dict[str, Any]:
        """主动消息生成分发（Vercel Cron 每小时触发）。

        按用户本地时区窗口过滤：只有本地时间命中「最佳发送小时（默认 push_time，
        活跃时段优化后可为活跃中位小时）± 每用户确定性抖动」所在小时的用户
        才会在本轮收到消息。
        """
        users = await user_repository.list_all(db)
        due_users: List[Tuple[User, int]] = []
        for u in users:
            now = local_now(u.timezone)
            target = self._target_minutes(u, now.date(), await self._best_send_hour(db, u))
            if now.hour == target // 60:
                due_users.append((u, target))
        if not due_users:
            return {
                "timezone": timezone_group,
                "users_total": len(users),
                "users_processed": 0,
                "status": "no_due_users",
            }

        # QStash 分发：逐用户 publish，由 QStash 回调 /cron/process-user 单用户处理
        if settings.qstash_token and settings.backend_base_url:
            published = 0
            publish_failed = 0
            for u, target in due_users:
                if await self._publish_user_task(u, target):
                    published += 1
                else:
                    publish_failed += 1
            return {
                "timezone": timezone_group,
                "users_total": len(users),
                "users_due": len(due_users),
                "published": published,
                "publish_failed": publish_failed,
                "status": "dispatched",
            }

        if settings.qstash_token:
            # QStash 已配置但缺回调地址：不能静默丢弃任务——回退 inline 并告警
            logger.warning("QStash 已配置但 backend_base_url 未设置，回退 inline 处理")

        user_ids = [u.id for u, _ in due_users]
        batches = [user_ids[i:i + BATCH_SIZE] for i in range(0, len(user_ids), BATCH_SIZE)]

        result = {"processed": 0, "success": 0, "failed": 0, "skipped_silent": 0, "skipped_already_sent": 0}
        for batch in batches:
            r = await self.process_batch(db, batch)
            for key in result:
                result[key] += r.get(key, 0)
        return {
            "timezone": timezone_group,
            "users_total": len(users),
            "batches": len(batches),
            "status": "completed",
            **result,
        }

    def _jitter_minutes(self, user: User, day: date) -> int:
        """每用户每日确定性抖动（-15..+15 分钟）。

        用 sha256 而非内建 hash()：后者按进程随机加盐，跨进程/跨实例不稳定。
        """
        digest = hashlib.sha256(f"{user.id}:{day.isoformat()}".encode()).hexdigest()
        return int(digest, 16) % 31 - 15

    def _target_minutes(self, user: User, day: date, hour: Optional[int] = None) -> int:
        """用户本地某日的目标发送时刻（一天中的分钟数 = 推送小时:分钟 + 抖动）。

        hour 缺省取 push_time 的小时；传入时（活跃时段优化）替换小时，
        分钟沿用 push_time 的分钟，抖动不变（每用户每日确定性）。
        """
        push_time = user.push_time or "09:00"
        try:
            hh, mm = push_time.split(":")
            push_minutes = (hour if hour is not None else int(hh)) * 60 + int(mm)
        except (ValueError, IndexError):
            push_minutes = (hour if hour is not None else 9) * 60
        return (push_minutes + self._jitter_minutes(user, day)) % (24 * 60)

    async def _best_send_hour(self, db: AsyncSession, user: User) -> int:
        """每用户最佳发送小时（PRD P1-F01 §5，规则统计，零 LLM）。

        默认 push_time 小时；近 7 天活跃（对话 + 签到）事件 ≥5 且覆盖 ≥3 天，
        且本地活跃中位小时与 push_time 小时偏离 ≥3h 时，改用活跃中位小时。
        数据不足 / 统计异常一律回退 push_time 小时。
        """
        default = _push_hour(user)
        try:
            zone = safe_zoneinfo(user.timezone)
            start = utc_now() - timedelta(days=ACTIVITY_WINDOW_DAYS)
            times = await chat_repository.list_recent_activity(db, user.id, start)
            checkins = await mood_repository.get_history(
                db, user.id, days=ACTIVITY_WINDOW_DAYS
            )
            hours: List[int] = []
            days = set()
            for dt in list(times) + [c.created_at for c in checkins]:
                aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                local = aware.astimezone(zone)
                hours.append(local.hour)
                days.add(local.date())
            if len(hours) < ACTIVITY_MIN_EVENTS or len(days) < ACTIVITY_MIN_DAYS:
                return default
            median_hour = int(median(hours))
            # 环形距离（23 点与 1 点只差 2 小时）
            diff = abs(median_hour - default)
            if min(diff, 24 - diff) >= ACTIVITY_DEVIATION_HOURS:
                return median_hour
        except Exception as exc:  # noqa: BLE001 - 统计失败回退默认推送小时
            logger.warning("活跃时段统计失败 user=%s: %s", user.id, exc)
        return default

    async def _publish_user_task(self, user: User, target_minutes: int) -> bool:
        """把单个用户的主动消息任务 publish 到 QStash（回调自身 process-user 端点）。

        Upstash-Delay 把实际执行时刻延迟到「目标发送时刻」的分钟级落点，
        避免所有用户在同一分钟打满 LLM/推送。失败安全降级：记日志返回 False。
        """
        base = (settings.qstash_url or QSTASH_DEFAULT_URL).rstrip("/")
        callback = f"{settings.backend_base_url.rstrip('/')}/api/v1/cron/process-user"
        headers = {
            "Authorization": f"Bearer {settings.qstash_token}",
            "Content-Type": "application/json",
        }
        now = local_now(user.timezone)
        delay = max(
            0,
            (target_minutes - (now.hour * 60 + now.minute)) * 60 - now.second,
        )
        if delay > 0:
            headers["Upstash-Delay"] = f"{delay}s"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{base}/v2/publish/{callback}",
                    json={"user_id": user.id},
                    headers=headers,
                )
            if 200 <= resp.status_code < 300:
                return True
            logger.warning("QStash publish 失败 user=%s status=%s", user.id, resp.status_code)
        except Exception as exc:  # noqa: BLE001 - 网络异常不阻断整轮分发
            logger.warning("QStash publish 异常 user=%s: %s", user.id, exc)
        return False

    async def process_user_task(self, db: AsyncSession, user_id: str) -> str:
        """QStash 回调入口：处理单个用户（独立事务边界）。

        返回 "sent" / "already_sent" / "skipped"；异常时 rollback 并抛出，
        由 HTTP 500 触发 QStash 重试（Transient 故障可恢复）。
        """
        try:
            status = await self._process_user(db, user_id)
            await db.commit()
            return status
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.warning("QStash 回调处理失败 user=%s: %s", user_id, exc)
            raise

    async def _days_silent(self, db: AsyncSession, user: User) -> Optional[int]:
        """连续未回复天数（None 表示从未发过消息，按注册时间计算）。

        一律换算到用户本地日历日再比较（created_at 存 UTC，直接 .date() 会漂移）。
        """
        last = await chat_repository.get_last_user_message(db, user.id)
        today = local_today(user.timezone)
        zone = safe_zoneinfo(user.timezone)

        def _local_date(dt) -> date:
            aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            return aware.astimezone(zone).date()

        if last is None:
            base = _local_date(user.created_at) if user.created_at else today
            return (today - base).days
        return (today - _local_date(last.created_at)).days

    async def process_batch(self, db: AsyncSession, user_ids: List[str]) -> Dict[str, Any]:
        """处理单批用户（QStash callback / 内联）。

        健壮性：
        - 今日已发去重：该用户本地今日已有 proactive 消息则跳过（cron 重复触发不重发）
        - 每用户独立事务边界：成功逐用户 commit，失败 rollback 不影响后续用户
        """
        success = 0
        failed = 0
        skipped_silent = 0
        skipped_already_sent = 0
        for uid in user_ids:
            try:
                status = await self._process_user(db, uid)
                await db.commit()
                if status == "sent":
                    success += 1
                elif status == "already_sent":
                    skipped_already_sent += 1
                else:
                    skipped_silent += 1
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                logger.warning("主动消息生成失败 user=%s: %s", uid, exc)
                failed += 1
        return {
            "processed": len(user_ids),
            "success": success,
            "failed": failed,
            "skipped_silent": skipped_silent,
            "skipped_already_sent": skipped_already_sent,
        }

    async def _process_user(self, db: AsyncSession, uid: str) -> str:
        """为单个用户生成并下发主动消息（事务边界由调用方控制）。

        返回 "sent" / "already_sent" / "skipped"。

        行锁串行化：加载用户时即对用户行加 FOR UPDATE，确保「今日已发去重检查」
        与「消息写入」在同一事务内串行完成，防止 QStash 回调超时重试时两个事务
        同时通过去重检查导致重复发送。
        """
        user = await user_repository.get_user(db, uid, for_update=True)
        if user is None:
            return "skipped"
        # 合规：未同意 AI 数据共享的用户不生成/推送 AI 内容（5.1.2(i)）
        if not user.ai_consent:
            return "skipped"
        language = user.language or "en"

        # ── 今日已发去重：cron 重复触发/多实例并发不重复发送 ──
        day_start = local_day_start_utc(user.timezone)
        already_sent = await chat_repository.count_today(
            db, uid, role="assistant", message_type="proactive", start=day_start
        )
        if already_sent:
            return "already_sent"

        # ── 连续未回复降级（PRD P0-F01 §6）──
        days_silent = await self._days_silent(db, user)
        if days_silent is not None and days_silent >= SILENT_STOP_DAYS:
            return "skipped"  # 停止主动发消息，等待用户回归

        if days_silent is not None and days_silent >= SILENT_WEEKLY_DAYS:
            # 降频：仅在用户本地周一发轻量关心
            if local_now(user.timezone).weekday() != 0:
                return "skipped"
            message = _reengage_template(language)
        elif days_silent is not None and days_silent >= SILENT_SHORT_DAYS:
            message = _reengage_template(language)
        else:
            # ── 配额：统一池（主动消息+签到解读）耗尽则跳过（PRD P0-F01 §8 / P1-F02 §2）──
            if await quota_service.is_proactive_quota_exceeded(db, user):
                return "skipped"
            message = await message_generator.generate_proactive_message(db, uid, language)
            # 四层公式命中不足 2 层：当日静默，宁可不发也不发泛泛问候（PRD P0-F01 §4）
            if message is None:
                return "skipped"

        await chat_repository.create(
            db, uid, "assistant", message, message_type="proactive", ai_generated=True
        )
        # 人格化推送：按频率档位 + 特殊事件月度上限过滤（PRD P1-F01 §6）
        if user.push_enabled and user.push_frequency != "off":
            milestone = self._milestone_day(user)
            if user.push_frequency == "important" and milestone is None:
                # 「仅重要事件」档：日常主动消息不推送（消息仍已存库，下次打开 App 可见）
                pass
            elif milestone is not None:
                # 里程碑属特殊事件：受每月 ≤4 次上限约束
                if self._special_push_allowed(user):
                    title, body = await push_copywriter.build_copy(
                        db, user, "milestone", milestone=milestone
                    )
                    await push_service.send_to_user(
                        db, uid, title, body, data={"type": "milestone"}
                    )
                    await user_repository.increment_special_push(
                        db, uid, self._special_month(user)
                    )
                else:
                    logger.info("特殊事件推送月度上限已达，跳过里程碑推送 user=%s", uid)
            else:
                title, body = await push_copywriter.build_copy(
                    db, user, "proactive", message=message
                )
                await push_service.send_to_user(
                    db, uid, title, body, data={"type": "proactive"}
                )
        return "sent"

    def _milestone_day(self, user: User) -> Optional[int]:
        """今日达成的里程碑天数（7/30/100/365），未达成返回 None。

        与 message_generator._milestone_message 同口径：streak 或累计活跃天数恰达。
        """
        streak = user.streak_count or 0
        active_days = user.active_days or 0
        for m in sorted(MILESTONE_DAYS, reverse=True):
            if streak == m or active_days == m:
                return m
        return None

    def _special_month(self, user: User) -> str:
        """特殊事件计数所属月份（用户本地 "YYYY-MM"）。"""
        return local_now(user.timezone).strftime("%Y-%m")

    def _special_push_allowed(self, user: User) -> bool:
        """特殊事件（里程碑/关心类）推送当月是否仍未超上限（PRD P1-F01 §6：≤4 次/月）。"""
        return not (
            user.special_push_month == self._special_month(user)
            and (user.special_push_count or 0) >= SPECIAL_PUSH_MONTHLY_LIMIT
        )

    async def dispatch_mood_reminders(self, db: AsyncSession) -> Dict[str, Any]:
        """签到轻提醒分发（PRD P0-F03 §7，Vercel Cron 每小时触发）。

        触发窗口：用户本地「push_time + 2 小时」且当日仍未签到 → 推送轻提醒。
        提醒是纯推送（静态模板，非 LLM 内容）：不计配额、不写对话流、
        不记 proactive 去重表（与主动消息去重逻辑互不干扰）。
        每用户独立事务边界，单用户失败不影响整批。
        """
        users = await user_repository.list_all(db)
        sent = 0
        skipped = 0
        failed = 0
        for user in users:
            try:
                status = await self._maybe_send_mood_reminder(db, user)
                await db.commit()
                if status == "sent":
                    sent += 1
                else:
                    skipped += 1
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                logger.warning("签到提醒失败 user=%s: %s", user.id, exc)
                failed += 1
        return {
            "users_total": len(users),
            "sent": sent,
            "skipped": skipped,
            "failed": failed,
            "status": "completed",
        }

    async def _maybe_send_mood_reminder(self, db: AsyncSession, user: User) -> str:
        """对单个用户按窗口 + 未签到条件发送轻提醒。返回 "sent" / "skipped"。"""
        # 「仅重要事件」档不发签到提醒（PRD P1-F01 §6：提醒非重要事件）
        if not user.push_enabled or user.push_frequency != "daily":
            return "skipped"
        # PRD：偏好时间后 2 小时仍未签到才提醒（每小时 cron 命中一次即每日最多一条）
        reminder_hour = (_push_hour(user) + 2) % 24
        if local_now(user.timezone).hour != reminder_hour:
            return "skipped"
        day_start = local_day_start_utc(user.timezone)
        if await mood_repository.get_today(db, user.id, day_start):
            return "skipped"
        title, body = await push_copywriter.build_copy(db, user, "mood_reminder")
        await push_service.send_to_user(
            db, user.id, title, body, data={"type": "mood_reminder"}
        )
        return "sent"

    async def dispatch_trial_reminders(self, db: AsyncSession) -> Dict[str, Any]:
        """试用到期提醒分发（PRD P1-F02 §4，Vercel Cron 每日触发）。

        扫描「未来 trial_remind_hours（默认 24h）内到期且未提醒」的试用订阅，
        推送提醒（按用户语言）；每条订阅只提醒一次（trial_reminder_sent 标记，
        无论是否实际推送都标记，避免每日重复扫描打扰）。
        每用户独立事务边界，单用户失败不影响整批。
        """
        now = utc_now()
        until = now + timedelta(hours=settings.trial_remind_hours)
        subs = await subscription_repository.list_expiring_trials(db, now, until)
        sent = 0
        skipped = 0
        failed = 0
        for sub in subs:
            try:
                status = await self._maybe_send_trial_reminder(db, sub)
                await db.commit()
                if status == "sent":
                    sent += 1
                else:
                    skipped += 1
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                logger.warning("试用到期提醒失败 sub=%s: %s", sub.id, exc)
                failed += 1
        return {
            "trials_due": len(subs),
            "sent": sent,
            "skipped": skipped,
            "failed": failed,
            "status": "completed",
        }

    async def _maybe_send_trial_reminder(self, db: AsyncSession, sub) -> str:
        """对单条试用订阅发送到期提醒（事务边界由调用方控制）。返回 "sent" / "skipped"。"""
        user = await user_repository.get_user(db, sub.user_id)
        if user is None:
            return "skipped"
        # 推送关闭的用户不打扰；同样标记已提醒，避免每日 cron 重复扫描
        if not user.push_enabled or user.push_frequency == "off":
            await subscription_repository.mark_trial_reminded(db, sub.id)
            return "skipped"
        body = _trial_reminder_template(user.language or "en")
        await push_service.send_to_user(
            db, user.id, "ClosenAI", body, data={"type": "trial_reminder"}
        )
        await subscription_repository.mark_trial_reminded(db, sub.id)
        return "sent"

    async def generate_daily_insights(self, db: AsyncSession) -> Dict[str, Any]:
        """每日洞察生成（P0-F07 §1，每日触发）。

        仅为「当日活跃用户」生成：当日有情绪签到或有用户对话（按用户本地日历日）。
        不活跃用户对当日没有内容可提炼，跳过（省 LLM 成本，也避免空泛模板洞察）。
        每用户独立事务边界，单用户失败不影响整批。
        """
        users = await user_repository.list_all(db)
        processed = 0
        skipped_inactive = 0
        failed = 0
        for user in users:
            # 合规：未同意 AI 数据共享的用户跳过（5.1.2(i)）
            if not user.ai_consent:
                continue
            try:
                if not await self._is_active_today(db, user):
                    skipped_inactive += 1
                    continue
                await growth_service.generate_daily_insight(db, user.id, user.language or "en")
                await db.commit()
                processed += 1
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                logger.warning("每日洞察生成失败 user=%s: %s", user.id, exc)
                failed += 1
        return {
            "users_processed": processed,
            "skipped_inactive": skipped_inactive,
            "failed": failed,
            "status": "completed",
        }

    async def _is_active_today(self, db: AsyncSession, user: User) -> bool:
        """当日活跃判定：当日（用户本地日历日）有签到或有用户对话。"""
        day_start = local_day_start_utc(user.timezone)
        if await mood_repository.get_today(db, user.id, day_start):
            return True
        chat_today = await chat_repository.count_today(
            db, user.id, role="user", start=day_start
        )
        return chat_today > 0

    async def generate_weekly_insights(self, db: AsyncSession) -> Dict[str, Any]:
        """周洞察生成（每周触发）。每用户独立事务边界，单用户失败不影响整批。

        周报生成成功后向开启推送的用户发送"周报已生成"通知（P1-F03 §1，
        文案中英按用户语言；推送失败安全降级，不影响生成结果）。
        """
        users = await user_repository.list_all(db)
        processed = 0
        failed = 0
        for user in users:
            # 合规：未同意 AI 数据共享的用户跳过（5.1.2(i)）
            if not user.ai_consent:
                continue
            try:
                await growth_service.generate_weekly_insight(db, user.id, user.language or "en")
                await self._push_weekly_insight(db, user)
                await db.commit()
                processed += 1
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                logger.warning("周洞察生成失败 user=%s: %s", user.id, exc)
                failed += 1
        return {"users_processed": processed, "failed": failed, "status": "completed"}

    async def _push_weekly_insight(self, db: AsyncSession, user: User) -> None:
        """周报已生成推送（daily / important 档均发：周报属重要事件，PRD P1-F01 §6）。

        推送异常安全降级：只记日志不抛出，避免推送故障导致已生成周报被回滚。
        """
        if not user.push_enabled or user.push_frequency == "off":
            return
        try:
            title, body = await push_copywriter.build_copy(db, user, "weekly")
            await push_service.send_to_user(
                db, user.id, title, body, data={"type": "weekly_insight"}
            )
        except Exception as exc:  # noqa: BLE001 - 推送失败不影响周报生成主流程
            logger.warning("周报推送失败 user=%s: %s", user.id, exc)

    async def dispatch_care_pushes(self, db: AsyncSession) -> Dict[str, Any]:
        """连续负面 3 天关心推送（PRD P1-F01 §6「仅重要事件」档含关心类，每小时触发）。

        触发条件（全部满足）：
        - 用户本地当前小时 == 最佳发送小时（活跃时段优化，与主动消息同源）
        - 最近连续负面签到恰达 CARE_NEGATIVE_DAYS 天（episode 内仅触发一次，
          第 4 天起不再发；中断后再次恰达 3 天才重新触发）
        - 推送开启且频率 != off（daily / important 档均发：关心类属重要事件）
        - 特殊事件推送当月未超 SPECIAL_PUSH_MONTHLY_LIMIT，超限静默跳过并记日志
        纯模板推送（非 LLM 内容）：不计配额、不写对话流。
        每用户独立事务边界，单用户失败不影响整批。
        """
        users = await user_repository.list_all(db)
        sent = 0
        skipped = 0
        failed = 0
        for user in users:
            try:
                status = await self._maybe_send_care_push(db, user)
                await db.commit()
                if status == "sent":
                    sent += 1
                else:
                    skipped += 1
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                logger.warning("关心推送失败 user=%s: %s", user.id, exc)
                failed += 1
        return {
            "users_total": len(users),
            "sent": sent,
            "skipped": skipped,
            "failed": failed,
            "status": "completed",
        }

    async def _maybe_send_care_push(self, db: AsyncSession, user: User) -> str:
        """对单个用户按连续负面条件发送关心推送。返回 "sent" / "skipped"。"""
        if not user.push_enabled or user.push_frequency == "off":
            return "skipped"
        # 与主动消息同源的活跃时段优化：在用户最可能点击的本地小时发送
        if local_now(user.timezone).hour != await self._best_send_hour(db, user):
            return "skipped"
        if await self._negative_streak_days(db, user) != CARE_NEGATIVE_DAYS:
            return "skipped"
        if not self._special_push_allowed(user):
            logger.info("特殊事件推送月度上限已达，跳过关心推送 user=%s", user.id)
            return "skipped"
        title, body = await push_copywriter.build_copy(db, user, "care")
        await push_service.send_to_user(db, user.id, title, body, data={"type": "care"})
        await user_repository.increment_special_push(db, user.id, self._special_month(user))
        return "sent"

    async def _negative_streak_days(self, db: AsyncSession, user: User) -> int:
        """最近连续负面签到日历天数（按用户本地日，同日取最新一条）。

        最近一次签到早于昨天视为中断（返回 0），避免对沉寂用户误发关心。
        负面判定与 mood_service 同口径：MOOD_MAP 情绪值 ≤2（焦虑/难过/生气）。
        """
        recent = await mood_repository.get_recent(db, user.id, limit=10)
        if not recent:
            return 0
        zone = safe_zoneinfo(user.timezone)
        day_moods: List[Tuple[date, str]] = []
        seen = set()
        for r in recent:
            aware = r.created_at if r.created_at.tzinfo else r.created_at.replace(
                tzinfo=timezone.utc
            )
            d = aware.astimezone(zone).date()
            if d in seen:
                continue
            seen.add(d)
            day_moods.append((d, r.mood_emoji))
        if not day_moods or (local_today(user.timezone) - day_moods[0][0]).days > 1:
            return 0
        streak = 0
        for i, (d, emoji) in enumerate(day_moods):
            if i > 0 and (day_moods[i - 1][0] - d).days != 1:
                break  # 日历日不连续，streak 中断
            if MOOD_MAP.get(emoji, (None, 3))[1] > 2:
                break  # 非负面情绪，streak 中断
            streak += 1
        return streak

    async def run_weekly_drift_detection(self, db: AsyncSession) -> Dict[str, Any]:
        """渐变漂移周检测（spec §3，每周日凌晨触发）。

        对每用户 status=current 且 conviction≥0.4 的记忆，计算其 embedding 与
        近 7 天 vs 前 7 天用户消息 embedding 均值的相似度差，下降 > 0.15 时
        写 drift_score 并生成 drift_alert 成长记录。
        每用户独立事务边界，单用户失败不影响整批。
        """
        users = await user_repository.list_all(db)
        processed = 0
        alerts = 0
        failed = 0
        for user in users:
            # 合规：embedding 需把用户消息发往 LLM provider，未同意则跳过（5.1.2(i)）
            if not user.ai_consent:
                continue
            try:
                alerts += await self._drift_detect_user(db, user)
                await db.commit()
                processed += 1
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                logger.warning("漂移检测失败 user=%s: %s", user.id, exc)
                failed += 1
        return {
            "users_processed": processed,
            "drift_alerts": alerts,
            "failed": failed,
            "status": "completed",
        }

    async def _drift_detect_user(self, db: AsyncSession, user: User) -> int:
        """单用户漂移检测，返回生成的渐变提醒条数（事务边界由调用方控制）。"""
        memories = await memory_repository.list_drift_candidates(
            db, user.id, limit=DRIFT_MEMORY_LIMIT
        )
        if not memories:
            return 0

        now = utc_now()
        recent_start = now - timedelta(days=7)
        prev_start = now - timedelta(days=14)
        recent_texts = await chat_repository.list_user_contents(
            db, user.id, recent_start, now, limit=DRIFT_WINDOW_MSG_LIMIT
        )
        prev_texts = await chat_repository.list_user_contents(
            db, user.id, prev_start, recent_start, limit=DRIFT_WINDOW_MSG_LIMIT
        )
        if not recent_texts or not prev_texts:
            return 0

        # 每窗口一次批量 embed（成本 ≤ 2 次调用/用户），均值作为该窗口语义中心
        recent_mean = await self._mean_embedding(recent_texts)
        prev_mean = await self._mean_embedding(prev_texts)
        if recent_mean is None or prev_mean is None:
            return 0

        language = user.language or "en"
        alerts = 0
        for memory in memories:
            try:
                drift = _cosine_similarity(memory.embedding, prev_mean) - _cosine_similarity(
                    memory.embedding, recent_mean
                )
                if drift <= DRIFT_SCORE_THRESHOLD:
                    continue
                await memory_repository.run_drift_score(db, memory.id, drift)
                await growth_repository.create(
                    db, user.id, "drift_alert",
                    _drift_alert_text(memory.content, language),
                    related_memory_ids=[memory.id],
                )
                alerts += 1
            except Exception as exc:  # noqa: BLE001  单条失败不影响其余记忆
                logger.warning("漂移计算失败 memory=%s: %s", memory.id, exc)
        return alerts

    async def _mean_embedding(self, texts: List[str]) -> Optional[List[float]]:
        """批量向量化并取列均值（任一步失败返回 None，不阻断整轮检测）。"""
        try:
            vectors = await llm.embed(texts)
            if not vectors:
                return None
            dim = len(vectors[0])
            return [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]
        except Exception as exc:  # noqa: BLE001
            logger.warning("漂移检测 embedding 失败: %s", exc)
            return None


cron_service = CronService()

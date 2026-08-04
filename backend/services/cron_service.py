"""
定时任务服务 (P0-F01/P0-F07)。

管理 Vercel Cron 触发的定时任务：主动消息生成、周洞察生成。

触发模型（PRD P0-F01）：
- Vercel Cron 每小时触发一次
- 对每个用户，仅当其「本地时区当前小时 == push_time 小时（默认 9:00）」才生成，
  保证消息在用户本地 9 点窗口送达（±15 分钟随机由 QStash delay 实现，inline 模式忽略）
- 连续未回复降级：≥2 天简短关心 / ≥5 天降频为每周一一次 / ≥14 天停止
"""
import logging
from datetime import date, timezone
from typing import List, Dict, Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.time_utils import (
    local_day_start_utc,
    local_now,
    local_today,
    safe_zoneinfo,
)
from backend.models.user import User
from backend.repositories import chat_repository, user_repository
from backend.services.growth_service import growth_service
from backend.services.message_generator import message_generator
from backend.services.push import push_service

logger = logging.getLogger("closenai.cron")

BATCH_SIZE = 50

# 连续未回复简短关心模板（按语言，缺省回退 en）
REENGAGE_TEMPLATES = {
    "zh": "好久没聊了，一切都好吗？",
    "en": "Haven't heard from you in a while — how's everything going?",
}

# 未回复降级阈值（PRD P0-F01 §6）
SILENT_SHORT_DAYS = 2    # 连续 2 天未回复 → 简短关心
SILENT_WEEKLY_DAYS = 5   # 连续 5 天未回复 → 降频每周一
SILENT_STOP_DAYS = 14    # 连续 14 天未回复 → 停止主动消息


def _reengage_template(language: str) -> str:
    return REENGAGE_TEMPLATES.get((language or "en")[:2], REENGAGE_TEMPLATES["en"])


class CronService:
    """定时任务管理服务。"""

    async def dispatch_daily_messages(self, db: AsyncSession, timezone_group: str) -> Dict[str, Any]:
        """主动消息生成分发（Vercel Cron 每小时触发）。

        按用户本地时区窗口过滤：只有本地时间命中 push_time（默认 09:00）
        所在小时的用户才会在本轮收到消息。
        """
        if settings.qstash_token:
            # QStash publish 尚未接入前，不能静默丢弃任务——回退 inline 并告警
            logger.warning("QStash 已配置但 publish 未实现，回退 inline 处理")

        users = await user_repository.list_all(db)
        due_users = [u for u in users if self._is_due(u)]
        if not due_users:
            return {
                "timezone": timezone_group,
                "users_total": len(users),
                "users_processed": 0,
                "status": "no_due_users",
            }

        user_ids = [u.id for u in due_users]
        batches = [user_ids[i:i + BATCH_SIZE] for i in range(0, len(user_ids), BATCH_SIZE)]

        result = {"processed": 0, "success": 0, "failed": 0, "skipped_silent": 0}
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

    def _is_due(self, user: User) -> bool:
        """判断用户当前是否处于推送时间窗口（本地 push_time 小时）。"""
        push_time = user.push_time or "09:00"
        try:
            push_hour = int(push_time.split(":")[0])
        except (ValueError, IndexError):
            push_hour = 9
        return local_now(user.timezone).hour == push_hour

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
        """处理单批用户（QStash callback / 内联）。"""
        success = 0
        failed = 0
        skipped_silent = 0
        for uid in user_ids:
            try:
                user = await user_repository.get_user(db, uid)
                if user is None:
                    continue
                # 合规：未同意 AI 数据共享的用户不生成/推送 AI 内容（5.1.2(i)）
                if not user.ai_consent:
                    skipped_silent += 1
                    continue
                language = user.language or "en"

                # ── 连续未回复降级（PRD P0-F01 §6）──
                days_silent = await self._days_silent(db, user)
                if days_silent is not None and days_silent >= SILENT_STOP_DAYS:
                    skipped_silent += 1
                    continue  # 停止主动发消息，等待用户回归

                if days_silent is not None and days_silent >= SILENT_WEEKLY_DAYS:
                    # 降频：仅在用户本地周一发轻量关心
                    if local_now(user.timezone).weekday() != 0:
                        skipped_silent += 1
                        continue
                    message = _reengage_template(language)
                elif days_silent is not None and days_silent >= SILENT_SHORT_DAYS:
                    message = _reengage_template(language)
                else:
                    # ── 配额：免费用户今日额度耗尽则跳过（PRD P0-F01 §8）──
                    if user.tier == "free":
                        used = await chat_repository.count_today(
                            db, uid, start=local_day_start_utc(user.timezone)
                        )
                        if used >= settings.free_daily_messages:
                            skipped_silent += 1
                            continue
                    message = await message_generator.generate_proactive_message(db, uid, language)

                await chat_repository.create(
                    db, uid, "assistant", message, message_type="proactive", ai_generated=True
                )
                # 人格化推送：仅当用户开启推送且频率允许
                if user.push_enabled and user.push_frequency != "off":
                    await push_service.send_to_user(
                        db, uid, "ClosenAI", message, data={"type": "proactive"}
                    )
                success += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("主动消息生成失败 user=%s: %s", uid, exc)
                failed += 1
        return {
            "processed": len(user_ids),
            "success": success,
            "failed": failed,
            "skipped_silent": skipped_silent,
        }

    async def generate_weekly_insights(self, db: AsyncSession) -> Dict[str, Any]:
        """周洞察生成（每周触发）。"""
        users = await user_repository.list_all(db)
        processed = 0
        for user in users:
            # 合规：未同意 AI 数据共享的用户跳过（5.1.2(i)）
            if not user.ai_consent:
                continue
            try:
                await growth_service.generate_weekly_insight(db, user.id, user.language or "en")
                processed += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("周洞察生成失败 user=%s: %s", user.id, exc)
        return {"users_processed": processed, "status": "completed"}


cron_service = CronService()

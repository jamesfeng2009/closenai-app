"""
用户服务 (P0-F04)。

管理用户信息、Streak、关系等级、里程碑徽章、Streak 冻结券、推送 Token。
所有方法均注入 `db: AsyncSession` 进行真实数据访问。
"""
from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.time_utils import local_today
from backend.models.chat import ChatMessage
from backend.models.growth import GrowthRecord, UserMilestone
from backend.models.memory import Memory
from backend.models.mood import MoodCheckin
from backend.models.personality import PersonalityAssessment, PersonalityProfile
from backend.models.schemas import MilestoneItem, UserProfileResponse, UserSettingsUpdate
from backend.models.push import PushToken
from backend.models.share import ShareExport
from backend.models.subscription import Receipt, Subscription
from backend.core.errors import AppException, ErrorCode
from backend.core.logging import get_logger
from backend.models.auth import RefreshToken
from backend.models.user import User
from backend.repositories import (
    auth_repository,
    chat_repository,
    milestone_repository,
    user_repository,
)
from backend.services.quota_service import quota_service
from backend.config import settings

logger = get_logger("closenai.user")

# 前端平台 → 推送通道映射（PushService 按 apns/fcm 分发）
PLATFORM_MAP = {"ios": "apns", "android": "fcm", "apns": "apns", "fcm": "fcm"}


class UserService:
    """用户管理服务。"""

    async def _require_user(
        self, db: AsyncSession, user_id: str, *, for_update: bool = False
    ) -> User:
        """获取用户，不存在/已注销抛 404 USER_NOT_FOUND。

        复用 user_repository.require_user（get_user + 抛错），与业务路径
        27 处 require_user 调用点统一口径，不再按需建匿名用户。
        """
        return await user_repository.require_user(db, user_id, for_update=for_update)

    # 关系等级定义（PRD P0-F05：名称形容 AI 对用户的理解深度，无恋爱含义）
    RELATIONSHIP_LEVELS = {
        1: "初识 L1",
        2: "熟悉 L2",
        3: "知己 L3",
        4: "至交 L4",
    }

    # 等级名称双语展示表（DB 存中文规范名，展示按语言本地化）
    LEVEL_NAMES = {
        1: {"zh": "初识 L1", "en": "Acquainted L1"},
        2: {"zh": "熟悉 L2", "en": "Familiar L2"},
        3: {"zh": "知己 L3", "en": "Confidant L3"},
        4: {"zh": "至交 L4", "en": "Kindred L4"},
    }

    # 各等级解锁能力（PRD P0-F05 §1 等级表，双语）
    LEVEL_UNLOCKS = {
        1: {
            "zh": ["基础对话", "情绪签到"],
            "en": ["Basic conversations", "Mood check-in"],
        },
        2: {
            "zh": ["AI 主动跟进", "记忆引用"],
            "en": ["Proactive AI follow-ups", "Memory references"],
        },
        3: {
            "zh": ["深度性格分析", "模式发现", "AI 周报"],
            "en": ["Deep personality analysis", "Pattern discovery", "AI weekly report"],
        },
        4: {
            "zh": ["季度性格深度报告", "预测性洞察", "深度反思模式"],
            "en": ["Quarterly personality deep report", "Predictive insights", "Deep reflection mode"],
        },
    }

    # 等级页说明文字（PRD P0-F05 验收：所有等级名称无恋爱含义，等级页有说明文字）
    LEVEL_NOTE = {
        "zh": "所有等级名称均形容 AI 对你的理解深度，不涉及恋爱关系。",
        "en": "All level names describe how well the AI understands you — they are not romantic.",
    }

    # 升级所需累计活跃天数（PRD P0-F05：L1 0-6 / L2 7-29 / L3 30-89 / L4 90+）
    LEVEL_THRESHOLDS = {1: 0, 2: 7, 3: 30, 4: 90}

    # 里程碑徽章（PRD P0-F05：连续 7/30/100/365 天，判定依据 streak 连续天数）
    MILESTONE_DAYS = {"streak_7": 7, "streak_30": 30, "streak_100": 100, "streak_365": 365}

    # 徽章名称双语（PRD P0-F05：7 天"一周坚持"、30 天"月度知己"；100/365 沿用 P0-F07 §3 里程碑语义）
    MILESTONE_NAMES = {
        "streak_7": {"zh": "一周坚持", "en": "7-Day Streak"},
        "streak_30": {"zh": "月度知己", "en": "30-Day Confidant"},
        "streak_100": {"zh": "百日相伴", "en": "100 Days Together"},
        "streak_365": {"zh": "周年挚友", "en": "One-Year Companion"},
    }

    # Streak 冻结券月度配额（PRD P0-F05：免费每月 1 张，Pro 每月 3 张）
    FREEZE_MONTHLY_QUOTA = {"free": 1, "pro": 3, "pro_plus": 3}

    async def get_profile(self, db: AsyncSession, user_id: str) -> UserProfileResponse:
        """获取用户信息（按需创建用户）。

        配额实时计算（无需每日重置任务）：统一走 quota_service 口径——
        用户本地今日 user 消息数 + 主动消息数 + 情绪签到解读数。
        """
        user = await self._require_user(db, user_id)
        await self.ensure_freeze_grant(db, user)
        quota_used = await quota_service.daily_usage(db, user)
        return UserProfileResponse(
            id=user.id,
            name=user.name,
            username=user.username,
            mbti_type=user.mbti_type,
            language=user.language,
            streak_count=user.streak_count,
            streak_freeze_count=user.streak_freeze_count,
            relationship_level=user.relationship_level,
            chat_count=user.chat_count,
            tier=user.tier,
            daily_quota_used=quota_used,
            daily_quota_total=settings.free_daily_messages,
            ai_consent=user.ai_consent,
            push_time=user.push_time,
            conversation_style=user.conversation_style,
        )

    async def set_ai_consent(self, db: AsyncSession, user_id: str, consent: bool) -> bool:
        """设置 AI 数据共享同意状态（5.1.2(i)：同意后可使用 AI 功能，撤回后立即停用）。"""
        await self._require_user(db, user_id)
        await user_repository.set_ai_consent(db, user_id, consent)
        return consent

    async def require_ai_consent(self, db: AsyncSession, user_id: str) -> None:
        """校验 AI 数据共享同意；未同意则抛 403 AI_CONSENT_REQUIRED（合规 5.1.2(i)）。"""
        from backend.core.errors import AppException, ErrorCode

        user = await self._require_user(db, user_id)
        if not user.ai_consent:
            raise AppException(
                ErrorCode.AI_CONSENT_REQUIRED,
                "使用 AI 功能前，请先阅读并同意 AI 数据共享说明 / "
                "Please review and agree to the AI data sharing notice before using AI features",
            )

    async def update_settings(
        self, db: AsyncSession, user_id: str, settings_update: UserSettingsUpdate
    ) -> UserProfileResponse:
        """更新用户设置。"""
        birthday = (
            date.fromisoformat(settings_update.birthday) if settings_update.birthday else None
        )
        user = await user_repository.update_settings(
            db,
            user_id,
            name=settings_update.name,
            language=settings_update.language,
            timezone=settings_update.timezone,
            chat_style=settings_update.chat_style,
            conversation_style=settings_update.conversation_style,
            push_time=settings_update.push_time,
            push_frequency=settings_update.push_frequency,
            push_enabled=settings_update.push_enabled,
            mbti_type=settings_update.mbti_type,
            birthday=birthday,
            enneagram_type=settings_update.enneagram_type,
        )
        if not user:
            user = await self._require_user(db, user_id)
        return await self.get_profile(db, user_id)

    async def get_language(self, db: AsyncSession, user_id: str) -> str:
        """读取用户语言偏好（默认 en）。"""
        user = await user_repository.get_user(db, user_id)
        if user and user.language:
            return user.language
        return "en"

    async def register_push_token(self, db: AsyncSession, user_id: str, token: str, platform: str):
        """注册设备推送 Token（登录时调用）。

        前端传 ios/android，统一映射为推送通道 apns/fcm 存储。

        竞态说明：并发注册（同一用户双端/重试同时登录）时「先查后插」存在窗口——
        两个请求都查不到同平台记录便各自插入重复行，导致同一设备重复推送。
        先用 require_user(for_update=True) 持用户行锁把并发串行化（与 update_streak
        同一模式；SQLite 方言忽略 FOR UPDATE，测试单连接天然串行），后到者读到
        前者已插入的记录走更新分支，保证 (user_id, platform) 至多一行。
        表上不加唯一约束（避免迁移与历史脏数据冲突），行锁即为防线。
        """
        await self._require_user(db, user_id, for_update=True)
        channel = PLATFORM_MAP.get((platform or "").lower())
        if not channel:
            return  # 未知平台不落库，避免产生永不送达的脏数据
        # 同一平台重复注册时覆盖旧 token
        result = await db.execute(
            select(PushToken).where(
                PushToken.user_id == user_id, PushToken.platform == channel
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.device_token = token
        else:
            db.add(
                PushToken(
                    id=str(uuid4()),
                    user_id=user_id,
                    platform=channel,
                    device_token=token,
                    timezone="UTC",
                )
            )
        await db.flush()

    async def update_streak(self, db: AsyncSession, user_id: str) -> dict:
        """
        更新连续签到天数（行锁串行化：冻结券消耗与 streak 写入同事务）。

        规则：
        - 今天已签到：不更新
        - 昨天已签到：streak + 1
        - 断签：每缺 1 天自动消耗 1 张冻结券（PRD P0-F05：中断当天自动使用）；
          冻结券足够覆盖缺勤天数则 streak 延续 +1，否则重置为 1
        """
        user = await self._require_user(db, user_id, for_update=True)
        # 月度冻结券发放（新月首次活跃时重置配额，先于断签消耗判定）
        await self.ensure_freeze_grant(db, user)
        # Streak 按用户本地时区的日历日计算（PRD：每日 00:00 用户本地重置）
        today = local_today(user.timezone)
        yesterday = today - timedelta(days=1)
        old_level = self.get_relationship_level(user.active_days)[0]

        freeze_used = 0
        if user.last_active_date == today:
            # 今天已签到，不重复累加
            streak = user.streak_count
            active_days = user.active_days
        elif user.last_active_date == yesterday:
            streak = user.streak_count + 1
            active_days = user.active_days + 1
        else:
            # 缺勤天数（last_active_date 之后到昨天的天数）
            gap_days = (today - user.last_active_date).days - 1 if user.last_active_date else 0
            if 0 < gap_days <= user.streak_freeze_count:
                # 冻结券覆盖缺勤，保住 streak
                freeze_used = gap_days
                streak = user.streak_count + 1
            else:
                streak = 1
            active_days = user.active_days + 1

        freeze_count = user.streak_freeze_count - freeze_used
        await user_repository.update_streak(
            db, user_id, streak, today, active_days,
            streak_freeze_count=freeze_count,
            streak_freeze_grant_month=user.streak_freeze_grant_month,
        )
        # 里程碑徽章：streak 跨过 7/30/100/365 阈值时达成（幂等）
        new_milestones = await self.sync_milestones(db, user_id, streak)
        # PRD P0-F05：等级按累计活跃天数晋升（与 streak 中断解耦）
        level, label, progress = self.get_relationship_level(active_days)
        await user_repository.update_relationship_level(db, user_id, label)
        # 等级晋升庆祝（PRD P0-F05：升级时 AI 发送庆祝消息并展示新解锁能力）
        level_up = None
        if level > old_level:
            level_up = await self._celebrate_level_up(db, user, level, active_days)
        return {
            "streak_count": streak,
            "level": label,
            "level_progress": progress,
            "streak_freeze_count": freeze_count,
            "freeze_used": freeze_used,
            "new_milestones": new_milestones,
            "level_up": level_up,
        }

    async def ensure_freeze_grant(self, db: AsyncSession, user: User) -> None:
        """月度冻结券发放（PRD P0-F05：免费每月 1 张 / Pro 每月 3 张）。

        按用户本地月份判断：进入新月且未发放时，重置为该档位月配额并记录发放月。
        直接改写 user 对象内存属性，调用方随后可读到最新值。
        """
        month = local_today(user.timezone).strftime("%Y-%m")
        if user.streak_freeze_grant_month == month:
            return
        quota = self.FREEZE_MONTHLY_QUOTA.get(user.tier or "free", 1)
        await user_repository.update_streak_freeze(db, user.id, quota, month)
        user.streak_freeze_count = quota
        user.streak_freeze_grant_month = month

    async def sync_milestones(self, db: AsyncSession, user_id: str, streak: int) -> list:
        """按当前 streak 补齐已达成的里程碑徽章（幂等），返回本次新达成的类型列表。"""
        existing = await milestone_repository.list_for_user(db, user_id)
        existing_types = {m.milestone_type for m in existing}
        newly: list = []
        for mtype, days in self.MILESTONE_DAYS.items():
            if streak >= days and mtype not in existing_types:
                record = await milestone_repository.create(db, user_id, mtype)
                if record:
                    newly.append(mtype)
        return newly

    async def get_milestones(self, db: AsyncSession, user_id: str, language: str | None = None) -> list:
        """里程碑徽章列表（4 枚，含未达成占位与本地化名称）；先按当前 streak 幂等补齐。"""
        user = await self._require_user(db, user_id)
        await self.sync_milestones(db, user_id, user.streak_count)
        records = await milestone_repository.list_for_user(db, user_id)
        achieved_map = {m.milestone_type: m.achieved_at for m in records}
        return [
            MilestoneItem(
                milestone_type=mtype,
                days=days,
                name=self.milestone_name(mtype, language),
                achieved=mtype in achieved_map,
                achieved_at=achieved_map.get(mtype),
            )
            for mtype, days in self.MILESTONE_DAYS.items()
        ]

    async def clear_push_tokens(self, db: AsyncSession, user_id: str) -> None:
        """清除用户全部推送 Token（登出 / 注销时调用）。"""
        await db.execute(delete(PushToken).where(PushToken.user_id == user_id))
        await db.flush()

    async def soft_delete_account(self, db: AsyncSession, user_id: str) -> dict:
        """注销账号（第一步）：软删除即时失效 + 吊销全部 refresh + 清推送 token。

        - users.status='deleted', deleted_at=now（get_user 立即过滤，业务路径抛 USER_NOT_FOUND）
        - revoke_all_for_user：吊销全部未吊销 refresh token（防 30 分钟窗口期内刷新 access）
        - clear_push_tokens：清推送 token（防注销后继续收到推送）

        物理删除由 auth_service 编排 QStash 异步任务调 purge_user 完成。
        幂等：重复调用对已 deleted 用户无副作用（soft_delete 返回 0）。
        """
        await self._require_user(db, user_id)  # 确认存在且 active（deleted 抛 USER_NOT_FOUND）
        await user_repository.soft_delete(db, user_id)
        await auth_repository.revoke_all_for_user(db, user_id)
        await self.clear_push_tokens(db, user_id)
        return {"deleted": True}

    async def purge_user(self, db: AsyncSession, user_id: str) -> dict:
        """注销账号（第二步）：物理删除用户全部数据（QStash 异步回调，幂等）。

        覆盖全部用户数据表，确保无残留：
        对话 / 情绪签到 / 记忆 / 性格画像 / 性格评估 / 成长记录 / 里程碑徽章 / 推送令牌 / 分享导出 / 订阅与收据 / 用户本体。
        已删除的表 rowcount=0，天然幂等（QStash 重试安全）。
        """
        counts: dict = {}
        for table, key in (
            (ChatMessage, "chat_messages"),
            (MoodCheckin, "mood_checkins"),
            (Memory, "memories"),
            (PersonalityProfile, "personality_profiles"),
            (PersonalityAssessment, "personality_assessments"),
            (GrowthRecord, "growth_records"),
            (UserMilestone, "user_milestones"),
            (PushToken, "push_tokens"),
            (ShareExport, "share_exports"),
            (Receipt, "receipts"),
            (Subscription, "subscriptions"),
            (RefreshToken, "refresh_tokens"),
            (User, "users"),
        ):
            result = await db.execute(delete(table).where(table.user_id == user_id)
                                      if table is not User else delete(User).where(User.id == user_id))
            counts[key] = result.rowcount or 0
        await db.flush()
        return counts

    async def purge_deleted_users(self, db: AsyncSession, stale_hours: int = 1) -> list[dict]:
        """cron 兜底：批量物理删除软删除超 stale_hours 的用户。

        QStash 投递失败/未配置时，软删除记录会残留；此方法供
        /cron/purge-deleted-users 定时扫描兜底（逐用户独立 SAVEPOINT，
        单用户失败不影响批次）。
        """
        stale_users = await user_repository.list_deleted_stale(db, stale_hours=stale_hours)
        results: list[dict] = []
        for user in stale_users:
            try:
                async with db.begin_nested():
                    counts = await self.purge_user(db, user.id)
                results.append({"user_id": user.id, "counts": counts})
            except Exception as exc:  # noqa: BLE001  单用户失败不连锁
                logger.warning("purge_deleted_users 失败 user=%s: %s", user.id, exc)
        return results

    def get_relationship_level(self, active_days: int) -> tuple:
        """根据累计活跃天数计算关系等级（PRD P0-F05）。"""
        level = 1
        for lv, threshold in self.LEVEL_THRESHOLDS.items():
            if active_days >= threshold:
                level = lv
        label = self.RELATIONSHIP_LEVELS.get(level, "初识 L1")
        # 进度到下一级
        next_level = level + 1
        if next_level in self.LEVEL_THRESHOLDS:
            current = self.LEVEL_THRESHOLDS[level]
            next_threshold = self.LEVEL_THRESHOLDS[next_level]
            progress = (active_days - current) / (next_threshold - current) if next_threshold > current else 1.0
        else:
            progress = 1.0
        return level, label, min(progress, 1.0)

    def level_label(self, level: int, language: str | None = None) -> str:
        """等级名称按语言本地化（zh* 中文，其余英文）。"""
        lang = "zh" if (language or "").startswith("zh") else "en"
        return self.LEVEL_NAMES.get(level, self.LEVEL_NAMES[1])[lang]

    def milestone_name(self, milestone_type: str, language: str | None = None) -> str:
        """徽章名称按语言本地化；未知类型回退原始类型串。"""
        lang = "zh" if (language or "").startswith("zh") else "en"
        return self.MILESTONE_NAMES.get(milestone_type, {}).get(lang, milestone_type)

    def get_level_info(self, active_days: int, language: str | None = None) -> dict:
        """当前等级信息（本地化名称 + 进度 + 解锁能力 + 升级剩余天数）。"""
        level, _canonical, progress = self.get_relationship_level(active_days)
        lang = "zh" if (language or "").startswith("zh") else "en"
        next_threshold = self.LEVEL_THRESHOLDS.get(level + 1)
        return {
            "level": level,
            "label": self.level_label(level, language),
            "progress": progress,
            "active_days": active_days,
            "next_threshold": next_threshold,
            "days_to_next": (next_threshold - active_days) if next_threshold else 0,
            "unlocks": self.LEVEL_UNLOCKS[level][lang],
        }

    async def get_level_detail(
        self, db: AsyncSession, user_id: str, language: str = "en"
    ) -> dict:
        """等级页数据（PRD P0-F05）：当前等级 + 全部等级/解锁能力表 + 无恋爱含义说明。"""
        user = await self._require_user(db, user_id)
        lang = "zh" if (language or "").startswith("zh") else "en"
        return {
            "current": self.get_level_info(user.active_days, language),
            "levels": [
                {
                    "level": lv,
                    "label": self.level_label(lv, language),
                    "threshold": self.LEVEL_THRESHOLDS[lv],
                    "unlocks": self.LEVEL_UNLOCKS[lv][lang],
                    "reached": user.active_days >= self.LEVEL_THRESHOLDS[lv],
                }
                for lv in sorted(self.LEVEL_THRESHOLDS)
            ],
            "note": self.LEVEL_NOTE[lang],
        }

    async def _celebrate_level_up(
        self, db: AsyncSession, user: User, level: int, active_days: int
    ) -> dict:
        """等级晋升庆祝（PRD P0-F05）：写入一条 AI 庆祝消息（规则模板，零 LLM 成本），
        并返回 level_up 负载供调用方（签到响应等）即时展示新解锁能力。

        active_days 单调递增（每日至多 +1），等级阈值仅在跨越当日触发一次，天然幂等。
        """
        language = user.language or "en"
        info = self.get_level_info(active_days, language)
        if (language or "").startswith("zh"):
            text = (
                f"🎉 经过 {active_days} 天的陪伴，我们的关系升级为「{info['label']}」！"
                f"新解锁能力：{'、'.join(info['unlocks'])}。"
            )
        else:
            text = (
                f"🎉 After {active_days} days together, our connection has grown to "
                f"\"{info['label']}\"! Newly unlocked: {', '.join(info['unlocks'])}."
            )
        await chat_repository.create(
            db, user.id, "assistant", text, message_type="level_up", ai_generated=False
        )
        return {
            "level": info["level"],
            "label": info["label"],
            "unlocks": info["unlocks"],
            "message": text,
        }


user_service = UserService()
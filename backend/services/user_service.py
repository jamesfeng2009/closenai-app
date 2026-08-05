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
from backend.models.user import User
from backend.repositories import milestone_repository, user_repository
from backend.services.quota_service import quota_service
from backend.config import settings

# 前端平台 → 推送通道映射（PushService 按 apns/fcm 分发）
PLATFORM_MAP = {"ios": "apns", "android": "fcm", "apns": "apns", "fcm": "fcm"}


class UserService:
    """用户管理服务。"""

    # 关系等级定义（PRD P0-F05：名称形容 AI 对用户的理解深度，无恋爱含义）
    RELATIONSHIP_LEVELS = {
        1: "初识 L1",
        2: "熟悉 L2",
        3: "知己 L3",
        4: "至交 L4",
    }

    # 升级所需累计活跃天数（PRD P0-F05：L1 0-6 / L2 7-29 / L3 30-89 / L4 90+）
    LEVEL_THRESHOLDS = {1: 0, 2: 7, 3: 30, 4: 90}

    # 里程碑徽章（PRD P0-F05：连续 7/30/100/365 天，判定依据 streak 连续天数）
    MILESTONE_DAYS = {"streak_7": 7, "streak_30": 30, "streak_100": 100, "streak_365": 365}

    # Streak 冻结券月度配额（PRD P0-F05：免费每月 1 张，Pro 每月 3 张）
    FREEZE_MONTHLY_QUOTA = {"free": 1, "pro": 3, "pro_plus": 3}

    async def get_profile(self, db: AsyncSession, user_id: str) -> UserProfileResponse:
        """获取用户信息（按需创建用户）。

        配额实时计算（无需每日重置任务）：统一走 quota_service 口径——
        用户本地今日 user 消息数 + 主动消息数 + 情绪签到解读数。
        """
        user = await user_repository.get_or_create_user(db, user_id)
        await self.ensure_freeze_grant(db, user)
        quota_used = await quota_service.daily_usage(db, user)
        return UserProfileResponse(
            id=user.id,
            name=user.name,
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
        )

    async def set_ai_consent(self, db: AsyncSession, user_id: str, consent: bool) -> bool:
        """设置 AI 数据共享同意状态（5.1.2(i)：同意后可使用 AI 功能，撤回后立即停用）。"""
        await user_repository.get_or_create_user(db, user_id)
        await user_repository.set_ai_consent(db, user_id, consent)
        return consent

    async def require_ai_consent(self, db: AsyncSession, user_id: str) -> None:
        """校验 AI 数据共享同意；未同意则抛 403 AI_CONSENT_REQUIRED（合规 5.1.2(i)）。"""
        from backend.core.errors import AppException, ErrorCode

        user = await user_repository.get_or_create_user(db, user_id)
        if not user.ai_consent:
            raise AppException(
                ErrorCode.AI_CONSENT_REQUIRED,
                "使用 AI 功能前，请先阅读并同意 AI 数据共享说明",
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
            push_time=settings_update.push_time,
            push_frequency=settings_update.push_frequency,
            push_enabled=settings_update.push_enabled,
            mbti_type=settings_update.mbti_type,
            birthday=birthday,
            enneagram_type=settings_update.enneagram_type,
        )
        if not user:
            user = await user_repository.get_or_create_user(db, user_id)
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
        """
        await user_repository.get_or_create_user(db, user_id)
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
        user = await user_repository.get_or_create_user(db, user_id, for_update=True)
        # 月度冻结券发放（新月首次活跃时重置配额，先于断签消耗判定）
        await self.ensure_freeze_grant(db, user)
        # Streak 按用户本地时区的日历日计算（PRD：每日 00:00 用户本地重置）
        today = local_today(user.timezone)
        yesterday = today - timedelta(days=1)

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
        return {
            "streak_count": streak,
            "level": label,
            "level_progress": progress,
            "streak_freeze_count": freeze_count,
            "freeze_used": freeze_used,
            "new_milestones": new_milestones,
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

    async def get_milestones(self, db: AsyncSession, user_id: str) -> list:
        """里程碑徽章列表（4 枚，含未达成占位）；先按当前 streak 幂等补齐。"""
        user = await user_repository.get_or_create_user(db, user_id)
        await self.sync_milestones(db, user_id, user.streak_count)
        records = await milestone_repository.list_for_user(db, user_id)
        achieved_map = {m.milestone_type: m.achieved_at for m in records}
        return [
            MilestoneItem(
                milestone_type=mtype,
                days=days,
                achieved=mtype in achieved_map,
                achieved_at=achieved_map.get(mtype),
            )
            for mtype, days in self.MILESTONE_DAYS.items()
        ]

    async def clear_push_tokens(self, db: AsyncSession, user_id: str) -> None:
        """清除用户全部推送 Token（登出 / 注销时调用）。"""
        await db.execute(delete(PushToken).where(PushToken.user_id == user_id))
        await db.flush()

    async def delete_account(self, db: AsyncSession, user_id: str) -> dict:
        """注销账号：级联删除用户全部数据（合规：数据删除权，不可恢复）。

        覆盖全部用户数据表，确保无残留：
        对话 / 情绪签到 / 记忆 / 性格画像 / 性格评估 / 成长记录 / 里程碑徽章 / 推送令牌 / 分享导出 / 订阅与收据 / 用户本体。
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
            (User, "users"),
        ):
            result = await db.execute(delete(table).where(table.user_id == user_id)
                                      if table is not User else delete(User).where(User.id == user_id))
            counts[key] = result.rowcount or 0
        await db.flush()
        return counts

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


user_service = UserService()
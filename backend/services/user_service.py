"""
用户服务 (P0-F04)。

管理用户信息、Streak、关系等级、推送 Token。
所有方法均注入 `db: AsyncSession` 进行真实数据访问。
"""
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.time_utils import local_day_start_utc, local_today
from backend.models.chat import ChatMessage
from backend.models.growth import GrowthRecord
from backend.models.memory import Memory
from backend.models.mood import MoodCheckin
from backend.models.personality import PersonalityProfile
from backend.models.schemas import UserProfileResponse, UserSettingsUpdate
from backend.models.push import PushToken
from backend.models.subscription import Receipt, Subscription
from backend.models.user import User
from backend.repositories import chat_repository, mood_repository, user_repository
from backend.config import settings

# 前端平台 → 推送通道映射（PushService 按 apns/fcm 分发）
PLATFORM_MAP = {"ios": "apns", "android": "fcm", "apns": "apns", "fcm": "fcm"}


class UserService:
    """用户管理服务。"""

    # 关系等级定义
    RELATIONSHIP_LEVELS = {
        1: "初识 L1",
        2: "知己 L2",
        3: "老友 L3",
        4: "灵魂伴侣 L4",
    }

    # 升级所需天数
    LEVEL_THRESHOLDS = {1: 0, 2: 7, 3: 30, 4: 90}

    async def get_profile(self, db: AsyncSession, user_id: str) -> UserProfileResponse:
        """获取用户信息（按需创建用户）。

        配额实时计算（无需每日重置任务）：用户本地今日
        user 消息数 + 情绪签到解读数 + 主动消息数。
        """
        user = await user_repository.get_or_create_user(db, user_id)
        day_start = local_day_start_utc(user.timezone)
        chat_used = await chat_repository.count_today(db, user_id, role="user", start=day_start)
        proactive_used = await chat_repository.count_today(
            db, user_id, role="assistant", message_type="proactive", start=day_start
        )
        mood_used = await mood_repository.count_since(db, user_id, day_start)
        quota_used = chat_used + proactive_used + mood_used
        return UserProfileResponse(
            id=user.id,
            name=user.name,
            mbti_type=user.mbti_type,
            language=user.language,
            streak_count=user.streak_count,
            relationship_level=user.relationship_level,
            chat_count=user.chat_count,
            tier=user.tier,
            daily_quota_used=quota_used,
            daily_quota_total=settings.free_daily_messages,
            ai_consent=user.ai_consent,
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
        更新连续签到天数。

        规则：
        - 今天已签到：不更新
        - 昨天已签到：streak + 1
        - 昨天未签到：streak 重置为 1
        """
        user = await user_repository.get_or_create_user(db, user_id)
        # Streak 按用户本地时区的日历日计算（PRD：每日 00:00 用户本地重置）
        today = local_today(user.timezone)
        yesterday = today - timedelta(days=1)

        if user.last_active_date == today:
            # 今天已签到，不重复累加
            streak = user.streak_count
        elif user.last_active_date == yesterday:
            streak = user.streak_count + 1
        else:
            streak = 1

        await user_repository.update_streak(db, user_id, streak, today)
        level, label, progress = self.get_relationship_level(user.chat_count)
        await user_repository.update_relationship_level(db, user_id, label)
        return {
            "streak_count": streak,
            "level": label,
            "level_progress": progress,
        }

    async def clear_push_tokens(self, db: AsyncSession, user_id: str) -> None:
        """清除用户全部推送 Token（登出 / 注销时调用）。"""
        await db.execute(delete(PushToken).where(PushToken.user_id == user_id))
        await db.flush()

    async def delete_account(self, db: AsyncSession, user_id: str) -> dict:
        """注销账号：级联删除用户全部数据（合规：数据删除权，不可恢复）。

        覆盖全部用户数据表，确保无残留：
        对话 / 情绪签到 / 记忆 / 性格画像 / 成长记录 / 推送令牌 / 订阅与收据 / 用户本体。
        """
        counts: dict = {}
        for table, key in (
            (ChatMessage, "chat_messages"),
            (MoodCheckin, "mood_checkins"),
            (Memory, "memories"),
            (PersonalityProfile, "personality_profiles"),
            (GrowthRecord, "growth_records"),
            (PushToken, "push_tokens"),
            (Receipt, "receipts"),
            (Subscription, "subscriptions"),
            (User, "users"),
        ):
            result = await db.execute(delete(table).where(table.user_id == user_id)
                                      if table is not User else delete(User).where(User.id == user_id))
            counts[key] = result.rowcount or 0
        await db.flush()
        return counts

    def get_relationship_level(self, chat_count: int) -> tuple:
        """根据聊天次数计算关系等级。"""
        level = 1
        for lv, threshold in self.LEVEL_THRESHOLDS.items():
            if chat_count >= threshold:
                level = lv
        label = self.RELATIONSHIP_LEVELS.get(level, "初识 L1")
        # 进度到下一级
        next_level = level + 1
        if next_level in self.LEVEL_THRESHOLDS:
            current = self.LEVEL_THRESHOLDS[level]
            next_threshold = self.LEVEL_THRESHOLDS[next_level]
            progress = (chat_count - current) / (next_threshold - current) if next_threshold > current else 1.0
        else:
            progress = 1.0
        return level, label, min(progress, 1.0)


user_service = UserService()
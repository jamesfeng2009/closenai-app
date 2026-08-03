"""
用户服务 (P0-F04)。

管理用户信息、Streak、关系等级、推送 Token。
"""
from datetime import datetime, date
from typing import Optional

from backend.models.schemas import (
    UserProfileResponse, UserSettingsUpdate,
)
from backend.config import settings


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

    async def get_profile(self, user_id: str) -> Optional[UserProfileResponse]:
        """获取用户信息。"""
        # TODO: 从 users 表查询
        # TODO: 检查今日是否已签到（更新 streak）
        return UserProfileResponse(
            id=user_id,
            name="Alex",
            mbti_type="INTP",
            streak_count=15,
            relationship_level="✨ 知己 L2",
            chat_count=247,
            tier="free",
            daily_quota_used=3,
            daily_quota_total=settings.free_daily_messages,
        )

    async def update_settings(self, user_id: str, settings_update: UserSettingsUpdate) -> UserProfileResponse:
        """更新用户设置。"""
        # TODO: 更新 users 表
        return await self.get_profile(user_id)

    async def register_push_token(self, user_id: str, token: str, platform: str):
        """注册设备推送 Token。"""
        # TODO: 写入 push_tokens 表
        pass

    async def update_streak(self, user_id: str) -> dict:
        """
        更新连续签到天数。

        规则：
        - 今天已签到：不更新
        - 昨天已签到：streak + 1
        - 昨天未签到：streak 重置为 1
        """
        # TODO: 从 users 表读取 last_active_date
        today = date.today()
        return {"streak_count": 15, "level": "知己 L2", "level_progress": 0.5}

    async def get_relationship_level(self, chat_count: int) -> tuple:
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
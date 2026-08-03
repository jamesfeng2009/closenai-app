"""
性格画像服务 (P0-F05)。

管理对话式性格发现、画像生成、刷新配额。
"""
from datetime import datetime
from typing import Optional, List

from backend.models.schemas import PersonalityProfileResponse, PersonalityRefreshResponse
from backend.config import settings


class PersonalityService:
    """性格画像管理服务。"""

    # 冷启动 5 阶段
    COLD_START_STAGES = ["破冰", "观察", "探索", "揭示", "运用"]

    async def get_profile(self, user_id: str) -> Optional[PersonalityProfileResponse]:
        """
        获取性格画像。

        冷启动阶段（< 7 天活跃）：返回进度条 + 阶段信息。
        成熟阶段：返回 MBTI + 观察 + 独特之处。
        """
        # TODO: 从 personality_profiles 表查询
        # TODO: 计算冷启动进度
        return PersonalityProfileResponse(
            id="profile-001",
            mbti_type="INTP",
            mbti_label="逻辑学家",
            observations=[
                "你偏好用逻辑分析问题，对深度话题充满兴趣",
                "7 天观察：5 次选'平静'，2 次选'焦虑'——焦虑都发生在工作日",
                "你的独特之处：外表冷静理性，但内心很在意亲密关系质量",
            ],
            unique_traits="外表冷静理性，但内心很在意亲密关系质量",
            version=1,
            refresh_quota_remaining=3,
            cold_start_progress=None,
            cold_start_stage=None,
            created_at=datetime.now(),
        )

    async def refresh(self, user_id: str) -> PersonalityRefreshResponse:
        """
        刷新性格画像。

        配额检查：
        - 免费用户：总计 3 次（不可恢复）
        - Pro 用户：每月 5 次
        - Pro+ 用户：不限
        """
        # TODO: 检查配额
        # TODO: 聚合最近数据 → LLM 生成新画像
        # TODO: 写入 personality_profiles 表（version+1）
        return PersonalityRefreshResponse(
            success=True,
            profile=None,  # 实际返回新画像
            message="画像已刷新",
        )

    async def get_cold_start_progress(self, user_id: str) -> dict:
        """获取冷启动进度（前 7 天）。"""
        # TODO: 计算活跃天数 / 7
        return {
            "progress": 0.14,  # 1/7
            "stage": "破冰",
            "days_remaining": 6,
            "hint": "再聊 6 天，AI 就会为你生成性格画像。",
        }


personality_service = PersonalityService()
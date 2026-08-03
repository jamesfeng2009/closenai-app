"""
情绪签到服务 (P0-F03)。

管理情绪签到、AI 解读生成、签到历史查询。
"""
from datetime import datetime
from typing import Optional, List, Dict

from backend.models.schemas import MoodCheckinResponse
from backend.config import settings


class MoodService:
    """情绪签到管理服务。"""

    # Rule Engine：免费用户基础解读模板
    RULE_ENGINE: Dict[str, str] = {
        "😊": "今天状态不错！保持这样的好心情，去完成重要的事情吧。",
        "😌": "平静也是一种力量。今天适合做一些需要专注的事情。",
        "😰": "焦虑感往往来自对未来的不确定。试着把注意力放在当下能控制的事情上。",
        "😢": "偶尔的低落是正常的。给自己一些空间，不需要强迫自己开心。",
        "😡": "愤怒是边界被触碰的信号。试着深呼吸，找到让你生气的原因是什么。",
        "😴": "累了就休息一下，不需要每时每刻都高效。",
    }

    async def checkin(self, user_id: str, emoji: str, note: Optional[str] = None) -> Optional[MoodCheckinResponse]:
        """
        情绪签到。

        1. 检查用户今日是否已签到（Redis 缓存）
        2. 已签到 → 返回已有结果
        3. 未签到 → 生成 AI 解读（Pro/Pro+ 用 LLM，免费用 Rule Engine）
        4. 更新 Streak
        5. 写入 mood_checkins 表
        """
        # TODO: 实现完整签到逻辑
        # 1. Redis 检查今日是否已签到
        # 2. 根据 tier 选择解读引擎
        # 3. 调用 LLM 或 Rule Engine 生成解读
        # 4. 更新 streak_count
        # 5. 写入 mood_checkins 表

        tier = "free"  # TODO: 从数据库获取
        interpretation = self.RULE_ENGINE.get(emoji, "记录你的心情，明天会更好。")
        if tier in ("pro", "pro_plus"):
            # TODO: 调用 LLM 生成深度解读
            pass

        return MoodCheckinResponse(
            id="mood-001",
            mood_emoji=emoji,
            mood_label=self._get_label(emoji),
            ai_interpretation=interpretation,
            streak_updated=15,
            created_at=datetime.now(),
        )

    async def get_history(self, user_id: str, days: int = 30) -> List[MoodCheckinResponse]:
        """查询签到历史。"""
        # TODO: 从 mood_checkins 表查询
        return []

    async def get_trend(self, user_id: str, weeks: int = 4) -> dict:
        """查询情绪趋势。"""
        # TODO: 聚合查询 + 趋势分析
        return {
            "labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "values": [3, 4, 3, 2, 4, 5, 4],
            "summary": "本周情绪总体平稳，周五略有压力。",
        }

    def _get_label(self, emoji: str) -> str:
        """表情映射到中文标签。"""
        labels = {
            "😊": "开心", "😌": "平静", "😰": "焦虑",
            "😢": "难过", "😡": "愤怒", "😴": "疲惫",
        }
        return labels.get(emoji, "其他")


mood_service = MoodService()
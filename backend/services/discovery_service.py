"""
每日发现服务 (P0-F10)。

基于 Personal Dataset 每日对比生成发现内容。
作为 AI 主动消息（P0-F01）四层公式的第②层发出。

发现按价值分级（priority），供主动消息决定融合位置：
- high：新发现模式（情绪均值显著偏移 ≥0.8）/ 里程碑（首次开始记录）/ 签到频率大幅变化（|Δ| ≥ 3）
- medium：签到频率中等变化（|Δ| = 2）/ 记录中断（本周不再记录）
- low：情绪均值轻微漂移（0.4 ≤ |Δ| < 0.8）——主动消息中跳过，用③④层补足
"""
from dataclasses import dataclass
from datetime import timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.time_utils import utc_now
from backend.models.schemas import DailyDiscoveryResponse
from backend.repositories import mood_repository, user_repository

# 价值分级阈值（数据变化幅度）
MOOD_SHIFT_HIGH = 0.8   # 情绪均值偏移 ≥ 0.8：新发现模式（显著）
MOOD_SHIFT_LOW = 0.4    # 0.4 ≤ 偏移 < 0.8：轻微漂移（低价值）
FREQ_DIFF_HIGH = 3      # 签到频率 |Δ| ≥ 3：大幅变化
FREQ_DIFF_MEDIUM = 2    # 签到频率 |Δ| = 2：中等变化


@dataclass
class DiscoveryResult:
    """每日发现结果（消息生成路径，含价值分级）。"""

    content: str
    comparison_type: str
    priority: str  # high | medium | low


class DiscoveryService:
    """每日发现服务。"""

    async def get_daily_discovery(
        self, db: AsyncSession, user_id: str, language: str = "en"
    ) -> DailyDiscoveryResponse:
        """获取每日发现（真实数据对比；文案按语言）。"""
        await user_repository.require_user(db, user_id)
        now = utc_now()
        this_week = await mood_repository.get_history(db, user_id, days=7)
        last_week = await mood_repository.get_history(db, user_id, days=14)
        cutoff = now - timedelta(days=7)
        last_week = [
            r for r in last_week
            if (r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc)) < cutoff
        ]

        content, comparison_type, priority = self._compare(this_week, last_week, language)
        return DailyDiscoveryResponse(
            has_discovery=content is not None,
            content=content,
            comparison_type=comparison_type,
            priority=priority,
            generated_at=now,
        )

    async def generate_discovery(
        self, db: AsyncSession, user_id: str, language: str = "en"
    ) -> Optional[DiscoveryResult]:
        """生成每日发现（Cron 触发，融入主动消息）。返回含价值分级的发现或 None。"""
        result = await self.get_daily_discovery(db, user_id, language)
        if not result.has_discovery or not result.content:
            return None
        return DiscoveryResult(
            content=result.content,
            comparison_type=result.comparison_type or "",
            priority=result.priority or "low",
        )

    def _compare(self, this_week: list, last_week: list, language: str = "en") -> tuple:
        """对比本周与上周，返回 (发现文本, 对比类型, 价值分级)（文案按语言两套）。

        分级依据：数据变化幅度、是否新发现模式、是否里程碑。
        """
        zh = (language or "").startswith("zh")
        if not this_week and not last_week:
            return None, None, None
        if not this_week:
            # 记录中断：行为改变但无数据幅度可言，不宜作为消息开头
            return (
                "最近一周没有再记录心情了，要不要花一分钟说说今天怎样？" if zh
                else "You haven't recorded your mood this week — want to take a minute to share how today is going?"
            ), "checkin_frequency", "medium"
        if not last_week:
            # 里程碑：首次开始记录（新行为模式建立）
            return (
                "这一周你开始记录心情了，这是个很好的开始。" if zh
                else "You started recording your mood this week — that's a great start."
            ), "checkin_frequency", "high"

        count_diff = len(this_week) - len(last_week)
        if abs(count_diff) >= FREQ_DIFF_MEDIUM:
            # 签到频率变化：|Δ| ≥ 3 大幅变化 high；|Δ| = 2 中等 medium
            priority = "high" if abs(count_diff) >= FREQ_DIFF_HIGH else "medium"
            if zh:
                direction = "更多" if count_diff > 0 else "更少"
                return f"这一周你记录了 {len(this_week)} 次心情，比上周{direction}，是最近生活有变化吗？", "checkin_frequency", priority
            direction = "more" if count_diff > 0 else "fewer"
            return (
                f"You recorded your mood {len(this_week)} times this week, {direction} than last week "
                "— has something changed in your life lately?"
            ), "checkin_frequency", priority

        # 情绪模式对比：本周均值 vs 上周均值
        from backend.services.mood_service import MOOD_MAP
        this_avg = sum(MOOD_MAP.get(r.mood_emoji, (None, 3))[1] for r in this_week) / len(this_week)
        last_avg = sum(MOOD_MAP.get(r.mood_emoji, (None, 3))[1] for r in last_week) / len(last_week)
        mood_diff = this_avg - last_avg
        # 显著偏移（≥0.8）：新发现模式 → high
        if mood_diff >= MOOD_SHIFT_HIGH:
            return (
                "这一周的整体心情比上周好了不少，是什么样的好事发生了？" if zh
                else "Your overall mood this week is quite a bit better than last week — did something good happen?"
            ), "mood_improvement", "high"
        if mood_diff <= -MOOD_SHIFT_HIGH:
            return (
                "这一周的心情比上周低落了一些，想聊聊发生了什么吗？" if zh
                else "Your mood this week has dipped a little compared to last week — want to talk about what happened?"
            ), "mood_decline", "high"
        # 轻微漂移（0.4–0.8）：弱信号 → low（主动消息中跳过，用③④层补足）
        if mood_diff >= MOOD_SHIFT_LOW:
            return (
                "这一周的心情比上周略有起色，是什么让一切轻快了一点？" if zh
                else "Your mood this week is a touch brighter than last week — what's been lifting things?"
            ), "mood_improvement", "low"
        if mood_diff <= -MOOD_SHIFT_LOW:
            return (
                "这一周的心情比上周略微低了一点，最近是不是有点累？" if zh
                else "Your mood this week has dipped just slightly compared to last week — have you been a bit tired lately?"
            ), "mood_decline", "low"
        return None, None, None


discovery_service = DiscoveryService()

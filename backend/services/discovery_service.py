"""
每日发现服务 (P0-F10)。

基于 Personal Dataset 每日对比生成发现内容。
作为 AI 主动消息（P0-F01）四层公式的第②层发出。

对比维度：
- 情绪签到频率变化（本周 vs 上周）
- 情绪模式变化（本周 vs 上周）
"""
from datetime import timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.time_utils import utc_now
from backend.models.schemas import DailyDiscoveryResponse
from backend.repositories import mood_repository, user_repository


class DiscoveryService:
    """每日发现服务。"""

    async def get_daily_discovery(
        self, db: AsyncSession, user_id: str
    ) -> DailyDiscoveryResponse:
        """获取每日发现（真实数据对比）。"""
        await user_repository.get_or_create_user(db, user_id)
        now = utc_now()
        this_week = await mood_repository.get_history(db, user_id, days=7)
        last_week = await mood_repository.get_history(db, user_id, days=14)
        cutoff = now - timedelta(days=7)
        last_week = [
            r for r in last_week
            if (r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc)) < cutoff
        ]

        content, comparison_type = self._compare(this_week, last_week)
        return DailyDiscoveryResponse(
            has_discovery=content is not None,
            content=content,
            comparison_type=comparison_type,
            generated_at=now,
        )

    async def generate_discovery(
        self, db: AsyncSession, user_id: str
    ) -> str | None:
        """生成每日发现（Cron 触发）。返回发现文本或 None。"""
        result = await self.get_daily_discovery(db, user_id)
        return result.content if result.has_discovery else None

    def _compare(self, this_week: list, last_week: list) -> tuple:
        """对比本周与上周，返回 (发现文本, 对比类型)。"""
        if not this_week and not last_week:
            return None, None
        if not this_week:
            return "最近一周没有再记录心情了，要不要花一分钟说说今天怎样？", "checkin_frequency"
        if not last_week:
            return "这一周你开始记录心情了，这是个很好的开始。", "checkin_frequency"

        count_diff = len(this_week) - len(last_week)
        if abs(count_diff) >= 2:
            direction = "更多" if count_diff > 0 else "更少"
            return f"这一周你记录了 {len(this_week)} 次心情，比上周{ direction }，是最近生活有变化吗？", "checkin_frequency"

        # 情绪模式对比：本周均值 vs 上周均值
        from backend.services.mood_service import MOOD_MAP
        this_avg = sum(MOOD_MAP.get(r.mood_emoji, (None, 3))[1] for r in this_week) / len(this_week)
        last_avg = sum(MOOD_MAP.get(r.mood_emoji, (None, 3))[1] for r in last_week) / len(last_week)
        if this_avg - last_avg >= 0.8:
            return "这一周的整体心情比上周好了不少，是什么样的好事发生了？", "mood_improvement"
        if this_avg - last_avg <= -0.8:
            return "这一周的心情比上周低落了一些，想聊聊发生了什么吗？", "mood_decline"
        return None, None


discovery_service = DiscoveryService()
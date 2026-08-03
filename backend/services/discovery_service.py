"""
每日发现服务 (P0-F10)。

基于 Personal Dataset 每日对比生成发现内容。
作为 AI 主动消息（P0-F01）四层公式的第②层发出。
"""
from datetime import datetime

from backend.models.schemas import DailyDiscoveryResponse


class DiscoveryService:
    """每日发现服务。"""

    async def get_daily_discovery(self, user_id: str) -> DailyDiscoveryResponse:
        """
        获取每日发现。

        对比维度：
        - 情绪签到频率变化（本周 vs 上周）
        - 高频话题变化（对话内容关键词）
        - 聊天频率变化（本周 vs 上周）
        - 情绪模式变化（如：焦虑 → 平静）

        无显著变化时返回 has_discovery=false。
        """
        # TODO: 实现数据对比逻辑
        # 1. 查询本周数据
        # 2. 查询上周数据
        # 3. 对比差异
        # 4. 生成发现文本
        return DailyDiscoveryResponse(
            has_discovery=True,
            content="你这周提到了 3 次'加班'，上周是 0 次——最近是不是特别忙？",
            comparison_type="topic_frequency",
            generated_at=datetime.now(),
        )

    async def generate_discovery(self, user_id: str) -> Optional[str]:
        """
        生成每日发现（Cron Job 触发，凌晨 6:00）。

        返回发现文本，注入 AI 主动消息生成上下文。
        """
        # TODO: 跑数据对比
        return "你这周提到了 3 次'加班'，上周是 0 次——最近是不是特别忙？"


discovery_service = DiscoveryService()
"""
成长仪表盘服务 (P0-F07)。

管理成长仪表盘数据聚合、洞察生成、Timeline 热力图。
"""
from datetime import datetime, timedelta
from typing import List, Optional

from backend.models.schemas import GrowthDashboardResponse, GrowthMetrics, WeeklyInsightResponse


class GrowthService:
    """成长数据聚合服务。"""

    async def get_dashboard(self, user_id: str) -> GrowthDashboardResponse:
        """
        获取成长仪表盘数据。

        聚合来源：
        - mood_checkins: 今日心情 + 情绪趋势
        - users: Streak + 等级 + 聊天次数
        - growth_records: 本周洞察 + 昨天记录
        - 冷启动用户返回空状态
        """
        # TODO: 从数据库聚合查询
        today = datetime.now()
        weekday_cn = ["一", "二", "三", "四", "五", "六", "日"][today.weekday()]

        return GrowthDashboardResponse(
            date=f"{today.month}月{today.day}日 星期{weekday_cn}",
            greeting="你好，今天感觉怎么样？",
            streak_count=15,
            relationship_level="✨ 知己 L2",
            level_label="知己 L2",
            level_progress=0.5,
            chat_count=247,
            today_mood="😌",
            growth_metrics=GrowthMetrics(
                emotion="😌", emotion_change=12.0,
                social="💛", social_change="↑",
                confidence="💪", confidence_change="→",
                stress="⚡", stress_change=-8.0,
                curiosity="🔍", curiosity_change="↑",
                goals="🎯", goals_change="→",
            ),
            mood_timeline=["#10B981", "#10B981", "#6B7280", "#F59E0B", "#10B981",
                          "#10B981", "#F59E0B", "#10B981", "#EF4444", "#F59E0B",
                          "#10B981", "#10B981", "#10B981", "#6B7280"],
            weekly_insight="最近 3 周你每周五压力都会升高，可能是工作节奏的影响。建议周四提前调整...",
            yesterday_record="昨天：工作压力 · 继续",
        )

    async def get_weekly_insight(self, user_id: str) -> WeeklyInsightResponse:
        """获取本周洞察（来自 growth_records 表）。"""
        # TODO: 查询最近 7 天洞察
        return WeeklyInsightResponse(
            level="L2 Pattern",
            content="最近 3 周你每周五压力都会升高，可能是工作节奏的影响。建议周四提前调整...",
            period="7月8日 - 7月14日",
        )

    async def get_timeline(self, user_id: str, days: int = 365) -> dict:
        """获取 Timeline 热力图数据。"""
        # TODO: 聚合 mood_checkins 数据
        return {
            "days": days,
            "data": [],
        }

    async def generate_daily_insight(self, user_id: str) -> Optional[str]:
        """
        生成每日洞察（Cron Job 触发）。

        1. 聚合当日对话 + 情绪
        2. 调用 LLM 生成洞察
        3. 写入 growth_records 表
        4. 反哺记忆注入（P0-F02）
        """
        # TODO: 实现 LLM 洞察生成
        return None


growth_service = GrowthService()
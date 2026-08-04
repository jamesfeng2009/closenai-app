"""
成长服务 (P0-F05 / P0-F07)。

成长仪表盘 + 成长记录（每日洞察 / 周报 / 模式发现）。

数据来源于真实聚合：情绪签到、记忆、对话、成长记录。
"""
import logging
from datetime import timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.llm import llm
from backend.core.time_utils import (
    local_day_start_utc,
    local_now,
    local_today,
    safe_zoneinfo,
    utc_now,
)
from backend.models.schemas import (
    GrowthDashboardResponse,
    GrowthMetrics,
    WeeklyInsightResponse,
)
from backend.repositories import (
    chat_repository,
    growth_repository,
    memory_repository,
    mood_repository,
    user_repository,
)
from backend.services.user_service import user_service

logger = logging.getLogger("closenai.growth")

# 情绪值 → 热力图颜色
MOOD_COLORS = {
    5: "#E8F5E9",  # 开心
    4: "#C8E6C9",  # 平静
    3: "#FFF9C4",  # 疲惫
    2: "#FFE0B2",  # 焦虑
    1: "#FFCDD2",  # 难过/生气
}
EMPTY_COLOR = "#F5F5F5"


class GrowthService:
    """成长服务。"""

    async def get_dashboard(self, db: AsyncSession, user_id: str) -> GrowthDashboardResponse:
        """成长仪表盘（真实聚合）。"""
        user = await user_repository.get_or_create_user(db, user_id)
        chat_count = await chat_repository.count_total(db, user_id)

        # 今日情绪（按用户本地时区的日历日判断）
        today = local_today(user.timezone)
        today_mood = await mood_repository.get_today(
            db, user_id, local_day_start_utc(user.timezone, today)
        )
        today_mood_str = today_mood.mood_emoji if today_mood else None

        # 关系等级
        _level, label, progress = user_service.get_relationship_level(chat_count)

        # 记忆分类计数（成长指标）
        cat_counts = await memory_repository.count_by_category(db, user_id)

        # 14 天情绪热力图
        mood_timeline = await self._mood_timeline(db, user_id, user.timezone)

        # 成长指标（6 维）
        metrics = await self._growth_metrics(db, user_id, mood_timeline, cat_counts)

        # 上周洞察 + 昨日记录
        weekly = await growth_repository.get_latest(db, user_id, "weekly_summary")
        daily = await growth_repository.get_latest(db, user_id, "daily_insight")

        return GrowthDashboardResponse(
            date=today.isoformat(),
            greeting=self._greeting(user.language, user.timezone),
            streak_count=user.streak_count,
            relationship_level=label,
            level_label=label,
            level_progress=progress,
            chat_count=chat_count,
            today_mood=today_mood_str,
            growth_metrics=metrics,
            mood_timeline=mood_timeline,
            weekly_insight=weekly.content if weekly else None,
            yesterday_record=daily.content if daily else None,
            mbti_type=user.mbti_type,
        )

    async def get_weekly_insight(self, db: AsyncSession, user_id: str) -> WeeklyInsightResponse:
        """本周洞察：优先返回已生成的周报，否则生成。"""
        user = await user_repository.get_or_create_user(db, user_id)
        weekly = await growth_repository.get_latest(db, user_id, "weekly_summary")
        if weekly:
            return WeeklyInsightResponse(
                level="L2 Pattern",
                content=weekly.content,
                period=weekly.created_at.strftime("%Y-%m-%d"),
            )
        content = await self.generate_weekly_insight(db, user_id, "en")
        return WeeklyInsightResponse(
            level="L2 Pattern", content=content, period=local_today(user.timezone).isoformat()
        )

    async def get_timeline(self, db: AsyncSession, user_id: str, days: int = 365) -> dict:
        """365 天成长热力图：按天返回 (date, 情绪值, 颜色)（按用户本地日历日）。"""
        user = await user_repository.get_or_create_user(db, user_id)
        rows = await mood_repository.count_by_day(db, user_id, days, tz=user.timezone)
        day_map: dict = {}
        for day, emoji in rows:
            key = day.isoformat()
            val = self._emoji_value(emoji)
            if key not in day_map:
                day_map[key] = val
        # 补齐缺失日期为 0
        start = local_today(user.timezone) - timedelta(days=days - 1)
        result = []
        for i in range(days):
            d = start + timedelta(days=i)
            val = day_map.get(d.isoformat(), 0)
            result.append({"date": d.isoformat(), "value": val})
        return {"days": result}

    async def generate_daily_insight(self, db: AsyncSession, user_id: str, language: str = "en") -> str:
        """生成当日洞察（P0-F07），写入成长记录。"""
        user = await user_repository.get_or_create_user(db, user_id)
        today_mood = await mood_repository.get_today(
            db, user_id, local_day_start_utc(user.timezone)
        )
        mood_desc = f"今日情绪：{today_mood.mood_emoji} {today_mood.mood_label or ''}（{today_mood.ai_interpretation or ''}）" if today_mood else "今日暂无情绪签到。"
        memories = await memory_repository.list(db, user_id, limit=5)

        memory_desc = "\n".join(f"- {m.content}" for m in memories) or "暂无记忆。"
        prompt = (
            f"基于以下数据，用2-3句话为今天生成一条温暖、有洞察的成长记录（不要空泛）：\n"
            f"{mood_desc}\n最近记忆：\n{memory_desc}"
        )
        content = await self._llm_or_fallback(prompt, language)
        await growth_repository.create(db, user_id, "daily_insight", content, related_memory_ids=[m.id for m in memories])
        return content

    async def generate_weekly_insight(self, db: AsyncSession, user_id: str, language: str = "en") -> str:
        """生成本周洞察（周报），写入成长记录。"""
        await user_repository.get_or_create_user(db, user_id)
        records = await growth_repository.list_recent(db, user_id, days=7, limit=20)
        rec_desc = "\n".join(f"- {r.content}" for r in records) or "本周暂无成长记录。"
        prompt = (
            "基于本周的成长记录与情绪，生成一份简短的周报（3-4句话），"
            "总结本周模式、发现与下周建议，要具体、不空泛：\n"
            f"{rec_desc}"
        )
        content = await self._llm_or_fallback(prompt, language)
        await growth_repository.create(db, user_id, "weekly_summary", content)
        return content

    # ── P1-F03 高级洞察（周期报告）──

    PERIOD_LABELS = {
        "monthly": "月度报告",
        "quarterly": "季度深度报告",
        "yearly": "年度人生报告",
    }

    async def generate_report(
        self, db: AsyncSession, user_id: str, period: str, language: str = "en"
    ) -> dict:
        """
        生成周期成长报告（月 / 季 / 年）。

        数据来源（真实聚合）：
        - 情绪签到分布（mood_checkins）
        - 对话统计（chat_messages）
        - 成长记录（growth_records）
        - 记忆（memories）
        """
        await user_repository.get_or_create_user(db, user_id)
        days = {"monthly": 30, "quarterly": 90, "yearly": 365}.get(period, 30)
        start = utc_now() - timedelta(days=days)

        # 情绪签到分布
        checkins = await mood_repository.get_history(db, user_id, days=days)
        mood_dist = self._mood_distribution(checkins)

        # 对话统计
        chat_count = await chat_repository.count_total(db, user_id)
        recent_chats = await chat_repository.get_history(db, user_id, limit=20)
        user_chats = [m for m in recent_chats if m.role == "user"]

        # 成长记录
        records = await growth_repository.list_recent(db, user_id, days=days, limit=30)

        # 记忆
        memories = await memory_repository.list(db, user_id, limit=20)

        # 洞察等级（按累计天数自动晋升）
        insight_level = self._insight_level(user_id, chat_count)

        data = {
            "period": period,
            "period_label": self.PERIOD_LABELS.get(period, "报告"),
            "range": f"{start.date()} ~ {utc_now().date()}",
            "insight_level": insight_level,
            "mood_distribution": mood_dist,
            "mood_trend": self._mood_trend_text(mood_dist),
            "chat_count": chat_count,
            "chat_days": len({m.created_at.date() for m in recent_chats}),
            "top_topics": self._top_topics(user_chats),
            "memories": [m.content for m in memories[:10]],
            "records": [r.content for r in records[:10]],
        }

        # LLM 生成洞察正文（失败回退模板）
        content = await self._generate_report_body(db, user_id, period, data, language)

        # 写入成长记录
        record_type = f"{period}_report"
        await growth_repository.create(
            db, user_id, record_type, content, related_memory_ids=[m.id for m in memories[:5]]
        )

        return {
            "period": period,
            "period_label": data["period_label"],
            "range": data["range"],
            "insight_level": insight_level,
            "mood_distribution": mood_dist,
            "mood_trend": data["mood_trend"],
            "chat_count": chat_count,
            "content": content,
        }

    async def generate_pattern_discovery(
        self, db: AsyncSession, user_id: str, language: str = "en"
    ) -> str:
        """跨维度模式发现（P1-F03）：AI 基于情绪 + 记忆 + 对话发现关联模式。"""
        user = await user_repository.get_or_create_user(db, user_id)
        checkins = await mood_repository.get_history(db, user_id, days=90)
        memories = await memory_repository.list(db, user_id, limit=20)
        chats = await chat_repository.get_history(db, user_id, limit=30)

        mood_desc = self._mood_distribution(checkins)
        mem_desc = "\n".join(f"- {m.content}" for m in memories) or "暂无记忆"
        topic_desc = " ".join(m.content for m in chats if m.role == "user")[-400:] or "暂无对话"

        # 负面情绪出现的星期分布
        weekday_map = self._negative_weekday(checkins, user.timezone)

        prompt = (
            "你是 ClosenAI，一位温暖有洞察力的成长陪伴者。基于以下真实数据，发现用户跨维度的成长模式，"
            "输出 2-3 条具体、可行动的洞察（不要空泛，不要算命/占星措辞）：\n"
            f"近90天情绪分布：{mood_desc}\n"
            f"负面情绪出现的星期：{weekday_map}\n"
            f"记忆：{mem_desc}\n"
            f"最近对话话题：{topic_desc}"
        )
        try:
            text = await llm.complete(prompt, language=language, lite=True, max_tokens=220)
            return text.strip() or self._fallback_pattern(mood_desc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("模式发现 LLM 失败，回退模板: %s", exc)
            return self._fallback_pattern(mood_desc)

    async def _generate_report_body(
        self, db, user_id, period, data: dict, language: str
    ) -> str:
        """生成报告正文（LLM 优先，失败回退模板）。"""
        prompt = (
            f"你是一位温暖、有洞察力的成长教练。请为用户生成一份「{data['period_label']}」，"
            f"基于以下真实数据，用 3-6 句话总结，包含情绪趋势、模式发现、性格洞察与成长建议，"
            "要具体、有温度、不空泛，不使用占星/算命措辞：\n"
            f"情绪趋势：{data['mood_trend']}\n"
            f"情绪分布：{data['mood_distribution']}\n"
            f"对话次数：{data['chat_count']}，对话天数：{data['chat_days']}\n"
            f"高频话题：{data['top_topics']}\n"
            f"记忆：{'; '.join(data['memories'][:5]) or '暂无'}\n"
            f"成长记录：{'; '.join(data['records'][:5]) or '暂无'}"
        )
        try:
            text = await llm.complete(prompt, language=language, lite=True, max_tokens=300)
            return text.strip() or self._fallback_report(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("报告生成 LLM 失败，回退模板: %s", exc)
            return self._fallback_report(data)

    def _mood_distribution(self, checkins) -> dict:
        """统计情绪分布（按标签计数）。"""
        from backend.services.mood_service import MOOD_MAP
        dist: dict = {}
        for c in checkins:
            label = c.mood_label or MOOD_MAP.get(c.mood_emoji, ("未知", 3))[0]
            dist[label] = dist.get(label, 0) + 1
        return dist

    def _mood_trend_text(self, dist: dict) -> str:
        if not dist:
            return "该时段暂无情绪签到。"
        total = sum(dist.values())
        parts = [f"{k} {v}天" for k, v in sorted(dist.items(), key=lambda x: -x[1])]
        return f"共 {total} 次签到，分布：{'，'.join(parts)}。"

    def _negative_weekday(self, checkins, tz: str | None = None) -> str:
        """负面情绪（值<=2）出现的星期分布（按用户本地日历日）。"""
        from backend.services.mood_service import MOOD_MAP
        WEEK = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        zone = safe_zoneinfo(tz)
        counts: dict = {}
        for c in checkins:
            val = MOOD_MAP.get(c.mood_emoji, (None, 3))[1]
            if val <= 2:
                aware = c.created_at if c.created_at.tzinfo else c.created_at.replace(tzinfo=timezone.utc)
                wd = aware.astimezone(zone).weekday()
                counts[WEEK[wd]] = counts.get(WEEK[wd], 0) + 1
        if not counts:
            return "无负面情绪记录"
        return "、".join(f"{k}{v}次" for k, v in sorted(counts.items(), key=lambda x: -x[1]))

    def _top_topics(self, chat_list) -> str:
        """提取高频话题（取前 3 条用户消息的短文本）。"""
        texts = [m.content for m in chat_list if m.content][:3]
        return "；".join(t[:40] for t in texts) or "暂无"

    def _insight_level(self, user_id: str, chat_count: int) -> str:
        """洞察等级自动晋升：L1 观察 / L2 模式 / L3 预测 / L4 教练。"""
        if chat_count >= 90:
            return "L4 Coaching"
        if chat_count >= 60:
            return "L3 Forecast"
        if chat_count >= 21:
            return "L2 Pattern"
        return "L1 Observation"

    def _fallback_report(self, data: dict) -> str:
        return (
            f"这段时间你累计签到 {data['mood_distribution'] and sum(data['mood_distribution'].values()) or 0} 次，"
            f"对话 {data['chat_count']} 次。你正在一点点更了解自己，继续记录，模式会逐渐浮现。"
        )

    def _fallback_pattern(self, mood_desc: str) -> str:
        return f"基于你的情绪记录（{mood_desc}），你正在慢慢建立对自我状态的觉察。继续记录，AI 会帮你发现更多模式。"

    async def _llm_or_fallback(self, prompt: str, language: str) -> str:
        """LLM 生成，失败回退规则模板。"""
        try:
            text = await llm.complete(prompt, language=language, lite=True, max_tokens=200)
            return text.strip() or "你正在一点点理解自己，继续记录吧。"
        except Exception as exc:  # noqa: BLE001
            logger.warning("成长洞察 LLM 失败，回退模板: %s", exc)
            return "这段时间你正在慢慢更了解自己，继续记录，模式会浮现。"

    async def _mood_timeline(self, db: AsyncSession, user_id: str, tz: str | None = None) -> list:
        """最近 14 天情绪热力图颜色（按用户本地日历日）。"""
        rows = await mood_repository.count_by_day(db, user_id, 14, tz=tz)
        day_map: dict = {}
        for day, emoji in rows:
            key = day.isoformat()
            if key not in day_map:
                day_map[key] = self._emoji_value(emoji)
        start = local_today(tz) - timedelta(days=13)
        colors = []
        for i in range(14):
            d = start + timedelta(days=i)
            val = day_map.get(d.isoformat(), 0)
            colors.append(MOOD_COLORS.get(val, EMPTY_COLOR))
        return colors

    async def _growth_metrics(self, db, user_id, mood_timeline, cat_counts) -> GrowthMetrics:
        """6 维成长指标（由情绪 + 记忆派生，默认方案）。"""
        recent = await mood_repository.get_recent(db, user_id, limit=14)
        values = [self._emoji_value(r.mood_emoji) for r in recent]
        avg_recent = sum(values) / len(values) if values else 0
        prev = values[:7]
        older = values[7:14]
        avg_prev = sum(older) / len(older) if older else 0
        emotion_change = round((avg_recent - avg_prev) * 100) if avg_prev else 0

        negative_ratio = round(sum(1 for v in values if v <= 2) / len(values) * 100) if values else 0
        rel_count = cat_counts.get("relationship", 0)
        goal_count = cat_counts.get("goal", 0)
        curiosity_count = cat_counts.get("preference", 0) + cat_counts.get("values", 0)

        return GrowthMetrics(
            emotion=self._emotion_label(avg_recent),
            emotion_change=emotion_change,
            social=f"{rel_count} 段关系" if rel_count else "建立联系",
            social_change="+",
            confidence=self._confidence_label(avg_recent, negative_ratio),
            confidence_change="-",
            stress=f"{negative_ratio}% 压力" if negative_ratio else "状态平稳",
            stress_change=float(negative_ratio),
            curiosity=f"{curiosity_count} 个探索点" if curiosity_count else "探索中",
            curiosity_change="+",
            goals=f"{goal_count} 个目标" if goal_count else "规划中",
            goals_change="+",
        )

    def _emoji_value(self, emoji: str) -> int:
        from backend.services.mood_service import MOOD_MAP
        return MOOD_MAP.get(emoji, (None, 3))[1]

    def _emotion_label(self, avg: float) -> str:
        if avg >= 4:
            return "开心"
        if avg >= 3:
            return "平稳"
        if avg > 0:
            return "低落"
        return "待记录"

    def _confidence_label(self, avg: float, negative_ratio: int) -> str:
        if avg >= 4 and negative_ratio < 30:
            return "自信"
        if avg >= 3:
            return "稳定"
        return "成长中"

    def _greeting(self, language: str, tz: str | None = None) -> str:
        """按用户本地小时生成问候语（不能用服务器时区）。"""
        hour = local_now(tz).hour
        if language == "zh":
            if hour < 6:
                return "夜深了，还在忙吗？"
            if hour < 12:
                return "早上好！今天过得怎么样？"
            if hour < 18:
                return "下午好，记得照顾好自己。"
            return "晚上好，今天辛苦了。"
        return "Hello! How are you today?"


growth_service = GrowthService()
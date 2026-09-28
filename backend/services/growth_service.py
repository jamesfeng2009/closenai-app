"""
成长服务 (P0-F05 / P0-F07)。

成长仪表盘 + 成长记录（每日洞察 / 周报 / 模式发现）。

数据来源于真实聚合：情绪签到、记忆、对话、成长记录。
"""
import json
import logging
from datetime import date, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.errors import AppException, ErrorCode
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
    milestone_repository,
    mood_repository,
    user_repository,
)
from backend.services.safety_service import safety_service
from backend.services.quota_service import FREE_TIER
from backend.services.user_service import user_service

logger = logging.getLogger("closenai.growth")

# 报告周期白名单（非法 period → 400）；月/季/年报为 Pro 权益（PRD P1-F02 §3），与 share_service 同口径
REPORT_PERIODS = ("weekly", "monthly", "quarterly", "yearly")
PRO_REPORT_PERIODS = ("monthly", "quarterly", "yearly")

# 事件后主动跟进的记录类型（PRD P0-F07：用户写下的反思 / 重要事件记录）
FOLLOW_UP_RECORD_TYPES = ("reflection", "milestone")


def _is_zh(language: str | None) -> bool:
    """zh*（含 zh-CN）走中文模板，其余语言走英文。"""
    return (language or "").startswith("zh")


# 情绪值 → 热力图颜色
MOOD_COLORS = {
    5: "#E8F5E9",  # 开心
    4: "#C8E6C9",  # 平静
    3: "#FFF9C4",  # 疲惫
    2: "#FFE0B2",  # 焦虑
    1: "#FFCDD2",  # 难过/生气
}
EMPTY_COLOR = "#F5F5F5"

# ── 记录标签体系（P0-F07 §6）：规则打标，零 LLM 成本 ──
# 标签取 PRD 规定的中文规范名；匹配关键词中英双语（内容语言不定）。
RECORD_TAG_RULES = {
    "情绪": [
        "情绪", "心情", "感受", "开心", "难过", "焦虑", "愤怒", "生气", "压力", "平静",
        "mood", "feel", "anxious", "sad", "happy", "angry", "stress", "calm",
    ],
    "关系": [
        "关系", "朋友", "家人", "伴侣", "同事", "父母", "恋爱", "社交",
        "relationship", "friend", "family", "partner", "colleague", "social",
    ],
    "工作": [
        "工作", "职场", "加班", "老板", "项目", "面试", "升职", "离职",
        "work", "job", "career", "boss", "deadline", "interview", "project",
    ],
    "目标": [
        "目标", "计划", "习惯", "坚持", "打卡", "决心",
        "goal", "plan", "habit", "resolution",
    ],
    "沟通": [
        "沟通", "表达", "争吵", "吵架", "误解", "倾听",
        "communication", "argue", "argument", "conflict", "express",
    ],
    "成长": [
        "成长", "学习", "改变", "进步", "反思", "收获",
        "growth", "learn", "improve", "change", "reflect",
    ],
}


def tag_record_content(content: str) -> list[str]:
    """规则打标：按关键词命中为记录内容标注标签（零 LLM 成本）。"""
    text = (content or "").lower()
    return [tag for tag, kws in RECORD_TAG_RULES.items() if any(kw.lower() in text for kw in kws)]


# 高频标签 → 话题推荐文案（P0-F07 §4：洞察标签 → 话题推荐）
TOPIC_SUGGESTIONS = {
    "情绪": {"zh": "聊聊最近的情绪变化", "en": "Talk about how your mood has been lately"},
    "关系": {"zh": "聊聊最近的人际相处", "en": "Talk about your recent relationships"},
    "工作": {"zh": "聊聊工作上的近况与压力", "en": "Talk about how work is going"},
    "目标": {"zh": "聊聊目标的最新进展", "en": "Talk about progress on your goals"},
    "沟通": {"zh": "聊聊最近的沟通困扰", "en": "Talk about recent communication challenges"},
    "成长": {"zh": "聊聊最近的成长与收获", "en": "Talk about your recent growth"},
}

# 反哺上下文中的记录类型标注（按语言）
INSIGHT_TYPE_LABELS = {
    "zh": {
        "daily_insight": "每日洞察",
        "weekly_summary": "周报",
        "pattern_discovery": "模式发现",
        "drift_alert": "变化提醒",
        "weekly_report": "周报",
        "monthly_report": "月度报告",
        "quarterly_report": "季度报告",
        "yearly_report": "年度报告",
    },
    "en": {
        "daily_insight": "Daily insight",
        "weekly_summary": "Weekly summary",
        "pattern_discovery": "Pattern discovery",
        "drift_alert": "Drift alert",
        "weekly_report": "Weekly report",
        "monthly_report": "Monthly report",
        "quarterly_report": "Quarterly report",
        "yearly_report": "Yearly report",
    },
}


class GrowthService:
    """成长服务。"""

    async def get_dashboard(self, db: AsyncSession, user_id: str) -> GrowthDashboardResponse:
        """成长仪表盘（真实聚合）。"""
        user = await user_repository.require_user(db, user_id)
        chat_count = await chat_repository.count_total(db, user_id)

        # 今日情绪（按用户本地时区的日历日判断）
        today = local_today(user.timezone)
        today_mood = await mood_repository.get_today(
            db, user_id, local_day_start_utc(user.timezone, today)
        )
        today_mood_str = today_mood.mood_emoji if today_mood else None

        # 关系等级（PRD P0-F05：按累计活跃天数晋升，与 update_streak 写入的 users.relationship_level 同源；
        # 展示名按用户语言本地化）
        level, _canonical, progress = user_service.get_relationship_level(user.active_days)
        label = user_service.level_label(level, user.language)

        # 月度冻结券发放（读路径保持展示值新鲜）+ 里程碑徽章（按当前 streak 幂等补齐）
        await user_service.ensure_freeze_grant(db, user)
        milestones = await user_service.get_milestones(db, user_id, user.language)

        # 记忆分类计数（成长指标）
        cat_counts = await memory_repository.count_by_category(db, user_id)

        # 14 天情绪热力图
        mood_timeline = await self._mood_timeline(db, user_id, user.timezone)

        # 成长指标（6 维）
        metrics = await self._growth_metrics(db, user_id, mood_timeline, cat_counts, user.language)

        # 上周洞察 + 昨日记录（时效校验：周报仅本周内有效，日报仅昨日/今日内有效，否则视为过期不展示）
        weekly = await growth_repository.get_latest(db, user_id, "weekly_summary")
        daily = await growth_repository.get_latest(db, user_id, "daily_insight")
        week_start = today - timedelta(days=today.weekday())  # 本周一（用户本地）
        weekly_content = (
            weekly.content
            if weekly and self._local_date(weekly.created_at, user.timezone) >= week_start
            else None
        )
        daily_content = (
            daily.content
            if daily and self._local_date(daily.created_at, user.timezone) >= today - timedelta(days=1)
            else None
        )

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
            weekly_insight=weekly_content,
            yesterday_record=daily_content,
            mbti_type=user.mbti_type,
            milestones=milestones,
            streak_freeze_count=user.streak_freeze_count,
        )

    def _local_date(self, dt, tz: str | None = None):
        """记录的 created_at（UTC）换算为用户本地日历日，用于时效判断。"""
        aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return aware.astimezone(safe_zoneinfo(tz)).date()

    async def get_weekly_insight(self, db: AsyncSession, user_id: str) -> WeeklyInsightResponse:
        """本周洞察：只读已生成的周报（生成由 cron 触发，GET 不写库）。

        无周报时返回 content=None，由前端展示空状态，避免 GET 产生写副作用。
        """
        await user_repository.require_user(db, user_id)
        weekly = await growth_repository.get_latest(db, user_id, "weekly_summary")
        # Insight Level 按用户实际累计对话量晋升（P1-F03 §1，不再硬编码）
        chat_count = await chat_repository.count_total(db, user_id)
        return WeeklyInsightResponse(
            level=self._insight_level(user_id, chat_count),
            content=weekly.content if weekly else None,
            period=weekly.created_at.strftime("%Y-%m-%d") if weekly else "",
        )

    async def get_recent_insights_context(
        self, db: AsyncSession, user_id: str, limit: int = 3, language: str = "zh"
    ) -> str:
        """最近 7 天洞察反哺上下文（P0-F07 §4 核心链路）。

        取最近 7 天内的成长记录（daily_insight / weekly_summary / drift_alert 等），
        按「[类型 日期] 内容」逐行标注后拼接为一段可直接注入 LLM prompt 的文本；
        无记录时返回空字符串（调用方据此跳过注入）。

        接入点（由对应任务负责方调用，本服务只提供承载方法）：
        - message_generator.generate_proactive_message（主动消息注入最近 3 条洞察）
        - message_generator._get_memory_context_str / chat 链路（记忆压缩注入最近 7 天洞察摘要）
        """
        user = await user_repository.get_user(db, user_id)
        tz = user.timezone if user else None
        records = await growth_repository.list_recent(db, user_id, days=7, limit=limit)
        if not records:
            return ""
        zh = _is_zh(language)
        labels = INSIGHT_TYPE_LABELS["zh" if zh else "en"]
        lines = [
            f"- [{labels.get(r.record_type, r.record_type)} "
            f"{self._local_date(r.created_at, tz).isoformat()}] {r.content}"
            for r in records
        ]
        header = "最近 7 天成长洞察：" if zh else "Growth insights from the past 7 days:"
        return header + "\n" + "\n".join(lines)

    async def get_timeline(
        self, db: AsyncSession, user_id: str, days: int = 365, language: str = "en"
    ) -> dict:
        """365 天成长热力图：按天返回 (date, 情绪值, 颜色)（按用户本地日历日）。

        附带已达成徽章的里程碑标注（PRD P0-F07 §3：里程碑在热力图上用特殊图标标注）。
        """
        user = await user_repository.require_user(db, user_id)
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
        milestones = await self._milestone_markers(db, user_id, user.timezone, language)
        return {"days": result, "milestones": milestones}

    async def _milestone_markers(
        self, db: AsyncSession, user_id: str, tz: str | None, language: str = "en"
    ) -> list[dict]:
        """热力图里程碑标注：已达成徽章 → 用户本地日历日 + 本地化徽章名。"""
        records = await milestone_repository.list_for_user(db, user_id)
        return [
            {
                "date": self._local_date(m.achieved_at, tz).isoformat(),
                "milestone_type": m.milestone_type,
                "name": user_service.milestone_name(m.milestone_type, language),
            }
            for m in records
        ]

    async def get_day_summary(
        self, db: AsyncSession, user_id: str, day: str, language: str = "en"
    ) -> dict:
        """单日摘要卡片（PRD P0-F07 §2：点击单日弹出，不跳转页面）。

        内容：情绪类型 + 当日记录（每日洞察即 AI 对话摘要）+ 关键事件（当日达成徽章）
        + 当日对话消息数。GET 只读、不触发 LLM；无任何数据时 has_data=False，
        由前端展示空状态。
        """
        from backend.services.mood_service import mood_label_for

        user = await user_repository.require_user(db, user_id)
        try:
            target = date.fromisoformat(day)
        except ValueError:
            raise AppException(ErrorCode.INVALID_PARAMS, f"无效的日期格式: {day}")
        start = local_day_start_utc(user.timezone, target)
        end = local_day_start_utc(user.timezone, target + timedelta(days=1))

        # 情绪签到（checkin_date 即用户本地日历日）
        checkin = await mood_repository.get_by_date(db, user_id, target)
        mood = None
        if checkin:
            mood = {
                "emoji": checkin.mood_emoji,
                "label": checkin.mood_label or mood_label_for(checkin.mood_emoji, language),
                "value": self._emoji_value(checkin.mood_emoji),
                "interpretation": checkin.ai_interpretation,
            }

        # 当日成长记录（每日洞察 = AI 对话摘要）
        records = await growth_repository.list_between(db, user_id, start, end)
        # 当日对话消息数
        chat_count = await chat_repository.count_between(db, user_id, start, end)
        # 关键事件：当日达成的徽章
        markers = await self._milestone_markers(db, user_id, user.timezone, language)
        day_milestones = [m for m in markers if m["date"] == target.isoformat()]

        return {
            "date": target.isoformat(),
            "has_data": bool(checkin or records or chat_count or day_milestones),
            "mood": mood,
            "color": MOOD_COLORS.get(mood["value"], EMPTY_COLOR) if mood else EMPTY_COLOR,
            "chat_count": chat_count,
            "records": [
                {
                    "record_type": r.record_type,
                    "content": r.content,
                    "tags": self._record_tags(r),
                }
                for r in records
            ],
            "milestones": day_milestones,
        }

    async def get_relationship_timeline(
        self, db: AsyncSession, user_id: str, language: str = "en", limit: int = 50
    ) -> dict:
        """关系时间线（PRD P0-F07 §7）。

        顶部统计：认识第 N 天（自注册本地日历日起）· 聊了 N 次 · 共同经历 N 个时刻；
        事件流按时间倒序：里程碑徽章 + 成长记录（每日洞察/周报/报告）。
        """
        user = await user_repository.require_user(db, user_id)
        today = local_today(user.timezone)
        days_known = (today - self._local_date(user.created_at, user.timezone)).days + 1
        chat_count = await chat_repository.count_total(db, user_id)

        events: list[dict] = []
        for m in await milestone_repository.list_for_user(db, user_id):
            events.append({
                "type": "milestone",
                "milestone_type": m.milestone_type,
                "title": user_service.milestone_name(m.milestone_type, language),
                "date": self._local_date(m.achieved_at, user.timezone).isoformat(),
                "created_at": m.achieved_at.isoformat() if m.achieved_at else None,
            })
        records = await growth_repository.list_recent(db, user_id, days=365, limit=limit)
        labels = INSIGHT_TYPE_LABELS["zh" if _is_zh(language) else "en"]
        for r in records:
            events.append({
                "type": "record",
                "record_type": r.record_type,
                "title": labels.get(r.record_type, r.record_type),
                "content": r.content,
                "tags": self._record_tags(r),
                "date": self._local_date(r.created_at, user.timezone).isoformat(),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        events.sort(key=lambda e: e["created_at"] or "", reverse=True)
        events = events[:limit]

        return {
            "days_known": days_known,
            "chat_count": chat_count,
            "event_count": len(events),
            "level": user_service.get_level_info(user.active_days, language),
            "events": events,
        }

    async def generate_daily_insight(self, db: AsyncSession, user_id: str, language: str = "en") -> str:
        """生成当日洞察（P0-F07），写入成长记录。

        生成依据：当日情绪签到 + 最近记忆 + 当日用户对话摘要（喂当日对话，PRD §1：
        洞察基于当日对话内容，非模板生成）。写入时规则打标（§6），生成的记录经
        get_recent_insights_context 回流对话上下文，构成"反哺摘要"沉淀。
        """
        user = await user_repository.require_user(db, user_id)
        day_start = local_day_start_utc(user.timezone)
        today_mood = await mood_repository.get_today(db, user_id, day_start)
        mood_desc = f"今日情绪：{today_mood.mood_emoji} {today_mood.mood_label or ''}（{today_mood.ai_interpretation or ''}）" if today_mood else "今日暂无情绪签到。"
        memories = await memory_repository.list(db, user_id, limit=5)

        # 当日用户对话摘要（洞察必须基于当日对话内容）
        today_chats = await chat_repository.list_user_contents(
            db, user_id, day_start, utc_now(), limit=20
        )
        chat_desc = "\n".join(f"- {t[:80]}" for t in reversed(today_chats)) or "今日暂无对话。"

        memory_desc = "\n".join(f"- {m.content}" for m in memories) or "暂无记忆。"
        prompt = (
            f"基于以下数据，用2-3句话为今天生成一条温暖、有洞察的成长记录（不要空泛）：\n"
            f"{mood_desc}\n今日对话：\n{chat_desc}\n最近记忆：\n{memory_desc}"
        )
        content = await self._llm_or_fallback(prompt, language)
        tags = tag_record_content(f"{content} {mood_desc} {chat_desc}")
        await growth_repository.create(
            db, user_id, "daily_insight", content,
            related_memory_ids=[m.id for m in memories],
            tags=tags,
        )
        return content

    async def generate_weekly_insight(self, db: AsyncSession, user_id: str, language: str = "en") -> str:
        """生成本周洞察（周报），写入成长记录。

        内容：本周模式总结 + 下周预测段（P1-F03 §7，LLM lite，失败省略该段）。
        同步沉淀：Insight Level（按累计对话晋升）、结构化摘要 metadata_json
        （mood_distribution/top_topics/forecast，P1-F03 §1）、记录标签。
        """
        user = await user_repository.require_user(db, user_id)
        records = await growth_repository.list_recent(db, user_id, days=7, limit=20)
        rec_desc = "\n".join(f"- {r.content}" for r in records) or "本周暂无成长记录。"
        prompt = (
            "基于本周的成长记录与情绪，生成一份简短的周报（3-4句话），"
            "总结本周模式、发现与下周建议，要具体、不空泛：\n"
            f"{rec_desc}"
        )
        content = await self._llm_or_fallback(prompt, language)

        # 结构化摘要：本周情绪分布 + 高频话题
        checkins = await mood_repository.get_history(db, user_id, days=7)
        mood_dist = self._mood_distribution(checkins, language)
        recent_chats = await chat_repository.get_history(db, user_id, limit=20)
        top_topics = [m.content[:40] for m in recent_chats if m.role == "user" and m.content][:3]

        # 预测性洞察：下周预测段（失败安全省略，不影响周报主体）
        forecast = await self._generate_next_week_forecast(db, user_id, user.timezone, language)
        if forecast:
            label = "下周预测" if _is_zh(language) else "Next week forecast"
            content = f"{content}\n\n【{label}】{forecast}"

        # Insight Level 按累计对话量晋升（P1-F03 §1）
        chat_count = await chat_repository.count_total(db, user_id)
        metadata = {
            "insight_level": self._insight_level(user_id, chat_count),
            "mood_distribution": mood_dist,
            "top_topics": top_topics,
            "forecast": forecast,
        }
        tags = tag_record_content(f"{content} {rec_desc}")
        await growth_repository.create(
            db, user_id, "weekly_summary", content,
            tags=tags,
            metadata_json=json.dumps(metadata, ensure_ascii=False),
        )
        return content

    async def _generate_next_week_forecast(
        self,
        db: AsyncSession,
        user_id: str,
        tz: str | None,
        language: str,
    ) -> str | None:
        """下周预测段（P1-F03 §7 预测性洞察）：基于近 14 天情绪趋势与星期模式，
        LLM lite 生成 1-2 句预警/建议；数据不足或 LLM 失败返回 None（周报省略该段）。
        """
        history = await mood_repository.get_history(db, user_id, days=14)
        if len(history) < 3:
            return None
        mood_desc = self._mood_distribution(history, language)
        weekday_map = self._negative_weekday(history, tz)
        prompt = (
            "基于以下真实情绪数据，给出 1-2 句对下周的预测与可行动建议"
            "（指出具体星期/趋势，不空泛，不使用占星/算命措辞）：\n"
            f"近14天情绪分布：{mood_desc}\n"
            f"负面情绪出现的星期：{weekday_map}"
        )
        try:
            text = await llm.complete(prompt, language=language, lite=True, max_tokens=120)
            # L3 输出过滤：命中则周报省略预测段
            text = await safety_service.check_or(text.strip(), language, "")
            return text or None
        except Exception as exc:  # noqa: BLE001
            logger.warning("下周预测 LLM 失败，周报省略预测段: %s", exc)
            return None

    # ── P0-F07 §6 成长记录页：标签筛选 / 话题推荐 / 回忆卡片 ──

    def _record_tags(self, record) -> list[str]:
        """记录标签：优先取落库标签；历史无标签记录按内容即时规则打标（免回填）。"""
        if record.tags:
            return [t for t in record.tags.split(",") if t]
        return tag_record_content(record.content)

    async def list_records(
        self,
        db: AsyncSession,
        user_id: str,
        days: int = 30,
        limit: int = 20,
        tag: str | None = None,
    ) -> list[dict]:
        """成长记录列表（成长记录页）：按时间倒序，支持按标签筛选。"""
        await user_repository.require_user(db, user_id)
        records = await growth_repository.list_recent(db, user_id, days=days, limit=100)
        items = []
        for r in records:
            tags = self._record_tags(r)
            if tag and tag not in tags:
                continue
            items.append({
                "id": r.id,
                "record_type": r.record_type,
                "content": r.content,
                "tags": tags,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
            if len(items) >= limit:
                break
        return items

    async def get_recommended_topics(
        self, db: AsyncSession, user_id: str, language: str = "en", days: int = 30, limit: int = 5
    ) -> list[dict]:
        """推荐话题（P0-F07 §4：洞察标签 → 话题推荐）。

        规则实现，零 LLM 成本：聚合近期记录的高频标签，映射为话题方向。
        """
        await user_repository.require_user(db, user_id)
        records = await growth_repository.list_recent(db, user_id, days=days, limit=100)
        counts: dict = {}
        for r in records:
            for t in self._record_tags(r):
                counts[t] = counts.get(t, 0) + 1
        lang = "zh" if _is_zh(language) else "en"
        return [
            {"tag": t, "count": c, "topic": TOPIC_SUGGESTIONS.get(t, {}).get(lang, t)}
            for t, c in sorted(counts.items(), key=lambda x: -x[1])[:limit]
        ]

    async def get_memory_card(
        self, db: AsyncSession, user_id: str, days_ago: int = 30, language: str = "en"
    ) -> dict:
        """回忆卡片（P0-F07 §7）：取 N 天前今日的记录/签到/记忆，生成"去年今日"式卡片。

        卡片正文由 LLM lite 生成（失败回退模板）；当日无任何数据时 has_data=False，
        不生成正文，由前端展示空状态。
        """
        user = await user_repository.require_user(db, user_id)
        target = local_today(user.timezone) - timedelta(days=days_ago)
        start = local_day_start_utc(user.timezone, target)
        end = local_day_start_utc(user.timezone, target + timedelta(days=1))

        records = await growth_repository.list_between(db, user_id, start, end)
        memories = await memory_repository.list_between(db, user_id, start, end, limit=5)

        # 当日情绪签到（取当天最近一条）
        mood = None
        checkins = await mood_repository.get_history(db, user_id, days=days_ago + 1)
        for c in checkins:
            if self._local_date(c.created_at, user.timezone) == target:
                mood = {
                    "emoji": c.mood_emoji,
                    "label": c.mood_label,
                    "interpretation": c.ai_interpretation,
                }
                break

        has_data = bool(records or memories or mood)
        card_text = None
        if has_data:
            zh = _is_zh(language)
            mood_desc = f"情绪：{mood['emoji']} {mood.get('label') or ''}" if mood else ("无签到" if zh else "No check-in")
            rec_desc = "；".join(r.content[:60] for r in records[:3]) or ("无记录" if zh else "No records")
            mem_desc = "；".join(m.content[:60] for m in memories[:3]) or ("无记忆" if zh else "No memories")
            if zh:
                prompt = (
                    f"基于以下 {days_ago} 天前（{target.isoformat()}）的数据，生成一段温暖的"
                    f"「回忆卡片」文案（2-3 句），带用户重温那一天的自己，不要空泛：\n"
                    f"{mood_desc}\n当日记录：{rec_desc}\n当日记忆：{mem_desc}"
                )
            else:
                prompt = (
                    f"Based on the following data from {days_ago} days ago ({target.isoformat()}), "
                    "write a warm 2-3 sentence memory card that helps the user revisit who they "
                    f"were that day. Be specific, not generic:\n"
                    f"{mood_desc}\nRecords: {rec_desc}\nMemories: {mem_desc}"
                )
            card_text = await self._llm_or_fallback(prompt, language)

        return {
            "date": target.isoformat(),
            "days_ago": days_ago,
            "has_data": has_data,
            "mood": mood,
            "records": [{"record_type": r.record_type, "content": r.content} for r in records],
            "memories": [m.content for m in memories],
            "card_text": card_text,
        }

    # ── P0-F07 事件后主动跟进（3-7 天窗口）──

    async def get_pending_follow_ups(self, db: AsyncSession, limit: int = 200) -> list:
        """查询跟进窗口内未发送跟进消息的成长记录（跨用户，每日 cron 扫描）。

        窗口由配置项 follow_up_window_start_days ~ follow_up_window_end_days
        决定（默认 3-7 天前），按 UTC 滚动窗口比较 created_at（精确到秒，
        不涉及本地日历日换算）；记录类型限定 FOLLOW_UP_RECORD_TYPES。
        """
        now = utc_now()
        start = now - timedelta(days=settings.follow_up_window_end_days)
        end = now - timedelta(days=settings.follow_up_window_start_days)
        return await growth_repository.list_pending_follow_ups(
            db, FOLLOW_UP_RECORD_TYPES, start, end, limit=limit
        )

    async def mark_follow_up_sent(self, db: AsyncSession, record_id: str) -> None:
        """标记记录已发送跟进消息（防 cron 重复触发导致重复发送）。"""
        await growth_repository.mark_follow_up_sent(db, record_id)

    # ── P1-F03 高级洞察（周期报告）──

    PERIOD_LABELS = {
        "zh": {
            "weekly": "每周洞察",
            "monthly": "月度报告",
            "quarterly": "季度深度报告",
            "yearly": "年度人生报告",
        },
        "en": {
            "weekly": "Weekly Insight",
            "monthly": "Monthly Report",
            "quarterly": "Quarterly Deep-Dive Report",
            "yearly": "Yearly Life Report",
        },
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

        门控：period 白名单校验（400）；月/季/年报为 Pro 权益（40302），
        与 share_service.export_report 同一口径，防止绕过分享端点的 Pro 门。
        """
        if period not in REPORT_PERIODS:
            raise AppException(ErrorCode.INVALID_PARAMS, f"无效的报告周期: {period}")
        user = await user_repository.require_user(db, user_id)
        if period in PRO_REPORT_PERIODS and user.tier == FREE_TIER:
            raise AppException(ErrorCode.PRO_REQUIRED, "该周期报告为 Pro 订阅权益")
        days = {"weekly": 7, "monthly": 30, "quarterly": 90, "yearly": 365}[period]
        start = utc_now() - timedelta(days=days)

        # 情绪签到分布
        checkins = await mood_repository.get_history(db, user_id, days=days)
        mood_dist = self._mood_distribution(checkins, language)

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
            "period_label": self._period_label(period, language),
            "range": f"{start.date()} ~ {utc_now().date()}",
            "insight_level": insight_level,
            "mood_distribution": mood_dist,
            "mood_trend": self._mood_trend_text(mood_dist, language),
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
        user = await user_repository.require_user(db, user_id)
        checkins = await mood_repository.get_history(db, user_id, days=90)
        memories = await memory_repository.list(db, user_id, limit=20)
        chats = await chat_repository.get_history(db, user_id, limit=30)

        mood_desc = self._mood_distribution(checkins, language)
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
            # L3 输出过滤：命中回退模板
            text = await safety_service.check_or(text.strip(), language, "")
            return text or self._fallback_pattern(mood_desc, language)
        except Exception as exc:  # noqa: BLE001
            logger.warning("模式发现 LLM 失败，回退模板: %s", exc)
            return self._fallback_pattern(mood_desc, language)

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
            # L3 输出过滤：命中回退模板
            text = await safety_service.check_or(text.strip(), language, "")
            return text or self._fallback_report(data, language)
        except Exception as exc:  # noqa: BLE001
            logger.warning("报告生成 LLM 失败，回退模板: %s", exc)
            return self._fallback_report(data, language)

    def _period_label(self, period: str, language: str | None = None) -> str:
        """报告周期名称（按语言）。"""
        labels = self.PERIOD_LABELS["zh" if _is_zh(language) else "en"]
        return labels.get(period, "报告" if _is_zh(language) else "Report")

    def _mood_distribution(self, checkins, language: str | None = None) -> dict:
        """统计情绪分布（按标签计数，标签按语言）。"""
        from backend.services.mood_service import mood_label_for
        dist: dict = {}
        for c in checkins:
            label = c.mood_label or mood_label_for(c.mood_emoji, language)
            dist[label] = dist.get(label, 0) + 1
        return dist

    def _mood_trend_text(self, dist: dict, language: str | None = None) -> str:
        if not dist:
            return "该时段暂无情绪签到。" if _is_zh(language) else "No mood check-ins in this period."
        total = sum(dist.values())
        if _is_zh(language):
            parts = [f"{k} {v}天" for k, v in sorted(dist.items(), key=lambda x: -x[1])]
            return f"共 {total} 次签到，分布：{'，'.join(parts)}。"
        parts = [f"{k} {v}d" for k, v in sorted(dist.items(), key=lambda x: -x[1])]
        return f"{total} check-ins in total: {', '.join(parts)}."

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

    def _fallback_report(self, data: dict, language: str | None = None) -> str:
        checkins = sum(data['mood_distribution'].values()) if data['mood_distribution'] else 0
        if _is_zh(language):
            return (
                f"这段时间你累计签到 {checkins} 次，"
                f"对话 {data['chat_count']} 次。你正在一点点更了解自己，继续记录，模式会逐渐浮现。"
            )
        return (
            f"Over this period you checked in {checkins} times and had "
            f"{data['chat_count']} conversations. You're getting to know yourself bit by bit — "
            "keep recording and patterns will gradually emerge."
        )

    def _fallback_pattern(self, mood_desc: str, language: str | None = None) -> str:
        if _is_zh(language):
            return f"基于你的情绪记录（{mood_desc}），你正在慢慢建立对自我状态的觉察。继续记录，AI 会帮你发现更多模式。"
        return (
            f"Based on your mood records ({mood_desc}), you're gradually building awareness "
            "of your own state. Keep recording and the AI will help you discover more patterns."
        )

    async def _llm_or_fallback(self, prompt: str, language: str) -> str:
        """LLM 生成，失败回退规则模板（模板按语言）。"""
        zh = _is_zh(language)
        try:
            text = await llm.complete(prompt, language=language, lite=True, max_tokens=200)
            return text.strip() or (
                "你正在一点点理解自己，继续记录吧。" if zh
                else "You're understanding yourself bit by bit — keep recording."
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("成长洞察 LLM 失败，回退模板: %s", exc)
            return (
                "这段时间你正在慢慢更了解自己，继续记录，模式会浮现。" if zh
                else "You're slowly getting to know yourself better these days. "
                     "Keep recording and patterns will surface."
            )

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

    async def _growth_metrics(self, db, user_id, mood_timeline, cat_counts, language: str | None = None) -> GrowthMetrics:
        """6 维成长指标（由情绪 + 记忆派生，默认方案；标签按语言）。"""
        zh = _is_zh(language)
        recent = await mood_repository.get_recent(db, user_id, limit=14)
        values = [self._emoji_value(r.mood_emoji) for r in recent]
        # get_recent 按时间倒序：前 7 条为"近 7 天"，后 7 条为"前 7 天"
        recent_week = values[:7]
        prev_week = values[7:14]
        avg_recent = sum(recent_week) / len(recent_week) if recent_week else 0
        avg_prev = sum(prev_week) / len(prev_week) if prev_week else 0
        emotion_change = round((avg_recent - avg_prev) * 100) if avg_prev else 0

        negative_ratio = round(sum(1 for v in values if v <= 2) / len(values) * 100) if values else 0
        rel_count = cat_counts.get("relationship", 0)
        goal_count = cat_counts.get("goal", 0)
        curiosity_count = cat_counts.get("preference", 0) + cat_counts.get("values", 0)

        return GrowthMetrics(
            emotion=self._emotion_label(avg_recent, language),
            emotion_change=emotion_change,
            social=(f"{rel_count} 段关系" if rel_count else "建立联系") if zh
                else (f"{rel_count} relationships" if rel_count else "Building connections"),
            social_change="+",
            confidence=self._confidence_label(avg_recent, negative_ratio, language),
            confidence_change="-",
            stress=(f"{negative_ratio}% 压力" if negative_ratio else "状态平稳") if zh
                else (f"{negative_ratio}% stress" if negative_ratio else "Steady"),
            stress_change=float(negative_ratio),
            curiosity=(f"{curiosity_count} 个探索点" if curiosity_count else "探索中") if zh
                else (f"{curiosity_count} explorations" if curiosity_count else "Exploring"),
            curiosity_change="+",
            goals=(f"{goal_count} 个目标" if goal_count else "规划中") if zh
                else (f"{goal_count} goals" if goal_count else "Planning"),
            goals_change="+",
        )

    def _emoji_value(self, emoji: str) -> int:
        from backend.services.mood_service import MOOD_MAP
        return MOOD_MAP.get(emoji, (None, 3))[1]

    def _emotion_label(self, avg: float, language: str | None = None) -> str:
        zh = _is_zh(language)
        if avg >= 4:
            return "开心" if zh else "Happy"
        if avg >= 3:
            return "平稳" if zh else "Steady"
        if avg > 0:
            return "低落" if zh else "Low"
        return "待记录" if zh else "No data yet"

    def _confidence_label(self, avg: float, negative_ratio: int, language: str | None = None) -> str:
        zh = _is_zh(language)
        if avg >= 4 and negative_ratio < 30:
            return "自信" if zh else "Confident"
        if avg >= 3:
            return "稳定" if zh else "Stable"
        return "成长中" if zh else "Growing"

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
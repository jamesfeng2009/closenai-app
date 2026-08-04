"""
情绪签到服务 (P0-F03)。

核心链路：情绪签到 → 记录 memory → 更新 Streak → 生成 AI 解读。

性能策略：
- 先写库 + 规则引擎给出确定性解读（保证 <2s 响应）
- 可选 LLM 深度解读（lite 模型 + 小 max_tokens），失败自动回退规则引擎
"""
import logging
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.errors import AppException, ErrorCode
from backend.core.llm import llm
from backend.core.time_utils import local_day_start_utc
from backend.models.schemas import (
    MoodCheckinResponse,
    MoodHistoryResponse,
    MoodTrendResponse,
)
from backend.repositories import chat_repository, mood_repository, user_repository
from backend.services.user_service import user_service

logger = logging.getLogger("closenai.mood")

# 情绪 → (标签, 情绪值 1-5)
MOOD_MAP = {
    "😊": ("开心", 5),
    "😌": ("平静", 4),
    "😰": ("焦虑", 2),
    "😢": ("难过", 1),
    "😡": ("生气", 1),
    "😴": ("疲惫", 3),
}

# 规则引擎解读模板（确定性，保证响应速度）
RULE_INTERPRET = {
    "😊": "今天看起来心情不错，继续保持这份好心情！",
    "😌": "平静的一天，很稳。记得给自己留一点独处的时间。",
    "😰": "感到有些焦虑，这很正常。先深呼吸，慢慢来。",
    "😢": "有点难过，我在这里陪着你。允许自己有情绪。",
    "😡": "感到生气了吗？先别急着做决定，把情绪放一放。",
    "😴": "有点疲惫，好好休息也很重要，别太累自己。",
}


class MoodService:
    """情绪签到服务。"""

    async def checkin(
        self,
        db: AsyncSession,
        user_id: str,
        emoji: str,
        note: Optional[str],
        language: str = "en",
    ) -> MoodCheckinResponse:
        """执行情绪签到。"""
        if emoji not in MOOD_MAP:
            raise AppException(ErrorCode.INVALID_EMOJI, "无效的情绪表情")

        # 0. 确保用户存在（后续时区/配额判断依赖 user 记录）
        user = await user_repository.get_or_create_user(db, user_id)
        day_start = local_day_start_utc(user.timezone)

        # 1. 今日唯一：已签到则拒绝（去重，按用户本地日历日）
        existing = await mood_repository.get_today(db, user_id, day_start)
        if existing:
            raise AppException(ErrorCode.ALREADY_CHECKED_IN, "今日已签到")

        # 2. 配额：免费用户每日 5 条（含主动消息 + 签到解读 + 对话）
        if user.tier == "free":
            chat_used = await chat_repository.count_today(db, user_id, role="user", start=day_start)
            proactive_used = await chat_repository.count_today(
                db, user_id, role="assistant", message_type="proactive", start=day_start
            )
            if chat_used + proactive_used >= settings.free_daily_messages:
                raise AppException(ErrorCode.QUOTA_EXCEEDED, "今天的次数已经用完，请明天再来")

        # 3. 更新 Streak
        streak = await user_service.update_streak(db, user_id)

        label, _value = MOOD_MAP[emoji]

        # 3. 生成 AI 解读（规则引擎即时 + LLM 深度解读，失败回退）
        interpretation = await self._interpret(db, user_id, emoji, note, language, label)

        # 4. 写库
        record = await mood_repository.create(
            db, user_id, emoji, label, interpretation
        )

        # 5. 若带备注，沉淀为一条记忆（记忆注入）
        if note and note.strip():
            await self._record_memory(db, user_id, note, emoji, label)

        return MoodCheckinResponse(
            id=record.id,
            mood_emoji=record.mood_emoji,
            mood_label=record.mood_label,
            ai_interpretation=record.ai_interpretation,
            streak_updated=streak["streak_count"],
            created_at=record.created_at,
        )

    async def _interpret(
        self,
        db: AsyncSession,
        user_id: str,
        emoji: str,
        note: Optional[str],
        language: str,
        label: str,
    ) -> str:
        """生成情绪解读：LLM 优先，失败回退规则引擎。"""
        base = RULE_INTERPRET.get(emoji, "今天心情如何？")
        if not note or not note.strip():
            return base
        try:
            prompt = (
                f"用户今天签到情绪为「{label}」{emoji}，备注：{note}\n"
                "请用2-3句话给一句温暖、真诚的回应，不要空洞，不要提建议过度。"
            )
            text = await llm.complete(
                prompt,
                language=language,
                lite=True,
                max_tokens=80,
                temperature=0.7,
            )
            return text.strip() or base
        except Exception as exc:  # noqa: BLE001  LLM 失败不阻塞签到
            logger.warning("mood LLM 解读失败，回退规则引擎: %s", exc)
            return base

    async def _record_memory(
        self, db: AsyncSession, user_id: str, note: str, emoji: str, label: str
    ) -> None:
        """把签到备注沉淀为一条记忆（类别 preference，情绪上下文）。"""
        try:
            from backend.services.memory_service import memory_service
            await memory_service.create_memory(
                db,
                user_id,
                content=note,
                category="preference",
                source="self_reflection",
                conviction=0.4,
                emotion_context=emoji,
            )
        except Exception as exc:  # noqa: BLE001  记忆写入失败不影响签到响应
            logger.warning("签到记忆沉淀失败: %s", exc)

    async def get_history(self, db: AsyncSession, user_id: str) -> MoodHistoryResponse:
        """获取最近签到历史。"""
        records = await mood_repository.get_recent(db, user_id, limit=30)
        return MoodHistoryResponse(
            checkins=[
                MoodCheckinResponse(
                    id=r.id,
                    mood_emoji=r.mood_emoji,
                    mood_label=r.mood_label or "",
                    ai_interpretation=r.ai_interpretation or "",
                    streak_updated=0,
                    created_at=r.created_at,
                )
                for r in records
            ]
        )

    async def get_trend(self, db: AsyncSession, user_id: str, days: int = 14) -> MoodTrendResponse:
        """获取情绪趋势（按天聚合，取每日首个情绪值）。"""
        user = await user_repository.get_user(db, user_id)
        rows = await mood_repository.count_by_day(
            db, user_id, days, tz=user.timezone if user else None
        )
        # 按天取首个情绪值
        day_map: dict = {}
        for day, emoji in rows:
            # day 是 date 对象
            key = day.strftime("%m-%d") if hasattr(day, "strftime") else str(day)
            if key not in day_map:
                val = MOOD_MAP.get(emoji, (None, 3))[1]
                day_map[key] = val

        labels = list(day_map.keys())
        values = list(day_map.values())
        summary = self._trend_summary(values)
        return MoodTrendResponse(labels=labels, values=values, summary=summary)

    def _trend_summary(self, values: List[int]) -> str:
        if not values:
            return "还没有足够的签到数据，开始记录你的第一天吧。"
        avg = sum(values) / len(values)
        if avg >= 4:
            return "整体情绪状态不错，保持这个节奏。"
        if avg >= 3:
            return "情绪总体平稳，偶尔关注自己的感受。"
        return "这段时间情绪有些低落，记得多照顾自己，我在这里。"


mood_service = MoodService()
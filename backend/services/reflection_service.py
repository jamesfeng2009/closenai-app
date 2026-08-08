"""
Reflection Card 服务 (P0-F04)。

基于用户情绪 + 记忆生成每日反思草稿，保存为成长记录（record_type='reflection'）。

草稿结构（PRD §2/§7）：今日反思（情绪 / 可能原因 / 感谢的事）+ AI 观察两段。
配额（PRD §2 成本控制）：草稿生成消耗 1 次 LLM 调用，前置统一免费配额校验，超额 40301。
新用户（无记忆无签到历史）：不生成草稿，返回对话式引导文案（PRD §4 fallback）。
"""
import logging
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AppException, ErrorCode
from backend.core.llm import llm
from backend.core.time_utils import local_day_start_utc
from backend.models.schemas import (
    ReflectionDraftResponse,
    ReflectionResponse,
)
from backend.repositories import (
    growth_repository,
    memory_repository,
    mood_repository,
    user_repository,
)
from backend.services.quota_service import quota_service
from backend.services.safety_service import safety_service

logger = logging.getLogger("closenai.reflection")

# 新用户对话式 fallback（PRD §4：像朋友一样问 1-2 个引导问题，而非空白/通用模板）
NEW_USER_FALLBACK = {
    "zh": "看起来我们才刚认识。先随便聊聊吧——今天有什么让你印象最深的事吗？现在的你感觉怎么样？",
    "en": "Looks like we're just getting to know each other. Let's start simple — what stood out most about your day? And how are you feeling right now?",
}

# LLM 失败降级模板（与正常草稿一致的两段结构：今日反思 + AI 观察）
FALLBACK_DRAFT = {
    "zh": (
        "📋 今日 Reflection 草稿\n\n"
        "【今日反思】\n"
        "情绪：{mood_part}\n"
        "可能的原因：请补充\n"
        "值得感谢的事：请补充\n\n"
        "【AI 观察】\n"
        "数据还不多，继续记录几天后我会给出更具体的观察。"
    ),
    "en": (
        "📋 Today's Reflection Draft\n\n"
        "[Today's Reflection]\n"
        "Mood: {mood_part}\n"
        "Possible reason: please fill in\n"
        "One thing to be grateful for: please fill in\n\n"
        "[AI Observation]\n"
        "Not enough data yet — keep recording for a few more days and I'll share more specific observations."
    ),
}


class ReflectionService:
    """Reflection 反思服务。"""

    async def generate_draft(
        self,
        db: AsyncSession,
        user_id: str,
        language: str = "en",
        context: Optional[str] = None,
    ) -> ReflectionDraftResponse:
        """生成今日 Reflection 草稿（今日反思 + AI 观察两段结构）。"""
        user = await user_repository.require_user(db, user_id)

        # 配额（B2）：反思草稿走统一池口径（仅主动消息 + 签到解读），不叠加对话轮数
        if await quota_service.is_proactive_quota_exceeded(db, user):
            raise AppException(
                ErrorCode.QUOTA_EXCEEDED,
                "今天的次数已经用完，请明天再来 / Daily limit reached, try again tomorrow",
            )

        zh = (language or "").startswith("zh")
        today_mood = await mood_repository.get_today(
            db, user_id, local_day_start_utc(user.timezone)
        )
        recent_memories = await memory_repository.list(db, user_id, limit=5)

        # 新用户 fallback（PRD §4）：无记忆且无签到历史 → 对话式引导，不生成模板草稿
        if not recent_memories:
            any_checkin = await mood_repository.get_recent(db, user_id, limit=1)
            if not any_checkin:
                return ReflectionDraftResponse(
                    draft=NEW_USER_FALLBACK["zh" if zh else "en"],
                    related_memory_ids=None,
                )

        mood_desc = (
            f"今日情绪：{today_mood.mood_emoji} {today_mood.mood_label or ''}"
            if today_mood
            else "今日暂无情绪签到。"
        )
        memory_desc = "\n".join(f"- {m.content}" for m in recent_memories) or "暂无记忆。"
        ctx_desc = f"\n补充上下文：{context}" if context else ""

        prompt = (
            "请为今天的 Reflection 生成一份草稿，严格分两段输出：\n"
            "【今日反思】包含：今日情绪与可能的原因、一件值得感谢的事"
            "（数据不足时用【占位】提示用户补充）。\n"
            "【AI 观察】基于近期记忆与今日情绪给出 1-2 句客观、具体的观察"
            "（如情绪模式、与之前记忆呼应的变化），不评价、不建议、不说教。\n"
            "语气温暖真诚，不要空泛。每段 2-3 句话。\n"
            f"{mood_desc}\n最近记忆：\n{memory_desc}{ctx_desc}"
        )
        try:
            draft = await llm.complete(prompt, language=language, lite=True, max_tokens=300)
            # L3 输出过滤：命中回退模板
            draft = await safety_service.check_or(draft, language, "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Reflection 草稿生成失败，回退模板: %s", exc)
            draft = ""
        if not draft.strip():
            if today_mood:
                mood_part = f"{today_mood.mood_emoji} {today_mood.mood_label}"
            else:
                mood_part = "待记录" if zh else "Not recorded"
            draft = FALLBACK_DRAFT["zh" if zh else "en"].format(mood_part=mood_part)
        return ReflectionDraftResponse(
            draft=draft.strip(),
            related_memory_ids=[m.id for m in recent_memories],
        )

    async def save(
        self,
        db: AsyncSession,
        user_id: str,
        content: str,
        related_memory_ids: Optional[List[str]] = None,
    ) -> ReflectionResponse:
        """保存 Reflection 为成长记录（透传关联记忆 ID 列表）。"""
        await user_repository.require_user(db, user_id)
        record = await growth_repository.create(
            db, user_id, "reflection", content, related_memory_ids
        )
        return ReflectionResponse(
            id=record.id,
            content=record.content,
            related_memory_ids=(
                record.related_memory_ids.split(",") if record.related_memory_ids else None
            ),
            created_at=record.created_at,
        )

    async def list(
        self, db: AsyncSession, user_id: str, limit: int = 20
    ) -> List[ReflectionResponse]:
        """列出最近的 Reflection 记录。"""
        records = await growth_repository.list_by_type(db, user_id, "reflection", limit=limit)
        return [
            ReflectionResponse(
                id=r.id,
                content=r.content,
                related_memory_ids=(
                    r.related_memory_ids.split(",") if r.related_memory_ids else None
                ),
                created_at=r.created_at,
            )
            for r in records
        ]


reflection_service = ReflectionService()

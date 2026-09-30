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

from backend.core import copy
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

        today_mood = await mood_repository.get_today(
            db, user_id, local_day_start_utc(user.timezone)
        )
        recent_memories = await memory_repository.list(db, user_id, limit=5)

        # 新用户 fallback（PRD §4）：无记忆且无签到历史 → 对话式引导，不生成模板草稿
        if not recent_memories:
            any_checkin = await mood_repository.get_recent(db, user_id, limit=1)
            if not any_checkin:
                return ReflectionDraftResponse(
                    draft=copy.t("reflection", "new_user", language),
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
            f"{safety_service.boundary(language)}\n"
            f"{safety_service.evidence_policy(language)}\n"
            f"{mood_desc}\n最近记忆：\n{memory_desc}{ctx_desc}"
        )
        try:
            draft = await llm.complete(prompt, language=language, lite=True, max_tokens=300)
            # L3 输出过滤：命中回退模板（【AI 观察】段是模式断言，走断言型 scope）
            draft = await safety_service.check_or(draft, language, "", scope="assertion")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Reflection 草稿生成失败，回退模板: %s", exc)
            draft = ""
        if not draft.strip():
            if today_mood:
                mood_part = f"{today_mood.mood_emoji} {today_mood.mood_label}"
            else:
                mood_part = copy.t("reflection", "mood_not_recorded", language)
            draft = copy.t("reflection", "fallback_draft", language).format(mood_part=mood_part)
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

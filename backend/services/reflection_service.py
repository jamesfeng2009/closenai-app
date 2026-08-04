"""
Reflection Card 服务 (P0-F04)。

基于用户情绪 + 记忆生成每日反思草稿，保存为成长记录（record_type='reflection'）。
"""
import logging
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

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
        """生成今日 Reflection 草稿。"""
        user = await user_repository.get_or_create_user(db, user_id)
        today_mood = await mood_repository.get_today(
            db, user_id, local_day_start_utc(user.timezone)
        )
        recent_memories = await memory_repository.list(db, user_id, limit=5)

        mood_desc = (
            f"今日情绪：{today_mood.mood_emoji} {today_mood.mood_label or ''}"
            if today_mood
            else "今日暂无情绪签到。"
        )
        memory_desc = "\n".join(f"- {m.content}" for m in recent_memories) or "暂无记忆。"
        ctx_desc = f"\n补充上下文：{context}" if context else ""

        prompt = (
            "请为今天的 Reflection 生成一份草稿，包含以下结构：\n"
            "1. 今日情绪与可能的原因\n"
            "2. 一件值得感谢的事（如果数据不足，用【占位】提示用户补充）\n"
            "3. 一个想对自己说的鼓励\n"
            "语气温暖真诚，不要空泛。用2-3句话概括。\n"
            f"{mood_desc}\n最近记忆：\n{memory_desc}{ctx_desc}"
        )
        try:
            draft = await llm.complete(prompt, language=language, lite=True, max_tokens=200)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Reflection 草稿生成失败，回退模板: %s", exc)
            draft = (
                f"📋 今日 Reflection 草稿\n\n情绪：{today_mood.mood_emoji} {today_mood.mood_label if today_mood else '待记录'}\n"
                "可能的原因：请补充\n值得感谢的事：请补充\n"
            )
        return ReflectionDraftResponse(
            draft=draft.strip(),
            related_memory_ids=[m.id for m in recent_memories],
        )

    async def save(
        self,
        db: AsyncSession,
        user_id: str,
        content: str,
    ) -> ReflectionResponse:
        """保存 Reflection 为成长记录。"""
        await user_repository.get_or_create_user(db, user_id)
        record = await growth_repository.create(
            db, user_id, "reflection", content
        )
        return ReflectionResponse(
            id=record.id,
            content=record.content,
            related_memory_ids=None,
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
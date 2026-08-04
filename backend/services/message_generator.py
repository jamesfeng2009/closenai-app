"""
消息生成服务 (P0-F01)。

负责 AI 对话消息的生成：流式对话、主动消息四层公式、记忆上下文注入。
"""
import logging
from typing import AsyncGenerator, Optional, List, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import llm
from backend.repositories import (
    chat_repository,
    memory_repository,
    mood_repository,
    user_repository,
)
from backend.services.discovery_service import discovery_service
from backend.services.memory_service import memory_service

logger = logging.getLogger("closenai.message")


class MessageGenerator:
    """AI 消息生成器。"""

    # Rule Engine 兜底模板（LLM 失败时使用）
    FALLBACK_TEMPLATES = [
        "今天过得怎么样？有什么想和我聊聊的吗？",
        "我注意到你最近很关注自己的成长，这真的很棒。",
        "你最近提到的那件事，现在进展如何了？",
        "如果今天有什么让你困扰的，我很愿意听你说说。",
    ]

    async def generate_chat_stream(
        self,
        db: AsyncSession,
        user_id: str,
        user_input: str,
        message_type: str = "reply",
        language: str = "en",
    ) -> AsyncGenerator[str, None]:
        """
        流式对话生成（SSE）。

        多语言：`language` 由请求头（X-Language）或用户偏好解析而来。
        记忆注入：根据用户输入检索相关记忆，注入上下文。
        """
        # 记忆上下文注入
        memory_context = await self._get_memory_context_str(db, user_id, user_input)

        system_prompt = (
            "You are ClosenAI, a warm, insightful AI companion. "
            "You help the user reflect on their emotions, relationships, and growth. "
            "Keep replies concise, empathetic, and in the user's language.\n\n"
            f"关于用户的记忆（可作为参考，不要生硬复述）：\n{memory_context}"
        )
        try:
            async for chunk in llm.stream(
                user_input,
                language=language,
                system_prompt=system_prompt,
                lite=False,
            ):
                yield chunk
        except Exception:
            yield self._fallback_reply(user_input)

    async def generate_proactive_message(
        self,
        db: AsyncSession,
        user_id: str,
        language: str = "en",
    ) -> str:
        """
        主动消息生成（Cron Job 触发）。

        四层公式：
        ① 引用记忆：检索最近相关记忆
        ② 发现模式：每日发现（P0-F10）对比洞察
        ③ 情绪关联：最近情绪签到
        ④ 互动：生成 Reflection 草稿预填
        """
        await user_repository.get_or_create_user(db, user_id)
        user = await user_repository.get_user(db, user_id)
        tier = user.tier if user else "free"

        # ① 记忆
        memories = await memory_repository.list(db, user_id, limit=3)
        memory_desc = "\n".join(f"- {m.content}" for m in memories) or ""

        # ② 每日发现
        discovery = await discovery_service.generate_discovery(db, user_id)
        discovery_desc = discovery or ""

        # ③ 最近情绪
        checkins = await mood_repository.get_recent(db, user_id, limit=3)
        mood_desc = " ".join(f"{c.mood_emoji}{c.mood_label or ''}" for c in checkins) or ""

        # ④ 引用最近的对话主题
        history = await chat_repository.get_history(db, user_id, limit=5)
        history_desc = " ".join(m.content for m in history if m.role == "user")[-200:] or ""

        # 免费用户：Rule Engine 模板 + 数据占位（成本控制）
        if tier == "free":
            return self._build_free_proactive(memory_desc, discovery_desc, mood_desc)

        # Pro/Pro+：LLM 个性化生成
        prompt = (
            "你是 ClosenAI，一位温暖、有洞察的 AI 陪伴者。请给用户发一条简短的主动问候（1-2句话），"
            "要自然、具体、不过度，像朋友一样关心。\n"
            f"用户近期记忆：{memory_desc}\n"
            f"数据洞察：{discovery_desc}\n"
            f"最近情绪：{mood_desc}\n"
            f"最近话题：{history_desc}"
        )
        try:
            text = await llm.complete(prompt, language=language, lite=True, max_tokens=80)
            return text.strip() or self._fallback_reply(user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("主动消息 LLM 失败，回退模板: %s", exc)
            return self._build_free_proactive(memory_desc, discovery_desc, mood_desc)

    def _build_free_proactive(self, memory_desc: str, discovery_desc: str, mood_desc: str) -> str:
        """免费用户主动消息（Rule Engine 模板 + 数据占位）。"""
        if discovery_desc:
            return discovery_desc
        if memory_desc:
            first = memory_desc.split("\n")[0].lstrip("- ").strip()
            return f"嗨，想接着聊聊「{first}」吗？我之前一直记着。"
        if mood_desc:
            return f"看到你最近的心情是 {mood_desc}，今天过得怎么样？"
        return "嗨，我是你的 AI 朋友。今天过得怎么样？想记录点什么吗？"

    async def get_memory_context(self, db: AsyncSession, user_id: str, query: str, n: int = 5) -> List[Dict]:
        """获取记忆上下文（用于对话注入）。"""
        memories = await memory_service.get_memory_context(db, user_id, query, limit=n)
        return [
            {"id": m.id, "content": m.content, "category": m.category}
            for m in memories
        ]

    async def _get_memory_context_str(self, db: AsyncSession, user_id: str, query: str) -> str:
        """记忆上下文 → 字符串（注入 system prompt）。"""
        try:
            memories = await memory_service.get_memory_context(db, user_id, query, limit=3)
            if not memories:
                return ""
            return "\n".join(f"- {m.content}" for m in memories)
        except Exception as exc:  # noqa: BLE001
            logger.warning("记忆上下文注入失败: %s", exc)
            return ""

    def _route_to_engine(self, user_input: str) -> str:
        """Rule Engine 路由决策。"""
        simple_keywords = ["你好", "谢谢", "好的", "嗯", "哦", "哈哈", "是的", "对"]
        if any(kw in user_input for kw in simple_keywords) and len(user_input) < 10:
            return "rule_engine"
        return "llm"

    def _fallback_reply(self, user_input: str) -> str:
        """LLM 失败时的兜底回复（Rule Engine 模板轮换）。"""
        idx = len(user_input) % len(self.FALLBACK_TEMPLATES)
        return self.FALLBACK_TEMPLATES[idx]


message_generator = MessageGenerator()
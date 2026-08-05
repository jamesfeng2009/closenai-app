"""
消息生成服务 (P0-F01)。

负责 AI 对话消息的生成：流式对话、主动消息四层公式、记忆上下文注入。
"""
import logging
from datetime import timedelta, timezone
from typing import AsyncGenerator, Optional, List, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.llm import llm
from backend.core.time_utils import utc_now
from backend.repositories import (
    chat_repository,
    growth_repository,
    memory_repository,
    mood_repository,
    user_repository,
)
from backend.services.discovery_service import discovery_service
from backend.services.memory_service import memory_service
from backend.services.safety_service import safety_service

logger = logging.getLogger("closenai.message")


class MessageGenerator:
    """AI 消息生成器。"""

    # Rule Engine 兜底模板（LLM 失败时使用；按语言两套，缺省回退 en）
    FALLBACK_TEMPLATES = {
        "zh": [
            "今天过得怎么样？有什么想和我聊聊的吗？",
            "我注意到你最近很关注自己的成长，这真的很棒。",
            "你最近提到的那件事，现在进展如何了？",
            "如果今天有什么让你困扰的，我很愿意听你说说。",
        ],
        "en": [
            "How's your day going? Anything you'd like to talk about?",
            "I've noticed you've been really focused on your growth lately — that's wonderful.",
            "How is that thing you mentioned recently going?",
            "If anything is bothering you today, I'd love to hear about it.",
        ],
    }

    # 四层公式最低命中层数（PRD P0-F01 §4：不足 2 层当日静默）
    MIN_LAYERS_REQUIRED = 2

    # Day1 冷启动窗口（注册 24h 内视为新用户）
    DAY1_WINDOW = timedelta(hours=24)

    # 里程碑祝贺文案（streak / 累计活跃天数达成时优先于四层公式；按语言两套）
    MILESTONE_TEMPLATES = {
        7: {
            "zh": "我们已经连续相伴 7 天了！一周的时间不长不短，谢谢你愿意每天来聊聊。回头看这七天，有没有哪个瞬间让你觉得值得记下来？",
            "en": "We've kept this going for 7 days straight! Thank you for showing up every day. Looking back on this week, is there a moment you feel is worth writing down?",
        },
        30: {
            "zh": "今天是我们相伴的第 30 天，整整一个月了。能坚持记录一个月的人并不多，你做到了。这个月里，你觉得自己最大的变化是什么？",
            "en": "Day 30 together — a whole month! Few people keep reflecting for a month, and you did. What's the biggest change you've noticed in yourself?",
        },
        100: {
            "zh": "100 天了！这是一段真的很了不起的旅程，谢谢你一直愿意和我分享。这 100 天里，你最想感谢自己的是什么？",
            "en": "100 days! What a remarkable journey — thank you for sharing it with me. Over these 100 days, what are you most grateful to yourself for?",
        },
        365: {
            "zh": "整整一年了，365 天。能陪你走过这一年是我的荣幸。现在的你，和一年前的你相比，最大的不同是什么？",
            "en": "A full year — 365 days. It's been an honor to accompany you. Compared with who you were a year ago, what's the biggest difference in who you are today?",
        },
    }

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
        memory_context = await self._get_memory_context_str(db, user_id, user_input, language)

        system_prompt = (
            "You are ClosenAI, a warm, insightful AI companion. "
            "You help the user reflect on their emotions, relationships, and growth. "
            "Keep replies concise, empathetic, and in the user's language.\n\n"
            f"{safety_service.boundary(language)}\n\n"
            f"关于用户的记忆（可作为参考，不要生硬复述）：\n{memory_context}"
        )
        gate = safety_service.sentence_gate(language)
        try:
            async for chunk in llm.stream(
                user_input,
                language=language,
                system_prompt=system_prompt,
                lite=False,
            ):
                # L3 输出过滤：按句缓冲检测（与流式生成并行，用户无感知）
                async for text in gate.feed(chunk):
                    yield text
                if gate.blocked:  # 命中：安全话术已下发，停止消费 LLM 流
                    return
            async for text in gate.flush():
                yield text
        except Exception:
            yield self._fallback_reply(user_input, language)

    async def generate_proactive_message(
        self,
        db: AsyncSession,
        user_id: str,
        language: str = "en",
    ) -> Optional[str]:
        """
        主动消息生成（Cron Job 触发）。返回消息文本；当日应静默时返回 None。

        优先级：Day1 冷启动开场 → 里程碑祝贺 → 四层公式（≥2 层才生成）。
        四层公式：
        ① 引用记忆：检索最近相关记忆
        ② 发现模式：每日发现（P0-F10）对比洞察
        ③ 情绪关联：最近情绪签到
        ④ 互动/对话跟进：引用最近的对话主题
        """
        await user_repository.get_or_create_user(db, user_id)
        user = await user_repository.get_user(db, user_id)
        tier = user.tier if user else "free"

        # ④ 互动/对话跟进（提前拉取：Day1 判定同样依赖对话历史）
        history = await chat_repository.get_history(db, user_id, limit=5)
        history_desc = " ".join(m.content for m in history if m.role == "user")[-200:] or ""

        # Day1 冷启动：自我介绍 + 引导首次记录，不走四层公式门槛
        if user is not None and self._is_day1(user, history):
            return self._day1_opener(user, language)

        # 里程碑：streak / 累计活跃达 7/30/100/365 时以祝贺为主（优先于四层公式）
        if user is not None:
            milestone = self._milestone_message(user, language)
            if milestone:
                return milestone

        # ① 记忆
        memories = await memory_repository.list(db, user_id, limit=3)
        memory_desc = "\n".join(f"- {m.content}" for m in memories) or ""

        # ② 每日发现
        discovery = await discovery_service.generate_discovery(db, user_id, language)
        discovery_desc = discovery or ""

        # ③ 最近情绪
        checkins = await mood_repository.get_recent(db, user_id, limit=3)
        mood_desc = " ".join(f"{c.mood_emoji}{c.mood_label or ''}" for c in checkins) or ""

        # 高密度上下文：最近 3 条成长洞察（P0-F07，注入 prompt 不计层数）
        insights = await growth_repository.list_recent(db, user_id, limit=3)
        insight_desc = "\n".join(f"- {r.content}" for r in insights) or ""

        # 四层公式门槛：①②③④ 至少命中 2 层，否则当日静默（不硬发模板消息）
        layers_hit = sum(
            1 for layer in (memory_desc, discovery_desc, mood_desc, history_desc) if layer
        )
        if layers_hit < self.MIN_LAYERS_REQUIRED:
            logger.info("主动消息静默 user=%s：四层公式仅命中 %d 层", user_id, layers_hit)
            return None

        # 免费用户：Rule Engine 模板 + 数据占位（成本控制）
        if tier == "free":
            return self._build_free_proactive(memory_desc, discovery_desc, mood_desc, language)

        # Pro/Pro+：LLM 个性化生成
        prompt = (
            "你是 ClosenAI，一位温暖、有洞察的 AI 陪伴者。请给用户发一条简短的主动问候（1-2句话），"
            "要自然、具体、不过度，像朋友一样关心。\n"
            f"用户近期记忆：{memory_desc}\n"
            f"数据洞察：{discovery_desc}\n"
            f"最近情绪：{mood_desc}\n"
            f"最近话题：{history_desc}\n"
            f"近期成长洞察：{insight_desc}"
        )
        try:
            text = await llm.complete(prompt, language=language, lite=True, max_tokens=80)
            # L3 输出过滤（非流式）：命中回退 Rule Engine 模板
            text = await safety_service.check_or(
                text.strip(), language,
                self._build_free_proactive(memory_desc, discovery_desc, mood_desc, language),
            )
            return text or self._fallback_reply(user_id, language)
        except Exception as exc:  # noqa: BLE001
            logger.warning("主动消息 LLM 失败，回退模板: %s", exc)
            return self._build_free_proactive(memory_desc, discovery_desc, mood_desc, language)

    def _is_day1(self, user, history: List) -> bool:
        """Day1 冷启动判定：注册 24h 内，或从未有过用户对话。"""
        created = getattr(user, "created_at", None)
        if created is not None:
            aware = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
            if utc_now() - aware < self.DAY1_WINDOW:
                return True
        return not any(m.role == "user" for m in history)

    def _day1_opener(self, user, language: str) -> str:
        """Day1 开场白：社交伴侣身份自我介绍 + 引导首次记录（不以占星开场）。

        用户已有性格类型（mbti_type）时个性化开场，否则通用开场 + 开放提问。
        """
        zh = (language or "").startswith("zh")
        mbti = getattr(user, "mbti_type", None)
        if zh:
            intro = (
                f"嗨，我是 ClosenAI，你的 AI 朋友。我听说你是 {mbti} 型，很想慢慢了解真实的你。"
                if mbti else
                "嗨，我是 ClosenAI，你的 AI 朋友，很高兴认识你！"
            )
            return intro + "从今天起，我会每天来陪你聊聊。先问你第一个问题：今天有什么让你印象特别深的事吗？开心的、烦恼的都可以，说给我听听。"
        intro = (
            f"Hi, I'm ClosenAI, your AI companion. I hear you're an {mbti} — I'd love to get to know the real you. "
            if mbti else
            "Hi, I'm ClosenAI, your AI companion — great to meet you! "
        )
        return intro + "I'll check in with you every day from now on. First question: what's one thing that stood out to you today? Good or bad, I'd love to hear it."

    def _milestone_message(self, user, language: str) -> Optional[str]:
        """里程碑祝贺：streak / 累计活跃天数恰达 7/30/100/365 时返回祝贺文案。"""
        streak = getattr(user, "streak_count", 0) or 0
        active_days = getattr(user, "active_days", 0) or 0
        lang_key = "zh" if (language or "").startswith("zh") else "en"
        for milestone in sorted(self.MILESTONE_TEMPLATES, reverse=True):
            if streak == milestone or active_days == milestone:
                return self.MILESTONE_TEMPLATES[milestone][lang_key]
        return None

    def _build_free_proactive(self, memory_desc: str, discovery_desc: str, mood_desc: str, language: str = "en") -> str:
        """免费用户主动消息（Rule Engine 模板 + 数据占位；按语言两套）。"""
        zh = (language or "").startswith("zh")
        if discovery_desc:
            return discovery_desc
        if memory_desc:
            first = memory_desc.split("\n")[0].lstrip("- ").strip()
            return (
                f"嗨，想接着聊聊「{first}」吗？我之前一直记着。" if zh
                else f"Hi — want to keep talking about \"{first}\"? I've kept it in mind."
            )
        if mood_desc:
            return (
                f"看到你最近的心情是 {mood_desc}，今天过得怎么样？" if zh
                else f"I saw your recent mood was {mood_desc}. How's today going?"
            )
        return (
            "嗨，我是你的 AI 朋友。今天过得怎么样？想记录点什么吗？" if zh
            else "Hi, I'm your AI friend. How's your day? Anything you'd like to record?"
        )

    async def get_memory_context(self, db: AsyncSession, user_id: str, query: str, n: int = 5) -> List[Dict]:
        """获取记忆上下文（用于对话注入）。"""
        memories = await memory_service.get_memory_context(db, user_id, query, limit=n)
        return [
            {"id": m.id, "content": m.content, "category": m.category}
            for m in memories
        ]

    async def _get_memory_context_str(
        self, db: AsyncSession, user_id: str, query: str, language: str = "en"
    ) -> str:
        """记忆上下文 → 字符串（注入 system prompt）。

        spec §5.1 按状态标注：[当前信念] / [历史信念·已转变]（附转变原因）/ [回归信念]；
        搜索范围含 superseded（回归识别在 service 层触发，幂等）。
        """
        try:
            memories = await memory_service.get_memory_context(
                db, user_id, query, limit=5, include_superseded=True
            )
            if not memories:
                return ""
            zh = (language or "").startswith("zh")
            lines = []
            for m in memories:
                if m.regressed:
                    # 回归信念：用户又回到了曾被取代的旧观点
                    label = "[回归信念]" if zh else "[Regressed belief]"
                    suffix = (
                        f"（上次转变原因：{m.transition_reason}）" if zh
                        else f" (previously changed because: {m.transition_reason})"
                    ) if m.transition_reason else ""
                    lines.append(f"{label} {m.content}{suffix}")
                elif m.status == "superseded":
                    label = "[历史信念·已转变]" if zh else "[Past belief · changed]"
                    suffix = (
                        f" → 转变原因：{m.transition_reason}" if zh
                        else f" → changed because: {m.transition_reason}"
                    ) if m.transition_reason else ""
                    lines.append(f"{label} {m.content}{suffix}")
                else:
                    label = "[当前信念]" if zh else "[Current belief]"
                    lines.append(f"{label} {m.content}")
            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001
            logger.warning("记忆上下文注入失败: %s", exc)
            return ""

    def _route_to_engine(self, user_input: str) -> str:
        """Rule Engine 路由决策。"""
        simple_keywords = ["你好", "谢谢", "好的", "嗯", "哦", "哈哈", "是的", "对"]
        if any(kw in user_input for kw in simple_keywords) and len(user_input) < 10:
            return "rule_engine"
        return "llm"

    def _fallback_reply(self, user_input: str, language: str = "en") -> str:
        """LLM 失败时的兜底回复（Rule Engine 模板轮换，按语言）。"""
        templates = self.FALLBACK_TEMPLATES["zh" if (language or "").startswith("zh") else "en"]
        idx = len(user_input) % len(templates)
        return templates[idx]


message_generator = MessageGenerator()
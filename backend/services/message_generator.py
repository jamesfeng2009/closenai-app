"""
消息生成服务 (P0-F01)。

负责 AI 对话消息的生成：流式对话、主动消息四层公式、记忆上下文注入。
"""
import logging
from datetime import timedelta, timezone
from typing import AsyncGenerator, Optional, List, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import copy
from backend.core import redis as redis_client
from backend.ai.llm import llm
from backend.core.time_utils import local_day_start_utc, utc_now
from backend.models.user import CONVERSATION_STYLES, DEFAULT_CONVERSATION_STYLE
from backend.repositories import (
    chat_repository,
    growth_repository,
    memory_repository,
    mood_repository,
    safety_repository,
    user_repository,
)
from backend.services.discovery_service import discovery_service
from backend.services.memory_service import memory_service
from backend.ai.safety import safety_service

logger = logging.getLogger("closenai.message")

# ═══ 对话风格 prompt（PRD P1-F04：温暖知心 / 理性分析 / 哲学思辨）═══
# key 定义见 models/user.py CONVERSATION_STYLES；非法/缺省一律回退默认风格。
# 15 语言指令文本走文案库（core/copy.py，style.*）。


def conversation_style_prompt(style: Optional[str], language: str = "en") -> str:
    """对话风格 prompt 段落（按语言；未知值回退默认风格，永不抛错）。"""
    key = style if style in CONVERSATION_STYLES else DEFAULT_CONVERSATION_STYLE
    desc = copy.t("style", key, language)
    return f"{desc}\n{copy.t('style', 'safety_note', language)}"


class MessageGenerator:
    """AI 消息生成器。"""

    # 跟进消息引用的原文摘要长度（防 prompt 膨胀，控制 LLM 成本）
    FOLLOW_UP_EXCERPT_LEN = 50

    # 四层公式最低命中层数（PRD P0-F01 §4：不足 2 层当日静默）
    MIN_LAYERS_REQUIRED = 2

    # Day1 冷启动窗口（注册 24h 内视为新用户）
    DAY1_WINDOW = timedelta(hours=24)

    # Day 1-2 冷启动窗口（注册 48h 内强制无 Discovery，防冷启动信息过载）
    DAY12_WINDOW = timedelta(hours=48)

    # Discovery 疲劳控制：连续第 3 天高价值发现自动降级 medium（防疲劳）
    DISCOVERY_HIGH_FATIGUE_DAYS = 3
    # 高价值计数 Redis 滑动窗口（48h；中间一天非 high 则计数随 TTL 失效）
    _DISCOVERY_STREAK_TTL = 48 * 3600

    # 活跃时段优化前的常量区已迁文案库（core/copy.py msg.*）：
    # 兜底回复 / 跟进降级 / 里程碑祝贺 / Day1 开场 / 免费主动消息模板。

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
        # 对话风格注入（P1-F04：用户偏好风格，读库失败回退默认 warm）
        user = await user_repository.get_user(db, user_id)

        # 危机转介（PRD §5.16 ④ / 规范 §二）：命中即中断常规 AI 回复——
        # 不进 LLM，因此不可能输出沟通行为归因或关系修复建议；只下发关怀 + 地区热线，并写审计。
        hit = safety_service.crisis_intercept(
            user_input, language, getattr(user, "timezone", None)
        )
        if hit is not None:
            try:
                await safety_repository.record(
                    db,
                    user_id=user_id,
                    kind=hit["kind"],
                    source="user_message",
                    matched_terms=hit["matched"],
                    hotline=hit["hotline"],
                    language=language,
                    excerpt=user_input,
                    advice_blocked=hit["advice_blocked"],
                )
            except Exception:  # noqa: BLE001  审计写失败不阻断关怀文案（规范 §二 2.3：文案缺失才是 P1）
                logger.exception("safety_events 审计写入失败 user=%s kind=%s", user_id, hit["kind"])
            yield hit["reply"]
            return

        style = getattr(user, "conversation_style", None) or DEFAULT_CONVERSATION_STYLE

        # 记忆上下文注入
        memory_context = await self._get_memory_context_str(db, user_id, user_input, language)

        system_prompt = (
            "You are ClosenAI, a warm, insightful AI companion. "
            "You help the user reflect on their emotions, relationships, and growth. "
            "Keep replies concise, empathetic, and in the user's language.\n\n"
            f"{conversation_style_prompt(style, language)}\n\n"
            f"{safety_service.boundary(language)}\n\n"
            f"{safety_service.evidence_policy(language)}\n\n"
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

        Discovery 融合规则（P0-F10）：
        - high：消息以 Discovery 开头（今日最有价值的发现）
        - medium：融入消息中段（先记忆/情绪开场，发现居中）
        - low：跳过，用①③④层补足（不计入命中层数）
        - 连续 3 天高价值发现自动降级 medium（防疲劳，Redis 滑动窗口计数）
        - 新用户 Day 1-2 强制无 Discovery（Day1 走冷启动开场，Day2 走其他层）
        """
        await user_repository.require_user(db, user_id)
        user = await user_repository.get_user(db, user_id)
        tier = user.tier if user else "free"

        # §5.16 ④：危机转介命中当日，营销/升级类主动消息整体挂起（safety_events 即事实源）
        if await safety_repository.has_hit_since(
            db, user_id, local_day_start_utc((user.timezone if user else None) or "UTC")
        ):
            logger.info("危机转介命中，当日停发主动消息 user=%s", user_id)
            return None

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

        # ② 每日发现（按价值分级融合；Day 1-2 新用户强制跳过）
        discovery_desc = ""
        discovery_priority: Optional[str] = None
        if user is not None and not self._in_day12_window(user):
            discovery_result = await discovery_service.generate_discovery(db, user_id, language)
            if discovery_result is not None:
                discovery_priority = await self._discovery_priority_with_fatigue(
                    user_id, discovery_result.priority
                )
                if discovery_priority != "low":  # low：跳过，用①③④层补足
                    discovery_desc = discovery_result.content

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
            return self._build_free_proactive(
                memory_desc, discovery_desc, mood_desc, language, discovery_priority
            )

        # Pro/Pro+：LLM 个性化生成（注入用户对话风格，P1-F04）
        style = getattr(user, "conversation_style", None) or DEFAULT_CONVERSATION_STYLE
        prompt = (
            "你是 ClosenAI，一位温暖、有洞察的 AI 陪伴者。请给用户发一条简短的主动问候（1-2句话），"
            "要自然、具体、不过度，像朋友一样关心。\n"
            f"{conversation_style_prompt(style, language)}\n"
            f"{safety_service.boundary(language)}\n"
            f"{safety_service.evidence_policy(language)}\n"
            f"{self._discovery_placement_hint(discovery_priority, language)}"
            f"用户近期记忆：{memory_desc}\n"
            f"数据洞察：{discovery_desc}\n"
            f"最近情绪：{mood_desc}\n"
            f"最近话题：{history_desc}\n"
            f"近期成长洞察：{insight_desc}"
        )
        try:
            text = await llm.complete(prompt, language=language, lite=True, max_tokens=80)
            # L3 输出过滤（非流式）：命中回退 Rule Engine 模板
            # scope=assertion：主动消息引用记忆与模式，属断言型出口，推理越界一并拦
            text = await safety_service.check_or(
                text.strip(), language,
                self._build_free_proactive(
                    memory_desc, discovery_desc, mood_desc, language, discovery_priority
                ),
                scope="assertion",
            )
            return text or self._fallback_reply(user_id, language)
        except Exception as exc:  # noqa: BLE001
            logger.warning("主动消息 LLM 失败，回退模板: %s", exc)
            return self._build_free_proactive(
                memory_desc, discovery_desc, mood_desc, language, discovery_priority
            )

    def _is_day1(self, user, history: List) -> bool:
        """Day1 冷启动判定：注册 24h 内，或从未有过用户对话。"""
        created = getattr(user, "created_at", None)
        if created is not None:
            aware = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
            if utc_now() - aware < self.DAY1_WINDOW:
                return True
        return not any(m.role == "user" for m in history)

    def _in_day12_window(self, user) -> bool:
        """Day 1-2 判定：注册 48h 内（强制无 Discovery；Day1 开场白在此之前已拦截）。"""
        created = getattr(user, "created_at", None)
        if created is None:
            return False
        aware = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
        return utc_now() - aware < self.DAY12_WINDOW

    async def _discovery_priority_with_fatigue(self, user_id: str, priority: str) -> str:
        """连续高价值 Discovery 疲劳控制：连续第 3 天起 high 自动降级 medium。

        Redis 滑动窗口计数（48h）：当日非 high 即清零；Redis 故障 fail-open，
        按原优先级放行，不阻塞主动消息主流程。
        """
        key = f"discovery:high_streak:{user_id}"
        try:
            if priority != "high":
                await redis_client.delete(key, fail_open=True)
                return priority
            raw = await redis_client.get(key, fail_open=True)
            streak = int(raw) + 1 if raw and raw.isdigit() else 1
            await redis_client.setex(
                key, self._DISCOVERY_STREAK_TTL, str(streak), fail_open=True
            )
            if streak >= self.DISCOVERY_HIGH_FATIGUE_DAYS:
                logger.info("Discovery 连续 %d 天高价值，降级为 medium user=%s", streak, user_id)
                return "medium"
            return "high"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Discovery 疲劳控制失败，按原优先级放行: %s", exc)
            return priority

    @staticmethod
    def _discovery_placement_hint(priority: Optional[str], language: str) -> str:
        """Discovery 融合位置指令（注入 LLM prompt）：high 开头 / medium 中段。"""
        zh = (language or "").startswith("zh")
        if priority == "high":
            return (
                "融合规则：数据洞察是今天最有价值的发现，请将它作为消息开头，自然引出其余内容。\n" if zh
                else "Fusion rule: the data insight is today's most valuable finding — open the message with it, then flow into the rest.\n"
            )
        if priority == "medium":
            return (
                "融合规则：请将数据洞察自然融入消息中段，不要作为开头。\n" if zh
                else "Fusion rule: weave the data insight naturally into the middle of the message, not the opening.\n"
            )
        return ""

    def _day1_opener(self, user, language: str) -> str:
        """Day1 开场白：社交伴侣身份自我介绍 + 引导首次记录（不以占星开场）。

        用户已有性格类型（mbti_type）时个性化开场，否则通用开场 + 开放提问。
        """
        mbti = getattr(user, "mbti_type", None)
        tail = copy.t("msg", "day1.tail", language)
        key = "day1.intro_mbti" if mbti else "day1.intro_plain"
        return copy.t("msg", key, language).format(mbti=mbti, tail=tail)

    def _milestone_message(self, user, language: str) -> Optional[str]:
        """里程碑祝贺：streak / 累计活跃天数恰达 7/30/100/365 时返回祝贺文案。"""
        streak = getattr(user, "streak_count", 0) or 0
        active_days = getattr(user, "active_days", 0) or 0
        for milestone in (365, 100, 30, 7):
            if streak == milestone or active_days == milestone:
                return copy.t("msg", f"milestone.{milestone}", language)
        return None

    def _build_free_proactive(
        self,
        memory_desc: str,
        discovery_desc: str,
        mood_desc: str,
        language: str = "en",
        discovery_priority: Optional[str] = None,
    ) -> str:
        """免费用户主动消息（Rule Engine 模板 + 数据占位；15 语言走文案库）。

        Discovery 融合（P0-F10）：high 以发现开头；medium 先记忆/情绪开场、发现居中。
        """
        if discovery_desc and discovery_priority == "high":
            return discovery_desc
        if memory_desc:
            first = memory_desc.split("\n")[0].lstrip("- ").strip()
            opener = copy.t("msg", "proactive.memory_opener", language).format(first=first)
            return f"{opener}\n{discovery_desc}" if discovery_desc else opener
        if mood_desc:
            opener = copy.t("msg", "proactive.mood_opener", language).format(mood_desc=mood_desc)
            return f"{opener}\n{discovery_desc}" if discovery_desc else opener
        if discovery_desc:
            # 发现是唯一内容层：位置无意义，直接发出
            return discovery_desc
        return copy.t("msg", "proactive.plain", language)

    async def generate_follow_up(self, record_content: str, language: str = "en") -> str:
        """事件后跟进消息（PRD P0-F07）：「上次你提到...现在怎么样了？」。

        输入原始成长记录内容，LLM lite 生成 1-2 句自然的跟进询问；
        LLM 失败 / 命中 L3 安全过滤一律回退模板（永不抛异常，cron 批量安全）。
        """
        zh = (language or "").startswith("zh")
        excerpt = " ".join((record_content or "").split())[: self.FOLLOW_UP_EXCERPT_LEN]
        fallback = copy.t("msg", "follow_up_fallback", language).format(excerpt=excerpt)
        if not excerpt:
            return fallback
        if zh:
            prompt = (
                "你是 ClosenAI，一位温暖、有洞察的 AI 陪伴者。用户几天前的成长记录里"
                "写下了下面的内容。请生成一条简短的跟进消息（1-2 句话），自然地询问"
                "那件事现在的进展，像朋友一样关心，具体、不空泛、不说教：\n"
                f"{safety_service.boundary(language)}\n"
                f"{safety_service.evidence_policy(language)}\n"
                f"用户当时的记录：「{excerpt}」"
            )
        else:
            prompt = (
                "You are ClosenAI, a warm, insightful AI companion. A few days ago the "
                "user wrote the following in their growth record. Write a short follow-up "
                "message (1-2 sentences) that naturally asks how that thing is going now, "
                "like a caring friend — specific, not generic, no lecturing:\n"
                f"{safety_service.boundary(language)}\n"
                f"{safety_service.evidence_policy(language)}\n"
                f"The user's record back then: \"{excerpt}\""
            )
        try:
            text = await llm.complete(prompt, language=language, lite=True, max_tokens=80)
            # L3 输出过滤：命中回退模板（承诺回访引用用户原话，属断言型出口）
            text = await safety_service.check_or(text.strip(), language, "", scope="assertion")
            return text or fallback
        except Exception as exc:  # noqa: BLE001
            logger.warning("跟进消息 LLM 失败，回退模板: %s", exc)
            return fallback

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
        """LLM 失败时的兜底回复（Rule Engine 模板轮换，15 语言走文案库）。"""
        templates = copy.t_list("msg", "fallback", language)
        idx = len(user_input) % len(templates)
        return templates[idx]


message_generator = MessageGenerator()
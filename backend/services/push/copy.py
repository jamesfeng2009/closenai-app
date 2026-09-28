"""
推送文案生成（P1-F01）。

人格化推送文案：按场景（主动消息/签到提醒/周报/里程碑/关心）× 中英双语
模板池，占位符注入用户名字 / 近期情绪 / 里程碑天数；Pro 用户可选 lite LLM
生成正文，失败安全回退模板（推送只许成功或降级，不得抛异常阻断 cron）。

文案约束（PRD §9）：温暖朋友口吻，不含"运势/预测/幸运数字"等占星措辞。
"""
import hashlib
import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.llm import llm
from backend.core.time_utils import local_today
from backend.models.user import User
from backend.repositories import mood_repository
from backend.services.message_generator import conversation_style_prompt
from backend.services.safety_service import safety_service

logger = logging.getLogger("closenai.push.copy")

# 场景模板池：scenario → lang → [(title, body)]
# body 为 None 表示正文由调用方提供（主动消息：推送内容即消息本身）
PUSH_TEMPLATES: Dict[str, Dict[str, List[Tuple[str, Optional[str]]]]] = {
    "proactive": {
        "zh": [
            ("{name}，来聊聊吧", None),
            ("今天也想听听你的声音", None),
            ("{name}，我有点事想问你", None),
        ],
        "en": [
            ("{name}, let's talk", None),
            ("I'd love to hear from you today", None),
            ("{name}, I've been thinking of you", None),
        ],
    },
    "mood_reminder": {
        "zh": [
            ("今天感觉怎么样？", "{name}，花 1 秒记录一下此刻的心情吧"),
            ("来打个卡吧", "最近的你记录了「{mood}」，今天的心情如何呢？"),
        ],
        "en": [
            ("How are you feeling today?", "{name}, take a second to note how you're feeling."),
            ("Quick check-in", "You noted \"{mood}\" recently — how about today?"),
        ],
    },
    "weekly": {
        "zh": [
            ("你的周报准备好了", "{name}，这一周的足迹我帮你整理好了，来看看吧"),
            ("一周回顾", "{name}，这一周你有哪些新变化？周报已为你写好"),
        ],
        "en": [
            ("Your weekly report is ready", "{name}, I've put together your week — come take a look."),
            ("Your week in review", "{name}, your weekly reflection is ready for you."),
        ],
    },
    "milestone": {
        "zh": [
            ("值得纪念的一天", "{name}，今天是我们相伴的第 {milestone} 天，谢谢你的坚持"),
            ("里程碑达成", "{name}，{milestone} 天了！这一刻值得好好聊聊"),
        ],
        "en": [
            ("A day worth celebrating", "{name}, day {milestone} together — thank you for showing up."),
            ("Milestone reached", "{name}, {milestone} days! Let's talk about this moment."),
        ],
    },
    "care": {
        "zh": [
            ("想问问你", "{name}，注意到你这几天心情不太好，想聊聊吗？我一直都在"),
            ("我在这里", "{name}，最近几天似乎有点难熬，如果你愿意，跟我说说吧"),
        ],
        "en": [
            ("Checking in on you", "{name}, these days seem heavy — want to talk? I'm here."),
            ("I'm here for you", "{name}, it feels like a tough stretch lately. I'm here if you'd like to share."),
        ],
    },
}

# lite LLM 生成用的场景描述（注入 prompt）
_SCENARIO_DESC = {
    "proactive": "每日主动问候（基于原始消息改写成通知体，保留其核心意思）",
    "mood_reminder": "提醒用户做今日情绪签到",
    "weekly": "告知用户本周成长周报已生成，引导点击查看",
    "milestone": "祝贺用户连续/累计陪伴达成里程碑天数",
    "care": "用户连续 3 天情绪低落，表达关心并邀请倾诉",
}

# Pro 档位（lite LLM 文案仅对这些档位开放，免费用户走模板控制成本）
_PRO_TIERS = ("pro", "pro_plus")


class PushCopywriter:
    """推送文案生成器。"""

    async def build_copy(
        self,
        db: AsyncSession,
        user: User,
        scenario: str,
        *,
        message: Optional[str] = None,
        milestone: Optional[int] = None,
    ) -> Tuple[str, str]:
        """生成 (title, body)。任何失败都回退模板/消息原文，不抛异常。

        scenario: proactive | mood_reminder | weekly | milestone | care
        message: proactive 场景的消息原文（推送内容即消息本身）
        milestone: milestone 场景的里程碑天数（7/30/100/365）
        """
        language = user.language or "en"
        lang = "zh" if language.startswith("zh") else "en"
        pool = PUSH_TEMPLATES.get(scenario, PUSH_TEMPLATES["proactive"])[lang]
        title_t, body_t = pool[self._pick(user, scenario, len(pool))]

        ctx: Dict[str, object] = {
            "name": self._display_name(user, lang),
            "milestone": milestone or "",
        }
        if "{mood}" in title_t or (body_t is not None and "{mood}" in body_t):
            ctx["mood"] = await self._recent_mood(db, user) or ("未知" if lang == "zh" else "unknown")
        title = title_t.format(**ctx)
        body = body_t.format(**ctx) if body_t is not None else (message or "")

        # Pro：lite LLM 个性化正文（失败/异常/空结果回退模板或消息原文）
        if (user.tier or "free") in _PRO_TIERS:
            llm_body = await self._llm_body(
                user,
                scenario,
                language,
                name=str(ctx["name"]),
                mood=str(ctx.get("mood", "")),
                milestone=milestone,
                message=body or message,
            )
            if llm_body:
                body = llm_body
        return title, body

    def _pick(self, user: User, scenario: str, n: int) -> int:
        """确定性模板轮换：同一用户同一天同场景选中同一模板（跨天轮换）。"""
        day = local_today(user.timezone).isoformat()
        digest = hashlib.sha256(f"{user.id}:{scenario}:{day}".encode()).hexdigest()
        return int(digest, 16) % n

    def _display_name(self, user: User, lang: str) -> str:
        return (user.name or "").strip() or ("朋友" if lang == "zh" else "friend")

    async def _recent_mood(self, db: AsyncSession, user: User) -> str:
        """最近一次签到的情绪标签（失败返回空串，不阻断文案生成）。"""
        try:
            recent = await mood_repository.get_recent(db, user.id, limit=1)
            if recent:
                return ((recent[0].mood_label or recent[0].mood_emoji) or "").strip()
        except Exception as exc:  # noqa: BLE001 - 查询失败回退无情绪占位
            logger.warning("近期情绪查询失败 user=%s: %s", user.id, exc)
        return ""

    async def _llm_body(
        self,
        user: User,
        scenario: str,
        language: str,
        *,
        name: str,
        mood: str = "",
        milestone: Optional[int] = None,
        message: Optional[str] = None,
    ) -> Optional[str]:
        """lite LLM 生成推送正文（30-60 字温暖通知体）。失败返回 None。"""
        # 对话风格感知（P1-F04）：文案语气跟随用户偏好风格，安全约束不变
        prompt = (
            "你是一位温暖的情绪陪伴者。请写一条手机推送通知正文，要求："
            "30-60 字、像朋友说话、具体不空泛；"
            "禁止出现运势、预测、幸运数字等占星措辞；"
            "不要引号、不要解释，只输出通知文本本身。\n"
            f"{conversation_style_prompt(getattr(user, 'conversation_style', None), language)}\n"
            f"{safety_service.boundary(language)}\n"
            f"{safety_service.evidence_policy(language)}\n"
            f"场景：{_SCENARIO_DESC.get(scenario, scenario)}\n"
            f"用户称呼：{name}\n"
        )
        if mood:
            prompt += f"用户近期情绪：{mood}\n"
        if milestone:
            prompt += f"里程碑天数：{milestone}\n"
        if message:
            prompt += f"原始消息：{message}\n"
        try:
            text = await llm.complete(
                prompt, language=language, lite=True, max_tokens=80
            )
            # 只取首行并去引号，防止模型输出解释性前后缀
            text = text.strip().splitlines()[0].strip().strip("\"'「」『』") if text.strip() else ""
            # L3 输出过滤：命中回退模板
            if not await safety_service.check_text(text, language):
                return None
            if 4 <= len(text) <= 140:
                return text
            logger.warning("推送文案 LLM 输出不合规（长度 %d），回退模板 user=%s", len(text), user.id)
        except Exception as exc:  # noqa: BLE001 - LLM 失败安全降级模板
            logger.warning("推送文案 LLM 失败，回退模板 user=%s: %s", user.id, exc)
        return None


push_copywriter = PushCopywriter()

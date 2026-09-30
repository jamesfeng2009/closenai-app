"""
推送文案生成（P1-F01）。

人格化推送文案：按场景（主动消息/签到提醒/周报/里程碑/关心）× 15 语言
模板池（core/copy.py push.*），占位符注入用户名字 / 近期情绪 / 里程碑天数；
Pro 用户可选 lite LLM 生成正文，失败安全回退模板（推送只许成功或降级，
不得抛异常阻断 cron）。

文案约束（PRD §9）：温暖朋友口吻，不含"运势/预测/幸运数字"等占星措辞。
"""
import hashlib
import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import copy
from backend.core.llm import llm
from backend.core.time_utils import local_today
from backend.models.user import User
from backend.repositories import mood_repository
from backend.services.message_generator import conversation_style_prompt
from backend.services.safety_service import safety_service

logger = logging.getLogger("closenai.push.copy")

_SCENARIOS = ("proactive", "mood_reminder", "weekly", "milestone", "care", "bedtime")


def templates_for(scenario: str, language: str) -> List[Tuple[str, Optional[str]]]:
    """场景模板池（15 语言走文案库 push.{scenario}，每项 {title, body|null}）。

    场景不存在（开发期错误）回退 proactive 池，永不空转崩溃。
    """
    items = copy.t_list("push", scenario, language)
    if not items and scenario != "proactive":
        items = copy.t_list("push", "proactive", language)
    return [
        (item["title"], item.get("body"))
        for item in items
        if isinstance(item, dict) and item.get("title")
    ]


# lite LLM 生成用的场景描述（注入 prompt，非用户可见）
_SCENARIO_DESC = {
    "proactive": "每日主动问候（基于原始消息改写成通知体，保留其核心意思）",
    "mood_reminder": "提醒用户做今日情绪签到",
    "weekly": "告知用户本周成长周报已生成，引导点击查看",
    "milestone": "祝贺用户连续/累计陪伴达成里程碑天数",
    "care": "用户连续 3 天情绪低落，表达关心并邀请倾诉",
    "bedtime": "温和提醒用户做睡前复盘（三步：一句话/值得记下的/留给明天），不施压不催促",
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

        scenario: proactive | mood_reminder | weekly | milestone | care | bedtime
        message: proactive 场景的消息原文（推送内容即消息本身）
        milestone: milestone 场景的里程碑天数（7/30/100/365）
        """
        language = user.language or "en"
        pool = templates_for(scenario, language)
        title_t, body_t = pool[self._pick(user, scenario, len(pool))]

        ctx: Dict[str, object] = {
            "name": self._display_name(user, language),
            "milestone": milestone or "",
        }
        if "{mood}" in title_t or (body_t is not None and "{mood}" in body_t):
            ctx["mood"] = await self._recent_mood(db, user) or copy.t(
                "push", "unknown_mood", language
            )
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

    def _display_name(self, user: User, language: str) -> str:
        return (user.name or "").strip() or copy.t("push", "default_name", language)

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

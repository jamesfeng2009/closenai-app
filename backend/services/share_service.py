"""
社交分享导出服务 (P1-F04)。

提供：
- 性格名片导出（JSON，多 variant 版式 + AI 标语 + App Store 落地链接）
- 周期报告导出（JSON + PDF，中文正常渲染；period 白名单 + 同周期缓存）

PDF 使用 reportlab 的 UnicodeCIDFont（STSong-Light），无需嵌入字体文件。

导出缓存（PRD P1-F04 §7）：同一用户同 period 同周期（period_key，按用户本地
时间）只生成一次，命中 share_exports 缓存直接返回，不重复调 LLM、不重复落库。
"""
import json
import logging
from datetime import timedelta
from io import BytesIO
from typing import List, Optional

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.errors import AppException, ErrorCode
from backend.core.llm import llm
from backend.core.time_utils import local_today, utc_now
from backend.models.user import User
from backend.repositories import (
    growth_repository,
    mood_repository,
    share_repository,
    user_repository,
)
from backend.services.growth_service import (
    PRO_REPORT_PERIODS,
    REPORT_PERIODS,
    growth_service,
)
from backend.services.personality_service import personality_service
from backend.services.quota_service import FREE_TIER

logger = logging.getLogger("closenai.share")

# 注册中文 CID 字体（无需嵌入字体文件）
try:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    _CJK_FONT = "STSong-Light"
except Exception:  # noqa: BLE001
    _CJK_FONT = "Helvetica"

# 导出周期白名单（非法 period → 400）与 Pro 权益周期（PRD P1-F02 §3）统一定义在 growth_service，
# 这里取别名保持原有引用不变，避免两处定义漂移
ALLOWED_PERIODS = REPORT_PERIODS
PRO_PERIODS = PRO_REPORT_PERIODS

# 名片变体（PRD P1-F04 §6）：classic 完整 / minimal 轻量 / combo Pro 三维度组合
CARD_VARIANTS = ("classic", "minimal", "combo")
PRO_CARD_VARIANTS = ("combo",)  # PRD P1-F04 §4：组合名片为 Pro 权益
CARD_LAYOUTS = {"classic": "full", "minimal": "compact", "combo": "combo"}

# MBTI 跨框架关联解读表（PRD P1-F04 §4：内置常见组合解读，如 INTP = 系统构建者）
MBTI_COMBO_ARCHETYPES = {
    "INTP": {"zh": "系统构建者", "en": "System Builder"},
    "INTJ": {"zh": "战略建筑师", "en": "Strategic Architect"},
    "ENTP": {"zh": "创意颠覆者", "en": "Creative Disruptor"},
    "ENTJ": {"zh": "天生统帅", "en": "Born Commander"},
    "INFP": {"zh": "理想治愈者", "en": "Idealistic Healer"},
    "INFJ": {"zh": "洞察引路人", "en": "Insightful Guide"},
    "ENFP": {"zh": "灵感点燃者", "en": "Spark Igniter"},
    "ENFJ": {"zh": "共情领航者", "en": "Empathic Navigator"},
    "ISTP": {"zh": "冷静拆解者", "en": "Calm Disassembler"},
    "ISFJ": {"zh": "温暖守护者", "en": "Warm Guardian"},
    "ESTP": {"zh": "果敢行动派", "en": "Bold Doer"},
    "ESFJ": {"zh": "热忱连接者", "en": "Heartfelt Connector"},
    "ISTJ": {"zh": "秩序坚守者", "en": "Order Keeper"},
    "ISFP": {"zh": "静谧美学家", "en": "Quiet Aesthete"},
    "ESTJ": {"zh": "高效执行官", "en": "Efficient Executive"},
    "ESFP": {"zh": "氛围制造者", "en": "Vibe Maker"},
}

# 标语风格（按变体差异化，保证多张名片不同标语）
_TAGLINE_STYLES = {
    "zh": {
        "classic": "温暖、有洞察，像一位懂你的朋友",
        "minimal": "极简、有力，不超过 12 个字",
        "combo": "深度、跨框架，点出性格内核",
    },
    "en": {
        "classic": "warm and insightful, like a friend who gets you",
        "minimal": "minimal and punchy, no more than 8 words",
        "combo": "deep and cross-framework, pointing to the core of personality",
    },
}


class ShareService:
    """社交分享导出服务。"""

    async def export_personality_card(
        self, db: AsyncSession, user_id: str, language: str = "en", variant: str = "classic"
    ) -> dict:
        """导出性格名片（JSON 结构 + 分享文案）。

        variant（PRD P1-F04 §6 名片变体，用户可生成多张挑选分享）：
        - classic：完整名片（含 AI 观察），layout=full
        - minimal：轻量名片（类型 + 标语），layout=compact
        - combo：Pro 三维度组合名片（MBTI + 星座 + 九型 + 跨框架解读），layout=combo
        """
        if variant not in CARD_VARIANTS:
            raise AppException(
                ErrorCode.INVALID_PARAMS, f"无效的名片变体: {variant} / Invalid card variant"
            )
        user = await user_repository.require_user(db, user_id)
        # Pro 权益门（40302）
        if variant in PRO_CARD_VARIANTS and user.tier == FREE_TIER:
            raise AppException(
                ErrorCode.PRO_REQUIRED,
                "三维度组合名片为 Pro 订阅权益 / Combo card requires Pro subscription",
            )
        profile = await personality_service.get_profile(db, user_id)
        tagline = await self._generate_tagline(profile, variant, language)

        card = {
            "card_type": "personality",
            "variant": variant,
            "layout": CARD_LAYOUTS[variant],
            "user_name": user.name or "ClosenAI 用户",
            "mbti_type": profile.mbti_type,
            "mbti_label": profile.mbti_label,
            "tagline": tagline,
            "unique_traits": profile.unique_traits,
            "streak_count": user.streak_count,
            "relationship_level": user.relationship_level,
            "share_url": settings.app_store_url,
            "exported_at": utc_now().isoformat(),
        }
        if variant in ("classic", "combo"):
            card["observations"] = profile.observations
        if variant == "combo":
            # 星座/九型数据源待 P0-F06 扩展后填充；当前以内置 MBTI 跨框架解读表给出组合洞察
            card["dimensions"] = {
                "mbti": profile.mbti_type,
                "zodiac": None,
                "enneagram": None,
            }
            card["combo_insight"] = self._combo_insight(profile.mbti_type, language)
        card["share_text"] = self._personality_share_text(card, language)
        return card

    async def export_report(
        self, db: AsyncSession, user_id: str, period: str, language: str = "en"
    ) -> dict:
        """导出周期报告（JSON）。

        - period 白名单：weekly / monthly / quarterly / yearly，非法 → 400
        - monthly / quarterly / yearly 为 Pro 权益（40302）
        - 同周期缓存：命中 share_exports 直接返回，不重复调 LLM、不重复落库
        """
        if period not in ALLOWED_PERIODS:
            raise AppException(
                ErrorCode.INVALID_PARAMS, f"无效的导出周期: {period} / Invalid export period"
            )
        user = await user_repository.require_user(db, user_id)
        if period in PRO_PERIODS and user.tier == FREE_TIER:
            raise AppException(
                ErrorCode.PRO_REQUIRED,
                "该周期报告为 Pro 订阅权益 / This report requires Pro subscription",
            )

        period_key = self._period_key(period, user.timezone)
        cached = await share_repository.get_cached(db, user_id, period, period_key)
        if cached is not None:
            return json.loads(cached.payload)

        if period == "weekly":
            # 口径修复：weekly 导出 = 近 7 天周报（此前错配为 30 天月报）
            report = await self._build_weekly_report(db, user, language)
        else:
            report = await growth_service.generate_report(db, user_id, period, language)
        report["share_url"] = settings.app_store_url
        await share_repository.save(
            db, user_id, period, period_key, json.dumps(report, ensure_ascii=False)
        )
        return report

    async def export_report_pdf(
        self, db: AsyncSession, user_id: str, period: str, language: str = "en"
    ) -> bytes:
        """导出周期报告（PDF，中文正常渲染）。复用 JSON 导出缓存，不重复调 LLM。"""
        report = await self.export_report(db, user_id, period, language)
        lines = self._report_lines(report, language)
        default_title = "成长报告" if (language or "").startswith("zh") else "Growth Report"
        return self._render_pdf(report.get("period_label", default_title), lines)

    # ── 周期报告（weekly 专属构建） ──

    async def _build_weekly_report(self, db: AsyncSession, user: User, language: str) -> dict:
        """每周洞察导出（近 7 天窗口）。

        内容复用 cron 已生成的 weekly_summary（P0-F07），不重复调 LLM；
        无周报时回退模板文案。
        """
        from backend.services.mood_service import mood_label_for

        zh = (language or "").startswith("zh")
        checkins = await mood_repository.get_history(db, user.id, days=7)
        dist: dict = {}
        for c in checkins:
            label = c.mood_label or mood_label_for(c.mood_emoji, language)
            dist[label] = dist.get(label, 0) + 1
        if dist:
            total = sum(dist.values())
            if zh:
                parts = [f"{k} {v}天" for k, v in sorted(dist.items(), key=lambda x: -x[1])]
                mood_trend = f"本周共 {total} 次签到，分布：{'，'.join(parts)}。"
            else:
                parts = [f"{k} {v}d" for k, v in sorted(dist.items(), key=lambda x: -x[1])]
                mood_trend = f"{total} check-ins this week: {', '.join(parts)}."
        else:
            mood_trend = "本周暂无情绪签到。" if zh else "No mood check-ins this week."

        weekly = await growth_repository.get_latest(db, user.id, "weekly_summary")
        if weekly and weekly.content:
            content = weekly.content
        elif zh:
            content = "本周还没有生成周报，先记录几天情绪与对话，周报会在周末生成。"
        else:
            content = (
                "No weekly insight yet — record a few days of moods and chats, "
                "and your weekly insight will be ready by the weekend."
            )

        end = local_today(user.timezone)
        start = end - timedelta(days=6)
        return {
            "period": "weekly",
            "period_label": "每周洞察" if zh else "Weekly Insight",
            "range": f"{start.isoformat()} ~ {end.isoformat()}",
            "insight_level": "L2 Pattern",
            "mood_distribution": dist,
            "mood_trend": mood_trend,
            "content": content,
        }

    def _period_key(self, period: str, tz: Optional[str]) -> str:
        """周期键（按用户本地日历）：weekly=ISO 周 / monthly=月 / quarterly=季 / yearly=年。"""
        today = local_today(tz)
        if period == "weekly":
            iso = today.isocalendar()
            return f"{iso[0]}-W{iso[1]:02d}"
        if period == "monthly":
            return f"{today.year}-{today.month:02d}"
        if period == "quarterly":
            return f"{today.year}-Q{(today.month - 1) // 3 + 1}"
        return str(today.year)

    # ── 文案生成 ──

    async def _generate_tagline(self, profile, variant: str, language: str) -> str:
        """AI 个性标语（LLM lite，失败安全降级模板；按变体差异化风格）。"""
        zh = (language or "").startswith("zh")
        mbti = profile.mbti_type or "?"
        traits = profile.unique_traits or ""
        style = _TAGLINE_STYLES["zh" if zh else "en"][variant]
        if zh:
            prompt = (
                f"为性格名片生成一句个性标语，风格：{style}。"
                "只输出标语本身，不要引号、不要解释。\n"
                f"16 型人格类型：{mbti}\n特质：{traits or '未知'}"
            )
        else:
            prompt = (
                f"Write a one-line tagline for a personality card, style: {style}. "
                "Output the tagline only, no quotes, no explanation.\n"
                f"Personality type (16-type framework): {mbti}\nTraits: {traits or 'unknown'}"
            )
        try:
            text = await llm.complete(prompt, language=language, lite=True, max_tokens=60)
            tagline = (text or "").strip().strip('"“”\'')
            if tagline:
                return tagline[:60]
        except Exception as exc:  # noqa: BLE001 - LLM 失败安全降级模板
            logger.warning("名片标语 LLM 失败，回退模板: %s", exc)
        if traits:
            return traits[:60]
        label = profile.mbti_label or ""
        return f"{mbti} · {label}".strip(" ·")

    def _combo_insight(self, mbti_type: Optional[str], language: str) -> Optional[str]:
        """MBTI 跨框架关联解读（PRD P1-F04 §4：内置常见组合解读表）。"""
        archetype = MBTI_COMBO_ARCHETYPES.get(mbti_type or "")
        if not archetype:
            return None
        if (language or "").startswith("zh"):
            return f"跨框架解读：{mbti_type} 指向「{archetype['zh']}」——多种性格框架共同勾勒出你的核心特质。"
        return (
            f"Cross-framework reading: {mbti_type} points to \"{archetype['en']}\" — "
            "multiple personality frameworks converge on your core traits."
        )

    def _personality_share_text(self, card: dict, language: str) -> str:
        tagline = card.get("tagline") or ""
        url = settings.app_store_url
        if (language or "").startswith("zh"):
            label = card.get("mbti_label") or "?"
            mbti = card.get("mbti_type") or "?"
            return (
                f"我是 {card.get('user_name')}，我的性格类型是 {mbti} {label}。"
                f"{tagline} #ClosenAI {url}"
            )
        return (
            f"I am {card.get('user_name')} — my personality type is "
            f"{card.get('mbti_type') or '?'} {card.get('mbti_label') or ''}. "
            f"{tagline} #ClosenAI {url}"
        )

    def _report_lines(self, report: dict, language: str = "en") -> List[str]:
        zh = (language or "").startswith("zh")
        default_title = "成长报告" if zh else "Growth Report"
        range_label = "统计区间：" if zh else "Range: "
        level_label = "洞察等级：" if zh else "Insight level: "
        trend_label = "情绪趋势：" if zh else "Mood trend:"
        content_label = "洞察正文：" if zh else "Insights:"
        lines = [
            report.get("period_label", default_title),
            f"{range_label}{report.get('range', '')}",
            f"{level_label}{report.get('insight_level', '')}",
            "",
            trend_label,
            report.get("mood_trend", ""),
            "",
            content_label,
            report.get("content", ""),
        ]
        return lines

    # ── PDF 渲染（reportlab + UnicodeCIDFont，支持中文）──

    def _render_pdf(self, title: str, lines: List[str]) -> bytes:
        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
            title=title,
        )

        title_style = ParagraphStyle(
            "Title", fontName=_CJK_FONT, fontSize=16, leading=22, spaceAfter=8
        )
        body_style = ParagraphStyle(
            "Body", fontName=_CJK_FONT, fontSize=11, leading=18, spaceAfter=4
        )
        label_style = ParagraphStyle(
            "Label", fontName=_CJK_FONT, fontSize=12, leading=18, spaceBefore=8, spaceAfter=2
        )

        story = [Paragraph(self._escape_xml(title), title_style)]
        for ln in lines:
            if not ln.strip():
                story.append(Spacer(1, 4))
            elif ln.endswith(("：", ":")):
                story.append(Paragraph(self._escape_xml(ln), label_style))
            else:
                story.append(Paragraph(self._escape_xml(ln), body_style))

        doc.build(story)
        return buf.getvalue()

    def _escape_xml(self, text: str) -> str:
        """转义 Paragraph 文本中的 XML 特殊字符。"""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )


share_service = ShareService()

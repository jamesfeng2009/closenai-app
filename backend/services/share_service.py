"""
社交分享导出服务 (P1-F04)。

提供：
- 性格名片导出（JSON，可分享文本文案）
- 周期报告导出（JSON + PDF，中文正常渲染）

PDF 使用 reportlab 的 UnicodeCIDFont（STSong-Light），无需嵌入字体文件。
"""
import logging
from io import BytesIO
from typing import List

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.time_utils import utc_now
from backend.repositories import user_repository
from backend.services.growth_service import growth_service
from backend.services.personality_service import personality_service

logger = logging.getLogger("closenai.share")

# 注册中文 CID 字体（无需嵌入字体文件）
try:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    _CJK_FONT = "STSong-Light"
except Exception:  # noqa: BLE001
    _CJK_FONT = "Helvetica"


class ShareService:
    """社交分享导出服务。"""

    async def export_personality_card(
        self, db: AsyncSession, user_id: str, language: str = "en"
    ) -> dict:
        """导出性格名片（JSON 结构 + 分享文案）。"""
        user = await user_repository.get_or_create_user(db, user_id)
        profile = await personality_service.get_profile(db, user_id)

        card = {
            "card_type": "personality",
            "user_name": user.name or "ClosenAI 用户",
            "mbti_type": profile.mbti_type,
            "mbti_label": profile.mbti_label,
            "observations": profile.observations,
            "unique_traits": profile.unique_traits,
            "streak_count": user.streak_count,
            "relationship_level": user.relationship_level,
            "exported_at": utc_now().isoformat(),
        }
        card["share_text"] = self._personality_share_text(card, language)
        return card

    async def export_report(
        self, db: AsyncSession, user_id: str, period: str, language: str = "en"
    ) -> dict:
        """导出周期报告（JSON）。"""
        return await growth_service.generate_report(db, user_id, period, language)

    async def export_report_pdf(
        self, db: AsyncSession, user_id: str, period: str, language: str = "en"
    ) -> bytes:
        """导出周期报告（PDF，中文正常渲染）。"""
        report = await self.export_report(db, user_id, period, language)
        lines = self._report_lines(report)
        return self._render_pdf(report.get("period_label", "成长报告"), lines)

    # ── 文案生成 ──

    def _personality_share_text(self, card: dict, language: str) -> str:
        if language == "zh" or card.get("mbti_label"):
            label = card.get("mbti_label") or "?"
            mbti = card.get("mbti_type") or "?"
            traits = card.get("unique_traits") or ""
            return (
                f"我是 {card.get('user_name')}，我的性格类型是 {mbti} {label}。"
                f"{traits} #ClosenAI"
            )
        return (
            f"I am {card.get('user_name')} — my MBTI type is "
            f"{card.get('mbti_type') or '?'} {card.get('mbti_label') or ''}. "
            f"#ClosenAI"
        )

    def _report_lines(self, report: dict) -> List[str]:
        lines = [
            report.get("period_label", "成长报告"),
            f"统计区间：{report.get('range', '')}",
            f"洞察等级：{report.get('insight_level', '')}",
            "",
            "情绪趋势：",
            report.get("mood_trend", ""),
            "",
            "洞察正文：",
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
            elif ln.endswith("：") or ln in ("情绪趋势：", "洞察正文："):
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
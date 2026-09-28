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
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import List, Optional, Tuple

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
from backend.services.safety_service import safety_service
from backend.models.user import User
from backend.repositories import (
    chat_repository,
    entitlement_repository,
    growth_repository,
    memory_repository,
    milestone_repository,
    mood_repository,
    share_repository,
    user_repository,
)
from backend.services.growth_service import (
    PRO_REPORT_PERIODS,
    REPORT_PERIODS,
    growth_service,
)
from backend.services.first_read_service import first_read_service
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


# ── Life Archive 一次性买断导出（架构方案 §二 M6）──

# 全量档（all / legacy）用"epoch 起算"表达不设窗口，SQL 与 Python 比较都走同一口径
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ALL_HISTORY_DAYS = 3650 * 3  # 10 年：档案的最长现实跨度


def _as_aware(value: datetime) -> datetime:
    """DB 读出的 datetime 在 SQLite 下不带 tzinfo，统一补 UTC 后才能与 utc_now() 比较。"""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# share_exports.period 取值（String(20) 内）
LIFE_ARCHIVE_PERIOD = "life_archive"
# 三档导出窗口：1y=近 365 天，all=全量，legacy=全量 + 跨年对比 + 里程碑全史
LIFE_ARCHIVE_SCOPES = ("1y", "all", "legacy")
LIFE_ARCHIVE_WINDOW_DAYS = {"1y": 365, "all": None, "legacy": None}
# 每章条目上限：档案是"纪念品"不是数据备份，超长导出既慢又没人读
LIFE_ARCHIVE_ITEM_LIMIT = {"1y": 60, "all": 200, "legacy": 300}

# AIGC 显式标识（GB 45438-2025）：LLM 产出的章节必须逐章标注 + 文档级声明。
# 用户原话/确定性统计不标注，避免把用户自己的话标成 AI 内容。
AIGC_CHAPTER_KEYS = ("portrait", "patterns", "insights")
AIGC_MARK = {
    "zh": "［AI 生成］",
    "en": " [AI-GENERATED]",
}
AIGC_NOTICE = {
    "zh": (
        "本档案中标注「AI 生成」的章节由人工智能根据你在 ClosenAI 的记录生成，"
        "依据 GB 45438-2025《网络安全技术 生成式人工智能服务安全基本要求》显式标识；"
        "其余内容为你本人的原始记录与确定性统计。"
    ),
    "en": (
        "Chapters marked [AI-GENERATED] in this archive were produced by artificial "
        "intelligence from your ClosenAI records and are labelled as such per "
        "GB 45438-2025 (Cybersecurity Technology - Security Basic Requirements for "
        "Generative AI Services). All other content is your own raw record data and "
        "deterministic statistics."
    ),
}

_ARCHIVE_TITLES = {
    "zh": {
        "overview": "档案概览",
        "portrait": "性格画像",
        "voice": "你说过的话",
        "mood": "情绪轨迹",
        "patterns": "反复出现的模式",
        "insights": "周期洞察",
        "milestones": "里程碑",
        "cross_year": "跨年对比",
    },
    "en": {
        "overview": "Archive Overview",
        "portrait": "Personality Portrait",
        "voice": "In Your Own Words",
        "mood": "Mood Trajectory",
        "patterns": "Recurring Patterns",
        "insights": "Periodic Insights",
        "milestones": "Milestones",
        "cross_year": "Year-over-Year Comparison",
    },
}

_ARCHIVE_EMPTY = {
    "zh": "（这段时间还没有记录）",
    "en": "(Nothing recorded for this window yet)",
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

    async def export_first_read_card_pdf(
        self, db: AsyncSession, user_id: str
    ) -> bytes:
        """初读卡分享 PDF（架构方案 §二 M1「复用 SVG→PDF 管线」）。

        v1 先用既有 reportlab 文本管线出 PDF；QR + 水印 + 深链 `closenai.app/r/{code}`
        属 M4 分享卡体系（B4），届时替换本方法内部渲染，端点签名不变。
        """
        card = await first_read_service.restore(db, user_id)
        if card is None:
            raise AppException(
                ErrorCode.RESOURCE_NOT_FOUND,
                "尚未生成初读卡 / First-read card not generated yet",
            )
        language = card.get("language") or "en"
        zh = (language or "").startswith("zh")
        title = "性格初读卡" if zh else "First Read"
        return self._render_pdf(title, self._first_read_lines(card, zh))

    def _first_read_lines(self, card: dict, zh: bool) -> List[str]:
        assertions = card.get("assertions") or []
        lines: List[str] = [
            "你写下第一条记录之前，它读到的只有你自己给的话。" if zh
            else "Before your first record, all it read were the words you gave yourself."
        ]
        lines.append("")
        lines.extend(f"· {text}" for text in assertions)
        summary = card.get("summary") or ""
        if summary:
            lines.append("")
            lines.append(summary)
        closer = card.get("closer") or ""
        if closer:
            lines.append("")
            lines.append(closer)
        lines.append("")
        lines.append(card.get("share_url") or settings.app_store_url)
        return lines

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

    # ── Life Archive 导出（M6 · 一次性买断）──

    async def export_life_archive(
        self,
        db: AsyncSession,
        user_id: str,
        scope: str = "all",
        language: str = "en",
        regenerate: bool = False,
    ) -> dict:
        """导出 Life Archive（章节化 JSON，PDF 渲染共用同一份数据）。

        权益口径（架构方案 §九-4：订阅卖"活体"，买断卖"标本"）：
        - 持有 ≥ scope 档买断 → 放行，缓存键 = scope（同档幂等，不重复渲染）
        - Pro/Pro+ 订阅期内 → 每月 1 次免费生成（联动权益），缓存键带 YYYY-MM
        - 两者都不满足 → 40304 ENTITLEMENT_REQUIRED
        """
        if scope not in LIFE_ARCHIVE_SCOPES:
            raise AppException(
                ErrorCode.INVALID_PARAMS, f"无效的导出档位: {scope} / Invalid archive scope"
            )
        user = await user_repository.require_user(db, user_id)
        period_key, gate = await self._life_archive_gate(db, user, scope)

        cached = await share_repository.get_cached(
            db, user_id, LIFE_ARCHIVE_PERIOD, period_key
        )
        if cached is not None and not regenerate:
            payload = json.loads(cached.payload)
            payload["from_cache"] = True
            return payload

        archive = await self._build_life_archive(db, user, scope, language, gate)
        raw = json.dumps(archive, ensure_ascii=False)
        if cached is not None:
            await share_repository.overwrite(db, cached, raw)
        else:
            await share_repository.save(db, user_id, LIFE_ARCHIVE_PERIOD, period_key, raw)
        return archive

    async def export_life_archive_pdf(
        self,
        db: AsyncSession,
        user_id: str,
        scope: str = "all",
        language: str = "en",
        regenerate: bool = False,
    ) -> bytes:
        """导出 Life Archive（PDF）。复用 JSON 导出缓存，权益判定同走一次。"""
        archive = await self.export_life_archive(db, user_id, scope, language, regenerate)
        return self._render_pdf(
            self._life_archive_title(archive), self._life_archive_lines(archive)
        )

    async def _life_archive_gate(
        self, db: AsyncSession, user: User, scope: str
    ) -> Tuple[str, str]:
        """返回 (缓存键, 放行来源)；无权益抛 40304。

        买断判定只看 user_entitlements.revoked_at IS NULL（退款即回收），
        不看 tier——买 Life Archive 不等于订阅，反之亦然。
        """
        if await entitlement_repository.has(db, user.id, scope):
            return scope, "entitlement"
        if user.tier != FREE_TIER:
            month = local_today(user.timezone).strftime("%Y-%m")
            return f"{scope}:{month}", "subscription"
        raise AppException(
            ErrorCode.ENTITLEMENT_REQUIRED,
            "Life Archive 为一次性买断（Pro/Pro+ 订阅期内每月可免费生成 1 次）"
            " / Life Archive requires a one-time purchase (Pro subscribers get one free export per month)",
        )

    async def _build_life_archive(
        self, db: AsyncSession, user: User, scope: str, language: str, gate: str
    ) -> dict:
        """组装档案数据：用户原始记录 + 既有 AI 产出，不新增 LLM 调用。

        "无新增生成"是 M6 的合规前提（架构方案 §二 M6）——档案里出现的所有
        叙述性文字都是此前已生成并落库的内容，导出只负责编排与标识。
        """
        zh = (language or "").startswith("zh")
        window_days = LIFE_ARCHIVE_WINDOW_DAYS[scope]
        window_start = (
            _EPOCH if window_days is None else utc_now() - timedelta(days=window_days)
        )
        item_limit = LIFE_ARCHIVE_ITEM_LIMIT[scope]

        memories = await memory_repository.list(db, user.id, limit=item_limit)
        if window_days is not None:
            memories = [m for m in memories if _as_aware(m.created_at) >= window_start]
        patterns = await growth_repository.list_recent(
            db, user.id, "pattern_discovery",
            days=window_days or _ALL_HISTORY_DAYS, limit=item_limit,
        )
        weekly = await growth_repository.list_recent(
            db, user.id, "weekly_summary",
            days=window_days or _ALL_HISTORY_DAYS, limit=item_limit,
        )
        daily = await growth_repository.list_recent(
            db, user.id, "daily_insight",
            days=window_days or _ALL_HISTORY_DAYS, limit=item_limit,
        )
        checkins = await mood_repository.get_history(
            db, user.id, days=window_days or _ALL_HISTORY_DAYS
        )
        milestones = await milestone_repository.list_for_user(db, user.id)
        if window_days is not None:
            milestones = [
                m for m in milestones if _as_aware(m.achieved_at) >= window_start
            ]

        stats = {
            "active_days": user.active_days,
            "chat_count": user.chat_count,
            "memory_count": await memory_repository.count_since(db, user.id, window_start),
            "checkin_count": await mood_repository.count_since(db, user.id, window_start),
            "message_count": await chat_repository.count_since(db, user.id, window_start),
        }

        chapters: List[dict] = [
            self._chapter(zh, "overview", self._overview_items(stats, scope, zh)),
            self._chapter(zh, "portrait", await self._portrait_items(db, user.id, language)),
            self._chapter(zh, "voice", self._voice_items(memories)),
            self._chapter(zh, "mood", self._mood_items(checkins, language, zh)),
            self._chapter(zh, "patterns", self._record_items(patterns)),
            self._chapter(
                zh,
                "insights",
                self._record_items(weekly) + self._record_items(daily),
            ),
            self._chapter(zh, "milestones", self._milestone_items(milestones, language, zh)),
        ]
        if scope == "legacy":
            chapters.append(
                self._chapter(zh, "cross_year", self._cross_year_items(checkins, memories, zh))
            )

        return {
            "archive_type": "life_archive",
            "scope": scope,
            "gate": gate,
            "language": language,
            "window_days": window_days,
            "generated_at": utc_now().isoformat(),
            "stats": stats,
            "chapters": chapters,
            "aigc_mark": AIGC_MARK["zh" if zh else "en"],
            "aigc_notice": AIGC_NOTICE["zh" if zh else "en"],
            "share_url": settings.app_store_url,
        }

    def _chapter(self, zh: bool, key: str, items: List[str]) -> dict:
        """章节对象：AIGC 标识随章节走（GB 45438 要求显式标识落在内容上）。"""
        generated = key in AIGC_CHAPTER_KEYS
        return {
            "key": key,
            "title": _ARCHIVE_TITLES["zh" if zh else "en"][key],
            "aigc_generated": generated,
            "aigc_label": AIGC_MARK["zh" if zh else "en"] if generated else None,
            "items": items,
        }

    def _overview_items(self, stats: dict, scope: str, zh: bool) -> List[str]:
        if zh:
            return [
                f"档案档位：{scope}",
                f"累计活跃 {stats['active_days']} 天",
                f"对话消息 {stats['message_count']} 条",
                f"情绪签到 {stats['checkin_count']} 次",
                f"长期记忆 {stats['memory_count']} 条",
            ]
        return [
            f"Archive scope: {scope}",
            f"{stats['active_days']} active days in total",
            f"{stats['message_count']} chat messages",
            f"{stats['checkin_count']} mood check-ins",
            f"{stats['memory_count']} long-term memories",
        ]

    async def _portrait_items(
        self, db: AsyncSession, user_id: str, language: str
    ) -> List[str]:
        profile = await personality_service.get_profile(db, user_id)
        items: List[str] = []
        if profile.mbti_type:
            label = profile.mbti_label or ""
            items.append(f"{profile.mbti_type} · {label}".strip(" ·"))
        if profile.unique_traits:
            items.append(profile.unique_traits)
        for obs in profile.observations or []:
            text = obs if isinstance(obs, str) else json.dumps(obs, ensure_ascii=False)
            items.append(text)
        return items

    def _voice_items(self, memories) -> List[str]:
        """用户原话（不是 AI 产出，故不加 AIGC 标识）。"""
        out: List[str] = []
        for m in memories:
            day = _as_aware(m.created_at).date().isoformat()
            out.append(f"{day} · {m.content}")
        return out

    def _mood_items(self, checkins, language: str, zh: bool) -> List[str]:
        from backend.services.mood_service import mood_label_for

        dist: dict = {}
        for c in checkins:
            label = c.mood_label or mood_label_for(c.mood_emoji, language)
            dist[label] = dist.get(label, 0) + 1
        if not dist:
            return [_ARCHIVE_EMPTY["zh" if zh else "en"]]
        parts = sorted(dist.items(), key=lambda x: -x[1])
        unit = "次" if zh else "times"
        return [f"{label} × {count} {unit}".strip() for label, count in parts]

    def _record_items(self, records) -> List[str]:
        out: List[str] = []
        for r in records:
            day = _as_aware(r.created_at).date().isoformat()
            out.append(f"{day} · {r.content}")
        return out

    def _milestone_items(self, milestones, language: str, zh: bool) -> List[str]:
        from backend.services.user_service import user_service

        if not milestones:
            return [_ARCHIVE_EMPTY["zh" if zh else "en"]]
        out: List[str] = []
        for m in milestones:
            day = _as_aware(m.achieved_at).date().isoformat()
            name = user_service.milestone_name(m.milestone_type, language)
            out.append(f"{day} · {name}")
        return out

    def _cross_year_items(self, checkins, memories, zh: bool) -> List[str]:
        """跨年对比：按自然年统计量（确定性计数，非 AI 推断）。"""
        buckets: dict = {}
        for c in checkins:
            year = _as_aware(c.created_at).year
            buckets.setdefault(year, {"checkins": 0, "memories": 0})["checkins"] += 1
        for m in memories:
            year = _as_aware(m.created_at).year
            buckets.setdefault(year, {"checkins": 0, "memories": 0})["memories"] += 1
        if len(buckets) < 2:
            return [
                "跨年对比需要至少两个自然年的记录。" if zh
                else "Year-over-year comparison needs records from at least two calendar years."
            ]
        out: List[str] = []
        for year in sorted(buckets):
            b = buckets[year]
            if zh:
                out.append(f"{year} 年：情绪签到 {b['checkins']} 次，记忆 {b['memories']} 条")
            else:
                out.append(
                    f"{year}: {b['checkins']} mood check-ins, {b['memories']} memories"
                )
        return out

    def _life_archive_title(self, archive: dict) -> str:
        zh = (archive.get("language") or "").startswith("zh")
        label = _ARCHIVE_TITLES["zh" if zh else "en"]
        scope = archive.get("scope", "all")
        if zh:
            return f"人生档案（{scope}）· {label['overview']}"
        return f"Life Archive ({scope}) — {label['overview']}"

    def _life_archive_lines(self, archive: dict) -> List[str]:
        """PDF 文本行：文档级 AIGC 声明 + 逐章标题带标识。"""
        zh = (archive.get("language") or "").startswith("zh")
        stats = archive.get("stats") or {}
        lines: List[str] = [archive.get("aigc_notice", ""), ""]
        lines.append(
            f"记录概况：累计活跃 {stats.get('active_days', 0)} 天，"
            f"记忆 {stats.get('memory_count', 0)} 条，签到 {stats.get('checkin_count', 0)} 次"
            if zh
            else
            f"Overview: {stats.get('active_days', 0)} active days, "
            f"{stats.get('memory_count', 0)} memories, "
            f"{stats.get('checkin_count', 0)} check-ins"
        )
        lines.append("")
        for chapter in archive.get("chapters", []):
            mark = chapter.get("aigc_label") or ""
            lines.append(f"{chapter.get('title', '')}{mark}".strip())
            items = chapter.get("items") or []
            if not items:
                lines.append(_ARCHIVE_EMPTY["zh" if zh else "en"])
            for item in items:
                lines.append(f"· {item}")
            lines.append("")
        lines.append(archive.get("aigc_notice", ""))
        return lines

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
                f"{safety_service.boundary(language)}\n"
                f"{safety_service.evidence_policy(language)}\n"
                f"16 型人格类型：{mbti}\n特质：{traits or '未知'}"
            )
        else:
            prompt = (
                f"Write a one-line tagline for a personality card, style: {style}. "
                "Output the tagline only, no quotes, no explanation.\n"
                f"{safety_service.boundary(language)}\n"
                f"{safety_service.evidence_policy(language)}\n"
                f"Personality type (16-type framework): {mbti}\nTraits: {traits or 'unknown'}"
            )
        try:
            text = await llm.complete(prompt, language=language, lite=True, max_tokens=60)
            tagline = (text or "").strip().strip('"“”\'')
            # L3 输出过滤：标语是对外分享内容，命中则降级为本地模板（措辞规范 §1.1 D 级）
            if tagline:
                tagline = await safety_service.check_or(
                    tagline, language, "", scope="assertion"
                )
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

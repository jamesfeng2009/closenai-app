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
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from typing import List, Optional, Tuple

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer
import qrcode
import qrcode.image.pil
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core import copy
from backend.core import generation_run
from backend.core.errors import AppException, ErrorCode
from backend.ai.llm import llm
from backend.core.referral import normalize_referral_code, referral_link
from backend.core.time_utils import (
    local_day_start_utc,
    local_now,
    local_today,
    safe_zoneinfo,
    utc_now,
)
from backend.ai.safety import safety_service
from backend.models.user import User
from backend.repositories import (
    chat_repository,
    entitlement_repository,
    growth_repository,
    memory_repository,
    milestone_repository,
    mood_repository,
    personality_repository,
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

# ── 分享卡品牌要素（架构方案 §二 M4：统一品牌水印 + 下载文案 + 深链）──
# 文案口径：说"它记得你说过什么"是产品的既有事实（记忆库），不是能力承诺；
# 不写"帮你修复关系/读懂 TA"——那是措辞规范 §一 禁的因果断言式卖点。
# 水印/下载文案的 15 语言展示走文案库（core/copy.py，share.brand.*）。

# MBTI 跨框架关联解读（PRD P1-F04 §4）：内置常见组合（如 INTP = 系统构建者），
# 组合名的 15 语言展示走文案库 share.archetype.{MBTI}，此处仅保留合法键集。
_ARCHETYPE_KEYS = frozenset({
    "INTP", "INTJ", "ENTP", "ENTJ", "INFP", "INFJ", "ENFP", "ENFJ",
    "ISTP", "ISFJ", "ESTP", "ESFJ", "ISTJ", "ISFP", "ESTJ", "ESFP",
})

# AIGC 隐式标识（GB 45438-2025）：含 LLM 产出的分享卡在 PDF 元数据里标注。
# 与 Life Archive 的逐章显式标识互补：分享卡是传播物，版面上印声明会毁掉设计，
# 但元数据跟着文件走，转发也甩不掉。
AIGC_DOC_METADATA = {
    "subject": "Contains AI-generated content (GB 45438-2025)",
    "keywords": "AIGC;AI-generated",
}


def _aigc_metadata(runs: Optional[dict] = None) -> dict:
    """AIGC 元数据：有生成留痕时把模型与 run id 一起写进 PDF（O4）。

    声明只说"含 AI 生成内容"是不够的——GB 45438 要求可追溯，元数据带 run id
    才能从一份转发出去的 PDF 回查到 ai_generation_runs 的具体那次调用。
    """
    meta = dict(AIGC_DOC_METADATA)
    models = (runs or {}).get("models") or []
    ids = (runs or {}).get("run_ids") or []
    if models:
        meta["creator"] = "AI: " + ", ".join(models[:4])
        meta["keywords"] = f"{meta['keywords']}; {'; '.join(ids[:8])}"
    return meta

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

# 名片变体（PRD P1-F04 §6）：classic 完整 / minimal 轻量 / combo Pro 双维度组合
CARD_VARIANTS = ("classic", "minimal", "combo")
PRO_CARD_VARIANTS = ("combo",)  # PRD P1-F04 §4：组合名片为 Pro 权益
CARD_LAYOUTS = {"classic": "full", "minimal": "compact", "combo": "combo"}

# 标语风格（按变体差异化，保证多张名片不同标语；LLM prompt 风格指令，不强翻）
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
# 标识与声明的 15 语言展示走文案库（share.aigc.mark / share.aigc.notice）。
AIGC_CHAPTER_KEYS = ("portrait", "patterns", "insights")


# ── 记忆考古（§10.1 ①）──
# 逐往年窗口查询上限：考古是「N 年前的今天」，回看 3 年足够唤起回忆，
# 再往前旧记录密度低且查询成本线性增长
ARCHAEOLOGY_MAX_YEARS = 3


# ── 年度报告卡（架构方案 §二 M4 year_in_review 模板位）──
# share_exports.period 列是 String(20)，"year_in_review" 14 字符放得下；
# period_key 用 year_2025_en 而不是裸 2025：与周期报告的 yearly（键就是年份）区分开，
# 否则年报卡会把「年度人生报告」的缓存吃掉；语种后缀见 _year_in_review_key。
_YEAR_IN_REVIEW_PERIOD = "year_in_review"

_YEAR_IN_REVIEW_EMPTY = {
    "zh": (
        "{year} 年还没有可回顾的记录 / Nothing to review yet for {year}"
    ),
    "en": "Nothing to review yet for {year} / {year} 年还没有可回顾的记录",
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
        - combo：Pro 双维度组合名片（MBTI + 九型 + 跨框架解读），layout=combo
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
                "双维度组合名片为 Pro 订阅权益 / Combo card requires Pro subscription",
            )
        profile = await personality_service.get_profile(db, user_id)
        tagline = await self._generate_tagline(profile, variant, language)
        # 分享文案里的链接统一走带本人推荐码的深链（决策点 3）：直接给 App Store 链接
        # 既没有 Universal Links 唤起，也没有归因——分享卡就退化成一张无人可追的海报。
        share_url = await self._referral_link(db, user)

        card = {
            "card_type": "personality",
            "variant": variant,
            "layout": CARD_LAYOUTS[variant],
            "user_name": user.name or copy.t("share", "personality.default_name", language),
            "mbti_type": profile.mbti_type,
            "mbti_label": profile.mbti_label,
            "tagline": tagline,
            "unique_traits": profile.unique_traits,
            "streak_count": user.streak_count,
            "relationship_level": user.relationship_level,
            "share_url": share_url,
            "exported_at": utc_now().isoformat(),
        }
        if variant in ("classic", "combo"):
            card["observations"] = profile.observations
        if variant == "combo":
            # 组合名片 = 16 型 × 九型两框架（占星已随 M7 下线）。九型取 users.enneagram_type
            # ——那是 36 题评估完成时写回的真值；填 None 会让「组合」卡退化成单框架还挂着两框架的名头。
            card["dimensions"] = {
                "mbti": profile.mbti_type,
                "enneagram": user.enneagram_type,
            }
            card["combo_insight"] = self._combo_insight(profile.mbti_type, language)
        card["share_text"] = self._personality_share_text(card, language)
        return card

    async def export_first_read_card_pdf(
        self, db: AsyncSession, user_id: str
    ) -> bytes:
        """初读卡分享 PDF（架构方案 §二 M1 复用导出管线 + §二 M4 品牌要素）。

        B1 登记的偏离已在此兑现：品牌水印 + 下载文案 + 带本人推荐码的深链二维码
        （M4），端点签名不变。
        """
        card = await first_read_service.restore(db, user_id)
        if card is None:
            raise AppException(
                ErrorCode.RESOURCE_NOT_FOUND,
                "尚未生成初读卡 / First-read card not generated yet",
            )
        language = card.get("language") or "en"
        title = copy.t("share", "first_read.title", language)
        brand = await self._brand_for(db, user_id, (language or "").startswith("zh"))
        # 初读卡全文由静态模板直出、零 LLM，故不打 AIGC 元数据标识
        return self._render_pdf(
            title, self._first_read_lines(card, language), brand=brand
        )

    def _first_read_lines(self, card: dict, language: str) -> List[str]:
        assertions = card.get("assertions") or []
        lines: List[str] = [copy.t("share", "first_read.intro", language)]
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
        # 落地链接不在正文里印：品牌区（水印 + QR + 明文短链）统一承载，印两遍会打架
        return lines

    # ── 记忆卡 / 年报占位（架构方案 §二 M4 模板清单）──

    async def export_memory_card_pdf(
        self,
        db: AsyncSession,
        user_id: str,
        memory_id: str,
        *,
        today: Optional[datetime] = None,
        tz: Optional[str] = None,
    ) -> bytes:
        """记忆卡分享 PDF：用户自己写过的一句原话 + 写下日期 + 品牌深链（M4）。

        原话不过措辞闸：闸是给 AI 产出兜底的，用户陈述自己的话被正则误杀会让
        功能整体发不出去（M2 同一结论，见 journey_service._gate_tpl）。
        归属校验在 memory_repository.get 内强制 user_id，取别人的 id 得到 None。

        周年标题（§10.1 ① 记忆考古，C3 偏离 2 根治）：服务端按用户本地日界
        自动判定「N 年前的今天」，周年才写周年标题——任何调用路径都不会造出
        假周年；非周年保持原模板「你说过的话」。
        """
        memory = await memory_repository.get(db, memory_id, user_id)
        if memory is None:
            raise AppException(
                ErrorCode.MEMORY_NOT_FOUND, "记忆不存在 / Memory not found"
            )
        user = await user_repository.get_user(db, user_id)
        language = ((user.language if user else "") or "en")
        title = self._memory_card_title(memory, user, today, tz, language)
        brand = await self._brand_for(db, user_id, (language or "").startswith("zh"))
        return self._render_pdf(
            title, self._memory_card_lines(memory, language), brand=brand
        )

    def _memory_card_title(
        self,
        memory,
        user,
        today: Optional[datetime],
        tz: Optional[str],
        language: str,
    ) -> str:
        """周年标题判定：同月日且年份差 ≥1（按用户本地时区）→「N 年前的今天」。"""
        zone = safe_zoneinfo(tz or (getattr(user, "timezone", None) or "UTC"))
        created = memory.created_at
        if created is None:
            return copy.t("share", "memory.title", language)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        today_local = (today or utc_now()).astimezone(zone)
        created_local = created.astimezone(zone)
        years_ago = today_local.year - created_local.year
        if (
            years_ago >= 1
            and created_local.month == today_local.month
            and created_local.day == today_local.day
        ):
            if years_ago == 1:
                return copy.t("share", "memory.anniversary_one", language)
            return copy.t("share", "memory.anniversary_n", language).format(n=years_ago)
        return copy.t("share", "memory.title", language)

    async def on_this_day(
        self,
        db: AsyncSession,
        user_id: str,
        today: Optional[datetime] = None,
        tz: Optional[str] = None,
    ) -> dict:
        """记忆考古候选（§10.1 ①）：「N 年前的今天」的原话列表（首页每日主位）。

        逐往年窗口查询（本地日界，时区铁律）：内容全部来自用户历史数据，
        零 LLM；按年份升序（1 年前优先）。新用户/无周年记忆 → 空候选。
        每条候选带当日情绪 mood（回忆卡色环数据源；当日无签到 → null）。
        """
        user = await user_repository.get_user(db, user_id)
        if user is None:
            raise AppException(ErrorCode.USER_NOT_FOUND, "用户不存在 / User not found")
        tz_name = tz or user.timezone or "UTC"
        zone = safe_zoneinfo(tz_name)
        today_local = (today or utc_now()).astimezone(zone)

        candidates = []
        for years_ago in range(1, ARCHAEOLOGY_MAX_YEARS + 1):
            try:
                local_day = date(
                    today_local.year - years_ago, today_local.month, today_local.day
                )
            except ValueError:  # 2/29 在非闰年不存在：该年无周年
                continue
            start = local_day_start_utc(tz_name, local_day)
            end = local_day_start_utc(tz_name, local_day + timedelta(days=1))
            rows = await memory_repository.list_between(db, user_id, start, end, limit=5)
            if not rows:
                continue
            # 同窗口再取一次当日签到：回忆卡第四模板的情绪色环数据源
            checkins = await mood_repository.list_between(db, user_id, start, end)
            mood = self._day_mood(checkins, user.language or "en")
            candidates += [
                {
                    "memory_id": r.id,
                    "content": r.content,
                    "years_ago": years_ago,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "mood": mood,
                }
                for r in rows
            ]
        return {"date": today_local.date().isoformat(), "candidates": candidates}

    def _day_mood(self, checkins: List, language: str) -> Optional[dict]:
        """当日首个签到 → 色环数据（§10.1 ① 回忆卡第四模板）。

        取首条即「那天第一次记下的心情」：`list_between` 按 created_at 升序，与
        memories 用同一条本地日界窗口（时区铁律同口径）。无签到返回 None——
        前端色环走中性降级，不拿别日的签到冒充当天。
        value 复用 growth_service 同一张分值表与同一个折中口径（表外 emoji 记 3），
        不另建第二份 emoji→分值映射。
        """
        if not checkins:
            return None
        from backend.services.mood_service import MOOD_MAP, mood_label_for

        first = checkins[0]
        return {
            "emoji": first.mood_emoji,
            "label": first.mood_label or mood_label_for(first.mood_emoji, language),
            "value": MOOD_MAP.get(first.mood_emoji, (None, 3))[1],
        }

    async def get_share_link(self, db: AsyncSession, user_id: str) -> dict:
        """本人深链与推荐码（App 内各分享入口统一取这里，不各自拼 URL）。

        读接口带一次写：首次调用即生成 referral_code（惰性归因，见 M4 迁移注释）。
        """
        user = await user_repository.get_user(db, user_id)
        if user is None:
            raise AppException(ErrorCode.USER_NOT_FOUND, "用户不存在 / User not found")
        code = await user_repository.ensure_referral_code(db, user)
        return {
            "referral_code": code,
            "link": referral_link(settings.site_url, code),
            # U3-b：这个字段回的是「点了会去哪」，不是「这是 App Store」——
            # 上架前后同一份响应里它分别指向站点与商店，客户端不必知道有没有上架。
            "app_store_url": settings.share_landing_url,
        }

    async def _referral_link(self, db: AsyncSession, user: User) -> str:
        """带本人推荐码的深链；副作用同 get_share_link（首次取链接即落码）。"""
        code = await user_repository.ensure_referral_code(db, user)
        return referral_link(settings.site_url, code)

    async def resolve_referral(self, db: AsyncSession, code: str) -> dict:
        """推荐码 → 落地信息（公开端点：CF Pages 落地页与 App 冷启动共用）。

        只回答「码是否有效、该跳去哪」，不回分享者的任何资料——一个陌生码不该
        变成探测「某人是否在用这个 App」的入口。查不到与非法码返回同一个
        valid=false，不区分（否则可用来枚举有效码）。
        """
        normalized = normalize_referral_code(code)
        valid = False
        if normalized:
            referrer = await user_repository.get_by_referral_code(db, normalized)
            valid = referrer is not None
        return {
            "valid": valid,
            "app_store_url": settings.share_landing_url,
            # 已安装时由 Universal Links 直接打开 App（AASA 的 /r/* 规则），
            # 未安装才需要落地页，因此两个 url 一起给，由调用方决定顺序。
            # 非法码回站点根而不是 `…/r/`——落地页拿到尾巴为空的链接会渲染出一个坏链。
            "universal_link": (
                referral_link(settings.site_url, normalized) if normalized else settings.site_url
            ),
        }

    def _memory_card_lines(self, memory, language: str) -> List[str]:
        """记忆卡正文：不写"一年前的今天"——本方法不假设被选中的记忆有多旧，
        周年选取规则属 C4（§10.1 记忆考古），这里只保证一句原话 + 它的日期。"""
        date_label = memory.created_at.strftime("%Y-%m-%d") if memory.created_at else ""
        return [
            copy.t("share", "memory.intro", language),
            "",
            f"「{memory.content.strip()}」",
            "",
            date_label,
        ]

    async def export_year_in_review(
        self, db: AsyncSession, user_id: str, year: Optional[int] = None, language: str = "en"
    ) -> dict:
        """年度报告卡（JSON）：一个自然年的确定性统计 + 分享深链（架构方案 §二 M4 的模板位）。

        年度取哪一个（默认上一个完整自然年）与空年口径见 _resolve_review_year；
        整年没有任何记录 → 40402 空态，不出数字全 0 的卡片。

        同 (user, year, 语种) 命中 share_exports 缓存：卡上的数字全部来自落库事实，
        一年内重算结果本就一致（新增记录也只在当年才会改变它），重算只是白读一遍库。
        语种进键是因为卡上的日期标签、区间措辞与 share_text 都是按语种生成的，
        不进键就会让换语言的用户拿到首次生成那个语种的卡（缓存按规范化语言码，
        zh / zh-CN 命中同一条）。
        """
        user = await user_repository.require_user(db, user_id)
        lang = language or user.language or "en"
        target = self._resolve_review_year(user, year)
        period_key = self._year_in_review_key(target, lang)
        cached = await share_repository.get_cached(
            db, user_id, _YEAR_IN_REVIEW_PERIOD, period_key
        )
        if cached is not None:
            return json.loads(cached.payload)

        data = await self._build_year_in_review(db, user, target, lang)
        data["share_url"] = await self._referral_link(db, user)
        data["share_text"] = self._year_in_review_share_text(data)
        await share_repository.save(
            db, user_id, _YEAR_IN_REVIEW_PERIOD, period_key,
            json.dumps(data, ensure_ascii=False),
        )
        return data

    def _resolve_review_year(self, user: User, year: Optional[int]) -> int:
        """要回顾哪一年。

        缺省取**上一个完整自然年**：产品口径是 1 月 1 日推年度回顾
        （competitive-analysis §Year in Review），过完的那一年才配叫「年度」。
        但刚注册不满一年的用户没有"上一年"可看——回 400 是在怪用户提了一个他没提的
        请求，所以这里退到当年（一张如实标注「进行中」的半年卡），有没有数据交给空态判。
        显式传 year 时才是校验：早于注册那年或未来年，请求本身不成立 → 400。
        """
        now_local = local_now(user.timezone)
        current = now_local.year
        registered = _as_aware(user.created_at).astimezone(now_local.tzinfo).year
        if year is None:
            return max(current - 1, registered)
        if year < registered or year > current:
            raise AppException(
                ErrorCode.INVALID_PARAMS,
                f"无效的回顾年份: {year}（可用区间 {registered}–{current}）"
                " / Review year is out of range",
            )
        return year

    def _year_in_review_key(self, year: int, language: str) -> str:
        """年报卡缓存键：year_{yyyy}_{语言码}。

        带 year_ 前缀是与周期报告的 yearly（裸年份键）区分开的既有口径，
        后缀取 copy.locale_for 的规范化码（15 语言清单内 2 字母），
        整键最长 12 字符，放在 String(20) 的 period_key 列里。
        """
        return f"year_{year}_{copy.locale_for(language)}"

    async def _build_year_in_review(
        self, db: AsyncSession, user: User, year: int, language: str
    ) -> dict:
        """组装一个自然年的年报卡数据（全部为已落库事实的计数与聚合，零 LLM）。"""
        from backend.services.user_service import user_service

        today = local_today(user.timezone)
        # 当年窗口只截到今天：写成 ~12-31 是在卡片上承诺一段还没发生的时间
        in_progress = year == today.year
        start = local_day_start_utc(user.timezone, date(year, 1, 1))
        end = (
            local_day_start_utc(user.timezone, today + timedelta(days=1))
            if in_progress
            else local_day_start_utc(user.timezone, date(year + 1, 1, 1))
        )

        checkins = await mood_repository.list_between(db, user.id, start, end)
        chat_count = await chat_repository.count_between(db, user.id, start, end)
        memory_count = await memory_repository.count_between(db, user.id, start, end)
        reflection_count = await growth_repository.count_between(
            db, user.id, start, end, record_type="reflection"
        )
        zh = (language or "").startswith("zh")
        if not (checkins or chat_count or memory_count or reflection_count):
            raise AppException(
                ErrorCode.RESOURCE_NOT_FOUND,
                _YEAR_IN_REVIEW_EMPTY["zh" if zh else "en"].format(year=year),
            )

        milestones = [
            m
            for m in await milestone_repository.list_for_user(db, user.id)
            if _as_aware(m.achieved_at).astimezone(safe_zoneinfo(user.timezone)).year == year
        ]
        profile = await personality_service.get_profile(db, user.id)
        day_counts = self._local_day_counts(checkins, user.timezone)
        return {
            "card_type": "year_in_review",
            "year": year,
            "language": language,
            "in_progress": in_progress,
            "period_label": self._year_in_review_label(year, in_progress, language),
            "range": f"{year}-01-01 ~ {today.isoformat() if in_progress else f'{year}-12-31'}",
            "stats": {
                "checkin_count": len(checkins),
                "checkin_days": len(day_counts),
                "chat_count": chat_count,
                "memory_count": memory_count,
                "reflection_count": reflection_count,
                "milestone_count": len(milestones),
                "longest_streak_days": self._longest_run(day_counts),
            },
            "top_moods": self._top_moods(checkins, language),
            "busiest_day": self._busiest_day(day_counts),
            "first_record_date": (min(day_counts).isoformat() if day_counts else None),
            "milestones": [
                {
                    "type": m.milestone_type,
                    "name": user_service.milestone_name(m.milestone_type, language),
                    "achieved_on": _as_aware(m.achieved_at)
                    .astimezone(safe_zoneinfo(user.timezone))
                    .date()
                    .isoformat(),
                }
                for m in milestones
            ],
            "personality": {
                # 未测评的框架留 None，不猜类型：名片与年报同一个口径
                "mbti": profile.mbti_type,
                "enneagram": user.enneagram_type,
            },
            "exported_at": utc_now().isoformat(),
        }

    def _local_day_counts(self, checkins, tz: Optional[str]) -> dict:
        """签到按用户本地日历日聚合成 {date: 次数}。

        优先用 checkin_date 列（写入时已按用户本地日算好），历史回填行为 NULL
        才回退 created_at 换算——两个口径不能混着用，否则跨时区用户的 streak 会断裂。
        """
        zone = safe_zoneinfo(tz)
        counts: dict = {}
        for c in checkins:
            day = c.checkin_date or _as_aware(c.created_at).astimezone(zone).date()
            counts[day] = counts.get(day, 0) + 1
        return counts

    def _longest_run(self, day_counts: dict) -> int:
        """最长连续签到天数（本地日历日相邻即算连续；无签到为 0）。"""
        if not day_counts:
            return 0
        days = sorted(day_counts)
        best = run = 1
        for prev, cur in zip(days, days[1:]):
            run = run + 1 if (cur - prev).days == 1 else 1
            best = max(best, run)
        return best

    def _top_moods(self, checkins, language: str, limit: int = 3) -> List[dict]:
        """本年出现次数最多的情绪（标签按语言取，次数并列时按标签稳定排序）。"""
        from backend.services.mood_service import mood_label_for

        dist: dict = {}
        for c in checkins:
            label = c.mood_label or mood_label_for(c.mood_emoji, language)
            dist[label] = dist.get(label, 0) + 1
        ordered = sorted(dist.items(), key=lambda x: (-x[1], x[0]))
        return [{"label": label, "count": count} for label, count in ordered[:limit]]

    def _busiest_day(self, day_counts: dict) -> Optional[dict]:
        """一年内签到最密的一天（并列取更早的那天；无签到为 None）。

        不报「最活跃的月份」：按月计数会把「一个月天天记」和「一个月只记一次」判成
        同一个结果，而这张卡要讲的恰恰是前者。
        """
        if not day_counts:
            return None
        peak = max(sorted(day_counts), key=lambda d: day_counts[d])
        return {"day": peak.isoformat(), "count": day_counts[peak]}

    def _year_in_review_label(self, year: int, in_progress: bool, language: str) -> str:
        # 「到目前为止」不是修饰词：把没过完的一年印成 in Review，卡片上的区间就是假的
        if in_progress:
            return copy.t("share", "review.label_progress", language).format(year=year)
        return copy.t("share", "review.label_full", language).format(year=year)

    def _year_in_review_title(self, data: dict, language: str) -> str:
        return data.get("period_label") or copy.t(
            "share", "review.label_full", language
        ).format(year=data.get("year"))

    def _year_in_review_share_text(self, data: dict) -> str:
        """分享文案：只有计数与类型，不带反思/记忆原文（这张卡会被发到公开社交平台）。"""
        language = data.get("language") or "en"
        stats = data.get("stats") or {}
        body = copy.t("share", "review.share_text", language).format(
            year=data.get("year"),
            checkins=stats.get("checkin_count", 0),
            memories=stats.get("memory_count", 0),
            reflections=stats.get("reflection_count", 0),
            streak=stats.get("longest_streak_days", 0),
        )
        return f"{body} #ClosenAI {data.get('share_url', '')}".strip()

    def _year_in_review_lines(self, data: dict, language: str) -> List[str]:
        """PDF 文本行：区间 + 统计 + 情绪 + 里程碑 + 画像，全部是已落库事实。"""
        stats = data.get("stats") or {}
        lines: List[str] = [
            copy.t("share", "review.range", language).format(range=data.get("range", "")),
            copy.t("share", "review.stats_checkin", language).format(
                count=stats.get("checkin_count", 0),
                days=stats.get("checkin_days", 0),
                streak=stats.get("longest_streak_days", 0),
            ),
            copy.t("share", "review.stats_counts", language).format(
                chats=stats.get("chat_count", 0),
                memories=stats.get("memory_count", 0),
                reflections=stats.get("reflection_count", 0),
            ),
            "",
        ]
        first_day = data.get("first_record_date")
        if first_day:
            lines.append(copy.t("share", "review.first_record", language).format(day=first_day))
        busiest = data.get("busiest_day")
        if busiest:
            lines.append(
                copy.t("share", "review.busiest_day", language).format(
                    day=busiest.get("day"), count=busiest.get("count")
                )
            )
        top_moods = data.get("top_moods") or []
        if top_moods:
            unit = copy.t("share", "unit.times", language)
            lines.append("")
            lines.append(copy.t("share", "review.moods_header", language))
            lines.extend(
                f"· {m.get('label')} × {m.get('count')} {unit}".strip() for m in top_moods
            )
        milestones = data.get("milestones") or []
        if milestones:
            lines.append("")
            lines.append(copy.t("share", "review.milestones_header", language))
            lines.extend(
                f"· {m.get('achieved_on')} · {m.get('name')}" for m in milestones
            )
        personality = data.get("personality") or {}
        frameworks = [
            str(v)
            for v in (personality.get("mbti"), personality.get("enneagram"))
            if v
        ]
        if frameworks:
            lines.append("")
            lines.append(
                copy.t("share", "review.personality", language).format(
                    frameworks=" · ".join(frameworks)
                )
            )
        return lines

    async def export_year_in_review_pdf(
        self, db: AsyncSession, user_id: str, year: Optional[int] = None, language: str = "en"
    ) -> bytes:
        """年度报告卡 PDF：整年确定性统计 + 品牌深链（架构方案 §二 M4 的 year_in_review 模板位）。

        不走 LLM：这张卡的卖点是「这一年你真的留下了这些」，每个数字都要能指回落库事实；
        生成一段读不出证据的漂亮话，正是 D1 evidence_policy 要关掉的口子。
        零 AI 产出 → 不打 AIGC 元数据标识（与初读卡同口径）。
        """
        data = await self.export_year_in_review(db, user_id, year, language)
        lang = data.get("language") or "en"
        brand = await self._brand_for(db, user_id, (lang or "").startswith("zh"))
        return self._render_pdf(
            self._year_in_review_title(data, lang),
            self._year_in_review_lines(data, lang),
            brand=brand,
        )

    async def export_report(
        self, db: AsyncSession, user_id: str, period: str, language: str = "en"
    ) -> dict:
        """导出周期报告（JSON）。

        - period 白名单：weekly / monthly / quarterly / yearly，非法 → 400
        - monthly / quarterly / yearly 为 Pro 权益（40302）
        - 同周期同语种缓存：命中 share_exports 直接返回，不重复调 LLM、不重复落库
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

        period_key = self._period_key(period, user.timezone, language)
        cached = await share_repository.get_cached(db, user_id, period, period_key)
        if cached is not None:
            return json.loads(cached.payload)

        if period == "weekly":
            # 口径修复：weekly 导出 = 近 7 天周报（此前错配为 30 天月报）
            report = await self._build_weekly_report(db, user, language)
        else:
            report = await growth_service.generate_report(db, user_id, period, language)
        report["share_url"] = await self._referral_link(db, user)
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
        zh = (language or "").startswith("zh")
        brand = await self._brand_for(db, user_id, zh)
        # 报告正文由 LLM 生成 → AIGC 隐式标识进元数据，并带上那次生成的模型与 run id
        runs = await generation_run.summarize_runs(
            db, generation_run.collect_run_ids(report.get("generation_run_id"))
        )
        return self._render_pdf(
            report.get("period_label") or copy.t("share", "report.default_title", language),
            lines,
            brand=brand,
            aigc=True,
            aigc_runs=runs,
        )

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
        # aigc=True：档案含已落库的 LLM 章节 → 隐式标识进元数据（显式逐章标注另见正文）
        # 不给 brand：Life Archive 是付费私人交付物，带引流二维码会让它转发时变成广告
        runs = await generation_run.summarize_runs(
            db, generation_run.collect_run_ids(archive.get("generation_run_ids"))
        )
        return self._render_pdf(
            self._life_archive_title(archive),
            self._life_archive_lines(archive),
            aigc=True,
            aigc_runs=runs,
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
        language = language or "en"
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
        portrait_row = await personality_repository.get_latest(db, user.id)
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
            self._chapter(language, "overview", self._overview_items(stats, scope, language)),
            self._chapter(language, "portrait", await self._portrait_items(db, user.id, language)),
            self._chapter(language, "voice", self._voice_items(memories)),
            self._chapter(language, "mood", self._mood_items(checkins, language)),
            self._chapter(language, "patterns", self._record_items(patterns)),
            self._chapter(
                language,
                "insights",
                self._record_items(weekly) + self._record_items(daily),
            ),
            self._chapter(language, "milestones", self._milestone_items(milestones, language)),
        ]
        if scope == "legacy":
            chapters.append(
                self._chapter(language, "cross_year", self._cross_year_items(checkins, memories, language))
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
            # AIGC 标识的可追溯源（O4）：档案里 AI 章节对应的生成事件
            "generation_run_ids": ",".join(
                generation_run.collect_run_ids(
                    *(
                        r.generation_run_id
                        for r in [*patterns, *weekly, *daily, portrait_row]
                        if r is not None
                    )
                )
            ),
            "aigc_mark": copy.t("share", "aigc.mark", language),
            "aigc_notice": copy.t("share", "aigc.notice", language),
            # 与分享卡不同：Life Archive 是买断的个人档案，不做引流，因此这里保持
            # App Store 直链，不替换成带推荐码的深链（它的 PDF 同样不加品牌区）。
            "share_url": settings.share_landing_url,
        }

    def _chapter(self, language: str, key: str, items: List[str]) -> dict:
        """章节对象：AIGC 标识随章节走（GB 45438 要求显式标识落在内容上）。"""
        generated = key in AIGC_CHAPTER_KEYS
        return {
            "key": key,
            "title": copy.t("share", f"archive.title.{key}", language),
            "aigc_generated": generated,
            "aigc_label": copy.t("share", "aigc.mark", language) if generated else None,
            "items": items,
        }

    def _overview_items(self, stats: dict, scope: str, language: str) -> List[str]:
        return [
            copy.t("share", "archive.scope", language).format(scope=scope),
            copy.t("share", "archive.active_days", language).format(days=stats['active_days']),
            copy.t("share", "archive.messages", language).format(count=stats['message_count']),
            copy.t("share", "archive.checkins", language).format(count=stats['checkin_count']),
            copy.t("share", "archive.memories", language).format(count=stats['memory_count']),
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

    def _mood_items(self, checkins, language: str) -> List[str]:
        from backend.services.mood_service import mood_label_for

        dist: dict = {}
        for c in checkins:
            label = c.mood_label or mood_label_for(c.mood_emoji, language)
            dist[label] = dist.get(label, 0) + 1
        if not dist:
            return [copy.t("share", "archive.empty", language)]
        parts = sorted(dist.items(), key=lambda x: -x[1])
        unit = copy.t("share", "unit.times", language)
        return [f"{label} × {count} {unit}".strip() for label, count in parts]

    def _record_items(self, records) -> List[str]:
        out: List[str] = []
        for r in records:
            day = _as_aware(r.created_at).date().isoformat()
            out.append(f"{day} · {r.content}")
        return out

    def _milestone_items(self, milestones, language: str) -> List[str]:
        from backend.services.user_service import user_service

        if not milestones:
            return [copy.t("share", "archive.empty", language)]
        out: List[str] = []
        for m in milestones:
            day = _as_aware(m.achieved_at).date().isoformat()
            name = user_service.milestone_name(m.milestone_type, language)
            out.append(f"{day} · {name}")
        return out

    def _cross_year_items(self, checkins, memories, language: str) -> List[str]:
        """跨年对比：按自然年统计量（确定性计数，非 AI 推断）。"""
        buckets: dict = {}
        for c in checkins:
            year = _as_aware(c.created_at).year
            buckets.setdefault(year, {"checkins": 0, "memories": 0})["checkins"] += 1
        for m in memories:
            year = _as_aware(m.created_at).year
            buckets.setdefault(year, {"checkins": 0, "memories": 0})["memories"] += 1
        if len(buckets) < 2:
            return [copy.t("share", "archive.cross_year_need", language)]
        out: List[str] = []
        for year in sorted(buckets):
            b = buckets[year]
            out.append(
                copy.t("share", "archive.cross_year_line", language).format(
                    year=year, checkins=b['checkins'], memories=b['memories']
                )
            )
        return out

    def _life_archive_title(self, archive: dict) -> str:
        language = archive.get("language") or "en"
        scope = archive.get("scope", "all")
        return copy.t("share", "archive.title_page", language).format(
            scope=scope, overview=copy.t("share", "archive.title.overview", language)
        )

    def _life_archive_lines(self, archive: dict) -> List[str]:
        """PDF 文本行：文档级 AIGC 声明 + 逐章标题带标识。"""
        language = archive.get("language") or "en"
        stats = archive.get("stats") or {}
        lines: List[str] = [archive.get("aigc_notice", ""), ""]
        lines.append(
            copy.t("share", "archive.overview_line", language).format(
                days=stats.get('active_days', 0),
                memories=stats.get('memory_count', 0),
                checkins=stats.get('checkin_count', 0),
            )
        )
        lines.append("")
        for chapter in archive.get("chapters", []):
            mark = chapter.get("aigc_label") or ""
            lines.append(f"{chapter.get('title', '')}{mark}".strip())
            items = chapter.get("items") or []
            if not items:
                lines.append(copy.t("share", "archive.empty", language))
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

        language = language or "en"
        zh = (language or "").startswith("zh")
        sep = "，" if zh else ", "
        day_unit = copy.t("share", "weekly.day_unit", language)
        checkins = await mood_repository.get_history(db, user.id, days=7)
        dist: dict = {}
        for c in checkins:
            label = c.mood_label or mood_label_for(c.mood_emoji, language)
            dist[label] = dist.get(label, 0) + 1
        if dist:
            total = sum(dist.values())
            parts = [f"{k} {v}{day_unit}" for k, v in sorted(dist.items(), key=lambda x: -x[1])]
            mood_trend = copy.t("share", "weekly.mood_trend", language).format(
                total=total, parts=sep.join(parts)
            )
        else:
            mood_trend = copy.t("share", "weekly.no_checkins", language)

        weekly = await growth_repository.get_latest(db, user.id, "weekly_summary")
        if weekly and weekly.content:
            content = weekly.content
        else:
            content = copy.t("share", "weekly.no_report", language)

        end = local_today(user.timezone)
        start = end - timedelta(days=6)
        return {
            "period": "weekly",
            "period_label": copy.t("share", "weekly.label", language),
            "range": f"{start.isoformat()} ~ {end.isoformat()}",
            "insight_level": "L2 Pattern",
            "mood_distribution": dist,
            "mood_trend": mood_trend,
            "content": content,
        }

    def _period_key(self, period: str, tz: Optional[str], language: Optional[str]) -> str:
        """周期键（按用户本地日历 + 语种）：weekly=ISO 周 / monthly=月 / quarterly=季 / yearly=年。

        语种后缀必须进键：报告载荷整体按 language 生成（`_build_weekly_report` 的
        `period_label`/`mood_trend`、`growth_service.generate_report` 的正文），不进键就是
        同一用户同一周期换 UI 语言拿回首次生成那个语种的报告。
        取 `copy.locale_for` 的规范化码，zh 与 zh-CN 命中同一条；年报卡的 `year_` 前缀键
        （`_year_in_review_key`）与本键不同形，两者不互吃。
        """
        today = local_today(tz)
        if period == "weekly":
            iso = today.isocalendar()
            base = f"{iso[0]}-W{iso[1]:02d}"
        elif period == "monthly":
            base = f"{today.year}-{today.month:02d}"
        elif period == "quarterly":
            base = f"{today.year}-Q{(today.month - 1) // 3 + 1}"
        else:
            base = str(today.year)
        return f"{base}_{copy.locale_for(language)}"

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
        mbti = (mbti_type or "").upper()
        if mbti not in _ARCHETYPE_KEYS:
            return None
        return copy.t("share", "combo.insight", language).format(
            mbti=mbti, archetype=copy.t("share", f"archetype.{mbti}", language)
        )

    def _personality_share_text(self, card: dict, language: str) -> str:
        tagline = card.get("tagline") or ""
        # 取卡片自己的 share_url（带推荐码深链），而不是再读一次 app_store_url：
        # 两个链接并存会让分享文案与卡片指向不同去处，归因也就断了
        url = card.get("share_url") or settings.share_landing_url
        return copy.t("share", "personality.share_text", language).format(
            name=card.get("user_name") or "?",
            mbti=card.get("mbti_type") or "?",
            label=card.get("mbti_label") or "?",
            tagline=tagline,
            url=url,
        )

    def _report_lines(self, report: dict, language: str = "en") -> List[str]:
        return [
            report.get("period_label", copy.t("share", "report.default_title", language)),
            copy.t("share", "report.range", language).format(value=report.get("range", "")),
            copy.t("share", "report.level", language).format(value=report.get("insight_level", "")),
            "",
            copy.t("share", "report.trend", language),
            report.get("mood_trend", ""),
            "",
            copy.t("share", "report.content", language),
            report.get("content", ""),
        ]

    # ── PDF 渲染（reportlab + UnicodeCIDFont，支持中文）──

    def _render_pdf(
        self,
        title: str,
        lines: List[str],
        *,
        brand: Optional[dict] = None,
        aigc: bool = False,
        aigc_runs: Optional[dict] = None,
    ) -> bytes:
        """文本行 → PDF。brand 非空时在文末追加品牌水印 + 引流二维码（M4 分享卡）。"""
        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=18 * mm,
            rightMargin=18 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
            title=title,
            **(_aigc_metadata(aigc_runs) if aigc else {}),
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

        if brand:
            brand_style = ParagraphStyle(
                "Brand", fontName=_CJK_FONT, fontSize=10, leading=15,
                spaceBefore=3, textColor=colors.HexColor("#8A8A99"),
            )
            story.append(Spacer(1, 18))
            story.append(Paragraph(self._escape_xml(brand["watermark"]), brand_style))
            story.append(Paragraph(self._escape_xml(brand["cta"]), brand_style))
            story.append(self._qr_image(brand["link"]))
            # 二维码之外再给一条可复制的明文短链：PDF 常被截图转发，扫不动时链接还在
            story.append(Paragraph(self._escape_xml(brand["link"]), brand_style))

        doc.build(story)
        return buf.getvalue()

    def _qr_image(self, url: str) -> Image:
        """深链二维码（32mm 见方：A4 上手机扫码在 20cm 距离内足够分辨）。"""
        img = qrcode.make(
            url, image_factory=qrcode.image.pil.PilImage, box_size=8, border=2
        )
        png = BytesIO()
        img.save(png, "PNG")
        png.seek(0)
        return Image(png, width=32 * mm, height=32 * mm)

    async def _brand_for(self, db: AsyncSession, user_id: str, zh: bool) -> dict:
        """分享卡品牌要素：水印 + 下载文案 + 带本人推荐码的深链（M4 / 决策点 3）。

        副作用是刻意的：首次导出即生成并落库 referral_code（惰性，见 ensure_referral_code），
        因此"分享卡 PDF 导出"这个读接口会写一行 users.referral_code。
        """
        user = await user_repository.get_user(db, user_id)
        if user is None:
            raise AppException(
                ErrorCode.USER_NOT_FOUND, "用户不存在 / User not found"
            )
        code = await user_repository.ensure_referral_code(db, user)
        loc = "zh" if zh else "en"
        return {
            "watermark": copy.t("share", "brand.watermark", loc),
            "cta": copy.t("share", "brand.cta", loc),
            "link": referral_link(settings.site_url, code),
        }

    def _escape_xml(self, text: str) -> str:
        """转义 Paragraph 文本中的 XML 特殊字符。"""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )


share_service = ShareService()

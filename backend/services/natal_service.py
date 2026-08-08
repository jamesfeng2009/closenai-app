"""
本命星盘服务（PRD 探索 Tab 可选入口）。

职责：
- 出生信息（日期 + 可选时间 + 城市）→ pyswisseph 计算三大星座与行星位置
  （Swiss Ephemeris 黄经 → 星座；上升 ASC 取 swe.houses_ex 的 Asc）。
- 时区由后端从城市推导（内置精简城市库 backend/data/cities.json，中英文模糊
  匹配），未命中回退用户 timezone 并标 tz_fallback=True；历史时区换算（含
  夏令时）由 zoneinfo 按出生日期处理。
- AI 文本解读：lite 模型 + language 参数 + try/except 模板降级；按语言缓存于
  interpretation_json，避免重复调用。
- 今日参考：seed=hash(user_id+用户本地日) 的确定性伪随机，零 LLM 成本。

PRD 禁止项：圆形星盘轮盘图数据、爱情运势、合盘/匹配 —— 本服务不产出。
"""
import hashlib
import json
import logging
import random
from datetime import date, datetime, time as dt_time, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

import swisseph as swe
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AppException, ErrorCode
from backend.core.llm import llm
from backend.core.time_utils import local_today, safe_zoneinfo
from backend.models.natal import NatalChart
from backend.models.schemas import (
    NatalChartRequest,
    NatalChartResponse,
    NatalDailyResponse,
)
from backend.repositories import natal_repository, user_repository
from backend.services.quota_service import quota_service
from backend.services.safety_service import safety_service

logger = logging.getLogger("closenai.natal")

# 黄经 0° 起每 30° 一个星座（key 与 personality_service.zodiac_sign_for 口径一致）
ZODIAC_SIGNS = [
    "aries", "taurus", "gemini", "cancer", "leo", "virgo",
    "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
]
ZODIAC_LABELS_ZH = {
    "aries": "白羊座", "taurus": "金牛座", "gemini": "双子座", "cancer": "巨蟹座",
    "leo": "狮子座", "virgo": "处女座", "libra": "天秤座", "scorpio": "天蝎座",
    "sagittarius": "射手座", "capricorn": "摩羯座", "aquarius": "水瓶座", "pisces": "双鱼座",
}
ZODIAC_LABELS_EN = {
    "aries": "Aries", "taurus": "Taurus", "gemini": "Gemini", "cancer": "Cancer",
    "leo": "Leo", "virgo": "Virgo", "libra": "Libra", "scorpio": "Scorpio",
    "sagittarius": "Sagittarius", "capricorn": "Capricorn", "aquarius": "Aquarius",
    "pisces": "Pisces",
}

# 行星位置（PRD：水星/金星/火星/木星/土星所在星座，纯文本数据）
_PLANETS = [
    ("mercury", swe.MERCURY),
    ("venus", swe.VENUS),
    ("mars", swe.MARS),
    ("jupiter", swe.JUPITER),
    ("saturn", swe.SATURN),
]
_PLANET_LABELS_ZH = {
    "mercury": "水星", "venus": "金星", "mars": "火星",
    "jupiter": "木星", "saturn": "土星",
}

_CITIES_PATH = Path(__file__).resolve().parent.parent / "data" / "cities.json"

# 今日参考素材（确定性伪随机的候选集；按请求语言本地化）
_LUCKY_COLORS = [
    ("金色", "Gold"), ("蓝色", "Blue"), ("绿色", "Green"), ("红色", "Red"),
    ("紫色", "Purple"), ("白色", "White"), ("橙色", "Orange"), ("银色", "Silver"),
]
_LUCKY_TIMES = [
    "06:00-08:00", "08:00-10:00", "10:00-12:00", "12:00-14:00",
    "14:00-16:00", "16:00-18:00", "18:00-20:00", "20:00-22:00",
]


def sign_for_longitude(longitude: float) -> str:
    """黄经（0-360）→ 星座 key。"""
    return ZODIAC_SIGNS[int(longitude % 360.0 // 30.0)]


@lru_cache(maxsize=1)
def _load_cities() -> list:
    """加载内置城市库（进程级缓存一次）。"""
    with open(_CITIES_PATH, encoding="utf-8") as f:
        return json.load(f)


def find_city(place: str) -> Optional[dict]:
    """城市库中英文模糊匹配（lower contains，双向）；未命中返回 None。

    匹配优先级：name/name_en 精确（大小写不敏感）→ 双向子串（如「北京市」命中「北京」）。
    """
    q = (place or "").strip().lower()
    if not q:
        return None
    cities = _load_cities()
    for c in cities:
        if q == c["name"].lower() or q == c["name_en"].lower():
            return c
    for c in cities:
        zh = c["name"].lower()
        en = c["name_en"].lower()
        if q in zh or zh in q or q in en or en in q:
            return c
    return None


def birth_local_to_utc(birth_date: date, birth_time: dt_time, tz_name: str) -> datetime:
    """出生地本地时间 → UTC（zoneinfo 按出生日期处理历史时区/夏令时）。"""
    tz = safe_zoneinfo(tz_name)
    return datetime.combine(birth_date, birth_time, tzinfo=tz).astimezone(timezone.utc)


def compute_chart(
    birth_date: date,
    birth_time: dt_time,
    latitude: float,
    longitude: float,
    tz_name: str,
    with_rising: bool = True,
) -> dict:
    """计算本命星盘：太阳/月亮/上升星座 + 水/金/火/木/土所在星座。

    with_rising=False（出生时间未知按 12:00 计算）时 rising_sign 为 None。
    高纬度（|lat| > 约 66°）houses 可能无解：上升回退 None，其余不受影响。
    """
    utc_dt = birth_local_to_utc(birth_date, birth_time, tz_name)
    jd = swe.julday(
        utc_dt.year, utc_dt.month, utc_dt.day,
        utc_dt.hour + utc_dt.minute / 60.0 + utc_dt.second / 3600.0,
    )
    sun = sign_for_longitude(swe.calc_ut(jd, swe.SUN)[0][0])
    moon = sign_for_longitude(swe.calc_ut(jd, swe.MOON)[0][0])
    planets = {
        name: sign_for_longitude(swe.calc_ut(jd, planet)[0][0])
        for name, planet in _PLANETS
    }
    rising = None
    if with_rising:
        try:
            _houses, ascmc = swe.houses_ex(jd, latitude, longitude)
            rising = sign_for_longitude(ascmc[0])
        except Exception as exc:  # noqa: BLE001  高纬度无解等，上升降级不可用
            logger.warning("上升星座计算失败（lat=%s）: %s", latitude, exc)
    return {"sun": sun, "moon": moon, "rising": rising, "planets": planets}


class NatalService:
    """本命星盘服务。"""

    async def upsert_chart(
        self, db: AsyncSession, user_id: str, req: NatalChartRequest, language: str
    ) -> NatalChartResponse:
        """生成/更新本命星盘：校验 → 城市时区推导 → 计算 → 落库 → 首次 AI 解读。

        每用户一条：已存在则整体重算覆盖（出生信息变化时 AI 解读缓存随之清空）。
        """
        user = await user_repository.require_user(db, user_id, for_update=True)
        if req.birth_date > local_today(user.timezone):
            raise AppException(
                ErrorCode.INVALID_PARAMS,
                "出生日期不能晚于今日 / Birth date cannot be in the future",
            )

        city = find_city(req.birth_place)
        tz_fallback = city is None
        tz_name = city["iana_tz"] if city else (user.timezone or "UTC")
        latitude = city["lat"] if city else 0.0
        longitude = city["lng"] if city else 0.0

        time_unknown = req.birth_time is None
        calc_time = req.birth_time or dt_time(12, 0)  # 时间未知按 12:00 计算
        result = compute_chart(
            req.birth_date, calc_time, latitude, longitude, tz_name,
            with_rising=not time_unknown,
        )

        chart = await natal_repository.get_by_user(db, user_id)
        if chart is None:
            chart = NatalChart(id="", user_id=user_id)
        chart.birth_date = req.birth_date
        chart.birth_time = None if time_unknown else req.birth_time
        chart.birth_time_unknown = time_unknown
        chart.birth_place = req.birth_place.strip()
        chart.latitude = latitude
        chart.longitude = longitude
        chart.timezone = tz_name
        chart.tz_fallback = tz_fallback
        chart.sun_sign = result["sun"]
        chart.moon_sign = result["moon"]
        chart.rising_sign = result["rising"]
        chart.planets_json = json.dumps(result["planets"], ensure_ascii=False)
        chart.interpretation_json = None  # 出生信息变化，清空语言缓存重新生成
        await natal_repository.save(db, chart)

        interpretation = await self._interpretation_for(db, chart, user, language)
        return self._to_response(chart, interpretation)

    async def get_chart(
        self, db: AsyncSession, user_id: str, language: str
    ) -> Optional[NatalChartResponse]:
        """查询本命星盘（无星盘返回 None → 路由返回 data=null）。

        interpretation 按请求语言取缓存；缓存未命中时惰性生成（受配额/同意约束，
        失败回退模板且不写缓存，次日可重试真实生成）。
        """
        user = await user_repository.require_user(db, user_id)
        chart = await natal_repository.get_by_user(db, user_id)
        if not chart:
            return None
        interpretation = await self._interpretation_for(db, chart, user, language)
        return self._to_response(chart, interpretation)

    async def get_daily(
        self, db: AsyncSession, user_id: str, language: str
    ) -> Optional[NatalDailyResponse]:
        """今日参考：综合/事业/财运评分(1-5) + 幸运色/数字/时刻 + 模板短评。

        seed = sha256(user_id + 用户本地日历日)，同日同人结果恒定，零 LLM 成本。
        PRD 禁止爱情维度，本端点不产出。无星盘返回 None → 路由返回 data=null。
        """
        user = await user_repository.require_user(db, user_id)
        chart = await natal_repository.get_by_user(db, user_id)
        if not chart:
            return None
        day = local_today(user.timezone)
        seed = int(
            hashlib.sha256(f"natal:{user_id}:{day.isoformat()}".encode()).hexdigest(), 16
        )
        rng = random.Random(seed)
        overall = rng.randint(1, 5)
        career = rng.randint(1, 5)
        wealth = rng.randint(1, 5)
        color = _LUCKY_COLORS[rng.randrange(len(_LUCKY_COLORS))]
        number = rng.randint(1, 9)
        lucky_time = _LUCKY_TIMES[rng.randrange(len(_LUCKY_TIMES))]

        zh = (language or "").startswith("zh")
        if overall >= 4:
            comment = (
                "今天整体状态不错，适合推进重要事项。"
                if zh else "A solid day overall — good for moving important things forward."
            )
        elif overall == 3:
            comment = (
                "今天节奏平稳，按部就班就是高效。"
                if zh else "A steady day — keeping your rhythm is productivity."
            )
        else:
            comment = (
                "今天适合放慢一点，照顾好自己就是进步。"
                if zh else "A slower day is fine — taking care of yourself is progress."
            )
        return NatalDailyResponse(
            date=day.isoformat(),
            overall_score=overall,
            career_score=career,
            wealth_score=wealth,
            lucky_color=color[0] if zh else color[1],
            lucky_number=number,
            lucky_time=lucky_time,
            comment=comment,
        )

    # ── AI 解读（lite + language + 降级模板；按语言缓存）──

    async def _interpretation_for(
        self, db: AsyncSession, chart: NatalChart, user, language: str
    ) -> str:
        """按语言取 AI 解读：命中缓存直接返回；未命中生成并写缓存。

        降级模板（配额耗尽 / LLM 失败 / 未同意 AI 数据共享）不写缓存，
        以便条件恢复后可重试真实生成。
        """
        lang = (language or "en").split("-")[0].lower() or "en"
        cache = self._parse_json_map(chart.interpretation_json)
        cached = cache.get(lang)
        if cached:
            return cached
        text = await self._generate_interpretation(db, chart, user, lang)
        if text:
            cache[lang] = text
            await natal_repository.save_interpretations(
                db, chart, json.dumps(cache, ensure_ascii=False)
            )
            return text
        return self._fallback_interpretation(chart, lang)

    async def _generate_interpretation(
        self, db: AsyncSession, chart: NatalChart, user, language: str
    ) -> Optional[str]:
        """LLM lite 生成星盘解读；不可用（未同意/配额耗尽/异常）返回 None。

        配额：星盘生成/解读属低频功能不扣对话轮数配额，但生成前过
        quota_service 统一池判断（与 reflection 同一口径），耗尽则降级模板。
        """
        if not getattr(user, "ai_consent", False):
            return None
        if await quota_service.is_proactive_quota_exceeded(db, user):
            logger.info("统一池配额耗尽，星盘解读降级模板: user=%s", user.id)
            return None
        zh = (language or "").startswith("zh")
        labels = ZODIAC_LABELS_ZH if zh else ZODIAC_LABELS_EN
        planets = self._parse_json_map(chart.planets_json)
        planet_desc = (
            "；".join(f"{_PLANET_LABELS_ZH[k]} {labels.get(v, v)}" for k, v in planets.items())
            if zh
            else "; ".join(f"{k.capitalize()} in {labels.get(v, v)}" for k, v in planets.items())
        )
        rising_desc = (
            labels.get(chart.rising_sign, chart.rising_sign) if chart.rising_sign
            else ("未知（出生时间未填）" if zh else "unknown (birth time not provided)")
        )
        if zh:
            prompt = (
                "你是温暖、有洞察力的 AI 成长伙伴。基于用户的本命星盘数据，给出 3-5 句文本解读，"
                "聚焦自我认知与成长视角（性格倾向、优势、一条可执行的成长建议）。"
                "禁止爱情运势与恋爱建议、禁止算命/占卜措辞、禁止描述轮盘图：\n"
                f"太阳星座：{labels.get(chart.sun_sign, chart.sun_sign)}\n"
                f"月亮星座：{labels.get(chart.moon_sign, chart.moon_sign)}\n"
                f"上升星座：{rising_desc}\n"
                f"行星位置：{planet_desc}"
            )
        else:
            prompt = (
                "You are a warm, insightful AI growth companion. Based on the user's natal chart "
                "data, give a 3-5 sentence text interpretation focused on self-awareness and growth "
                "(personality tendencies, strengths, one actionable growth suggestion). "
                "No love/romance fortune, no fortune-telling wording, no wheel chart descriptions.\n"
                f"Sun sign: {labels.get(chart.sun_sign, chart.sun_sign)}\n"
                f"Moon sign: {labels.get(chart.moon_sign, chart.moon_sign)}\n"
                f"Rising sign: {rising_desc}\n"
                f"Planet placements: {planet_desc}"
            )
        try:
            text = await llm.complete(prompt, language=language, lite=True, max_tokens=300)
            # L3 输出过滤：命中回退空串 → 走模板
            text = await safety_service.check_or(text.strip(), language, "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("星盘解读 LLM 失败，回退模板: %s", exc)
            return None
        return text.strip() or None

    def _fallback_interpretation(self, chart: NatalChart, language: str) -> str:
        """AI 解读降级模板（LLM 不可用时的静态文案）。"""
        zh = (language or "").startswith("zh")
        labels = ZODIAC_LABELS_ZH if zh else ZODIAC_LABELS_EN
        sun = labels.get(chart.sun_sign or "", chart.sun_sign or "")
        moon = labels.get(chart.moon_sign or "", chart.moon_sign or "")
        if zh:
            rising_part = (
                f"，上升星座是{labels.get(chart.rising_sign, chart.rising_sign)}"
                if chart.rising_sign else ""
            )
            return (
                f"你的太阳星座是{sun}，月亮星座是{moon}{rising_part}。"
                "星盘是认识自我的一面镜子——继续记录与对话，我会陪你看到更清晰的自己。"
            )
        rising_part = (
            f", and your rising sign is {labels.get(chart.rising_sign, chart.rising_sign)}"
            if chart.rising_sign else ""
        )
        return (
            f"Your sun sign is {sun}, your moon sign is {moon}{rising_part}. "
            "Your chart is a mirror for self-understanding — keep recording and chatting, "
            "and I'll help you see yourself more clearly."
        )

    # ── 工具 ──

    @staticmethod
    def _parse_json_map(raw: Optional[str]) -> dict:
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _to_response(self, chart: NatalChart, interpretation: Optional[str]) -> NatalChartResponse:
        return NatalChartResponse(
            id=chart.id,
            birth_date=chart.birth_date,
            birth_time=None if chart.birth_time_unknown else chart.birth_time,
            birth_time_unknown=chart.birth_time_unknown,
            birth_place=chart.birth_place,
            timezone=chart.timezone,
            tz_fallback=chart.tz_fallback,
            sun_sign=chart.sun_sign,
            moon_sign=chart.moon_sign,
            rising_sign=chart.rising_sign,
            planets=self._parse_json_map(chart.planets_json),
            interpretation=interpretation,
            created_at=chart.created_at,
            updated_at=chart.updated_at,
        )


natal_service = NatalService()

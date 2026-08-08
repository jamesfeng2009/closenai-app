"""natal_service：黄经→星座边界、Swiss Ephemeris 已知出生数据校验、
城市时区推导/回退、出生时间未知降级、AI 解读语言缓存与降级、今日参考确定性。

pyswisseph 计算本身与 DB 无关，sqlite 内存库即可跑通服务层全链路。
"""
import json
from datetime import date, time as dt_time

import pytest

from backend.core.errors import AppException, ErrorCode
from backend.models.schemas import NatalChartRequest
from backend.repositories import natal_repository
from backend.services.natal_service import (
    birth_local_to_utc,
    compute_chart,
    find_city,
    natal_service,
    sign_for_longitude,
)


# ── 纯函数：黄经 → 星座 ──

def test_sign_for_longitude_boundaries():
    assert sign_for_longitude(0.0) == "aries"
    assert sign_for_longitude(29.99) == "aries"
    assert sign_for_longitude(30.0) == "taurus"
    assert sign_for_longitude(359.99) == "pisces"
    assert sign_for_longitude(360.0) == "aries"  # 取模回卷


# ── 纯函数：Swiss Ephemeris 计算（已知数据，值经独立验证）──

def test_compute_chart_known_birth():
    """1990-06-15 08:00 上海：太阳双子 / 月亮双鱼 / 上升巨蟹。"""
    result = compute_chart(
        date(1990, 6, 15), dt_time(8, 0), 31.2304, 121.4737, "Asia/Shanghai"
    )
    assert result["sun"] == "gemini"
    assert result["moon"] == "pisces"
    assert result["rising"] == "cancer"
    assert set(result["planets"]) == {"mercury", "venus", "mars", "jupiter", "saturn"}
    assert all(result["planets"].values())


def test_compute_chart_without_rising():
    """出生时间未知（with_rising=False）：上升为 None，其余照常。"""
    result = compute_chart(
        date(1990, 6, 15), dt_time(12, 0), 31.2304, 121.4737, "Asia/Shanghai",
        with_rising=False,
    )
    assert result["rising"] is None
    assert result["sun"] == "gemini"


def test_birth_local_to_utc_historical_dst():
    """历史夏令时：纽约 1990 年夏（EDT UTC-4）与冬（EST UTC-5）正午差 1 小时。"""
    summer = birth_local_to_utc(date(1990, 6, 15), dt_time(12, 0), "America/New_York")
    winter = birth_local_to_utc(date(1990, 1, 15), dt_time(12, 0), "America/New_York")
    assert summer.hour == 16  # 12:00 EDT → 16:00 UTC
    assert winter.hour == 17  # 12:00 EST → 17:00 UTC


# ── 城市库匹配 ──

@pytest.mark.parametrize(
    "place,tz",
    [
        ("北京", "Asia/Shanghai"),
        ("Beijing", "Asia/Shanghai"),
        ("beijing", "Asia/Shanghai"),      # 大小写不敏感
        ("北京市", "Asia/Shanghai"),       # 双向子串
        ("New York", "America/New_York"),
        ("东京", "Asia/Tokyo"),
    ],
)
def test_find_city_hit(place, tz):
    city = find_city(place)
    assert city is not None
    assert city["iana_tz"] == tz


def test_find_city_miss():
    assert find_city("一个不存在的小城") is None
    assert find_city("") is None


# ── 服务层：生成 / 查询 / 缓存 ──

def _gen_calls(mock_llm):
    """解读生成调用（剔除 safety L3 分类调用——max_tokens=10 的固定特征）。"""
    return [c for c in mock_llm.calls["complete"] if c.get("max_tokens") != 10]

async def test_upsert_chart_persists_and_generates_interpretation(db, make_user, mock_llm):
    """POST 主流程：计算落库 + 首次 AI 解读（lite 模型）。"""
    await make_user("u1", language="en", timezone="Asia/Shanghai", ai_consent=True)
    req = NatalChartRequest(
        birth_date=date(1990, 6, 15), birth_time=dt_time(8, 0), birth_place="上海"
    )
    resp = await natal_service.upsert_chart(db, "u1", req, "en")

    assert resp.sun_sign == "gemini"
    assert resp.moon_sign == "pisces"
    assert resp.rising_sign == "cancer"
    assert resp.timezone == "Asia/Shanghai"
    assert resp.tz_fallback is False
    assert resp.birth_time_unknown is False
    assert resp.planets["mercury"]
    assert resp.interpretation == "MOCK_LLM_REPLY"
    # LLM 走 lite 模型且带 language
    assert len(_gen_calls(mock_llm)) == 1
    call = _gen_calls(mock_llm)[0]
    assert call["lite"] is True
    assert call["language"] == "en"

    chart = await natal_repository.get_by_user(db, "u1")
    assert chart is not None
    assert json.loads(chart.interpretation_json)["en"] == "MOCK_LLM_REPLY"


async def test_interpretation_cached_per_language(db, make_user, mock_llm):
    """按 (user, language) 缓存：同语言不再调 LLM，换语言惰性生成一次。"""
    await make_user("u1", language="en", timezone="Asia/Shanghai", ai_consent=True)
    req = NatalChartRequest(birth_date=date(1990, 6, 15), birth_place="上海")
    await natal_service.upsert_chart(db, "u1", req, "en")
    assert len(_gen_calls(mock_llm)) == 1

    # 同语言：命中缓存
    resp = await natal_service.get_chart(db, "u1", "en")
    assert resp.interpretation == "MOCK_LLM_REPLY"
    assert len(_gen_calls(mock_llm)) == 1

    # 换语言：新生成一次并写缓存
    resp = await natal_service.get_chart(db, "u1", "zh")
    assert resp.interpretation == "MOCK_LLM_REPLY"
    assert len(_gen_calls(mock_llm)) == 2
    assert _gen_calls(mock_llm)[1]["language"] == "zh"
    cache = json.loads((await natal_repository.get_by_user(db, "u1")).interpretation_json)
    assert set(cache) == {"en", "zh"}


async def test_upsert_replaces_existing_chart(db, make_user):
    """每用户一条：重复 POST 覆盖重算，清空旧解读缓存。"""
    await make_user("u1", language="en", timezone="UTC", ai_consent=True)
    req = NatalChartRequest(birth_date=date(1990, 6, 15), birth_place="上海")
    first = await natal_service.upsert_chart(db, "u1", req, "en")
    second = await natal_service.upsert_chart(db, "u1", req, "en")
    assert first.id == second.id
    charts = [
        c for c in [await natal_repository.get_by_user(db, "u1")] if c
    ]
    assert len(charts) == 1


async def test_upsert_unknown_city_falls_back_user_timezone(db, make_user):
    """城市库未命中：回退用户 timezone 并标 tz_fallback=True。"""
    await make_user("u1", timezone="Europe/Berlin", ai_consent=True)
    req = NatalChartRequest(birth_date=date(1990, 6, 15), birth_place="无名小镇xyz")
    resp = await natal_service.upsert_chart(db, "u1", req, "en")
    assert resp.tz_fallback is True
    assert resp.timezone == "Europe/Berlin"


async def test_upsert_without_birth_time_marks_unknown_and_no_rising(db, make_user):
    """出生时间未知：按 12:00 计算，上升不可用。"""
    await make_user("u1", timezone="UTC", ai_consent=True)
    req = NatalChartRequest(birth_date=date(1990, 6, 15), birth_place="上海")
    resp = await natal_service.upsert_chart(db, "u1", req, "en")
    assert resp.birth_time_unknown is True
    assert resp.birth_time is None
    assert resp.rising_sign is None
    assert resp.sun_sign == "gemini"


async def test_upsert_future_birth_date_rejected(db, make_user):
    """出生日期晚于用户本地今日 → 40001。"""
    await make_user("u1", timezone="UTC", ai_consent=True)
    req = NatalChartRequest(birth_date=date(2999, 1, 1), birth_place="上海")
    with pytest.raises(AppException) as exc_info:
        await natal_service.upsert_chart(db, "u1", req, "en")
    assert exc_info.value.code == ErrorCode.INVALID_PARAMS


async def test_interpretation_llm_failure_falls_back_template_not_cached(
    db, make_user, mock_llm
):
    """LLM 失败：降级模板（含星座文案），不写缓存以便恢复后重试。"""
    await make_user("u1", language="en", timezone="UTC", ai_consent=True)

    def _boom(prompt, **kwargs):
        raise RuntimeError("llm down")

    mock_llm.state["complete_fn"] = _boom
    req = NatalChartRequest(birth_date=date(1990, 6, 15), birth_place="上海")
    resp = await natal_service.upsert_chart(db, "u1", req, "en")
    assert "Gemini" in resp.interpretation  # 英文降级模板
    chart = await natal_repository.get_by_user(db, "u1")
    assert chart.interpretation_json is None  # 模板不入缓存


async def test_interpretation_skipped_without_ai_consent(db, make_user, mock_llm):
    """未同意 AI 数据共享：不调 LLM，直接降级模板。"""
    await make_user("u1", language="en", timezone="UTC", ai_consent=False)
    req = NatalChartRequest(birth_date=date(1990, 6, 15), birth_place="上海")
    resp = await natal_service.upsert_chart(db, "u1", req, "en")
    assert _gen_calls(mock_llm) == []
    assert resp.interpretation  # 降级模板


async def test_get_chart_returns_none_when_absent(db, make_user):
    """无星盘：GET 返回 None（路由 data=null）。"""
    await make_user("u1")
    assert await natal_service.get_chart(db, "u1", "en") is None
    assert await natal_service.get_daily(db, "u1", "en") is None


# ── 今日参考 ──

async def test_daily_deterministic_same_day(db, make_user):
    """同用户同日两次调用结果一致；评分 1-5；无爱情维度字段。"""
    await make_user("u1", timezone="UTC", ai_consent=True)
    req = NatalChartRequest(birth_date=date(1990, 6, 15), birth_place="上海")
    await natal_service.upsert_chart(db, "u1", req, "en")

    first = await natal_service.get_daily(db, "u1", "en")
    second = await natal_service.get_daily(db, "u1", "en")
    assert first == second
    assert 1 <= first.overall_score <= 5
    assert 1 <= first.career_score <= 5
    assert 1 <= first.wealth_score <= 5
    assert 1 <= first.lucky_number <= 9
    assert first.lucky_color
    assert first.lucky_time
    assert not hasattr(first, "love_score")


async def test_daily_localized_by_language(db, make_user):
    """幸运色按请求语言本地化（同一确定性索引）。"""
    await make_user("u1", timezone="UTC", ai_consent=True)
    req = NatalChartRequest(birth_date=date(1990, 6, 15), birth_place="上海")
    await natal_service.upsert_chart(db, "u1", req, "en")
    en = await natal_service.get_daily(db, "u1", "en")
    zh = await natal_service.get_daily(db, "u1", "zh")
    assert en.date == zh.date
    assert en.overall_score == zh.overall_score  # 确定性结果与语言无关
    assert en.lucky_color != zh.lucky_color      # 颜色本地化

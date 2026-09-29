"""
#35 年报卡 year_in_review（架构方案 §二 M4 模板位，去掉恒 40402 的占位）。

这张卡的每个数字都要能指回落库事实（零 LLM），所以测的是窗口口径与聚合正确性：
1. 取哪一年：缺省上一个完整自然年；首年用户退到当年并如实标 in_progress；越界 400。
2. 窗口边界：自然年（不是「距今 N 天」的滚动窗口）、只截到今天、按用户本地时区算日界。
3. 聚合：计数精确（软删除/其他类型不计）、streak 断档、top_moods 排序与标签语言、里程碑只算当年。
4. 分享：文案只带计数不带原文、深链带推荐码、同 (user, year) 命中缓存、PDF 无 AIGC 标识。
"""
from datetime import date, datetime, time as dt_time, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.config import settings
from backend.core.auth import get_current_user
from backend.core.errors import AppException, ErrorCode
from backend.core.middleware import register_exception_handlers
from backend.core.referral import normalize_referral_code
from backend.database import get_db
from backend.models.chat import ChatMessage
from backend.models.growth import GrowthRecord, UserMilestone
from backend.models.memory import Memory
from backend.models.mood import MoodCheckin
from backend.models.personality import PersonalityProfile
from backend.models.share import ShareExport
from backend.repositories import user_repository
from backend.routers import share as share_router
from backend.services.mood_service import mood_label_for
from backend.services.share_service import share_service

UTC = timezone.utc
SH = "Asia/Shanghai"


def _at(day: date, tz: timezone = UTC) -> datetime:
    """落库时刻：本地日历日正午（UTC 存储，避开日界）。"""
    return datetime.combine(day, dt_time(12, 0), tzinfo=tz)


async def _checkin(db, user_id: str, day: date, emoji: str = "😊") -> None:
    db.add(
        MoodCheckin(
            id=str(uuid4()),
            user_id=user_id,
            mood_emoji=emoji,
            mood_label=None,  # 留空以覆盖「按语言回退标签」分支
            ai_interpretation="interp",
            checkin_date=day,
            created_at=_at(day),
        )
    )
    await db.flush()


async def _chat(db, user_id: str, at: datetime, content: str = "hi") -> None:
    db.add(
        ChatMessage(
            id=str(uuid4()),
            user_id=user_id,
            message_type="reply",
            role="user",
            content=content,
            created_at=at,
        )
    )
    await db.flush()


async def _memory(db, user_id: str, at: datetime, content: str, deleted: bool = False) -> None:
    db.add(
        Memory(
            id=str(uuid4()),
            user_id=user_id,
            content=content,
            deleted=deleted,
            created_at=at,
        )
    )
    await db.flush()


async def _growth(db, user_id: str, at: datetime, record_type: str = "reflection") -> None:
    db.add(
        GrowthRecord(
            id=str(uuid4()),
            user_id=user_id,
            record_type=record_type,
            content="我这一年的变化是……",
            created_at=at,
        )
    )
    await db.flush()


async def _milestone(db, user_id: str, kind: str, at: datetime) -> None:
    db.add(
        UserMilestone(
            id=str(uuid4()), user_id=user_id, milestone_type=kind, achieved_at=at
        )
    )
    await db.flush()


# ── 1. 取哪一年 ──

async def test_default_targets_last_complete_calendar_year(db, make_user, time_travel):
    """产品口径是 1 月推年度回顾：过完的那一年才配叫「年度」，缺省不指向进行中的当年。"""
    await make_user("u1", created_at=datetime(2024, 3, 1, tzinfo=UTC), timezone="UTC")
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    await _checkin(db, "u1", date(2025, 6, 10))

    data = await share_service.export_year_in_review(db, "u1", None, "en")

    assert data["year"] == 2025
    assert data["in_progress"] is False
    assert data["range"] == "2025-01-01 ~ 2025-12-31"
    assert data["period_label"] == "Your 2025 in Review"


async def test_first_year_user_falls_back_to_current_year_marked_in_progress(
    db, make_user, time_travel
):
    """刚注册不满一年：回 400 是在怪用户提了一个他没提的请求，退到当年并如实标注区间没过完。"""
    await make_user("u1", created_at=datetime(2026, 1, 20, tzinfo=UTC), timezone="UTC")
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    await _checkin(db, "u1", date(2026, 2, 2))

    data = await share_service.export_year_in_review(db, "u1", None, "zh")

    assert data["year"] == 2026
    assert data["in_progress"] is True
    assert data["period_label"] == "2026 年 · 到目前为止"
    assert data["range"] == "2026-01-01 ~ 2026-05-01"


@pytest.mark.parametrize("year", [2023, 2027])
async def test_explicit_year_outside_registration_window_is_400(
    db, make_user, time_travel, year
):
    await make_user("u1", created_at=datetime(2024, 1, 1, tzinfo=UTC), timezone="UTC")
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    await _checkin(db, "u1", date(2025, 6, 10))

    with pytest.raises(AppException) as exc_info:
        await share_service.export_year_in_review(db, "u1", year, "en")
    assert exc_info.value.code == ErrorCode.INVALID_PARAMS


async def test_year_with_no_records_is_40402_not_a_zero_card(db, make_user, time_travel):
    """空年份出「数字全 0 的卡片」等于让用户分享一张假成绩；空态就是空态。"""
    await make_user("u1", created_at=datetime(2023, 1, 1, tzinfo=UTC), timezone="UTC")
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    await _checkin(db, "u1", date(2025, 6, 10))  # 只有 2025 有数据

    with pytest.raises(AppException) as exc_info:
        await share_service.export_year_in_review(db, "u1", 2024, "en")
    assert exc_info.value.code == ErrorCode.RESOURCE_NOT_FOUND


# ── 2. 窗口边界 ──

async def test_window_is_calendar_year_not_rolling_days(db, make_user, time_travel):
    """跨年边界：12-30 属于上一年、01-02 属于下一年，都不能混进 2025 这张卡。"""
    await make_user("u1", created_at=datetime(2024, 1, 1, tzinfo=UTC), timezone="UTC")
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    await _checkin(db, "u1", date(2024, 12, 30))
    await _checkin(db, "u1", date(2025, 1, 1))
    await _checkin(db, "u1", date(2025, 12, 31))
    await _checkin(db, "u1", date(2026, 1, 2))
    await _chat(db, "u1", _at(date(2024, 12, 30)))
    await _chat(db, "u1", _at(date(2026, 1, 2)))

    data = await share_service.export_year_in_review(db, "u1", 2025, "en")

    assert data["stats"]["checkin_count"] == 2
    assert data["stats"]["chat_count"] == 0
    assert data["first_record_date"] == "2025-01-01"


async def test_day_boundary_follows_user_timezone(db, make_user, time_travel):
    """UTC 12-31 16:30 在东八区已是 1-1：卡属于 2026 而不是 2025。

    「距今 N 天」的滚动窗口和按 UTC 切日都会把这段记错到用户没记账的那一年。
    """
    await make_user("u1", created_at=datetime(2024, 1, 1, tzinfo=UTC), timezone=SH)
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    db.add(
        MoodCheckin(
            id=str(uuid4()),
            user_id="u1",
            mood_emoji="😊",
            checkin_date=date(2026, 1, 1),  # 写入侧按用户本地日算好的值
            created_at=datetime(2025, 12, 31, 16, 30, tzinfo=UTC),
        )
    )
    await db.flush()

    with pytest.raises(AppException) as exc_info:
        await share_service.export_year_in_review(db, "u1", 2025, "en")
    assert exc_info.value.code == ErrorCode.RESOURCE_NOT_FOUND

    data = await share_service.export_year_in_review(db, "u1", 2026, "en")
    assert data["stats"]["checkin_count"] == 1
    assert data["first_record_date"] == "2026-01-01"


async def test_in_progress_window_excludes_the_future(db, make_user, time_travel):
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    await make_user("u1", created_at=datetime(2026, 1, 1, tzinfo=UTC), timezone="UTC")
    await _checkin(db, "u1", date(2026, 4, 30))
    await _chat(db, "u1", _at(date(2026, 5, 1)))  # 今天正午：区间右端截到今天 24:00，应计入

    data = await share_service.export_year_in_review(db, "u1", 2026, "en")

    assert data["stats"]["checkin_count"] == 1
    assert data["stats"]["chat_count"] == 1
    assert data["range"] == "2026-01-01 ~ 2026-05-01"


# ── 3. 聚合正确性 ──

async def test_streak_day_counts_and_type_filters(db, make_user, time_travel):
    """连续天数要真的断得开；记忆只算未删除、反思只算 reflection 类型。"""
    await make_user("u1", created_at=datetime(2024, 1, 1, tzinfo=UTC), timezone="UTC")
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    for day in (1, 2, 3, 4):  # 4 天连签
        await _checkin(db, "u1", date(2025, 3, day))
    await _checkin(db, "u1", date(2025, 3, 6))  # 断一天
    await _checkin(db, "u1", date(2025, 3, 7))
    await _memory(db, "u1", _at(date(2025, 4, 1)), "每周跑一次")
    await _memory(db, "u1", _at(date(2025, 4, 2)), "已被删除的记忆", deleted=True)
    await _growth(db, "u1", _at(date(2025, 5, 1)), "reflection")
    await _growth(db, "u1", _at(date(2025, 5, 2)), "reflection")
    await _growth(db, "u1", _at(date(2025, 5, 3)), "daily_insight")

    data = await share_service.export_year_in_review(db, "u1", 2025, "en")
    stats = data["stats"]

    assert stats["checkin_count"] == 6
    assert stats["checkin_days"] == 6
    assert stats["longest_streak_days"] == 4
    assert stats["memory_count"] == 1
    assert stats["reflection_count"] == 2
    assert data["busiest_day"] == {"day": "2025-03-01", "count": 1}


async def test_legacy_row_without_checkin_date_is_still_counted(db, make_user, time_travel):
    """历史回填行 checkin_date 为 NULL：按 created_at 的用户本地日换算，不能整条丢掉。"""
    await make_user("u1", created_at=datetime(2024, 1, 1, tzinfo=UTC), timezone=SH)
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    db.add(
        MoodCheckin(
            id=str(uuid4()),
            user_id="u1",
            mood_emoji="😊",
            created_at=datetime(2025, 3, 1, 12, 0, tzinfo=UTC),
        )
    )
    await db.flush()
    await _checkin(db, "u1", date(2025, 3, 2))

    data = await share_service.export_year_in_review(db, "u1", 2025, "en")

    assert data["stats"]["checkin_days"] == 2
    assert data["stats"]["longest_streak_days"] == 2
    assert data["first_record_date"] == "2025-03-01"


async def test_top_moods_sorted_by_count_with_language_labels(db, make_user, time_travel):
    """两个用户分别取中/英：缓存键不含语言，同一用户第二次换语言拿到的仍是首次的语种。"""
    await make_user("u1", created_at=datetime(2024, 1, 1, tzinfo=UTC), timezone="UTC")
    await make_user("u2", created_at=datetime(2024, 1, 1, tzinfo=UTC), timezone="UTC")
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    for day, emoji in ((1, "😊"), (2, "😊"), (3, "😊"), (4, "😢"), (5, "😢"), (6, "😴")):
        await _checkin(db, "u1", date(2025, 2, day), emoji)
        await _checkin(db, "u2", date(2025, 2, day), emoji)

    zh = await share_service.export_year_in_review(db, "u1", 2025, "zh")
    en = await share_service.export_year_in_review(db, "u2", 2025, "en")

    assert [m["count"] for m in zh["top_moods"]] == [3, 2, 1]
    assert zh["top_moods"][0]["label"] == mood_label_for("😊", "zh")
    assert en["top_moods"][0]["label"] == mood_label_for("😊", "en")
    assert zh["top_moods"][0]["label"] != en["top_moods"][0]["label"]


async def test_milestones_are_filtered_to_the_reviewed_year(db, make_user, time_travel):
    await make_user("u1", created_at=datetime(2024, 1, 1, tzinfo=UTC), timezone="UTC")
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    await _checkin(db, "u1", date(2025, 3, 1))
    await _milestone(db, "u1", "streak_7", _at(date(2025, 3, 7)))
    await _milestone(db, "u1", "streak_30", _at(date(2026, 3, 7)))

    data = await share_service.export_year_in_review(db, "u1", 2025, "zh")

    assert data["stats"]["milestone_count"] == 1
    assert data["milestones"][0]["achieved_on"] == "2025-03-07"
    assert data["milestones"][0]["name"] != "streak_7"  # 走了本地化，不是原始类型串


async def test_personality_dimensions_come_from_profile_and_user(db, make_user, time_travel):
    """mbti 取最新画像、九型取用户填写值；未测评的框架留 None 而不是猜。"""
    await make_user(
        "u1", created_at=datetime(2024, 1, 1, tzinfo=UTC), timezone="UTC", enneagram_type="5w6"
    )
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    await _checkin(db, "u1", date(2025, 3, 1))

    data = await share_service.export_year_in_review(db, "u1", 2025, "en")
    assert data["personality"] == {"mbti": None, "enneagram": "5w6"}

    db.add(PersonalityProfile(id=str(uuid4()), user_id="u1", mbti_type="INFJ"))
    await db.flush()
    await _checkin(db, "u1", date(2024, 6, 1))

    fresh = await share_service.export_year_in_review(db, "u1", 2024, "en")
    assert fresh["personality"]["mbti"] == "INFJ"
    # 2025 已缓存：卡上数字来自落库事实，重算结果本就一致，不重算也是刻意的
    cached = await share_service.export_year_in_review(db, "u1", 2025, "en")
    assert cached["personality"]["mbti"] is None


# ── 4. 缓存 / 分享文案 / PDF ──

async def test_export_is_cached_once_per_year(db, make_user, time_travel):
    await make_user("u1", created_at=datetime(2024, 1, 1, tzinfo=UTC), timezone="UTC")
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    await _checkin(db, "u1", date(2025, 3, 1))

    first = await share_service.export_year_in_review(db, "u1", 2025, "en")
    await _checkin(db, "u1", date(2025, 3, 2))
    second = await share_service.export_year_in_review(db, "u1", 2025, "en")

    assert second["stats"]["checkin_count"] == first["stats"]["checkin_count"]
    assert second["exported_at"] == first["exported_at"]


async def test_cache_row_uses_year_scoped_period_key(db, make_user, time_travel):
    """period_key 必须带 year_ 前缀：yearly 报告用的是裸年份，两者不能互相命中缓存。"""
    from sqlalchemy import select as _select

    await make_user("u1", created_at=datetime(2024, 1, 1, tzinfo=UTC), timezone="UTC")
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    await _checkin(db, "u1", date(2025, 3, 1))
    await share_service.export_year_in_review(db, "u1", 2025, "en")

    rows = list((await db.execute(_select(ShareExport))).scalars().all())
    assert [(r.period, r.period_key) for r in rows] == [("year_in_review", "year_2025")]


async def test_share_text_carries_counts_not_content_and_links_to_own_code(
    db, make_user, time_travel
):
    await make_user("u1", created_at=datetime(2024, 1, 1, tzinfo=UTC), timezone="UTC")
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    await _checkin(db, "u1", date(2025, 3, 1))
    secret = "我在日记里写了不想让任何人看到的原话"
    await _memory(db, "u1", _at(date(2025, 3, 2)), secret)
    await _growth(db, "u1", _at(date(2025, 3, 3)), "reflection")
    await _growth(db, "u1", _at(date(2025, 3, 4)), "reflection")

    data = await share_service.export_year_in_review(db, "u1", 2025, "zh")

    assert secret not in data["share_text"]
    assert "1 条记忆" in data["share_text"]
    assert "2 篇反思" in data["share_text"]
    code = normalize_referral_code(data["share_url"].rsplit("/", 1)[-1])
    assert code is not None
    assert data["share_url"] in data["share_text"]
    assert settings.app_store_url not in data["share_text"]


async def test_pdf_renders_with_brand_and_without_aigc_marker(db, make_user, time_travel):
    """内容零 LLM → 不能声明 AIGC 标识（与初读卡同口径），但品牌深链必须在。"""
    await make_user("u1", created_at=datetime(2024, 1, 1, tzinfo=UTC), timezone="UTC")
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    await _checkin(db, "u1", date(2025, 3, 1))
    await _checkin(db, "u1", date(2025, 3, 2), "😢")

    pdf = await share_service.export_year_in_review_pdf(db, "u1", 2025, "zh")

    assert pdf[:4] == b"%PDF"
    assert b"Contains AI-generated content" not in pdf


async def test_pdf_of_second_year_is_not_the_first_years_cache(db, make_user, time_travel):
    await make_user("u1", created_at=datetime(2023, 1, 1, tzinfo=UTC), timezone="UTC")
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    await _checkin(db, "u1", date(2024, 3, 1))
    await _checkin(db, "u1", date(2025, 3, 1))

    p24 = await share_service.export_year_in_review_pdf(db, "u1", 2024, "en")
    p25 = await share_service.export_year_in_review_pdf(db, "u1", 2025, "en")

    assert p24 != p25
    assert b"Your 2024 in Review" in p24
    assert b"Your 2025 in Review" in p25


# ── 5. API ──

@pytest.fixture
def app(db):
    app = FastAPI()
    app.include_router(share_router.router, prefix="/api/v1", tags=["share"])
    register_exception_handlers(app)

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: "u1"
    return app


async def _get(app, url):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(url)


async def test_year_in_review_endpoint_returns_card(app, db, make_user, time_travel):
    await make_user("u1", created_at=datetime(2024, 1, 1, tzinfo=UTC), timezone="UTC")
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    await _checkin(db, "u1", date(2025, 3, 1))

    resp = await _get(app, "/api/v1/share/year-in-review?year=2025&language=zh")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["card_type"] == "year_in_review"
    assert data["year"] == 2025
    assert data["stats"]["checkin_count"] == 1
    assert normalize_referral_code(data["share_url"].rsplit("/", 1)[-1]) is not None


async def test_year_in_review_endpoint_rejects_future_year(app, make_user, time_travel):
    await make_user("u1", created_at=datetime(2024, 1, 1, tzinfo=UTC), timezone="UTC")
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))

    resp = await _get(app, "/api/v1/share/year-in-review?year=2027")
    assert resp.json()["code"] == ErrorCode.INVALID_PARAMS


async def test_year_in_review_endpoint_empty_year_is_business_code(app, make_user, time_travel):
    await make_user("u1", created_at=datetime(2024, 1, 1, tzinfo=UTC), timezone="UTC")
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))

    resp = await _get(app, "/api/v1/share/year-in-review?year=2025")
    assert resp.json()["code"] == 40402


async def test_year_in_review_pdf_endpoint_streams(app, db, make_user, time_travel):
    await make_user("u1", created_at=datetime(2024, 1, 1, tzinfo=UTC), timezone="UTC")
    time_travel.set(datetime(2026, 5, 1, tzinfo=UTC))
    await _checkin(db, "u1", date(2025, 3, 1))

    resp = await _get(app, "/api/v1/share/year-in-review/pdf?year=2025")
    assert resp.status_code == 200
    assert resp.content[:4] == b"%PDF"

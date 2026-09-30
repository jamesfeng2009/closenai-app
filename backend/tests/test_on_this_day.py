"""§10.1 ① 记忆考古（C4）：首页每日主位「一年前的今天」候选选取 + 周年分享卡。

覆盖：按年窗口的考古候选查询（周年记忆才入选，非今天/未来的不入）/
候选按年份升序（1 年前优先）与归属隔离 / memory-card PDF 自动周年标题
（C3 偏离 2 的根治：服务端自动判定，任何路径都不会造出假周年）/ 新用户空态。
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.repositories import memory_repository
from backend.services.share_service import share_service

# 固定未来日期冻结：候选窗口与周年判定全部走显式传入的 today 参数 +
# 手动挪 created_at，无真实墙钟依赖。
TODAY = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)  # 2026-11-01
TZ = "UTC"

USER_KW = dict(timezone="UTC", language="zh", ai_consent=True)


def _local(year: int, month: int, day: int, hour: int = 10) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


async def _seed_memory(db, user_id: str, content: str, created: datetime):
    memory = await memory_repository.create(
        db, user_id, content=content, category="preference", source="self", conviction=0.6
    )
    memory.created_at = created
    await db.flush()
    return memory


# ── 1. 考古候选选取 ──


async def test_on_this_day_returns_anniversary_candidates(db, make_user, time_travel):
    time_travel.set(TODAY)
    await make_user("u1", **USER_KW)
    one_year = await _seed_memory(db, "u1", "一年前的今天我在准备面试", _local(2025, 11, 1))
    two_years = await _seed_memory(db, "u1", "两年前的今天搬家了", _local(2024, 11, 1))
    # 不该入选的记忆：今年的今天（不满一年）/ 去年的别的日子 / 未来
    await _seed_memory(db, "u1", "今天刚写的", _local(2026, 11, 1))
    await _seed_memory(db, "u1", "去年夏天的", _local(2025, 6, 15))
    await _seed_memory(db, "u1", "未来的一条", _local(2027, 11, 1))

    result = await share_service.on_this_day(db, "u1", TODAY, TZ)

    assert result["date"] == "2026-11-01"
    candidates = result["candidates"]
    assert [c["years_ago"] for c in candidates] == [1, 2]  # 年份升序，1 年前优先
    assert candidates[0]["memory_id"] == one_year.id
    assert candidates[1]["memory_id"] == two_years.id
    assert candidates[0]["content"] == "一年前的今天我在准备面试"
    # 候选带原话摘录与日期，供首页卡直接渲染
    assert candidates[0]["created_at"].startswith("2025-11-01")


async def test_on_this_day_ownership_isolation(db, make_user, time_travel):
    time_travel.set(TODAY)
    await make_user("u1", **USER_KW)
    await make_user("u2", **USER_KW)
    await _seed_memory(db, "u2", "别人的周年记忆", _local(2025, 11, 1))

    result = await share_service.on_this_day(db, "u1", TODAY, TZ)

    assert result["candidates"] == []


async def test_on_this_day_new_user_empty(db, make_user, time_travel):
    time_travel.set(TODAY)
    await make_user("u1", **USER_KW)
    await _seed_memory(db, "u1", "只有今天的记忆", _local(2026, 11, 1))

    result = await share_service.on_this_day(db, "u1", TODAY, TZ)

    assert result["candidates"] == []


async def test_on_this_day_local_day_boundary(db, make_user, time_travel):
    """周年窗口按用户本地日界（时区铁律）：UTC+8 用户 11 月 1 日的记忆
    落在 UTC 10 月 31 日 16:00，也应被 11-01 的考古命中。"""
    time_travel.set(TODAY)
    await make_user("u1", **{**USER_KW, "timezone": "Asia/Shanghai"})
    memory = await _seed_memory(db, "u1", "上海时间的周年记忆", _local(2025, 10, 31, 16))

    result = await share_service.on_this_day(db, "u1", TODAY, "Asia/Shanghai")

    assert [c["memory_id"] for c in result["candidates"]] == [memory.id]


# ── 2. memory-card 自动周年标题（C3 偏离 2 根治）──


def _pdf_text(pdf: bytes) -> str:
    import io

    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


async def test_memory_card_auto_anniversary_title(db, make_user, time_travel):
    time_travel.set(TODAY)
    await make_user("u1", **USER_KW)
    memory = await _seed_memory(db, "u1", "一年前的今天我在准备面试", _local(2025, 11, 1))

    pdf = await share_service.export_memory_card_pdf(db, "u1", memory.id, today=TODAY, tz=TZ)
    text = _pdf_text(pdf)

    assert "一年前的今天" in text
    assert "一年前的今天我在准备面试" in text


async def test_memory_card_multi_year_anniversary_title(db, make_user, time_travel):
    time_travel.set(TODAY)
    await make_user("u1", **{**USER_KW, "language": "en"})
    memory = await _seed_memory(db, "u1", "moved to a new city", _local(2024, 11, 1))

    pdf = await share_service.export_memory_card_pdf(db, "u1", memory.id, today=TODAY, tz=TZ)
    text = _pdf_text(pdf)

    assert "2 Years Ago Today" in text
    assert "moved to a new city" in text


async def test_memory_card_non_anniversary_stays_plain(db, make_user, time_travel):
    """非周年记忆（周年分享卡也能分享任意一条）保持原模板，不造假周年。"""
    time_travel.set(TODAY)
    await make_user("u1", **USER_KW)
    recent = await _seed_memory(db, "u1", "今天刚写的", _local(2026, 11, 1))

    pdf = await share_service.export_memory_card_pdf(db, "u1", recent.id, today=TODAY, tz=TZ)
    text = _pdf_text(pdf)

    assert "一年前的今天" not in text
    assert "你说过的话" in text
    assert "今天刚写的" in text

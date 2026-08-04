"""
时区工具（P0-F01/P0-F03/P0-F04）。

所有"今日"语义必须基于用户本地时区（users.timezone），而非服务器时区。
PRD 要求：配额每日在用户本地 00:00 重置，主动消息按用户本地 9:00 触发。
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "UTC"


def safe_zoneinfo(tz_name: str | None) -> ZoneInfo:
    """解析 IANA 时区名，非法/为空时回退 UTC（不抛异常）。"""
    try:
        return ZoneInfo(tz_name) if tz_name else ZoneInfo(DEFAULT_TZ)
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError 等一律回退
        return ZoneInfo(DEFAULT_TZ)


def local_now(tz_name: str | None) -> datetime:
    """用户本地当前时间（tz-aware）。"""
    return datetime.now(safe_zoneinfo(tz_name))


def local_today(tz_name: str | None) -> date:
    """用户本地日历日期。"""
    return local_now(tz_name).date()


def utc_now() -> datetime:
    """UTC 当前时间（tz-aware）。禁止再用 naive datetime.now()/utcnow()。"""
    return datetime.now(timezone.utc)


def local_day_start_utc(tz_name: str | None, day: date | None = None) -> datetime:
    """用户本地某日 00:00 对应的 UTC 时间（tz-aware）。

    用于与 timestamptz 列做范围比较（"用户本地今日"的记录 = created_at >= 该值）。
    禁止用 naive datetime.combine(day, min.time()) —— 会被当作 UTC，边界最多偏 ±14h。
    """
    tz = safe_zoneinfo(tz_name)
    d = day or local_now(tz_name).date()
    return datetime.combine(d, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)

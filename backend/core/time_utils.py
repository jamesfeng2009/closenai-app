"""
时区工具（P0-F01/P0-F03/P0-F04）。

所有"今日"语义必须基于用户本地时区（users.timezone），而非服务器时区。
PRD 要求：配额每日在用户本地 00:00 重置，主动消息按用户本地 9:00 触发。
"""
from datetime import date, datetime, time as dt_time, timezone
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


def in_quiet_hours(now_local: dt_time, start: dt_time | None, end: dt_time | None) -> bool:
    """免打扰窗口判定（T5-02 纯函数，无 IO、无时钟）。

    口径（三条都是会被打红的判据，不是注释）：
    - start 或 end 任一为 None ⇒ False（关闭；两列成对写，见 users.quiet_start 注释）；
    - 边界**含头不含尾**：start 时刻本身算静默，end 时刻本身已经恢复投递，
      否则 22:00–08:00 会在两端各多出一分钟的"既静默又可投递"歧义；
    - 跨零点（start > end，如 22:00–08:00）：窗口是 [start,24:00) ∪ [00:00,end)；
    - start == end 是退化窗口，按"不静默"处理。API 校验层已拒掉这种输入，但库里
      仍可能出现（历史数据、直接改库）。走跨零点分支会把它读成全天静默＝主动
      消息永久消失，这比误投递一次更难发现，所以这里兜第二道。
    """
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= now_local < end
    return now_local >= start or now_local < end


def user_in_quiet_hours(user, now: datetime | None = None) -> bool:
    """按用户本地墙钟判断当前是否处于免打扰时段（cron 投递门与推送门共用）。

    `now` 传入 tz-aware 时刻便于测试冻结；缺省取用户本地此刻。墙钟（而不是 UTC 时刻）
    才是"免打扰"的用户语义——DST 切换时本地 22:00 就是 22:00，不受偏移影响。
    """
    start = getattr(user, "quiet_start", None)
    end = getattr(user, "quiet_end", None)
    if start is None or end is None:
        return False
    local = (now or local_now(user.timezone)).astimezone(safe_zoneinfo(user.timezone))
    return in_quiet_hours(local.time(), start, end)

"""
本命星盘表模型。

对应 PRD 本命星盘（探索 Tab 可选入口）：三大星座 + 行星位置 + AI 解读缓存。
禁止项（PRD）：圆形星盘轮盘图数据、爱情运势、合盘/匹配 —— 本表不存此类字段。
"""
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class NatalChart(Base):
    """本命星盘（每用户一条，UniqueConstraint(user_id)）。"""

    __tablename__ = "natal_charts"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_natal_charts_user"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)

    # ── 出生信息（时区由后端从城市推导，用户不填时区）──
    birth_date: Mapped[date] = mapped_column(Date)
    birth_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    # 出生时间未知：按 12:00 计算，上升星座不可用（rising_sign 为 None）
    birth_time_unknown: Mapped[bool] = mapped_column(Boolean, default=False)
    birth_place: Mapped[str] = mapped_column(String(100))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    # IANA 时区名（由出生城市推导；城市库未命中时回退用户 timezone 并标 tz_fallback）
    timezone: Mapped[str] = mapped_column(String(64))
    tz_fallback: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── 星盘结果（黄经 → 星座 key：aries/taurus/...）──
    sun_sign: Mapped[str | None] = mapped_column(String(20), nullable=True)
    moon_sign: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rising_sign: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 行星位置（JSON 字符串：{"mercury": "gemini", "venus": ..., "mars": ..., "jupiter": ..., "saturn": ...}）
    planets_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AI 解读缓存（JSON 字符串：{"zh": "...", "en": "..."}，按语言惰性生成）
    interpretation_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

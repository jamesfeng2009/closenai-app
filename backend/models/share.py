"""
分享导出缓存表模型（P1-F04）。

同一用户同 period 同周期同语种（period_key 末段是 `copy.locale_for` 的规范化码）的导出结果只生成一次：
重复请求直接返回缓存，不重复调 LLM、不重复写 growth_records。
"""
from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base

# `share_exports.period` 的 Life Archive 取值（String(20) 内）。
# 放这里而不是只放 share_service：交付审计（delivery_service）要按同一格筛行，
# 而它不能 import share_service——share_service 反过来要 import 审计侧的事件发射器，
# 常量留在服务层就是一条循环 import，抄一份字面量就是第二个真源。
LIFE_ARCHIVE_PERIOD = "life_archive"


class ShareExport(Base):
    __tablename__ = "share_exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    # 导出周期: weekly | monthly | quarterly | yearly
    period: Mapped[str] = mapped_column(String(20))
    # 周期键（按用户本地时间）：2026-W32 | 2026-08 | 2026-Q3 | 2026
    period_key: Mapped[str] = mapped_column(String(20))
    # 导出结果（JSON 字符串）
    payload: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "period", "period_key", name="uq_share_exports_user_period_key"
        ),
    )

"""
本命星盘数据访问层。

职责：只与 natal_charts 表交互，不含业务逻辑。
"""
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.time_utils import utc_now
from backend.models.natal import NatalChart


class NatalRepository:
    """natal_charts 表数据访问。"""

    async def get_by_user(self, db: AsyncSession, user_id: str) -> Optional[NatalChart]:
        """获取用户本命星盘（每用户一条；不存在返回 None）。"""
        result = await db.execute(
            select(NatalChart).where(NatalChart.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def save(self, db: AsyncSession, chart: NatalChart) -> NatalChart:
        """写入星盘字段（新建或更新已托管对象），显式刷新 updated_at。"""
        if not chart.id:
            chart.id = str(uuid4())
            db.add(chart)
        chart.updated_at = utc_now()
        await db.flush()
        return chart

    async def save_interpretations(
        self, db: AsyncSession, chart: NatalChart, interpretation_json: str
    ) -> NatalChart:
        """更新 AI 解读缓存（按语言键的 JSON）。"""
        chart.interpretation_json = interpretation_json
        chart.updated_at = utc_now()
        await db.flush()
        return chart


natal_repository = NatalRepository()

"""
AI 生成留痕（ai_generation_runs）的**只读**查询面。

写面在 backend/core/generation_run.py（record_run / mark_fallback），本模块只查不写。
为什么读面单独成仓储：留痕表是「一次调用一行」的观测面，业务侧要按调用点统计用量时
（KG-28 的三层次分析配额），拿它当事实源可以避免为单个功能再开一张计数表——
代价是这套计数只在 LLM 真的被调用过时才前进，所以 status 必须一起过滤：
`error` 那次用户没得到任何产出，扣额度是错的。
"""
from datetime import datetime
from typing import Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.generation_run import AiGenerationRun


class GenerationRunRepository:
    """ai_generation_runs 只读查询。"""

    async def count_since(
        self,
        db: AsyncSession,
        user_id: str,
        start: datetime,
        purposes: Sequence[str],
        statuses: Iterable[str] = ("ok", "fallback"),
    ) -> int:
        """统计某用户自 `start` 起、落在 `purposes` 里且结局在 `statuses` 里的调用条数。

        `purposes` 为空＝不筛调用点（本模块刻意不支持这种调用，见下）：留痕表按
        (purpose, created_at) 建索引，一个空集合会退化成「该用户今天所有 LLM 调用」，
        把签到解读也算进分析额度里。宁可在调用点报错，不要静默换口径。
        """
        purpose_list = list(purposes)
        if not purpose_list:
            raise ValueError("count_since 不接受空 purposes：会退化成全量调用计数")
        stmt = (
            select(func.count())
            .select_from(AiGenerationRun)
            .where(
                AiGenerationRun.user_id == user_id,
                AiGenerationRun.created_at >= start,
                AiGenerationRun.purpose.in_(purpose_list),
                AiGenerationRun.status.in_(list(statuses)),
            )
        )
        result = await db.execute(stmt)
        return int(result.scalar() or 0)


generation_run_repository = GenerationRunRepository()

"""
AI 生成留痕的记录与取用（架构方案 O4）。

为什么单独成模块：留痕点只有一处（backend/ai/llm.py 的三个入口，2026-10-16 O0 之前在
backend/core/llm.py），读面有三处
（安全闸门写 fallback、派生行写 generation_run_id、分享 PDF 读 AIGC 元数据）。
把「当前这次生成是哪一条 run」的语义收在这里，业务侧就不必互相传递 run_id。

三条口径：
1. 写失败不阻断主流程——留痕是观测面，用户回复优先；失败降级为 warning 日志。
2. `current_run_id()` 只指向「产出可用」的那次生成（status=ok 且非空）。异常调用、
   空产出、被闸门丢弃的产出都会把指针清空，于是派生行的 generation_run_id 为 NULL，
   即「这行内容是模板直出的，不是 AI 写的」。
3. 辅助调用（如安全分类器自检）传 track=False：留痕照常写，但不抢占指针——
   否则「生成正文 → 调分类器 → 落库」会把正文的 run 链接错到分类器那次调用上。
"""
import logging
from contextvars import ContextVar
from typing import Any, Iterable, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.logging import get_trace_id, get_user_id
from backend.database import SessionLocal
from backend.models.generation_run import AiGenerationRun

logger = logging.getLogger("closenai.generation_run")

# 当前任务内「最近一次可用生成」的 run id（每请求一个 asyncio task，互不串味）
_current_run_id: ContextVar[Optional[str]] = ContextVar("current_run_id", default=None)


def current_run_id() -> Optional[str]:
    """最近一次可用生成的 run id；无可用生成（异常/空产出/被拦截）时为 None。"""
    return _current_run_id.get()


def reset_current_run_id() -> None:
    """清空指针（调用方自行判定产出不可用时使用，如 LLM 正文过闸后为空）。"""
    _current_run_id.set(None)


async def record_run(**fields: Any) -> Optional[str]:
    """写入一条生成留痕，返回 run id；落库失败返回 None。

    fields 为 AiGenerationRun 的列；额外接受 track（bool，默认 True）：
    track=False 表示辅助调用，写留痕但不改指针。
    """
    track = fields.pop("track", True)
    op = fields.get("op", "complete")
    status = fields.get("status", "ok")
    output_chars = fields.get("output_chars") or 0

    uid = get_user_id()
    tid = get_trace_id()
    if uid in ("", "anonymous"):
        uid = None
    run_id = str(uuid4())
    row = AiGenerationRun(
        id=run_id,
        user_id=uid,
        trace_id=tid or None,
        **fields,
    )
    try:
        async with SessionLocal() as session:
            session.add(row)
            await session.commit()
    except Exception as exc:  # noqa: BLE001  留痕失败不阻断生成主流程
        logger.warning("生成留痕写入失败 op=%s purpose=%s: %s", op, fields.get("purpose"), exc)
        return None

    # 只有正文型调用参与指针；embed 产出的是向量，不是用户可见内容
    if track and op in ("complete", "stream"):
        _current_run_id.set(run_id if status == "ok" and output_chars > 0 else None)
    return run_id


async def mark_fallback(run_id: Optional[str], reason: str = "safety_gate") -> None:
    """把一次生成标记为「产出被闸门丢弃，调用方回退模板」，并清空指针。

    指针清空是语义要求：回退后落库的正文来自模板，不该回链到这次 AI 调用。
    """
    if not run_id:
        return
    if _current_run_id.get() == run_id:
        _current_run_id.set(None)
    try:
        async with SessionLocal() as session:
            row = await session.get(AiGenerationRun, run_id)
            if row is None:
                return
            row.status = "fallback"
            row.fallback_reason = reason[:40]
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("生成留痕标记 fallback 失败 run=%s: %s", run_id, exc)


def collect_run_ids(*values: Optional[str]) -> list[str]:
    """把若干 generation_run_id 值（逗号分隔或单值）摊平成去重有序列表。"""
    ids: list[str] = []
    for value in values:
        for part in (value or "").split(","):
            pid = part.strip()
            if pid and pid not in ids:
                ids.append(pid)
    return ids


async def summarize_runs(
    db: AsyncSession, run_ids: Iterable[str]
) -> dict[str, list[str]]:
    """按 run id 取模型与时间，供 AIGC 标识引用。

    只查 status=ok 的行：被闸门丢弃的产出不会落库成内容，不该出现在合规声明里。
    """
    ids = [i for i in run_ids if i]
    if not ids:
        return {"run_ids": [], "models": [], "created_at": []}
    result = await db.execute(
        select(AiGenerationRun)
        .where(AiGenerationRun.id.in_(ids), AiGenerationRun.status == "ok")
        .order_by(AiGenerationRun.created_at.asc())
    )
    rows = list(result.scalars().all())
    models: list[str] = []
    stamps: list[str] = []
    for row in rows:
        label = "/".join(p for p in (row.provider, row.model) if p)
        if label and label not in models:
            models.append(label)
        if row.created_at is not None:
            stamps.append(row.created_at.isoformat())
    return {"run_ids": [r.id for r in rows], "models": models, "created_at": stamps}

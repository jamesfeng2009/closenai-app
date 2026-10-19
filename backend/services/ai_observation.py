"""``observation.extract`` 的**端口实现**与组合根（T2-02 §怎么做.6）。

为什么读库代码住在 ``backend/services/`` 而不是 ``backend/ai/mq/handlers/``：
``backend/tests/test_ai_boundary.py`` 禁止 ``backend/ai/`` import
``backend.models``／``backend.repositories``／``backend.database``，而 T2-02 的 handler
要按 ``TaskRequested.sourceRef`` 读回**那一行**原文。分层的代价不是「读不到」，
而是「读的方式不能被 AI 层自己决定」——于是 AI 层只声明端口形状（``ObservationPorts``），
这里给出对着真库与真 LLM 的那一份实现，两边由 ``build_ports()`` 一处对接。

组合根 ``build_registry()`` 只在 ``worker.build_production_registry()`` 的**函数体**里被调用
（工作单逐字：「不在模块 import 时注册」）。因此本模块除了被那一句 import，
不该被任何模块级 import 引到——引了就把 SQLAlchemy 拖进 ``backend/ai/`` 的依赖图。

三条口径写在这里而不是 handler 里，因为它们是实现细节：
1. **配额口径**用统一池（``is_proactive_quota_exceeded``）：抽取不是对话轮次。
   ⚠️ 已知偏差（2026-10-18 登记，归 quota 桶，本批不动）：统一池的计数面是
   「今日主动消息数＋签到数」（``quota_service.daily_usage``），**本模块自己的调用不落在
   任何一个计数面上**，所以这一格判的是「这个用户今天还容不容得下再花一次 AI」，
   而不是「这个用户今天跑了多少次抽取」。真正把抽取成本框住的是 :func:`already_extracted`
   那那道 24h 同源闸；要按调用条数封顶，得先给配额加第三个计数面，那是另一个工作单。
2. **原文时刻**取 ``memories.created_at``：``TaskRequested.sourceOccurredAt`` 是可选键，
   命令没带就用来源行自己的时刻，两者都没有才留空（下游拿消费时刻兜底会把重放的老消息
   算成新近证据，见 ``contract.validate_source_ref`` 那条 reason）。
3. **危机留痕**走 :mod:`backend.repositories.safety_repository`（唯一事实源），
   类别与命中词都取自 ``check_text_tri`` 的判定结果，不在这里重新判一遍。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import select

from backend.ai.llm import llm
from backend.ai.mq.handlers.observation import (
    ObservationExtractHandler,
    ObservationPorts,
    SourceText,
    crisis_kind_of,
)
from backend.ai.mq.registry import Registry
from backend.ai.prompts.observation import SYSTEM_PROMPT
from backend.ai.safety import safety_service
from backend.core.time_utils import utc_now
from backend.database import SessionLocal
from backend.models.canonical import Evidence, SourceRecord, UserFeedback
from backend.models.memory import Memory
from backend.repositories.safety_repository import safety_repository
from backend.repositories.user_repository import user_repository
from backend.services.quota_service import quota_service

logger = logging.getLogger("closenai.services.ai_observation")

#: 进 ``ai_generation_runs.purpose`` 的调用点标识（与 ``backend/ai`` 侧那种推导出来的
#: ``module.func`` 同层，但这里是后台任务，推导只会得到消费者帧，对账时没用）。
EXTRACT_PURPOSE = "services.ai_observation.extract"

#: 一次抽取的输出预算：契约最多 3 条、每条两句（观测＋逐字摘录），给到 800 已经宽裕；
#: 再大就是让模型有话可说给到别处去。
EXTRACT_MAX_TOKENS = 800

#: 负样本取哪几种表态（工作单 §怎么做.5 点名的 correct／reject，逐字对着
#: ``go-core/internal/domain/outcome.go:124-131`` 的六态取值面里的这两条）。
NEGATIVE_FEEDBACK_TYPES: tuple[str, ...] = ("correct", "reject")


def _rfc3339(value: datetime | None) -> str:
    """DB 时间 → protojson ``Timestamp`` 认得的字符串（UTC、``Z`` 结尾）。

    ``SourcePointer.occurred_at`` 是 ``google.protobuf.Timestamp``（closenai_common.proto:66），
    对面 ``protojson`` 只按 RFC3339 解它；``isoformat()`` 默认带 ``+00:00``，
    而 naive 行（历史数据里 server_default 缺失的那一格）连偏移都没有——两种都归一成 ``Z``。
    """
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def load_source(user_id: str, memory_id: str) -> SourceText | None:
    """按 ``sourceRef`` 的行 id 读那一行原文（``status='current'``、未软删、归属本人）。

    三个条件一个都不许少：``superseded`` 的旧版本已经被用户改写掉，拿它抽证据等于
    把用户已经否认过的话又端回去；``deleted`` 的行是用户删的（软删保留是为了撤销与审计）；
    跨 ``user_id`` 更是直接违反「归属只认 header」那条契约。
    """
    async with SessionLocal() as session:
        row = await session.scalar(
            select(Memory)
            .where(
                Memory.id == memory_id,
                Memory.user_id == user_id,
                Memory.status == "current",
                Memory.deleted == False,  # noqa: E712
            )
            .limit(1)
        )
        if row is None:
            return None
        user = await user_repository.get_user(session, user_id)
        return SourceText(
            content=row.content or "",
            occurred_at=_rfc3339(row.created_at),
            language=(user.language if user is not None else None) or "en",
        )


async def recent_corrections(user_id: str, limit: int) -> tuple[str, ...]:
    """该用户最近 ``limit`` 条 correct／reject 表态里**用户自己写下的那句话**。

    只取 ``correction_text``：非空的那些是用户逐字说过「不是这样」的话，能直接进 prompt。
    已知覆盖面（2026-10-18 登记，归 T3 洞察闭环桶）：``reject`` 常常只点一下按钮、
    ``correction_text`` 为空，被否的那句归纳住在 ``target_type`` 指向的那张表里
    （七类目标见 ``domain/outcome.go:151-153``），要拿它得读七张 canonical 表——
    那不属于「读一行原文」的这一批。空文本不进 prompt：一行行 ``- `` 后面跟着空，
    模型只会把它当成格式噪声。
    """
    async with SessionLocal() as session:
        rows = (
            await session.scalars(
                select(UserFeedback)
                .where(
                    UserFeedback.user_id == user_id,
                    UserFeedback.feedback_type.in_(NEGATIVE_FEEDBACK_TYPES),
                )
                .order_by(UserFeedback.created_at.desc())
                .limit(max(int(limit), 0))
            )
        ).all()
    return tuple(
        text for text in ((row.correction_text or "").strip() for row in rows) if text
    )


async def already_extracted(
    user_id: str, table: str, row_id: str, window: timedelta
) -> bool:
    """这一行原文在 ``window`` 内**已经产出过证据**吗（成本闸，工作单 §怎么做.7）。

    事实源选 ``evidence`` ⋈ ``source_records`` 而不是自己另记一笔：
    ① 「提取过」的语义是「这一行已经变成过证据」，而那条事实只有落库侧知道；
    ② ``source_records`` 有 ``UNIQUE (user_id, source_table, source_id)``
    （``backend/models/canonical.py:132``），指针就是天然的分组键；
    ③ 不新增表、不新增列（V3 的「避免双写历史数据」口径）。
    依赖：Go 的 ``AdmitEvidence`` 会 upsert ``source_records``（``db/queries/canonical.sql:149``），
    所以候选被接纳之后这里才查得到——「候选投出去但被门拒了」的情况下一小时后再来一次
    是**期望行为**，那次值得再抽。
    """
    async with SessionLocal() as session:
        found = await session.scalar(
            select(Evidence.id)
            .join(SourceRecord, SourceRecord.id == Evidence.source_record_id)
            .where(
                SourceRecord.user_id == user_id,
                SourceRecord.source_table == table,
                SourceRecord.source_id == row_id,
                Evidence.created_at >= utc_now() - window,
            )
            .limit(1)
        )
    return found is not None


async def quota_available(user_id: str) -> bool:
    """当日 AI 预算还在不在（口径见模块 docstring 第 1 条）。

    查不到用户（注销／不存在）⇒ 返回 False：让命令撞瞬时错走阶梯、最后进 DLQ，
    比「读不到用户还继续调模型」诚实。
    """
    async with SessionLocal() as session:
        user = await user_repository.get_user(session, user_id)
        if user is None:
            return False
        return not await quota_service.is_proactive_quota_exceeded(session, user)


async def record_safety_event(
    user_id: str, verdict: Mapping[str, Any], language: str, excerpt: str
) -> None:
    """危机命中落一行 ``safety_events`` 审计（PRD §5.16 ④／措辞规范 §二 2.3）。

    类别与命中词**取自判定结果**（``crisis_kind_of``），热线按 ``users.timezone`` 推断——
    这三件事都归服务侧，因为 AI 层拿不到用户时区，而「让生产方自己拼一条 A 级文案」
    正是措辞规范要防的形状。
    """
    async with SessionLocal() as session:
        user = await user_repository.get_user(session, user_id)
        await safety_repository.record(
            session,
            user_id,
            kind=crisis_kind_of(verdict),
            source="observation_extract",
            matched_terms=list(verdict.get("matched") or []),
            hotline=safety_service.resolve_hotline(user.timezone if user else None),
            language=language,
            excerpt=excerpt,
            advice_blocked=bool(verdict.get("advice_blocked")),
        )
        await session.commit()


async def complete(prompt: str, language: str) -> str:
    """问一次模型（不 track 之外的副作用一个都没有：留痕由 ``llm.complete`` 自己写）。"""
    return await llm.complete(
        prompt,
        language=language,
        system_prompt=SYSTEM_PROMPT,
        max_tokens=EXTRACT_MAX_TOKENS,
        temperature=0.0,
        purpose=EXTRACT_PURPOSE,
    )


def generation_run_id() -> str:
    """这次生成的留痕 id（``AdmitEvidence`` 的必填四列之一）。"""
    return llm.current_run_id() or ""


def model_id() -> str:
    """这次生成用的模型名，进 ``metadata_json.modelId``。

    ⚠️ 必须与 :func:`complete` 那次调用的档位**同档**：``llm.get_model()`` 不带 ``lite``
    返回的是主模型，而 ``complete`` 用的正是主模型档。哪天抽取改 lite 档，
    这里要一起改——不然 ``modelId`` 会指着一条根本没跑过的模型。
    """
    return llm.get_model()


def build_ports() -> ObservationPorts:
    """生产端口表（八个函数，没有一件是业务分支）。"""
    return ObservationPorts(
        load_source=load_source,
        recent_corrections=recent_corrections,
        already_extracted=already_extracted,
        quota_available=quota_available,
        record_safety_event=record_safety_event,
        complete=complete,
        generation_run_id=generation_run_id,
        model_id=model_id,
    )


def build_registry(registry: Registry, ports: ObservationPorts | None = None) -> Registry:
    """组合根：把 ``observation`` 注册进传入的那张表并返回它。

    只做一件事，而且只做一次：``Registry.register`` 对重复注册抛 ``RegistryError``，
    所以「进程里两张表／注册两遍」这种接线错误会在启动时炸，而不是运行时静默覆盖。
    其余三个任务种类（pattern／insight／proactive）**故意不注册**——
    它们各自的生成逻辑还在别的桶里，注册了就是「把没做完的东西挂上现网入口」。
    """
    registry.register("observation", ObservationExtractHandler(ports or build_ports()))
    return registry


__all__: list[str] = [
    "EXTRACT_MAX_TOKENS",
    "EXTRACT_PURPOSE",
    "NEGATIVE_FEEDBACK_TYPES",
    "already_extracted",
    "build_ports",
    "build_registry",
    "complete",
    "generation_run_id",
    "load_source",
    "model_id",
    "quota_available",
    "record_safety_event",
    "recent_corrections",
]

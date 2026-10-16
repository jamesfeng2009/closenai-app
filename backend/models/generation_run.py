"""
AI 生成留痕表模型（架构方案 O4 · V3 契约 ai_generation_runs）。

职责：记录每一次 LLM/embedding 调用的 provider/model/purpose/耗时/token 与结局，
供三类判定使用：
1. 合规（GB 45438-2025）：分享卡与 Life Archive 的 AIGC 标识可回指到具体生成事件；
2. 成本与降级观测：status=fallback 的行即「模型产出被闸门丢弃、改发模板」；
3. V3 迁移面：AI 写权威态的行（memories / personality_profiles / growth_records）
   经 generation_run_id 指回这里，Go Core 接管权威态时据此判定哪些内容不是 AI 产出。

口径：一条 run 行描述一次调用，不描述内容是否最终生效——生效与否看 status，
派生行的 generation_run_id 只在 status=ok 且产出非空时被写入。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base

# 调用类型（与 backend/ai/llm.py 的三个入口一一对应）
AI_RUN_OPS = ("complete", "stream", "embed")

# 结局：ok=产出可用；error=调用抛异常；fallback=产出被安全闸门丢弃、调用方回退模板
AI_RUN_STATUSES = ("ok", "error", "fallback")


class AiGenerationRun(Base):
    __tablename__ = "ai_generation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # 归属用户（取自请求上下文 contextvar；cron/匿名路径为空）
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # 与日志 trace_id 同源，排障时从日志跳到留痕行
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    op: Mapped[str] = mapped_column(String(12), default="complete")
    # 调用点标识：显式 purpose 优先，否则由调用帧推导（module.func）
    purpose: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lite: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="ok", server_default="ok")
    # status=fallback 时的丢弃原因（safety_gate 等）；status=error 时为异常类名
    fallback_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    input_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # provider 未回传 usage 时为空（OpenAI 兼容接口的可选字段）
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 附加信息（JSON 字符串：embed 的 texts 条数与维度等）
    meta: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_ai_generation_runs_user_created", "user_id", "created_at"),
        Index("ix_ai_generation_runs_purpose_created", "purpose", "created_at"),
        Index("ix_ai_generation_runs_status_created", "status", "created_at"),
    )

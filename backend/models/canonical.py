"""N2 canonical 权威表声明（Personal Model 运行时领域层，V3 01 §27 / 02 §5–§26）。

口径（DDL 属主是 `backend/alembic/versions/c8a2e4b6d0f2_add_canonical_runtime_tables.py`，
本文件只做声明，两遍一致性由 `backend/tests/test_canonical_schema.py` 逐列对账）：

- **Python 不写这些表**（V3 06 §5，D-1 分层）。本模块存在只因为 D-5 把新表删除策略
  定为「沿用 `user_service.PURGE_TARGETS` 逐表 rowcount」，而注销链路跑在 Python 侧；
  除 purge 之外引用这些模型的一律算越界，闸门见 test_canonical_schema.py。
- 状态列是 String 不是 Postgres enum（裁决 §五-4 覆写 V3 02 §3）：合法取值由 Go domain
  层校验，本文件不重复维护白名单，避免第二套事实源。
- 不加外键（D-5 不引入 ON DELETE CASCADE，实测全库也仅 refresh_tokens 一条 FK）：
  引用完整性是 N3 Domain Gate 的「证据存在性与归属」校验职责，不是库级约束职责。
- 每张表都带 user_id，含 7 张关联表与 personal_model_versions —— 这是 D-5 的直接后果：
  V3 的关联表靠 CASCADE 清理，本项目靠逐表 rowcount，进不了 PURGE_TARGETS 就等于删不净。
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


def _id() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True)


def _user_id() -> Mapped[str]:
    # 不单独建 index：每张 canonical 表的 __table_args__ 里都有以 user_id 打头的复合索引
    # （purge 按 user_id 逐表删，D-5），再加一条单列索引只是重复的写放大。
    return mapped_column(String(36), nullable=False)


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _conf() -> Mapped[float | None]:
    """02 §28：confidence/strength/eligibility 一律 0–1 可空。"""
    return mapped_column(Numeric(5, 4), nullable=True)


class PersonalModel(Base):
    """02 §5：一用户一模型，是聚合根；current_version 承载 02 §32 的乐观并发。"""

    __tablename__ = "personal_models"

    id: Mapped[str] = _id()
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    current_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class PersonalModelVersion(Base):
    __tablename__ = "personal_model_versions"

    id: Mapped[str] = _id()
    user_id: Mapped[str] = _user_id()
    personal_model_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    source_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("personal_model_id", "version", name=(
            "personal_model_versions_personal_model_id_version_key")),
        Index("ix_personal_model_versions_user_created", "user_id", "created_at"),
    )


class PersonalModelEvent(Base):
    """02 §24：「改了什么」的唯一答案（02 §37）；不变量 6 的用户纠正审计落这里。"""

    __tablename__ = "personal_model_events"

    id: Mapped[str] = _id()
    user_id: Mapped[str] = _user_id()
    personal_model_id: Mapped[str] = mapped_column(String(36), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    model_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    generation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        Index("ix_personal_model_events_user_created", "user_id", "created_at"),
        Index("ix_personal_model_events_aggregate", "aggregate_type", "aggregate_id"),
        Index("ix_personal_model_events_type_created", "event_type", "created_at"),
        Index("ix_personal_model_events_run", "generation_run_id"),
    )


class SourceRecord(Base):
    """02 §7 的薄版（D-2）：只登记来源类型/时间/指针，不复制原始内容。

    source_table + source_id 指向既有行（memories / chat_messages / mood_checkins…），
    这是「避免双写历史数据」的落点；V3 原文在此放 content TEXT，未采纳。
    """

    __tablename__ = "source_records"

    id: Mapped[str] = _id()
    user_id: Mapped[str] = _user_id()
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_table: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    revision_of: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("user_id", "source_table", "source_id", name=(
            "source_records_user_id_source_table_source_id_key")),
        Index("ix_source_records_user_occurred", "user_id", "occurred_at"),
        Index("ix_source_records_user_type", "user_id", "source_type"),
        Index("ix_source_records_pointer", "source_table", "source_id"),
    )


class Evidence(Base):
    """02 §11：一切派生物的唯一支撑。无证据即无洞察（不变量 1）。"""

    __tablename__ = "evidence"

    id: Mapped[str] = _id()
    user_id: Mapped[str] = _user_id()
    source_record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False, default="supporting")
    strength: Mapped[float | None] = _conf()
    # 03 §4 字段清单含 observation（02 §11 的 DDL 缺，V3 文档自身缺陷）
    observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    generation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        Index("ix_evidence_user_source", "user_id", "source_record_id"),
        Index("ix_evidence_user_created", "user_id", "created_at"),
        Index("ix_evidence_run", "generation_run_id"),
    )


class PersonalFact(Base):
    """02 §8：derived 层的规范化事实。source 层仍是 memories 等既有表。"""

    __tablename__ = "personal_facts"

    id: Mapped[str] = _id()
    user_id: Mapped[str] = _user_id()
    personal_model_id: Mapped[str] = mapped_column(String(36), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(128), nullable=False)
    predicate: Mapped[str] = mapped_column(String(128), nullable=False)
    object_value: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="candidate")
    confidence: Mapped[float | None] = _conf()
    first_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    model_version: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    generation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()

    __table_args__ = (
        Index("ix_personal_facts_user_status", "user_id", "status"),
        Index("ix_personal_facts_user_predicate", "user_id", "predicate"),
        Index("ix_personal_facts_model_status", "personal_model_id", "status"),
        Index("ix_personal_facts_run", "generation_run_id"),
    )


class FactEvidence(Base):
    __tablename__ = "fact_evidence"

    fact_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = _user_id()

    __table_args__ = (
        Index("ix_fact_evidence_user", "user_id", "fact_id"),
        Index("ix_fact_evidence_evidence", "evidence_id"),
    )


class PersonalGoal(Base):
    """02 §9：裁决 §五-3 采纳建表（Goal 需要 goal_followup 生命周期与 Outcome 关联）。"""

    __tablename__ = "personal_goals"

    id: Mapped[str] = _id()
    user_id: Mapped[str] = _user_id()
    personal_model_id: Mapped[str] = mapped_column(String(36), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    target_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()

    __table_args__ = (
        Index("ix_personal_goals_user_status", "user_id", "status"),
        Index("ix_personal_goals_model", "personal_model_id", "status"),
    )


class PersonalCommitment(Base):
    """02 §10：架构方案 §五-5「不建新表」在 Go 权威侧的反转落点（登记于 tasks.md N2）。

    T4-01 起这一张表**同时**是「行动」：`goal_id` 让它挂到目标下，`skipped_at` 让「跳过」
    成为一等状态（PLAN §5 决策 6／§7 状态映射表）。合法状态 `active/completed/skipped/
    cancelled` 在 Go 的 domain 校验，库里不加 CHECK 也不加 enum（裁决 §五-4）。
    """

    __tablename__ = "personal_commitments"

    id: Mapped[str] = _id()
    user_id: Mapped[str] = _user_id()
    personal_model_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # 无 FK（D-5：删除靠逐表 rowcount，FK 会把 purge 顺序变成硬依赖）；归属靠 user_id 谓词。
    goal_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    skipped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()

    __table_args__ = (
        Index("ix_personal_commitments_user_status", "user_id", "status"),
        # 读路径是「某人名下、某目标下按状态筛」；前导列 user_id 同时兜住 purge 那条闸。
        Index("ix_personal_commitments_goal", "user_id", "goal_id", "status"),
    )


class Pattern(Base):
    """02 §13 + 01 §5.2（scope）+ 03 §9–§11（生命周期与分数）。"""

    __tablename__ = "patterns"

    id: Mapped[str] = _id()
    user_id: Mapped[str] = _user_id()
    personal_model_id: Mapped[str] = mapped_column(String(36), nullable=False)
    pattern_type: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="candidate")
    confidence: Mapped[float | None] = _conf()
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observed_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    observed_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_supported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    detector_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 08 §3 FindPatternByFingerprint / 18 §11：重跑同一检测不产生第二条 pattern
    fingerprint: Mapped[str | None] = mapped_column(String(160), nullable=True)
    score_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_version: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    generation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()

    __table_args__ = (
        Index("ix_patterns_user_status", "user_id", "status"),
        Index("ix_patterns_user_updated", "user_id", "updated_at"),
        Index("ix_patterns_user_type", "user_id", "pattern_type"),
        Index("ix_patterns_model_active", "personal_model_id", "status"),
        Index(
            "ix_patterns_user_fingerprint_unique",
            "user_id",
            "fingerprint",
            unique=True,
            postgresql_where=text("fingerprint IS NOT NULL"),
            sqlite_where=text("fingerprint IS NOT NULL"),
        ),
        Index("ix_patterns_run", "generation_run_id"),
    )


class PatternEvidence(Base):
    __tablename__ = "pattern_evidence"

    pattern_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = _user_id()

    __table_args__ = (
        Index("ix_pattern_evidence_user", "user_id", "pattern_id"),
        Index("ix_pattern_evidence_evidence", "evidence_id"),
    )


class Insight(Base):
    """02 §15 + claim_type（03 §14 第 4 关要求逐条判定，02 §15 无此列）。"""

    __tablename__ = "insights"

    id: Mapped[str] = _id()
    user_id: Mapped[str] = _user_id()
    personal_model_id: Mapped[str] = mapped_column(String(36), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    confidence: Mapped[float | None] = _conf()
    time_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    time_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    generation_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    generation_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gate_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_version: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = _ts()
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = _ts()

    __table_args__ = (
        Index("ix_insights_user_status", "user_id", "status"),
        Index("ix_insights_user_published", "user_id", "published_at"),
        Index("ix_insights_model_status", "personal_model_id", "status"),
        Index("ix_insights_claim_type", "user_id", "claim_type"),
        Index("ix_insights_run", "generation_run_id"),
    )


class InsightEvidence(Base):
    """02 §16：洞察的证据边。N9 的读路径回链走这张表。"""

    __tablename__ = "insight_evidence"

    insight_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = _user_id()
    role: Mapped[str] = mapped_column(String(24), nullable=False, default="supporting")

    __table_args__ = (
        Index("ix_insight_evidence_user", "user_id", "insight_id"),
        Index("ix_insight_evidence_evidence", "evidence_id"),
    )


class InsightPattern(Base):
    __tablename__ = "insight_patterns"

    insight_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pattern_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = _user_id()

    __table_args__ = (
        Index("ix_insight_patterns_user", "user_id", "insight_id"),
        Index("ix_insight_patterns_pattern", "pattern_id"),
    )


class Proactive(Base):
    """02 §19 + 04 §3–§11：资格判定与投递态归 Go；journey_event_id 是与现行
    deliver 频控链的唯一关联点（N5 要求合并为一套管线，不建第二套）。"""

    __tablename__ = "proactives"

    id: Mapped[str] = _id()
    user_id: Mapped[str] = _user_id()
    personal_model_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="candidate")
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    eligibility_score: Mapped[float | None] = _conf()
    eligibility_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    block_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generation_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    generation_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    model_version: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    journey_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()

    __table_args__ = (
        Index("ix_proactives_user_due", "user_id", "status", "scheduled_at"),
        Index("ix_proactives_model_status", "personal_model_id", "status"),
        Index("ix_proactives_trigger", "user_id", "trigger_type"),
        Index("ix_proactives_journey_event", "journey_event_id"),
        Index("ix_proactives_run", "generation_run_id"),
    )


class ProactiveInsight(Base):
    __tablename__ = "proactive_insights"

    proactive_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    insight_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = _user_id()

    __table_args__ = (
        Index("ix_proactive_insights_user", "user_id", "proactive_id"),
        Index("ix_proactive_insights_insight", "insight_id"),
    )


class ProactivePattern(Base):
    __tablename__ = "proactive_patterns"

    proactive_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pattern_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = _user_id()

    __table_args__ = (
        Index("ix_proactive_patterns_user", "user_id", "proactive_id"),
        Index("ix_proactive_patterns_pattern", "pattern_id"),
    )


class Outcome(Base):
    """02 §22 + 04 §15 九类（outcome_type 在 02 §22 的 DDL 里缺）。
    origin 只允许用户显式陈述/显式动作/结构化产品事件（不变量 5：沉默不是结局）。"""

    __tablename__ = "outcomes"

    id: Mapped[str] = _id()
    user_id: Mapped[str] = _user_id()
    outcome_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="recorded")
    result: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    model_version: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    generation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()

    __table_args__ = (
        Index("ix_outcomes_user_occurred", "user_id", "occurred_at"),
        Index("ix_outcomes_user_type", "user_id", "outcome_type"),
    )


class OutcomeLink(Base):
    """02 §23：多态指向 goal/commitment/pattern/insight/proactive，
    合法性由领域层校验（V3 原文即要求「不得无校验地使用多态指向」）。"""

    __tablename__ = "outcome_links"

    outcome_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    target_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = _user_id()

    __table_args__ = (
        Index("ix_outcome_links_user", "user_id", "target_type", "target_id"),
        Index("ix_outcome_links_target", "target_type", "target_id"),
    )


class UserFeedback(Base):
    """02 §18：用户确认/纠正/拒绝是持久领域记录，不是埋点。
    取值集以 tasks.md N6 的六态为准（ignored ≠ 负向）。"""

    __tablename__ = "user_feedback"

    id: Mapped[str] = _id()
    user_id: Mapped[str] = _user_id()
    feedback_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    correction_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        Index("ix_user_feedback_user_created", "user_id", "created_at"),
        Index("ix_user_feedback_target", "user_id", "target_type", "target_id"),
    )


class InboxMessage(Base):
    """18 §7：消费幂等表。唯一键是 (message_id, consumer) 而非 message_id 单列——
    同一条消息被两个消费者取用是各自的幂等，不是全局去重。不带 user_id：
    本表只有投递元数据，无个人内容，故不入 PURGE_TARGETS。"""

    __tablename__ = "inbox_messages"

    id: Mapped[str] = _id()
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    message_type: Mapped[str] = mapped_column(String(128), nullable=False)
    consumer: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_at: Mapped[datetime] = _ts()
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("message_id", "consumer", name=(
            "inbox_messages_message_id_consumer_key")),
        Index("ix_inbox_messages_type", "message_type", "status"),
        Index("ix_inbox_messages_received", "received_at"),
    )


class OutboxMessage(Base):
    """18 §2–§3：权威事务里一并写入的投递意图（N7）。

    DDL 属主是 `d9b1f3a5c7e9_add_outbox_messages.py`，三处偏离（user_id / correlation_id /
    causation_id）的理由逐条登记在裁决七-6 N7-1。带 user_id 的直接后果是本表**必须**进
    `PURGE_TARGETS`——payload 存的是事件正文，属个人数据面，删不净就是合规事故。
    """

    __tablename__ = "outbox_messages"

    id: Mapped[str] = _id()
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    message_type: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    routing_key: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = _user_id()
    # ''＝无父消息：刻意不用 NULL（N7-1 偏离②/③）
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    causation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # ''＝产生这一行的上下文没有 trace（后台 cron／存量行）——批 T-B，工作单 T-2
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 两用：失败退避的「何时可再取」+ publishing 的租约到期时刻（N7-2）
    available_at: Mapped[datetime] = _ts()
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("message_id", name="outbox_messages_message_id_key"),
        Index("ix_outbox_messages_user", "user_id", "created_at"),
        Index("ix_outbox_messages_claim", "status", "available_at"),
        Index("ix_outbox_messages_type", "message_type", "status"),
    )

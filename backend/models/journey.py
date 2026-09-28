"""
过桥与安全审计表模型（架构方案 §二 M1/M2/M3/M6 · §三）。

- JourneyEvent：第一周剧本（§5.19.3 D0–D7）与 §5.15a 等级情感回报共用的确定性事件表。
  生成时机为 onboarding 完成事务内预写 pending 行（不是 cron 现算），可审计、可测试。
- OnboardingProfile：性格初读卡（§5.19.2 B01）答案与断言结果。
- UserEntitlement：Life Archive 一次性买断权益（M6，非订阅，走本表而非 tier）。
- SafetyEvent：§5.16 ④ 危机转介命中审计（24h 人工抽检口径）。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, false, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base

# 第一周剧本事件类型（§5.19.3 时刻表 + §5.15a 等级回报，架构方案 §二 M2/M3）
JOURNEY_EVENT_KINDS = (
    "day0_read",          # D0 性格初读卡
    "day1_checkin_echo",  # D1 首次签到 → AI 回应情绪词
    "day2_first_outreach",  # D2 AI 第一次主动开口（唯一走推送的一条）
    "day3_weak_assert",   # D3 首条弱断言
    "day5_stat",          # D5 数据性观察
    "day7_report",        # D7《第一周观察报告》
    "level_reward",       # §5.15a L2/L3/L4 情感回报
    "commitment_follow_up",  # §5.18 承诺回访
)

# 事件状态机：pending → sent → seen；条件不满足时置 skipped（永不删除）
JOURNEY_STATUSES = ("pending", "sent", "seen", "skipped")

# 危机转介命中类别（§5.16 ④ 两类；规范 §二）
SAFETY_HIT_KINDS = ("self_harm", "relationship_violence")


class JourneyEvent(Base):
    __tablename__ = "journey_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    # 剧本日序 0–7；level_reward 取等级阈值天数（7/30/90），承诺回访取捕获后第 N 天
    day_no: Mapped[int] = mapped_column(Integer, default=0)
    event_kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(
        String(12), default="pending", server_default="pending"
    )
    # 计划触达时刻（UTC）；沉默降频通过顺延 scheduled_at 实现，永不删行
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 渲染负载（JSON 字符串：模板参数、报告统计、memory_refs 展开文本）
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 引用依据（JSON 字符串：[{memory_id, created_at, excerpt}]，§5.19.3 全局 SLA）
    memory_refs: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 用户已读标记（App 内红点消费）
    acknowledged: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # 幂等：预写/重试前先按 (user_id, event_kind, day_no) 查重（仓库层判定）。
    # 不在表上加唯一约束——承诺回访同一日可能有多条不同承诺，且与 register_push_token
    # 既有约定一致：避免历史脏数据阻塞迁移，靠事务内查重保证。
    __table_args__ = (
        Index("ix_journey_events_status_scheduled", "status", "scheduled_at"),
        Index("ix_journey_events_user_kind_day", "user_id", "event_kind", "day_no"),
    )


class OnboardingProfile(Base):
    __tablename__ = "onboarding_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    # 引导期用户自选性格类型（初读卡模板主键；D 级标签，仅用于描述用户自身）
    persona_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # 3 道快问答案（JSON 字符串：[{q, option}]）
    answers: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 生成的初读卡（JSON 字符串：assertions[2–3] + summary + card_payload）
    first_read: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 生成时语言（重进 App 按当时语言复现，不随偏好切换漂移）
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserEntitlement(Base):
    """一次性买断权益（Life Archive 三档，架构方案 §二 M6）。

    与 subscriptions 表解耦：买断不产生 tier，权益判定只查本表 revoked_at IS NULL 的行。
    """

    __tablename__ = "user_entitlements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    # App Store 产品 ID：life_archive_1y | life_archive_all | life_archive_legacy
    product_id: Mapped[str] = mapped_column(String(64))
    # 权益档位键：1y | all | legacy（业务侧判定用，避免散落产品 ID 字符串）
    scope: Mapped[str] = mapped_column(String(20))
    transaction_id: Mapped[str] = mapped_column(String(255), unique=True)
    original_transaction_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    purchase_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 退款/撤销时间：非空即权益失效（保留行以审计，不做物理删除）
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Sandbox | Production（Apple 收据环境，隔离测试与真实权益）
    environment: Mapped[str | None] = mapped_column(String(12), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SafetyEvent(Base):
    """危机转介命中审计（§5.16 ④ / 规范 §二 2.3）。

    命中当日为该用户停推营销/升级类消息的判定依据也来自本表（见 is_paused_on）。
    """

    __tablename__ = "safety_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    # self_harm | relationship_violence
    kind: Mapped[str] = mapped_column(String(30))
    # 命中来源：user_message | bedtime_review | proactive_render
    source: Mapped[str] = mapped_column(String(30), default="user_message")
    # 命中的触发词（JSON 字符串数组，供人工抽检复核误报率）
    matched_terms: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 展示地区热线（12356 / 988 / configured fallback）
    hotline: Mapped[str | None] = mapped_column(String(60), nullable=True)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # 原文摘录（截断留存，仅供 24h 人工抽检，不导出展示）
    excerpt: Mapped[str | None] = mapped_column(String(280), nullable=True)
    # 已按铁律阻断沟通改进类建议（关系暴力类命中为 True）
    advice_blocked: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    # 人工抽检结论：pending | ok | false_positive | missed
    review_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

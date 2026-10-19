"""
数据模型层。

职责：
- Pydantic 模型：API 请求/响应的数据验证（见 schemas.py）
- SQLAlchemy 模型：数据库表结构映射

禁止：在此层中编写任何业务逻辑或数据访问逻辑。
"""
from backend.database import Base
from backend.models.auth import RefreshToken
# N2 canonical 权威表：注册进 Base.metadata 即可（purge 清单与闸门按元数据逐表对账），
# Python 侧不读写，口径见 backend/models/canonical.py 模块 docstring。
from backend.models.canonical import (  # noqa: F401
    Evidence,
    FactEvidence,
    InboxMessage,
    Insight,
    InsightEvidence,
    InsightPattern,
    OutboxMessage,
    Outcome,
    OutcomeLink,
    Pattern,
    PatternEvidence,
    PersonalCommitment,
    PersonalFact,
    PersonalGoal,
    PersonalModel,
    PersonalModelEvent,
    PersonalModelVersion,
    Proactive,
    ProactiveInsight,
    ProactivePattern,
    SourceRecord,
    UserFeedback,
)
from backend.models.chat import ChatMessage
from backend.models.consent import CONSENT_PURPOSES, ConsentPurpose, UserConsent
from backend.models.erasure import PostHogErasureRetry
from backend.models.export import EXPORT_STATUSES, DataExportJob
from backend.models.generation_run import AiGenerationRun
from backend.models.growth import GrowthRecord, UserMilestone
from backend.models.journey import (
    JourneyEvent,
    OnboardingProfile,
    SafetyEvent,
    UserEntitlement,
)
from backend.models.memory import Memory
from backend.models.mood import MoodCheckin
from backend.models.personality import PersonalityAssessment, PersonalityProfile
from backend.models.push import PushToken
from backend.models.share import ShareExport
from backend.models.subscription import Receipt, Subscription
from backend.models.user import User

__all__ = [
    "Base",
    "ChatMessage",
    "GrowthRecord",
    "JourneyEvent",
    "Memory",
    "MoodCheckin",
    "OnboardingProfile",
    "PersonalityAssessment",
    "PersonalityProfile",
    "PushToken",
    "Receipt",
    "RefreshToken",
    "SafetyEvent",
    "ShareExport",
    "Subscription",
    "User",
    "UserEntitlement",
    "UserMilestone",
    "UserConsent",
    "ConsentPurpose",
    "PostHogErasureRetry",
    "DataExportJob",
    "EXPORT_STATUSES",
]
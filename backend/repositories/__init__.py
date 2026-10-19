"""
数据访问层。

职责：只与数据库交互，不含业务逻辑。
- 执行 CRUD 操作
- 封装数据库查询
- 依赖注入 AsyncSession

禁止：在此层中编写业务逻辑、业务规则。
"""
from backend.repositories.chat_repository import ChatRepository, chat_repository
from backend.repositories.consent_repository import (
    ConsentRepository,
    consent_repository,
)
from backend.repositories.entitlement_repository import (
    EntitlementRepository,
    entitlement_repository,
)
from backend.repositories.erasure_repository import (
    ErasureRepository,
    erasure_repository,
)
from backend.repositories.export_repository import ExportRepository, export_repository
from backend.repositories.growth_repository import GrowthRepository, growth_repository
from backend.repositories.journey_repository import (
    JourneyRepository,
    journey_repository,
)
from backend.repositories.memory_repository import MemoryRepository, memory_repository
from backend.repositories.milestone_repository import (
    MilestoneRepository,
    milestone_repository,
)
from backend.repositories.mood_repository import MoodRepository, mood_repository
from backend.repositories.onboarding_repository import (
    OnboardingRepository,
    onboarding_repository,
)
from backend.repositories.personality_repository import (
    PersonalityRepository,
    personality_repository,
)
from backend.repositories.push_token_repository import (
    PushTokenRepository,
    push_token_repository,
)
from backend.repositories.safety_repository import (
    SafetyRepository,
    safety_repository,
)
from backend.repositories.share_repository import ShareRepository, share_repository
from backend.repositories.subscription_repository import (
    SubscriptionRepository,
    subscription_repository,
)
from backend.repositories.auth_repository import AuthRepository, auth_repository
from backend.repositories.user_repository import UserRepository, user_repository

__all__ = [
    "ChatRepository",
    "chat_repository",
    "ConsentRepository",
    "consent_repository",
    "EntitlementRepository",
    "entitlement_repository",
    "ErasureRepository",
    "erasure_repository",
    "ExportRepository",
    "export_repository",
    "GrowthRepository",
    "growth_repository",
    "JourneyRepository",
    "journey_repository",
    "MemoryRepository",
    "memory_repository",
    "MilestoneRepository",
    "milestone_repository",
    "MoodRepository",
    "mood_repository",
    "OnboardingRepository",
    "onboarding_repository",
    "PersonalityRepository",
    "personality_repository",
    "PushTokenRepository",
    "push_token_repository",
    "SafetyRepository",
    "safety_repository",
    "ShareRepository",
    "share_repository",
    "AuthRepository",
    "auth_repository",
    "UserRepository",
    "user_repository",
    "SubscriptionRepository",
    "subscription_repository",
]
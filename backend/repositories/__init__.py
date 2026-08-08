"""
数据访问层。

职责：只与数据库交互，不含业务逻辑。
- 执行 CRUD 操作
- 封装数据库查询
- 依赖注入 AsyncSession

禁止：在此层中编写业务逻辑、业务规则。
"""
from backend.repositories.chat_repository import ChatRepository, chat_repository
from backend.repositories.growth_repository import GrowthRepository, growth_repository
from backend.repositories.memory_repository import MemoryRepository, memory_repository
from backend.repositories.milestone_repository import (
    MilestoneRepository,
    milestone_repository,
)
from backend.repositories.mood_repository import MoodRepository, mood_repository
from backend.repositories.natal_repository import NatalRepository, natal_repository
from backend.repositories.personality_repository import (
    PersonalityRepository,
    personality_repository,
)
from backend.repositories.push_token_repository import (
    PushTokenRepository,
    push_token_repository,
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
    "GrowthRepository",
    "growth_repository",
    "MemoryRepository",
    "memory_repository",
    "MilestoneRepository",
    "milestone_repository",
    "MoodRepository",
    "mood_repository",
    "NatalRepository",
    "natal_repository",
    "PersonalityRepository",
    "personality_repository",
    "PushTokenRepository",
    "push_token_repository",
    "ShareRepository",
    "share_repository",
    "AuthRepository",
    "auth_repository",
    "UserRepository",
    "user_repository",
    "SubscriptionRepository",
    "subscription_repository",
]
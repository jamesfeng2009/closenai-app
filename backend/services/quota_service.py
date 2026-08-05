"""
每日配额统一判定（唯一事实源，P1-F02 四层体系差异化权益）。

双口径（PRD P1-F02 §2 Free 层）：
1. 对话轮数：chat/stream 的 user 消息数，免费 10 轮/天（free_daily_chat_rounds）
2. LLM 消耗统一池：主动消息 + 情绪签到解读，免费 5 条/天（free_daily_messages）

tier 语义：free 受上述每日上限约束；pro / pro_plus 走高限（≈不限，
保留上限仅作异常调用的成本熔断，见 config 注释）。

调用点说明：
- is_daily_quota_exceeded 被 chat.py / mood_service / reflection_service 共用
  （调用方不可按场景拆参），语义为「任一池耗尽」——对话入口实际受对话轮数
  约束，签到/反思入口实际受统一池约束。
- cron 主动消息走 is_proactive_quota_exceeded（纯统一池口径）。
- 主页配额展示走 daily_usage（统一池口径，对应"5 条主动消息额度"）。
"""
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.time_utils import local_day_start_utc
from backend.models.user import User
from backend.repositories import chat_repository, mood_repository

FREE_TIER = "free"

# tier → 每日上限映射（free 之外为成本熔断高限）
_UNIFIED_LIMITS = {
    "pro": "pro_daily_messages",
    "pro_plus": "pro_plus_daily_messages",
}
_CHAT_ROUND_LIMITS = {
    "pro": "pro_daily_chat_rounds",
    "pro_plus": "pro_plus_daily_chat_rounds",
}


class QuotaService:
    """每日配额判定（额度与计数口径均以本模块为准）。"""

    def _unified_limit(self, tier: str) -> int:
        """统一池（主动消息 + 签到解读）每日上限。"""
        key = _UNIFIED_LIMITS.get(tier)
        return getattr(settings, key) if key else settings.free_daily_messages

    def _chat_round_limit(self, tier: str) -> int:
        """对话轮数每日上限。"""
        key = _CHAT_ROUND_LIMITS.get(tier)
        return getattr(settings, key) if key else settings.free_daily_chat_rounds

    async def daily_usage(self, db: AsyncSession, user: User) -> int:
        """统一池已用量：用户本地今日主动消息数 + 签到解读数（不含对话轮数）。"""
        day_start = local_day_start_utc(user.timezone)
        proactive_used = await chat_repository.count_today(
            db, user.id, role="assistant", message_type="proactive", start=day_start
        )
        mood_used = await mood_repository.count_since(db, user.id, day_start)
        return proactive_used + mood_used

    async def chat_rounds_today(self, db: AsyncSession, user: User) -> int:
        """对话轮数口径：用户本地今日 chat/stream 的 user 消息数。"""
        day_start = local_day_start_utc(user.timezone)
        return await chat_repository.count_today(
            db, user.id, role="user", start=day_start
        )

    async def is_daily_quota_exceeded(self, db: AsyncSession, user: User) -> bool:
        """共用入口配额判定（chat / 签到解读 / 反思草稿）：任一池耗尽即拒绝。"""
        if await self.chat_rounds_today(db, user) >= self._chat_round_limit(user.tier):
            return True
        return await self.is_proactive_quota_exceeded(db, user)

    async def is_proactive_quota_exceeded(self, db: AsyncSession, user: User) -> bool:
        """统一池（主动消息 / 签到解读）是否已耗尽（cron 主动消息调用点）。"""
        return await self.daily_usage(db, user) >= self._unified_limit(user.tier)


quota_service = QuotaService()

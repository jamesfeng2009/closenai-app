"""
用户服务 (P0-F04)。

管理用户信息、Streak、关系等级、里程碑徽章、Streak 冻结券、推送 Token。
所有方法均注入 `db: AsyncSession` 进行真实数据访问。
"""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.time_utils import local_today, safe_zoneinfo, utc_now
# N2 canonical 权威表：Python 侧只在这里引用它们。V3 06 §5 禁 Python 直连 canonical，
# 而 D-5 把新表删除策略定为「沿用 purge 逐表 rowcount、不引入 ON DELETE CASCADE」，
# 注销链路又跑在 Python —— 两者叠加后「删得净」优先于「不直连」，例外登记在 N2。
from backend.models.canonical import (
    Evidence,
    FactEvidence,
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
from backend.models.schemas import MilestoneItem, UserProfileResponse, UserSettingsUpdate
from backend.models.push import PushToken
from backend.models.share import ShareExport
from backend.models.subscription import Receipt, Subscription
from backend.core import copy
from backend.core.errors import AppException, ErrorCode
from backend.core.logging import get_logger
from backend.models.auth import RefreshToken
from backend.models.user import User
from backend.repositories import (
    auth_repository,
    chat_repository,
    journey_repository,
    milestone_repository,
    user_repository,
)
from backend.services.analytics_service import erase_person_data
from backend.services.quota_service import quota_service
from backend.config import settings

logger = get_logger("closenai.user")

# 前端平台 → 推送通道映射（PushService 按 apns/fcm 分发）
PLATFORM_MAP = {"ios": "apns", "android": "fcm", "apns": "apns", "fcm": "fcm"}

# 注销物理删除清单：顺序即删除顺序，`users` 必须最后（其余表按 user_id 关联，本体删除是举证终点）。
# 新增带 user_id 的表**必须**在这里登记，`test_purge_covers_every_user_scoped_table` 会按
# Base.metadata 逐表对账（2026-10-13 实测：O4 的 ai_generation_runs 建表时漏登记，靠该闸门兜住）。
PURGE_TARGETS = (
    (ChatMessage, "chat_messages"),
    (MoodCheckin, "mood_checkins"),
    (Memory, "memories"),
    (PersonalityProfile, "personality_profiles"),
    (PersonalityAssessment, "personality_assessments"),
    (GrowthRecord, "growth_records"),
    (AiGenerationRun, "ai_generation_runs"),
    (UserMilestone, "user_milestones"),
    (PushToken, "push_tokens"),
    (ShareExport, "share_exports"),
    (Receipt, "receipts"),
    (Subscription, "subscriptions"),
    (UserEntitlement, "user_entitlements"),
    (JourneyEvent, "journey_events"),
    (OnboardingProfile, "onboarding_profiles"),
    (SafetyEvent, "safety_events"),
    # ── N2 canonical 权威表（Go 是唯一写入口，Python 只负责注销清理）──
    # 无外键、无级联（D-5），所以关联表必须自己出现在清单里，否则永远删不净。
    (FactEvidence, "fact_evidence"),
    (PatternEvidence, "pattern_evidence"),
    (InsightEvidence, "insight_evidence"),
    (InsightPattern, "insight_patterns"),
    (ProactiveInsight, "proactive_insights"),
    (ProactivePattern, "proactive_patterns"),
    (OutcomeLink, "outcome_links"),
    (PersonalModelVersion, "personal_model_versions"),
    (PersonalModelEvent, "personal_model_events"),
    (PersonalFact, "personal_facts"),
    (PersonalGoal, "personal_goals"),
    (PersonalCommitment, "personal_commitments"),
    (Evidence, "evidence"),
    (SourceRecord, "source_records"),
    (Pattern, "patterns"),
    (Insight, "insights"),
    (Proactive, "proactives"),
    (Outcome, "outcomes"),
    (UserFeedback, "user_feedback"),
    # N7 投递面：outbox 带 user_id 是因为它的 payload 存的是事件正文（个人数据面），
    # 不登记就等于注销后残留用户可读内容（裁决七-6 N7-1 偏离①、N7-13）。
    # inbox_messages 反过来**刻意不登记**：它只有投递元数据、无 user_id（N2 已登记该口径）。
    (OutboxMessage, "outbox_messages"),
    (PersonalModel, "personal_models"),
    (RefreshToken, "refresh_tokens"),
    (User, "users"),
)


class UserService:
    """用户管理服务。"""

    async def _require_user(
        self, db: AsyncSession, user_id: str, *, for_update: bool = False
    ) -> User:
        """获取用户，不存在/已注销抛 404 USER_NOT_FOUND。

        复用 user_repository.require_user（get_user + 抛错），与业务路径
        27 处 require_user 调用点统一口径，不再按需建匿名用户。
        """
        return await user_repository.require_user(db, user_id, for_update=for_update)

    # 关系等级定义（PRD P0-F05：名称形容 AI 对用户的理解深度，无恋爱含义）
    RELATIONSHIP_LEVELS = {
        1: "初识 L1",
        2: "熟悉 L2",
        3: "知己 L3",
        4: "至交 L4",
    }

    # 等级名称 15 语言展示表（文案库 levels.*；DB 存中文规范名 RELATIONSHIP_LEVELS 仅作内部口径）
    # 见 backend/data/copy/{locale}.json 的 levels 段（PRD P0-F05 等级表）。

    # §5.15a 等级情感回报的 journey 渲染分派键（文案 levels.reward.* 由文案库按语言出）
    LEVEL_REWARD_KEYS = {
        2: "memory_echo",
        3: "monthly_story_preview",
        4: "letter_90d",
    }

    # 升级所需累计活跃天数（PRD P0-F05：L1 0-6 / L2 7-29 / L3 30-89 / L4 90+）
    LEVEL_THRESHOLDS = {1: 0, 2: 7, 3: 30, 4: 90}

    # 里程碑徽章（PRD P0-F05：连续 7/30/100/365 天，判定依据 streak 连续天数；
    # 徽章名 15 语言走文案库 levels.milestone.*）
    MILESTONE_DAYS = {"streak_7": 7, "streak_30": 30, "streak_100": 100, "streak_365": 365}

    # Streak 冻结券月度配额（PRD P0-F05：免费每月 1 张，Pro 每月 3 张）
    FREEZE_MONTHLY_QUOTA = {"free": 1, "pro": 3, "pro_plus": 3}

    async def get_profile(self, db: AsyncSession, user_id: str) -> UserProfileResponse:
        """获取用户信息（按需创建用户）。

        配额实时计算（无需每日重置任务）：统一走 quota_service 口径——
        用户本地今日 user 消息数 + 主动消息数 + 情绪签到解读数。
        """
        user = await self._require_user(db, user_id)
        await self.ensure_freeze_grant(db, user)
        quota_used = await quota_service.daily_usage(db, user)
        return UserProfileResponse(
            id=user.id,
            name=user.name,
            username=user.username,
            mbti_type=user.mbti_type,
            language=user.language,
            streak_count=user.streak_count,
            streak_freeze_count=user.streak_freeze_count,
            relationship_level=user.relationship_level,
            chat_count=user.chat_count,
            tier=user.tier,
            daily_quota_used=quota_used,
            daily_quota_total=settings.free_daily_messages,
            ai_consent=user.ai_consent,
            analytics_consent=user.analytics_consent,
            push_time=user.push_time,
            conversation_style=user.conversation_style,
            # 列非空且迁移带了 server_default='daily'，但老行仍可能是空串（迁移前写的）
            # ⇒ 归一到 daily，否则 Literal 校验会当场 500 把整个个人页打挂。
            push_frequency=user.push_frequency if user.push_frequency in ("daily", "important", "off") else "daily",
        )

    async def set_ai_consent(self, db: AsyncSession, user_id: str, consent: bool) -> bool:
        """设置 AI 数据共享同意状态（5.1.2(i)：同意后可使用 AI 功能，撤回后立即停用）。"""
        await self._require_user(db, user_id)
        await user_repository.set_ai_consent(db, user_id, consent)
        return consent

    async def set_analytics_consent(self, db: AsyncSession, user_id: str, consent: bool) -> dict:
        """设置匿名行为分析同意（与 ai_consent 独立一项，可随时撤回）。

        撤回必须真的停：
        - 未上送的本地队列由前端清空（这里无从代劳）；
        - 已中继到 PostHog 的历史事件由 erase_person_data 抹除——只翻标志位
          等于「同意的瞬间收过数据，撤回后数据照旧留着」，那不叫可撤回。
        erase 失败只回状态、不阻断撤回（分析平台抖动不能让合规动作卡住）。
        """
        await self._require_user(db, user_id)
        await user_repository.set_analytics_consent(db, user_id, consent)
        if not consent:
            erased = await erase_person_data(user_id)
            return {"analytics_consent": False, "posthog_erased": bool(erased.get("erased"))}
        return {"analytics_consent": True, "posthog_erased": False}

    async def require_ai_consent(self, db: AsyncSession, user_id: str) -> None:
        """校验 AI 数据共享同意；未同意则抛 403 AI_CONSENT_REQUIRED（合规 5.1.2(i)）。"""
        from backend.core.errors import AppException, ErrorCode

        user = await self._require_user(db, user_id)
        if not user.ai_consent:
            raise AppException(
                ErrorCode.AI_CONSENT_REQUIRED,
                "使用 AI 功能前，请先阅读并同意 AI 数据共享说明 / "
                "Please review and agree to the AI data sharing notice before using AI features",
            )

    async def update_settings(
        self, db: AsyncSession, user_id: str, settings_update: UserSettingsUpdate
    ) -> UserProfileResponse:
        """更新用户设置。"""
        user = await user_repository.update_settings(
            db,
            user_id,
            name=settings_update.name,
            language=settings_update.language,
            timezone=settings_update.timezone,
            chat_style=settings_update.chat_style,
            conversation_style=settings_update.conversation_style,
            push_time=settings_update.push_time,
            push_frequency=settings_update.push_frequency,
            push_enabled=settings_update.push_enabled,
            mbti_type=settings_update.mbti_type,
            enneagram_type=settings_update.enneagram_type,
        )
        if not user:
            user = await self._require_user(db, user_id)
        return await self.get_profile(db, user_id)

    async def get_language(self, db: AsyncSession, user_id: str) -> str:
        """读取用户语言偏好（默认 en）。"""
        user = await user_repository.get_user(db, user_id)
        if user and user.language:
            return user.language
        return "en"

    async def register_push_token(self, db: AsyncSession, user_id: str, token: str, platform: str):
        """注册设备推送 Token（登录时调用）。

        前端传 ios/android，统一映射为推送通道 apns/fcm 存储。

        竞态说明：并发注册（同一用户双端/重试同时登录）时「先查后插」存在窗口——
        两个请求都查不到同平台记录便各自插入重复行，导致同一设备重复推送。
        先用 require_user(for_update=True) 持用户行锁把并发串行化（与 update_streak
        同一模式；SQLite 方言忽略 FOR UPDATE，测试单连接天然串行），后到者读到
        前者已插入的记录走更新分支，保证 (user_id, platform) 至多一行。
        表上不加唯一约束（避免迁移与历史脏数据冲突），行锁即为防线。
        """
        await self._require_user(db, user_id, for_update=True)
        channel = PLATFORM_MAP.get((platform or "").lower())
        if not channel:
            return  # 未知平台不落库，避免产生永不送达的脏数据
        # 同一平台重复注册时覆盖旧 token
        result = await db.execute(
            select(PushToken).where(
                PushToken.user_id == user_id, PushToken.platform == channel
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.device_token = token
        else:
            db.add(
                PushToken(
                    id=str(uuid4()),
                    user_id=user_id,
                    platform=channel,
                    device_token=token,
                    timezone="UTC",
                )
            )
        await db.flush()

    async def update_streak(self, db: AsyncSession, user_id: str) -> dict:
        """
        更新连续签到天数（行锁串行化：冻结券消耗与 streak 写入同事务）。

        规则：
        - 今天已签到：不更新
        - 昨天已签到：streak + 1
        - 断签：每缺 1 天自动消耗 1 张冻结券（PRD P0-F05：中断当天自动使用）；
          冻结券足够覆盖缺勤天数则 streak 延续 +1，否则重置为 1
        """
        user = await self._require_user(db, user_id, for_update=True)
        # 月度冻结券发放（新月首次活跃时重置配额，先于断签消耗判定）
        await self.ensure_freeze_grant(db, user)
        # Streak 按用户本地时区的日历日计算（PRD：每日 00:00 用户本地重置）
        today = local_today(user.timezone)
        yesterday = today - timedelta(days=1)
        old_level = self.get_relationship_level(user.active_days)[0]

        freeze_used = 0
        if user.last_active_date == today:
            # 今天已签到，不重复累加
            streak = user.streak_count
            active_days = user.active_days
        elif user.last_active_date == yesterday:
            streak = user.streak_count + 1
            active_days = user.active_days + 1
        else:
            # 缺勤天数（last_active_date 之后到昨天的天数）
            gap_days = (today - user.last_active_date).days - 1 if user.last_active_date else 0
            if 0 < gap_days <= user.streak_freeze_count:
                # 冻结券覆盖缺勤，保住 streak
                freeze_used = gap_days
                streak = user.streak_count + 1
            else:
                streak = 1
            active_days = user.active_days + 1

        freeze_count = user.streak_freeze_count - freeze_used
        await user_repository.update_streak(
            db, user_id, streak, today, active_days,
            streak_freeze_count=freeze_count,
            streak_freeze_grant_month=user.streak_freeze_grant_month,
        )
        # 里程碑徽章：streak 跨过 7/30/100/365 阈值时达成（幂等）
        new_milestones = await self.sync_milestones(db, user_id, streak)
        # PRD P0-F05：等级按累计活跃天数晋升（与 streak 中断解耦）
        level, label, progress = self.get_relationship_level(active_days)
        await user_repository.update_relationship_level(db, user_id, label)
        # 等级晋升庆祝（PRD P0-F05：升级时 AI 发送庆祝消息并展示新解锁能力）
        level_up = None
        if level > old_level:
            level_up = await self._celebrate_level_up(db, user, level, active_days)
        return {
            "streak_count": streak,
            "level": label,
            "level_progress": progress,
            "streak_freeze_count": freeze_count,
            "freeze_used": freeze_used,
            "new_milestones": new_milestones,
            "level_up": level_up,
        }

    async def ensure_freeze_grant(self, db: AsyncSession, user: User) -> None:
        """月度冻结券发放（PRD P0-F05：免费每月 1 张 / Pro 每月 3 张）。

        按用户本地月份判断：进入新月且未发放时，重置为该档位月配额并记录发放月。
        直接改写 user 对象内存属性，调用方随后可读到最新值。
        """
        month = local_today(user.timezone).strftime("%Y-%m")
        if user.streak_freeze_grant_month == month:
            return
        quota = self.FREEZE_MONTHLY_QUOTA.get(user.tier or "free", 1)
        await user_repository.update_streak_freeze(db, user.id, quota, month)
        user.streak_freeze_count = quota
        user.streak_freeze_grant_month = month

    async def sync_milestones(self, db: AsyncSession, user_id: str, streak: int) -> list:
        """按当前 streak 补齐已达成的里程碑徽章（幂等），返回本次新达成的类型列表。"""
        existing = await milestone_repository.list_for_user(db, user_id)
        existing_types = {m.milestone_type for m in existing}
        newly: list = []
        for mtype, days in self.MILESTONE_DAYS.items():
            if streak >= days and mtype not in existing_types:
                record = await milestone_repository.create(db, user_id, mtype)
                if record:
                    newly.append(mtype)
        return newly

    async def get_milestones(self, db: AsyncSession, user_id: str, language: str | None = None) -> list:
        """里程碑徽章列表（4 枚，含未达成占位与本地化名称）；先按当前 streak 幂等补齐。"""
        user = await self._require_user(db, user_id)
        await self.sync_milestones(db, user_id, user.streak_count)
        records = await milestone_repository.list_for_user(db, user_id)
        achieved_map = {m.milestone_type: m.achieved_at for m in records}
        return [
            MilestoneItem(
                milestone_type=mtype,
                days=days,
                name=self.milestone_name(mtype, language),
                achieved=mtype in achieved_map,
                achieved_at=achieved_map.get(mtype),
            )
            for mtype, days in self.MILESTONE_DAYS.items()
        ]

    async def clear_push_tokens(self, db: AsyncSession, user_id: str) -> None:
        """清除用户全部推送 Token（登出 / 注销时调用）。"""
        await db.execute(delete(PushToken).where(PushToken.user_id == user_id))
        await db.flush()

    async def soft_delete_account(self, db: AsyncSession, user_id: str) -> dict:
        """注销账号（第一步）：软删除即时失效 + 吊销全部 refresh + 清推送 token。

        - users.status='deleted', deleted_at=now（get_user 立即过滤，业务路径抛 USER_NOT_FOUND）
        - revoke_all_for_user：吊销全部未吊销 refresh token（防 30 分钟窗口期内刷新 access）
        - clear_push_tokens：清推送 token（防注销后继续收到推送）

        物理删除由 auth_service 编排 QStash 异步任务调 purge_user 完成。
        幂等：重复调用对已 deleted 用户无副作用（soft_delete 返回 0）。
        """
        await self._require_user(db, user_id)  # 确认存在且 active（deleted 抛 USER_NOT_FOUND）
        await user_repository.soft_delete(db, user_id)
        await auth_repository.revoke_all_for_user(db, user_id)
        await self.clear_push_tokens(db, user_id)
        return {"deleted": True}

    async def purge_user(self, db: AsyncSession, user_id: str) -> dict:
        """注销账号（第二步）：物理删除用户全部数据（QStash 异步回调，幂等）。

        覆盖全部用户数据表，确保无残留：
        对话 / 情绪签到 / 记忆 / 性格画像 / 性格评估 / 成长记录 / 里程碑徽章 / 推送令牌 /
        分享导出 / 订阅与收据 / 买断权益 / 第一周剧本事件 / 初读卡档案 / 危机命中审计 /
        用户本体。
        已删除的表 rowcount=0，天然幂等（QStash 重试安全）。
        表删完后调用 analytics_service.erase_person_data 抹除 PostHog 侧残留；
        该调用失败只告警、不影响本方法返回（分析平台抖动不能让注销卡在半途）。

        删除举证走日志而不是墓碑表（J4 收口）：墓碑表要成立得有个消费方能拿新
        user_id 关联到旧 user_id，而重新注册是全新 id、两者之间没有任何合法关联键
        —— 一张只有孤立 id 的表谁都读不到，只会多留一份「谁注销了」的数据，
        与「注销即删干净」的承诺相反。所以取证口径是：删前取 deleted_at、
        删后打一行结构化 INFO（user_id + 时间戳 + 各表 rowcount + PostHog 结果）。
        """
        # 必须在 DELETE 之前读：删完就再也拿不到注销时刻了
        deleted_at = (
            await db.execute(select(User.deleted_at).where(User.id == user_id))
        ).scalar_one_or_none()
        counts: dict = {}
        for table, key in PURGE_TARGETS:
            result = await db.execute(delete(table).where(table.user_id == user_id)
                                      if table is not User else delete(User).where(User.id == user_id))
            counts[key] = result.rowcount or 0
        await db.flush()
        # 库内删干净 ≠ 删除完成：事件还留在 PostHog（distinct_id = user_id）。
        # 放在 flush 之后、且不并入 counts——counts 的口径是「各表 rowcount」，
        # 幂等测试按 all(v == 0) 断言，塞入非数值键会让它恒假。
        posthog = await erase_person_data(user_id)
        logger.info(
            "purge 举证 user_id=%s deleted_at=%s total_rows=%d posthog=%s counts=%s",
            user_id,
            deleted_at.isoformat() if deleted_at else '-',
            sum(counts.values()),
            'erased' if posthog.get('erased') else f"pending:{posthog.get('reason', '?')}",
            ','.join(f'{k}={v}' for k, v in counts.items()),
        )
        return counts

    async def purge_deleted_users(self, db: AsyncSession, stale_hours: int = 1) -> list[dict]:
        """cron 兜底：批量物理删除软删除超 stale_hours 的用户。

        QStash 投递失败/未配置时，软删除记录会残留；此方法供
        /cron/purge-deleted-users 定时扫描兜底（逐用户独立 SAVEPOINT，
        单用户失败不影响批次）。
        """
        stale_users = await user_repository.list_deleted_stale(db, stale_hours=stale_hours)
        results: list[dict] = []
        for user in stale_users:
            try:
                async with db.begin_nested():
                    counts = await self.purge_user(db, user.id)
                results.append({"user_id": user.id, "counts": counts})
            except Exception as exc:  # noqa: BLE001  单用户失败不连锁
                logger.warning("purge_deleted_users 失败 user=%s: %s", user.id, exc)
        return results

    def get_relationship_level(self, active_days: int) -> tuple:
        """根据累计活跃天数计算关系等级（PRD P0-F05）。"""
        level = 1
        for lv, threshold in self.LEVEL_THRESHOLDS.items():
            if active_days >= threshold:
                level = lv
        label = self.RELATIONSHIP_LEVELS.get(level, "初识 L1")
        # 进度到下一级
        next_level = level + 1
        if next_level in self.LEVEL_THRESHOLDS:
            current = self.LEVEL_THRESHOLDS[level]
            next_threshold = self.LEVEL_THRESHOLDS[next_level]
            progress = (active_days - current) / (next_threshold - current) if next_threshold > current else 1.0
        else:
            progress = 1.0
        return level, label, min(progress, 1.0)

    def level_label(self, level: int, language: str | None = None) -> str:
        """等级名称按语言本地化（15 语言文案库）。"""
        return copy.t("levels", f"name.{level}", language)

    def milestone_name(self, milestone_type: str, language: str | None = None) -> str:
        """徽章名称按语言本地化；未知类型回退原始类型串。"""
        if milestone_type not in self.MILESTONE_DAYS:
            return milestone_type
        return copy.t("levels", f"milestone.{milestone_type}", language)

    def reward_text(self, level: int, language: str | None = None) -> str | None:
        """§5.15a 情感回报文案（无回报的等级返回 None，不走键名兜底）。"""
        if level not in self.LEVEL_REWARD_KEYS:
            return None
        return copy.t("levels", f"reward.{level}", language)

    def level_unlocks(self, level: int, language: str | None = None) -> list:
        """等级展示用解锁表：情感回报在前、功能解锁在后（PRD §5.15a）。

        两处单独维护会漂移（等级页与升级庆祝读同一张表），因此只在此合成一次。
        """
        reward = self.reward_text(level, language)
        features = copy.t_list("levels", f"features.{level}", language)
        return ([reward] if reward else []) + list(features)

    def get_level_info(self, active_days: int, language: str | None = None) -> dict:
        """当前等级信息（本地化名称 + 进度 + 解锁能力 + 情感回报 + 升级剩余天数）。"""
        level, _canonical, progress = self.get_relationship_level(active_days)
        next_threshold = self.LEVEL_THRESHOLDS.get(level + 1)
        return {
            "level": level,
            "label": self.level_label(level, language),
            "progress": progress,
            "active_days": active_days,
            "next_threshold": next_threshold,
            "days_to_next": (next_threshold - active_days) if next_threshold else 0,
            "unlocks": self.level_unlocks(level, language),
            "reward": self.reward_text(level, language),
        }

    async def get_level_detail(
        self, db: AsyncSession, user_id: str, language: str = "en"
    ) -> dict:
        """等级页数据（PRD P0-F05）：当前等级 + 全部等级/解锁能力表 + 无恋爱含义说明。"""
        user = await self._require_user(db, user_id)
        return {
            "current": self.get_level_info(user.active_days, language),
            "levels": [
                {
                    "level": lv,
                    "label": self.level_label(lv, language),
                    "threshold": self.LEVEL_THRESHOLDS[lv],
                    "unlocks": self.level_unlocks(lv, language),
                    # §5.15a：等级页要能看出这一级"关系上"得到什么，而不只有功能
                    "reward": self.reward_text(lv, language),
                    "reached": user.active_days >= self.LEVEL_THRESHOLDS[lv],
                }
                for lv in sorted(self.LEVEL_THRESHOLDS)
            ],
            "note": copy.t("levels", "note", language),
        }

    async def _celebrate_level_up(
        self, db: AsyncSession, user: User, level: int, active_days: int
    ) -> dict:
        """等级晋升庆祝（PRD P0-F05 + §5.15a）。

        两件事：
        1. 写一条 AI 庆祝消息（规则模板，零 LLM 成本），正文含新解锁能力与情感回报；
        2. 预写 journey_events(kind=level_reward) 的 pending 行——§5.15a 的情感回报
           不是"看到一行文案"就算数，正文（L2 记忆引用 / L3 Monthly Story 预览 /
           L4 90 天信）由剧本调度管线（架构方案 §二 M2）在计划时刻渲染并附引用依据。

        active_days 单调递增（每日至多 +1），等级阈值仅在跨越当日触发一次，天然幂等；
        仓库层再按 (user_id, kind, day_no) 去重，cron 重试不会写出第二条。
        """
        language = user.language or "en"
        zh = (language or "").startswith("zh")
        info = self.get_level_info(active_days, language)
        reward_key = self.LEVEL_REWARD_KEYS.get(level)
        reward_text = self.reward_text(level, language) if reward_key else None
        functional = [
            item for item in info["unlocks"] if reward_text is None or item != reward_text
        ]
        # 列表连接符是纯标点，代码侧二值；句式文案走文案库（levels.celebrate.*）
        sep = "、" if zh else ", "
        text = copy.t("levels", "celebrate.body", language).format(
            days=active_days, label=info["label"], unlocks=sep.join(functional)
        )
        if reward_text:
            text += copy.t("levels", "celebrate.reward", language).format(reward=reward_text)
        await chat_repository.create(
            db, user.id, "assistant", text, message_type="level_up", ai_generated=False
        )
        if reward_key is not None:
            await journey_repository.schedule(
                db,
                user_id=user.id,
                event_kind="level_reward",
                day_no=self.LEVEL_THRESHOLDS[level],
                scheduled_at=self._next_push_moment(user),
                payload={
                    "level": level,
                    "label": info["label"],
                    "reward_key": reward_key,
                    "active_days": active_days,
                    "language": language,
                },
            )
        return {
            "level": info["level"],
            "label": info["label"],
            "unlocks": info["unlocks"],
            "reward": reward_text,
            "reward_key": reward_key,
            "message": text,
        }

    def _next_push_moment(self, user: User) -> datetime:
        """下一个用户本地推送时刻（UTC）：push_time 当日未到用当日，否则顺延一天。

        情感回报属于主动触达，必须走用户偏好的时刻而非服务器时钟的"现在"
        （PRD §5.19.3 频控铁律：主动消息只落在本地推送窗口）。
        """
        zone = safe_zoneinfo(user.timezone)
        now = utc_now().astimezone(zone)
        try:
            hour = int((user.push_time or "09:00").split(":")[0])
        except (ValueError, IndexError):
            hour = 9
        hour = min(max(hour, 0), 23)
        candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)


user_service = UserService()
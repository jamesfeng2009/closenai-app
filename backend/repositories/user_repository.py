"""
用户数据访问层（P0-F04）。

职责：只与 users 表交互，不含业务逻辑。
- get_or_create_user：按需创建用户（开发态 anonymous，无鉴权时使用）
- Streak / 关系等级 / 配额 / 语言 / 推送 Token 的读写
"""
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import case, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AppException, ErrorCode
from backend.core.time_utils import utc_now
from backend.models.user import User

# list_all 分页大小（offset 迭代直至取完，避免硬上限漏用户）
LIST_ALL_PAGE_SIZE = 500


class UserRepository:
    """users 表数据访问。"""

    async def get_or_create_user(
        self, db: AsyncSession, user_id: str, *, for_update: bool = False
    ) -> User:
        """按 user_id 获取用户，不存在则创建（返回新行）。

        软删除防护：get_user 过滤 status='active'，查不到时先查 get_user_any_status；
        若命中 deleted 用户 → 抛 USER_NOT_FOUND（账号已注销），避免在注销 30 分钟
        access_token 窗口期内重建 orphan 用户或触发 PK 冲突。

        for_update=True 时对用户行加 SELECT ... FOR UPDATE 行锁（同事务内
        串行化配额校验+写入，防并发超配额/双写；SQLite 方言忽略该子句）。
        并发创建同 user_id 时捕获 PK 冲突（IntegrityError），回滚到 savepoint
        后重查并返回已存在的行。
        """
        user = await self.get_user(db, user_id, for_update=for_update)
        if user:
            return user
        # 查不到：区分「已注销」vs「完全不存在」
        any_user = await self.get_user_any_status(db, user_id)
        if any_user and any_user.status == "deleted":
            raise AppException(
                ErrorCode.USER_NOT_FOUND, "账号已注销 / Account deleted"
            )
        user = User(
            id=user_id,
            timezone="UTC",
            language="en",
            streak_count=0,
            relationship_level="L1",
            chat_count=0,
            daily_quota_used=0,
            tier="free",
        )
        db.add(user)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            # 并发请求已抢先创建：重查已存在的行（新用户行锁由获胜事务持有，
            # 此处 FOR UPDATE 会等待其提交后拿到锁，天然完成串行化）
            user = await self.get_user(db, user_id, for_update=for_update)
            if user is None:
                raise
        return user

    async def get_user(
        self, db: AsyncSession, user_id: str, *, for_update: bool = False
    ) -> Optional[User]:
        """按 user_id 查询活跃用户（status='active'；deleted 用户查不到）。

        for_update=True 加行锁。
        """
        stmt = select(User).where(User.id == user_id, User.status == "active")
        if for_update:
            stmt = stmt.with_for_update()
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_any_status(
        self, db: AsyncSession, user_id: str, *, for_update: bool = False
    ) -> Optional[User]:
        """按 user_id 查询用户（不过滤 status，含 deleted）。

        供 get_or_create_user 检测 deleted、purge_user 物理删除前确认存在。
        """
        stmt = select(User).where(User.id == user_id)
        if for_update:
            stmt = stmt.with_for_update()
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def require_user(
        self, db: AsyncSession, user_id: str, *, for_update: bool = False
    ) -> User:
        """获取活跃用户，不存在/已注销抛 USER_NOT_FOUND。

        业务路径统一用此方法替代 get_or_create_user（不再按需建用户）。
        用户仅在注册 / Apple / 验证码登录时创建；已认证请求的 user_id 必然存在，
        若不存在则说明已注销或数据异常 → 抛错而非建 orphan。
        """
        user = await self.get_user(db, user_id, for_update=for_update)
        if not user:
            raise AppException(
                ErrorCode.USER_NOT_FOUND, "用户不存在 / User not found"
            )
        return user

    async def list_all(
        self, db: AsyncSession, page_size: int = LIST_ALL_PAGE_SIZE
    ) -> list[User]:
        """列出全部活跃用户（用于 Cron 批量处理；跳过 deleted）。

        分页拉取（每批 page_size，offset 迭代直至取完），避免硬上限漏处理。
        """
        users: list[User] = []
        offset = 0
        while True:
            result = await db.execute(
                select(User)
                .where(User.status == "active")
                .order_by(User.created_at.asc())
                .limit(page_size)
                .offset(offset)
            )
            batch = list(result.scalars().all())
            users.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        return users

    async def update_settings(
        self,
        db: AsyncSession,
        user_id: str,
        *,
        name: Optional[str] = None,
        language: Optional[str] = None,
        timezone: Optional[str] = None,
        chat_style: Optional[str] = None,
        conversation_style: Optional[str] = None,
        push_time: Optional[str] = None,
        push_frequency: Optional[str] = None,
        push_enabled: Optional[bool] = None,
        mbti_type: Optional[str] = None,
        birthday: Optional[date] = None,
        enneagram_type: Optional[str] = None,
    ) -> Optional[User]:
        """更新用户设置（仅更新传入的非空字段）。"""
        values: dict = {}
        if name is not None:
            values["name"] = name
        if language is not None:
            values["language"] = language
        if timezone is not None:
            values["timezone"] = timezone
        if chat_style is not None:
            values["chat_style"] = chat_style
        if conversation_style is not None:
            values["conversation_style"] = conversation_style
        if push_time is not None:
            values["push_time"] = push_time
        if push_frequency is not None:
            values["push_frequency"] = push_frequency
        if push_enabled is not None:
            values["push_enabled"] = push_enabled
        if mbti_type is not None:
            values["mbti_type"] = mbti_type
        if birthday is not None:
            values["birthday"] = birthday
        if enneagram_type is not None:
            values["enneagram_type"] = enneagram_type
        if not values:
            return await self.get_user(db, user_id)
        await db.execute(
            update(User).where(User.id == user_id).values(**values)
        )
        await db.flush()
        return await self.get_user(db, user_id)

    async def update_streak(
        self,
        db: AsyncSession,
        user_id: str,
        streak_count: int,
        last_active_date: date,
        active_days: int,
        streak_freeze_count: Optional[int] = None,
        streak_freeze_grant_month: Optional[str] = None,
    ) -> None:
        """更新连续签到天数、最近活跃日期、累计活跃天数与冻结券状态。"""
        values: dict = {
            "streak_count": streak_count,
            "last_active_date": last_active_date,
            "active_days": active_days,
        }
        if streak_freeze_count is not None:
            values["streak_freeze_count"] = streak_freeze_count
        if streak_freeze_grant_month is not None:
            values["streak_freeze_grant_month"] = streak_freeze_grant_month
        await db.execute(
            update(User)
            .where(User.id == user_id)
            .values(**values)
        )
        await db.flush()

    async def update_streak_freeze(
        self, db: AsyncSession, user_id: str, count: int, grant_month: str
    ) -> None:
        """月度发放冻结券（重置为该档位月配额并记录发放月份）。"""
        await db.execute(
            update(User)
            .where(User.id == user_id)
            .values(streak_freeze_count=count, streak_freeze_grant_month=grant_month)
        )
        await db.flush()

    async def increment_chat_count(self, db: AsyncSession, user_id: str) -> None:
        """对话轮次 +1（累计聊天次数）。"""
        await db.execute(
            update(User).where(User.id == user_id).values(chat_count=User.chat_count + 1)
        )
        await db.flush()

    async def update_relationship_level(
        self, db: AsyncSession, user_id: str, level: str
    ) -> None:
        """更新关系等级。"""
        await db.execute(
            update(User).where(User.id == user_id).values(relationship_level=level)
        )
        await db.flush()

    async def update_tier(self, db: AsyncSession, user_id: str, tier: str) -> None:
        """更新订阅档位（free/pro/pro_plus）。"""
        await db.execute(
            update(User).where(User.id == user_id).values(tier=tier)
        )
        await db.flush()

    async def set_ai_consent(self, db: AsyncSession, user_id: str, consent: bool) -> None:
        """更新 AI 数据共享同意状态（合规 5.1.2(i)）。"""
        await db.execute(
            update(User).where(User.id == user_id).values(ai_consent=consent)
        )
        await db.flush()

    async def increment_special_push(self, db: AsyncSession, user_id: str, month: str) -> int:
        """特殊事件推送月度计数 +1（month 为用户本地 "YYYY-MM"，跨月重置为 1）。

        原子 UPDATE：用 SQL CASE 表达式在单条 UPDATE 内完成「同月 +1 / 跨月重置为 1」，
        避免读-改-写竞态——两个并发调用同时读到相同计数、各自 +1 写回，导致超月度上限。
        special_push_count 列为 NOT NULL（server_default=0），故 `+ 1` 无需 coalesce。
        RETURNING 直接返回更新后的计数，省去二次 SELECT。
        调用方的 pre-check（_special_push_allowed）仅为乐观过滤，真正的上限约束由本
        原子操作保证；调用方亦可对返回值二次校验是否超 SPECIAL_PUSH_MONTHLY_LIMIT。
        """
        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(
                special_push_count=case(
                    (User.special_push_month == month, User.special_push_count + 1),
                    else_=1,
                ),
                special_push_month=month,
            )
            .returning(User.special_push_count)
        )
        result = await db.execute(stmt)
        return int(result.scalar_one())

    # ── 认证：按凭证查询 / 创建 / 改密 / 改用户名 ──

    async def get_by_email(self, db: AsyncSession, email: str) -> Optional[User]:
        """按邮箱查询活跃用户（注册/登录/重置；deleted 用户查不到，可重新注册）。"""
        result = await db.execute(
            select(User).where(User.email == email, User.status == "active")
        )
        return result.scalar_one_or_none()

    async def get_by_username(self, db: AsyncSession, username: str) -> Optional[User]:
        """按用户名查询活跃用户（登录；deleted 用户查不到）。"""
        result = await db.execute(
            select(User).where(User.username == username, User.status == "active")
        )
        return result.scalar_one_or_none()

    async def get_by_apple_id(self, db: AsyncSession, apple_id: str) -> Optional[User]:
        """按 Apple sub 查询活跃用户（Apple 登录；deleted 用户查不到，可重新建账号）。"""
        result = await db.execute(
            select(User).where(User.apple_id == apple_id, User.status == "active")
        )
        return result.scalar_one_or_none()

    async def create_user(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        email: Optional[str] = None,
        username: Optional[str] = None,
        password_hash: Optional[str] = None,
        apple_id: Optional[str] = None,
        language: str = "en",
        timezone: str = "UTC",
        email_verified: bool = False,
    ) -> User:
        """创建用户（注册 / Apple / 验证码登录时调用）。"""
        user = User(
            id=user_id,
            email=email,
            username=username,
            password_hash=password_hash,
            apple_id=apple_id,
            email_verified_at=utc_now() if email_verified else None,
            timezone=timezone,
            language=language,
            streak_count=0,
            relationship_level="L1",
            chat_count=0,
            daily_quota_used=0,
            tier="free",
        )
        db.add(user)
        await db.flush()
        return user

    async def set_password(
        self, db: AsyncSession, user_id: str, password_hash: str
    ) -> None:
        await db.execute(
            update(User).where(User.id == user_id).values(password_hash=password_hash)
        )
        await db.flush()

    async def set_username(
        self, db: AsyncSession, user_id: str, username: str
    ) -> None:
        await db.execute(
            update(User).where(User.id == user_id).values(username=username)
        )
        await db.flush()

    async def set_apple_id(
        self, db: AsyncSession, user_id: str, apple_id: str
    ) -> None:
        """绑定 Apple sub 到已有账号（Apple 登录命中同邮箱账号时）。"""
        await db.execute(
            update(User).where(User.id == user_id).values(apple_id=apple_id)
        )
        await db.flush()

    async def mark_email_verified(self, db: AsyncSession, user_id: str) -> None:
        await db.execute(
            update(User).where(User.id == user_id).values(email_verified_at=utc_now())
        )
        await db.flush()

    # ── 注销：软删除 ──

    async def soft_delete(self, db: AsyncSession, user_id: str) -> int:
        """软删除用户：status='deleted' + deleted_at=now（幂等，返回受影响行数）。

        仅对 active 用户生效；已 deleted 或不存在返回 0。
        注销即时失效（get_user 过滤 deleted），物理删除由 QStash 异步任务执行。
        """
        result = await db.execute(
            update(User)
            .where(User.id == user_id, User.status == "active")
            .values(status="deleted", deleted_at=utc_now())
        )
        await db.flush()
        return result.rowcount or 0

    async def list_deleted_stale(
        self, db: AsyncSession, stale_hours: int = 1
    ) -> list[User]:
        """列出软删除超过 stale_hours 小时的用户（供 cron 兜底物理删除）。

        QStash 投递失败或未配置时，软删除记录会残留；此方法供
        /cron/purge-deleted-users 定时扫描兜底清理。
        stale_hours 默认 1 小时（给 QStash 正常重试留窗口）。
        """
        threshold = utc_now() - timedelta(hours=stale_hours)
        result = await db.execute(
            select(User).where(
                User.status == "deleted",
                User.deleted_at < threshold,
            )
        )
        return list(result.scalars().all())


user_repository = UserRepository()
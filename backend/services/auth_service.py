"""
认证服务。

覆盖：注册 / 登录（密码 / 验证码 / Apple）/ refresh 轮转 / 登出 /
忘记密码 / 重置密码 / 改密码 / 改用户名。

事务：repository 仅 flush，commit 由 get_db 依赖在路由层完成（遵循项目约定）。

安全要点：
- refresh token 明文不落库，仅存 sha256 哈希；轮转 + 吊销链防重放
- 验证码：6 位、TTL 5 分钟、60s 冷却、日上限 10 次；哈希存 Redis，验证成功即删
- 改密 / 重置后吊销该用户全部 refresh token（强制重新登录）
- 忘记密码防枚举：未注册邮箱统一返回成功、不发送
"""
import asyncio
import hashlib
import re
import secrets
from datetime import timedelta, timezone
from uuid import uuid4

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core import redis as redis_client
from backend.core.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_refresh_token,
    verify_apple_identity_token,
    verify_password,
)
from backend.core.errors import AppException, ErrorCode
from backend.core.logging import get_logger
from backend.core.time_utils import utc_now
from backend.models.schemas import AuthResponse, AuthUserResponse
from backend.models.user import User
from backend.repositories import auth_repository, user_repository
from backend.services.email_service import send_verification_code
from backend.services.user_service import user_service

logger = get_logger("closenai.auth_service")

# 验证码：6 位数字，TTL 5 分钟，60s 冷却，日上限 10 次
CODE_TTL = 300
CODE_COOLDOWN = 60
DAILY_SEND_LIMIT = 10
# 验证码校验失败次数上限（防爆破：超过即作废验证码，需重新获取）
CODE_MAX_ATTEMPTS = 5
# 密码登录失败次数上限与锁定时长（防口令爆破）
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_SECONDS = 300
# refresh token 重放宽限期（秒）：宽限内重放视为客户端网络重试，不吊销家族
REPLAY_GRACE_SECONDS = 30
# 密码强度：≥8 位且含字母+数字
PASSWORD_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{8,}$")


class AuthService:
    """认证业务逻辑。"""

    # ── 内部工具 ──

    def _validate_password(self, password: str) -> None:
        if not PASSWORD_RE.match(password):
            raise AppException(
                ErrorCode.PASSWORD_TOO_WEAK,
                "密码至少 8 位，且需包含字母和数字 / Password too weak",
            )

    @staticmethod
    def _gen_code() -> str:
        return f"{secrets.randbelow(1000000):06d}"

    @staticmethod
    def _code_key(email: str, purpose: str) -> str:
        return f"email_code:{purpose}:{email.lower()}"

    @staticmethod
    def _cooldown_key(email: str) -> str:
        return f"email_cooldown:{email.lower()}"

    @staticmethod
    def _daily_key(email: str) -> str:
        return f"email_send_count:{email.lower()}:{utc_now().strftime('%Y%m%d')}"

    @staticmethod
    def _attempts_key(email: str, purpose: str) -> str:
        return f"email_code_attempts:{purpose}:{email.lower()}"

    @staticmethod
    def _login_fail_key(subject: str) -> str:
        return f"login_fail:{subject}"

    @staticmethod
    def _daily_window_ttl() -> int:
        """日限固定窗口 TTL：到下一个 UTC 0 点的秒数。

        key 内嵌日期保证窗口按日历日切分；TTL 对齐窗口边界，
        避免平铺 86400 导致 key 在窗口结束后仍滞留一天。
        """
        now = utc_now()
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(int((midnight - now).total_seconds()), 60)

    @staticmethod
    def _hash_code(code: str) -> str:
        return hashlib.sha256(code.encode()).hexdigest()

    @staticmethod
    def _user_auth_response(user: User) -> AuthUserResponse:
        return AuthUserResponse(
            id=user.id,
            email=user.email,
            username=user.username,
            name=user.name,
            tier=user.tier,
            email_verified=user.email_verified_at is not None,
        )

    async def _issue_tokens(self, db: AsyncSession, user: User) -> AuthResponse:
        """签发 access + refresh token 并落库 refresh 哈希。"""
        token_id = str(uuid4())
        refresh_token = create_refresh_token(user.id, token_id)
        expires_at = utc_now() + timedelta(days=settings.jwt_refresh_expire_days)
        await auth_repository.create_refresh_token(
            db,
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
            expires_at=expires_at,
            token_id=token_id,
        )
        return AuthResponse(
            access_token=create_access_token(user.id),
            refresh_token=refresh_token,
            user=self._user_auth_response(user),
        )

    # ── 注册（邮箱 + 用户名 + 密码）──

    async def register_with_password(
        self,
        db: AsyncSession,
        email: str,
        username: str,
        password: str,
        language: str = "en",
    ) -> AuthResponse:
        self._validate_password(password)
        email = email.lower().strip()
        username = username.strip()
        if await user_repository.get_by_email(db, email):
            raise AppException(ErrorCode.EMAIL_ALREADY_USED, "该邮箱已注册 / Email already used")
        if await user_repository.get_by_username(db, username):
            raise AppException(ErrorCode.USERNAME_ALREADY_USED, "用户名已被占用 / Username already used")
        user = await user_repository.create_user(
            db,
            user_id=str(uuid4()),
            email=email,
            username=username,
            password_hash=hash_password(password),
            language=language,
            email_verified=False,
        )
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            # 并发注册撞唯一索引：区分 email / username 冲突，返回 409 业务码而非 500
            if await user_repository.get_by_email(db, email):
                raise AppException(ErrorCode.EMAIL_ALREADY_USED, "该邮箱已注册 / Email already used")
            raise AppException(ErrorCode.USERNAME_ALREADY_USED, "用户名已被占用 / Username already used")
        return await self._issue_tokens(db, user)

    # ── 密码登录 ──

    async def login_with_password(
        self, db: AsyncSession, identifier: str, password: str
    ) -> AuthResponse:
        ident = identifier.strip()
        user = await user_repository.get_by_email(db, ident.lower())
        if not user:
            user = await user_repository.get_by_username(db, ident)
        # 限流主体：命中账号按 user_id（防账号口令爆破）；未命中按 identifier（防枚举探测）
        fail_key = self._login_fail_key(f"user:{user.id}" if user else f"ident:{ident.lower()}")
        fails = await redis_client.get(fail_key)
        if fails and int(fails) >= LOGIN_MAX_FAILURES:
            raise AppException(
                ErrorCode.RATE_LIMITED,
                "尝试次数过多，请 5 分钟后再试 / Too many attempts, try again later",
            )
        if not user or not user.password_hash or not verify_password(password, user.password_hash):
            # 失败计数（滑动窗口：持续失败持续锁定，阻断爆破）
            await redis_client.incr_with_ttl(fail_key, LOGIN_LOCK_SECONDS)
            raise AppException(ErrorCode.INVALID_CREDENTIALS, "账号或密码错误 / Invalid credentials")
        if fails:
            await redis_client.delete(fail_key)  # 登录成功清零计数
        return await self._issue_tokens(db, user)

    # ── 邮箱验证码 ──

    async def send_email_code(
        self, db: AsyncSession, email: str, purpose: str, language: str = "en"
    ) -> dict:
        email = email.lower().strip()
        # 限流前置：冷却 + 日上限先于业务校验，
        # 防止 purpose=register 的"邮箱已注册"报错被无速率限制地用于枚举已注册邮箱
        if await redis_client.get(self._cooldown_key(email)):
            raise AppException(
                ErrorCode.EMAIL_SEND_RATE_LIMITED, "请求过于频繁，请稍后再试 / Too many requests"
            )
        count = await redis_client.incr_with_ttl(self._daily_key(email), self._daily_window_ttl())
        if count > DAILY_SEND_LIMIT:
            raise AppException(
                ErrorCode.EMAIL_SEND_RATE_LIMITED, "今日发送次数已达上限 / Daily limit reached"
            )

        # 业务校验
        if purpose == "register":
            if await user_repository.get_by_email(db, email):
                raise AppException(ErrorCode.EMAIL_ALREADY_USED, "该邮箱已注册 / Email already used")
        elif purpose == "reset":
            # 防枚举：未注册静默跳过发送，对外仍返回成功
            if not await user_repository.get_by_email(db, email):
                logger.info("reset code requested for unregistered email, silently skip: %s", email)
                return {"status": "ok"}
        # login 不校验（验证码登录自动建账号）

        code = self._gen_code()
        await redis_client.setex(
            self._code_key(email, purpose), CODE_TTL, self._hash_code(code)
        )
        await redis_client.setex(self._cooldown_key(email), CODE_COOLDOWN, "1")
        # resend SDK 为同步阻塞 IO：放线程池执行，避免阻塞 event loop
        await asyncio.to_thread(send_verification_code, email, code, purpose, language)
        return {"status": "ok"}

    async def _verify_code(self, email: str, code: str, purpose: str) -> None:
        key = self._code_key(email, purpose)
        stored = await redis_client.get(key)
        if stored is None:
            raise AppException(ErrorCode.VERIFICATION_CODE_EXPIRED, "验证码已过期，请重新获取 / Code expired")
        if stored != self._hash_code(code):
            # 试错计数：超限即作废验证码（6 位码 1e6 空间，防爆破），需重新获取
            attempts = await redis_client.incr_with_ttl(
                self._attempts_key(email, purpose), CODE_TTL
            )
            if attempts >= CODE_MAX_ATTEMPTS:
                await redis_client.delete(key)
                await redis_client.delete(self._attempts_key(email, purpose))
                raise AppException(
                    ErrorCode.VERIFICATION_CODE_EXPIRED,
                    "尝试次数过多，验证码已失效，请重新获取 / Too many attempts, code invalidated",
                )
            raise AppException(ErrorCode.VERIFICATION_CODE_INVALID, "验证码不正确 / Invalid code")
        await redis_client.delete(key)
        await redis_client.delete(self._attempts_key(email, purpose))

    async def login_with_email_code(
        self, db: AsyncSession, email: str, code: str, language: str = "en"
    ) -> AuthResponse:
        email = email.lower().strip()
        await self._verify_code(email, code, "login")
        user = await user_repository.get_by_email(db, email)
        if not user:
            # 验证码登录：未注册自动建账号（邮箱已验证）
            user = await user_repository.create_user(
                db,
                user_id=str(uuid4()),
                email=email,
                language=language,
                email_verified=True,
            )
        elif user.email_verified_at is None:
            await user_repository.mark_email_verified(db, user.id)
            user.email_verified_at = utc_now()
        return await self._issue_tokens(db, user)

    # ── Apple 登录 ──

    async def login_with_apple(
        self, db: AsyncSession, identity_token: str, language: str = "en"
    ) -> AuthResponse:
        apple_id, _email, _is_private = await verify_apple_identity_token(identity_token)
        user = await user_repository.get_by_apple_id(db, apple_id)
        if user:
            return await self._issue_tokens(db, user)
        # 邮箱已注册（密码/验证码账号）：Apple 已验证邮箱所有权 → 绑定 apple_id 免密合并账号
        if _email:
            existing = await user_repository.get_by_email(db, _email)
            if existing:
                await user_repository.set_apple_id(db, existing.id, apple_id)
                return await self._issue_tokens(db, existing)
        # 首次 Apple 登录：建账号（email 来自 Apple payload，可能为 None）
        user = await user_repository.create_user(
            db,
            user_id=str(uuid4()),
            apple_id=apple_id,
            email=_email,
            email_verified=bool(_email),
            language=language,
        )
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            # 并发首次登录撞唯一索引：重查已创建的账号
            user = await user_repository.get_by_apple_id(db, apple_id)
            if not user and _email:
                user = await user_repository.get_by_email(db, _email)
            if not user:
                raise
        return await self._issue_tokens(db, user)

    # ── refresh 轮转 ──

    async def refresh_tokens(self, db: AsyncSession, refresh_token: str) -> AuthResponse:
        try:
            payload = decode_token(refresh_token)
        except AppException:
            raise AppException(
                ErrorCode.REFRESH_TOKEN_INVALID, "登录已过期，请重新登录 / Session expired"
            )
        if payload.get("type") != "refresh":
            raise AppException(ErrorCode.REFRESH_TOKEN_INVALID, "无效的凭证 / Invalid refresh token")
        user_id = payload.get("sub")
        token_id = payload.get("jti")
        if not user_id or not token_id:
            raise AppException(ErrorCode.REFRESH_TOKEN_INVALID, "无效的凭证 / Invalid refresh token")

        # 行锁串行化并发刷新（同 token 并发请求后到者等待锁，读到已吊销态走重放分支）
        record = await auth_repository.get_by_hash_any(
            db, hash_refresh_token(refresh_token), for_update=True
        )
        if not record or record.id != token_id or record.user_id != user_id:
            raise AppException(
                ErrorCode.REFRESH_TOKEN_INVALID, "登录已过期，请重新登录 / Session expired"
            )
        if record.revoked_at is not None:
            # 重放检测：宽限期内视为客户端网络重试（仅拒绝，不牵连新 token）；
            # 超过宽限期判定为 token 被盗用 → 吊销该用户全家族 refresh token（强制重新登录）
            revoked_at = record.revoked_at
            if revoked_at.tzinfo is None:  # SQLite 读出为 naive，归一化为 UTC
                revoked_at = revoked_at.replace(tzinfo=timezone.utc)
            if utc_now() - revoked_at > timedelta(seconds=REPLAY_GRACE_SECONDS):
                await auth_repository.revoke_all_for_user(db, record.user_id)
                logger.warning(
                    "refresh token 重放检测（疑似盗用）：吊销用户全家族 token user=%s",
                    record.user_id,
                )
            raise AppException(
                ErrorCode.REFRESH_TOKEN_INVALID, "登录已过期，请重新登录 / Session expired"
            )

        user = await user_repository.get_user(db, user_id)
        if not user:
            raise AppException(
                ErrorCode.REFRESH_TOKEN_INVALID, "登录已过期，请重新登录 / Session expired"
            )

        # 轮转：发新 → 吊销旧（replaced_by 指向新 token id）
        new_token_id = str(uuid4())
        new_refresh = create_refresh_token(user.id, new_token_id)
        expires_at = utc_now() + timedelta(days=settings.jwt_refresh_expire_days)
        await auth_repository.create_refresh_token(
            db,
            user_id=user.id,
            token_hash=hash_refresh_token(new_refresh),
            expires_at=expires_at,
            token_id=new_token_id,
        )
        await auth_repository.revoke_token(db, token_id, replaced_by=new_token_id)
        return AuthResponse(
            access_token=create_access_token(user.id),
            refresh_token=new_refresh,
            user=self._user_auth_response(user),
        )

    # ── 登出 ──

    async def logout(self, db: AsyncSession, refresh_token: str) -> dict:
        """吊销当前 refresh token（无效 token 也返回成功，幂等）。"""
        try:
            payload = decode_token(refresh_token)
        except AppException:
            return {"status": "ok"}
        if payload.get("type") != "refresh":
            return {"status": "ok"}
        token_id = payload.get("jti")
        if token_id:
            record = await auth_repository.get_active_by_hash(db, hash_refresh_token(refresh_token))
            if record and record.id == token_id:
                await auth_repository.revoke_token(db, token_id)
        return {"status": "ok"}

    # ── 忘记密码 / 重置 ──

    async def request_password_reset(
        self, db: AsyncSession, email: str, language: str = "en"
    ) -> dict:
        """忘记密码：复用 send_email_code(purpose=reset)，内部已防枚举。"""
        return await self.send_email_code(db, email, "reset", language)

    async def reset_password(
        self, db: AsyncSession, email: str, code: str, new_password: str
    ) -> dict:
        email = email.lower().strip()
        self._validate_password(new_password)
        await self._verify_code(email, code, "reset")
        user = await user_repository.get_by_email(db, email)
        if not user:
            raise AppException(ErrorCode.EMAIL_NOT_REGISTERED, "账号不存在 / Account not found")
        await user_repository.set_password(db, user.id, hash_password(new_password))
        await auth_repository.revoke_all_for_user(db, user.id)  # 强制重新登录
        return {"status": "ok"}

    # ── 改密码 / 改用户名（需登录）──

    async def change_password(
        self, db: AsyncSession, user_id: str, old_password: str, new_password: str
    ) -> dict:
        self._validate_password(new_password)
        user = await user_repository.get_user(db, user_id)
        if not user or not user.password_hash or not verify_password(old_password, user.password_hash):
            raise AppException(ErrorCode.INVALID_CREDENTIALS, "原密码不正确 / Old password incorrect")
        await user_repository.set_password(db, user_id, hash_password(new_password))
        await auth_repository.revoke_all_for_user(db, user.id)  # 改密后全端下线
        return {"status": "ok"}

    async def update_username(
        self, db: AsyncSession, user_id: str, new_username: str
    ) -> dict:
        existing = await user_repository.get_by_username(db, new_username)
        if existing and existing.id != user_id:
            raise AppException(ErrorCode.USERNAME_ALREADY_USED, "用户名已被占用 / Username already used")
        await user_repository.set_username(db, user_id, new_username)
        return {"status": "ok"}

    async def get_me(self, db: AsyncSession, user_id: str) -> AuthUserResponse:
        user = await user_repository.get_user(db, user_id)
        if not user:
            raise AppException(ErrorCode.USER_NOT_FOUND, "用户不存在 / User not found")
        return self._user_auth_response(user)

    # ── 注销账号（软删除 + QStash 异步物理删除）──

    async def delete_account(self, db: AsyncSession, user_id: str) -> dict:
        """注销账号：软删除即时失效 + QStash 异步投递物理删除任务。

        时序：
        1. soft_delete_account（status=deleted + 吊销 refresh + 清 push token）
        2. await db.commit() — 确保软删除落库后再投递 QStash，
           防 QStash 回调早于 commit 导致 purge_user 与软删除事务行锁冲突
        3. _publish_purge_task — 投递物理删除任务（失败不阻断注销，记日志）

        注销即时生效且不可逆：即使 QStash 投递失败，软删除已 commit，
        30 分钟窗口期内业务路径抛 USER_NOT_FOUND，物理删除依赖后续 cron 兜底。
        """
        result = await user_service.soft_delete_account(db, user_id)
        await db.commit()  # 软删除立即落库（不可逆，QStash 投递失败不影响注销生效）
        await self._publish_purge_task(user_id)
        return result

    async def _publish_purge_task(self, user_id: str) -> None:
        """投递物理删除任务到 QStash（回调 /cron/purge-user）。

        仿 cron_service._publish_user_task 模式：httpx POST {base}/v2/publish/{callback}。
        失败不阻断注销（软删除已 commit），记日志；物理删除依赖 QStash 重试或 cron 兜底。
        QStash 未配置（开发环境）时跳过投递，仅记日志。
        """
        if not (settings.qstash_token and settings.backend_base_url):
            logger.warning(
                "QStash 未配置，purge 任务未投递 user=%s（软删除已生效，物理删除待 cron 兜底）",
                user_id,
            )
            return
        base = (settings.qstash_url or "https://qstash.upstash.io").rstrip("/")
        callback = f"{settings.backend_base_url.rstrip('/')}/api/v1/cron/purge-user"
        headers = {
            "Authorization": f"Bearer {settings.qstash_token}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{base}/v2/publish/{callback}",
                    json={"user_id": user_id},
                    headers=headers,
                )
            if not (200 <= resp.status_code < 300):
                logger.warning(
                    "QStash purge publish 失败 user=%s status=%s", user_id, resp.status_code
                )
        except Exception as exc:  # noqa: BLE001  网络异常不阻断注销
            logger.warning("QStash purge publish 异常 user=%s: %s", user_id, exc)


auth_service = AuthService()

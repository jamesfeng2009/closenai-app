"""同意台账服务（Baseline §14.2）。

一条台账写入只做三件事，且都在这一个入口里：
1. 校验用途（枚举白名单，不在 `CONSENT_PURPOSES` 里就是 400）；
2. 写 user_consents 行（授予刷新 granted_at／撤回写 revoked_at）；
3. 兼容期双写 users 上的布尔位＋执行撤回联动（analytics ⇒ PostHog 擦除）。

并发口径：与 register_push_token / update_streak 同一模式——先 `require_user(for_update=True)`
持用户行锁把同一用户的表态串行化，表上的 (user_id, purpose, version) 唯一约束只是兜底
（SQLite 忽略 FOR UPDATE，测试单连接天然串行）。
"""
from backend.core.errors import AppException, ErrorCode
from backend.core.logging import get_logger
from backend.models.consent import (
    CONSENT_PURPOSES,
    CONSENT_TEXT_VERSION,
    PURPOSE_LINKED_FLAGS,
)
from backend.repositories import consent_repository, user_repository
from backend.services.analytics_service import erase_person_data
from backend.core.time_utils import utc_now
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger("closenai.consent")


class ConsentService:
    """同意台账读写。"""

    async def list_consents(self, db: AsyncSession, user_id: str) -> dict:
        """当前版本各用途的表态（缺行＝从未表态）。

        老用户在本功能之前建的账号没有台账行，GET 仍要把六个用途列全（granted=False），
        否则客户端要自己维护一份用途清单，等于把枚举的第二定义源推给前端。
        """
        rows = await consent_repository.list_for_user(db, user_id)
        by_key = {(r.purpose, r.version): r for r in rows}
        items = []
        for purpose in CONSENT_PURPOSES:
            row = by_key.get((purpose, CONSENT_TEXT_VERSION))
            items.append(
                {
                    "purpose": purpose,
                    "version": CONSENT_TEXT_VERSION,
                    "granted": bool(row and row.granted_at and not row.revoked_at),
                    "granted_at": row.granted_at if row else None,
                    "revoked_at": row.revoked_at if row else None,
                }
            )
        # 历史版本一并回显：只回当前版本会让「在哪个版本同意过」这条台账查询重新丢一半
        for (purpose, version), row in by_key.items():
            if version != CONSENT_TEXT_VERSION:
                items.append(
                    {
                        "purpose": purpose,
                        "version": version,
                        "granted": bool(row.granted_at and not row.revoked_at),
                        "granted_at": row.granted_at,
                        "revoked_at": row.revoked_at,
                    }
                )
        return {"version": CONSENT_TEXT_VERSION, "purposes": list(CONSENT_PURPOSES), "items": items}

    async def set_consent(
        self, db: AsyncSession, user_id: str, purpose: str, granted: bool
    ) -> dict:
        """记一次表态，并同步兼容期布尔位与撤回联动。"""
        if purpose not in CONSENT_PURPOSES:
            raise AppException(
                ErrorCode.INVALID_PARAMS,
                f"未知用途 purpose={purpose}，允许值：{', '.join(CONSENT_PURPOSES)}",
            )
        # 行锁：同一用户的并发表态串行化，唯一约束只兜底
        await user_repository.require_user(db, user_id, for_update=True)
        at = utc_now()
        row = await consent_repository.record(db, user_id, purpose, granted, at)
        await self._sync_linked_flag(db, user_id, purpose, granted)
        extra: dict = {}
        if not granted and purpose == "analytics":
            # 撤回必须真的停：已中继到 PostHog 的历史事件要抹除（口径同旧 POST /user/analytics-consent）
            erased = await erase_person_data(user_id)
            extra["posthog_erased"] = bool(erased.get("erased"))
        logger.info(
            "consent user=%s purpose=%s version=%s granted=%s",
            user_id, purpose, row.version, granted,
        )
        return {
            "purpose": row.purpose,
            "version": row.version,
            "granted": granted,
            "granted_at": row.granted_at,
            "revoked_at": row.revoked_at,
            **extra,
        }

    async def note_consent(
        self, db: AsyncSession, user_id: str, purpose: str, granted: bool
    ) -> None:
        """由别的入口顺带补记台账（注册默认、PATCH push_enabled），不双写、不联动。

        与 set_consent 分开是因为调用方已经自己写了布尔位（update_settings），
        再同步一次会把同一列写两遍；联动（PostHog 擦除）也不该由设置 PATCH 触发。
        """
        await consent_repository.record(db, user_id, purpose, granted, utc_now())

    async def grant_registration_consents(self, db: AsyncSession, user_id: str) -> None:
        """注册即同意的默认值：core_service（服务本体，未同意就无法提供账号）。

        其余五项一律不预置——Baseline §11.2 末句「不得以模糊文案推定同意」，
        注册勾选页没有列出的用途必须留空行（granted=False）。
        """
        await consent_repository.record(
            db, user_id, "core_service", True, utc_now()
        )

    async def _sync_linked_flag(
        self, db: AsyncSession, user_id: str, purpose: str, granted: bool
    ) -> None:
        flag = PURPOSE_LINKED_FLAGS.get(purpose)
        if flag is None:
            return
        if flag == "ai_consent":
            await user_repository.set_ai_consent(db, user_id, granted)
        elif flag == "analytics_consent":
            await user_repository.set_analytics_consent(db, user_id, granted)
        elif flag == "push_enabled":
            await user_repository.update_settings(db, user_id, push_enabled=granted)


consent_service = ConsentService()

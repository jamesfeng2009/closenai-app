"""
IAP 订阅服务 (P1-F02)。

管理 StoreKit 收据校验、订阅状态查询、退款/续订处理、权益下发。

安全核心：
- 收据必须由服务端调用 App Store Server API 校验，禁止信任客户端
- 校验通过后才写入 subscriptions 并下发权益
- 退款 / 过期通知立即回收权益（降级为 free）

未配置 `APP_STORE_SHARED_SECRET` 时（开发态）：
- debug=True 时返回模拟成功，便于本地联调
- 生产环境缺失密钥则拒收并抛出清晰错误
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from backend.config import settings
from backend.repositories import subscription_repository, user_repository

logger = logging.getLogger("closenai.iap")

# App Store verifyReceipt 端点
VERIFY_PROD_URL = "https://buy.itunes.apple.com/verifyReceipt"
VERIFY_SANDBOX_URL = "https://sandbox.itunes.apple.com/verifyReceipt"

# 升级回退处理：同样的收据在沙盒/生产间切换时重试
APPLE_21007 = 21007  # 沙盒收据打到生产端点
APPLE_21008 = 21008  # 生产收据打到沙盒端点

# 产品 → 档位映射（与现有 tier 体系 free/pro/pro_plus 对齐）
# 支持 P1-F02 四层体系中的 Pro/Premium/Life Archive 映射到现有档位
PRODUCT_TIER_MAP = {
    "pro": "pro",
    "pro_plus": "pro_plus",
    "premium": "pro_plus",
    "life_archive": "pro_plus",
}


def _map_tier(product_id: str) -> str:
    """根据 product_id 推断档位。优先精确匹配，否则按前缀模糊匹配。"""
    pid = (product_id or "").lower()
    for key, tier in PRODUCT_TIER_MAP.items():
        if key in pid:
            return tier
    return "pro"


class IAPService:
    """IAP 收据校验与订阅管理服务。"""

    async def verify(
        self,
        db,
        user_id: str,
        receipt_data: str,
        product_id: str,
        is_sandbox: bool = False,
    ) -> Dict[str, Any]:
        """
        收据校验（调用 App Store verifyReceipt）。

        1. 调用 App Store Server API 校验收据
        2. Sandbox → sandbox.itunes.apple.com；Production → buy.itunes.apple.com
        3. 校验通过 → 写入 subscriptions + receipts → 更新用户 tier
        4. 校验失败 → 拒绝
        """
        result = await self._validate(db, user_id, receipt_data, product_id, is_sandbox)
        if not result.get("valid"):
            return result
        return result

    async def _validate(
        self,
        db,
        user_id: str,
        receipt_data: str,
        product_id: str,
        is_sandbox: bool,
    ) -> Dict[str, Any]:
        # 开发态且未配置密钥：模拟成功（同样落库，保证 dev 联调时状态一致）
        if not settings.app_store_shared_secret:
            if settings.debug:
                result = self._dev_success(user_id, product_id, is_sandbox)
                await user_repository.get_or_create_user(db, user_id)
                await subscription_repository.upsert(
                    db,
                    user_id,
                    product_id=product_id,
                    tier=result["tier"],
                    expires_at=result["expires_at"],
                    original_transaction_id=None,
                    is_active=True,
                )
                await user_repository.update_tier(db, user_id, result["tier"])
                return result
            return {"valid": False, "tier": "free", "expires_at": None, "message": "IAP 未配置共享密钥"}

        url = VERIFY_SANDBOX_URL if is_sandbox else VERIFY_PROD_URL
        payload = {
            "receipt-data": receipt_data,
            "password": settings.app_store_shared_secret,
            "exclude-old-transactions": True,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.error("App Store 校验请求失败: %s", exc)
            return {"valid": False, "message": "App Store 校验服务不可用"}

        status = data.get("status")

        # 环境切换：沙盒/生产 21007/21008 时换端点重试一次
        if status in (APPLE_21007, APPLE_21008):
            alt_url = VERIFY_PROD_URL if is_sandbox else VERIFY_SANDBOX_URL
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(alt_url, json=payload)
                    data = resp.json()
                status = data.get("status")
            except httpx.HTTPError:
                return {"valid": False, "message": "App Store 校验服务不可用"}

        if status != 0:
            return {"valid": False, "message": f"App Store 校验失败 ({status})"}

        # 解析最新一笔订阅交易
        latest_info = data.get("latest_receipt_info") or data.get("receipt", {}).get("in_app") or []
        info = latest_info[-1] if latest_info else {}
        expires_ms = info.get("expires_date_ms") or info.get("expires_date")
        expires_at = self._parse_expiry(expires_ms)
        original_tx = info.get("original_transaction_id")
        tier = _map_tier(product_id)

        # 写入订阅 + 收据 + 下发权益
        await user_repository.get_or_create_user(db, user_id)
        await subscription_repository.upsert(
            db,
            user_id,
            product_id=product_id,
            tier=tier,
            expires_at=expires_at,
            original_transaction_id=original_tx,
            is_active=True,
        )
        await subscription_repository.create_receipt(
            db,
            user_id,
            product_id=product_id,
            receipt_data=receipt_data,
            status="valid",
            transaction_id=original_tx,
        )
        await user_repository.update_tier(db, user_id, tier)

        return {
            "valid": True,
            "tier": tier,
            "expires_at": expires_at,
            "message": "校验通过",
        }

    async def get_status(self, db, user_id: str) -> Dict[str, Any]:
        """查询订阅状态（从 subscriptions 表读取真实数据）。"""
        await user_repository.get_or_create_user(db, user_id)
        sub = await subscription_repository.get_active(db, user_id)
        now = datetime.now(timezone.utc)

        if not sub or sub.expires_at is None or sub.expires_at <= now:
            # 无有效订阅 → free
            await user_repository.update_tier(db, user_id, "free")
            return {
                "tier": "free",
                "is_active": False,
                "expires_at": None,
                "product_id": None,
                "auto_renew": False,
            }

        return {
            "tier": sub.tier,
            "is_active": True,
            "expires_at": sub.expires_at,
            "product_id": sub.product_id,
            "auto_renew": True,
        }

    async def handle_refund(self, db, original_transaction_id: str) -> None:
        """处理退款：立即回收权益（降级为 free）。"""
        sub = await subscription_repository.get_by_original_transaction(db, original_transaction_id)
        if not sub:
            logger.warning("退款通知：未找到订阅 %s", original_transaction_id)
            return
        await subscription_repository.set_active(db, sub.id, False)
        await user_repository.update_tier(db, sub.user_id, "free")
        logger.info("退款处理完成 user=%s sub=%s", sub.user_id, sub.id)

    async def handle_renewal(self, db, original_transaction_id: str, expires_at: datetime) -> None:
        """处理续订：更新过期时间并保持激活。"""
        sub = await subscription_repository.get_by_original_transaction(db, original_transaction_id)
        if not sub:
            logger.warning("续订通知：未找到订阅 %s", original_transaction_id)
            return
        sub.expires_at = expires_at
        sub.is_active = True
        await db.flush()
        await user_repository.update_tier(db, sub.user_id, sub.tier)
        logger.info("续订处理完成 user=%s expires=%s", sub.user_id, expires_at)

    async def handle_expired(self, db, original_transaction_id: str) -> None:
        """处理过期：降级为 free。"""
        sub = await subscription_repository.get_by_original_transaction(db, original_transaction_id)
        if not sub:
            return
        await subscription_repository.set_active(db, sub.id, False)
        await user_repository.update_tier(db, sub.user_id, "free")
        logger.info("订阅过期处理完成 user=%s", sub.user_id)

    async def handle_notification(self, db, payload: Dict[str, Any]) -> str:
        """处理 App Store Server Notifications V2（实时同步续订/退款/过期）。"""
        notification_type = payload.get("notificationType", "")
        data = payload.get("data", {})
        signed_tx = data.get("signedTransactionInfo", {})
        original_tx = signed_tx.get("originalTransactionId")
        expires_ms = signed_tx.get("expiresDate")
        expires_at = self._parse_expiry(expires_ms)

        if notification_type == "DID_RENEW" and original_tx:
            await self.handle_renewal(db, original_tx, expires_at or datetime.now(timezone.utc))
        elif notification_type in ("REFUND", "REVOKE") and original_tx:
            await self.handle_refund(db, original_tx)
        elif notification_type in ("EXPIRED", "DID_FAIL_TO_RENEW") and original_tx:
            await self.handle_expired(db, original_tx)
        elif notification_type == "DID_CHANGE_RENEWAL_STATUS":
            # 自动续订开关变化，仅记录
            logger.info("自动续订状态变化 user tx=%s", original_tx)
        return notification_type or "UNKNOWN"

    def _dev_success(self, user_id: str, product_id: str, is_sandbox: bool) -> Dict[str, Any]:
        """开发态模拟成功（未配置密钥时）。"""
        tier = _map_tier(product_id)
        expires_at = datetime.now(timezone.utc).replace(
            hour=23, minute=59, second=59, microsecond=0
        )
        logger.info("开发态模拟订阅成功 user=%s tier=%s", user_id, tier)
        return {"valid": True, "tier": tier, "expires_at": expires_at, "message": "校验通过（开发态）"}

    def _parse_expiry(self, raw: Any) -> Optional[datetime]:
        """解析 Apple 的过期时间（毫秒时间戳或 ISO 字符串）。"""
        if not raw:
            return None
        try:
            if isinstance(raw, (int, float)):
                return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
            if isinstance(raw, str) and raw.isdigit():
                return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
            # ISO 8601
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None


iap_service = IAPService()
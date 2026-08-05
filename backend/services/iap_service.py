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
import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from backend.config import settings
from backend.core.errors import AppException, ErrorCode
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
    # 按键长降序匹配：必须先匹配 "pro_plus" 再匹配 "pro"，
    # 否则 "pro" in "xxx_pro_plus_monthly" 恒 True，Pro+ 会被错误降级为 Pro
    for key, tier in sorted(PRODUCT_TIER_MAP.items(), key=lambda kv: -len(kv[0])):
        if key in pid:
            return tier
    return "pro"


def _b64url_decode(data: str) -> bytes:
    """base64url 解码（补齐 padding）。"""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def decode_apple_jws(token: str) -> Dict[str, Any]:
    """解码并验证 Apple JWS（App Store Server Notifications V2 / signedTransactionInfo）。

    验证链：
    1. header.x5c 证书链：每个证书由下一个证书签发（ECDSA/RSA 自适应）
    2. 根证书 subject 必须含 "Apple Root CA"
    3. 叶子证书在有效期内
    4. 用叶子证书公钥验签 JWS（ES256）

    任何一步失败都抛 AppException（fail-closed），防止伪造通知升降级订阅。
    """
    try:
        from cryptography import x509
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    except ImportError as exc:  # cryptography 缺失时 fail-closed
        raise AppException(ErrorCode.IAP_VERIFY_FAILED, "服务器缺少 JWS 验签依赖") from exc

    parts = token.split(".")
    if len(parts) != 3:
        raise AppException(ErrorCode.IAP_VERIFY_FAILED, "无效的 JWS 格式")

    try:
        header = json.loads(_b64url_decode(parts[0]))
        chain_b64 = header.get("x5c") or []
        certs = [x509.load_der_x509_certificate(base64.b64decode(c)) for c in chain_b64]
    except Exception as exc:
        raise AppException(ErrorCode.IAP_VERIFY_FAILED, "JWS 证书链解析失败") from exc

    if len(certs) < 2:
        raise AppException(ErrorCode.IAP_VERIFY_FAILED, "JWS 证书链不完整")

    def _verify_cert(cert, issuer_cert) -> None:
        pub = issuer_cert.public_key()
        if isinstance(pub, rsa.RSAPublicKey):
            pub.verify(cert.signature, cert.tbs_certificate_bytes, padding.PKCS1v15(), cert.signature_hash_algorithm)
        elif isinstance(pub, ec.EllipticCurvePublicKey):
            pub.verify(cert.signature, cert.tbs_certificate_bytes, ec.ECDSA(cert.signature_hash_algorithm))
        else:
            raise InvalidSignature("不支持的证书公钥类型")

    try:
        # 链内签发关系：leaf ← intermediate ← root
        for i in range(len(certs) - 1):
            _verify_cert(certs[i], certs[i + 1])
        # 根必须自签且为 Apple Root CA
        root = certs[-1]
        if "Apple Root CA" not in root.subject.rfc4514_string():
            raise InvalidSignature("根证书非 Apple Root CA")
        _verify_cert(root, root)
        # 叶子证书有效期
        leaf = certs[0]
        now = datetime.now(timezone.utc)
        if not (leaf.not_valid_before_utc <= now <= leaf.not_valid_after_utc):
            raise InvalidSignature("叶子证书已过期或未生效")
        # JWS 签名
        leaf.public_key().verify(
            _b64url_decode(parts[2]),
            f"{parts[0]}.{parts[1]}".encode(),
            ec.ECDSA(ec.SHA256()),
        )
    except InvalidSignature as exc:
        raise AppException(ErrorCode.IAP_VERIFY_FAILED, "JWS 验签失败") from exc

    return json.loads(_b64url_decode(parts[1]))


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
                    is_trial=result["is_trial"],
                )
                await user_repository.update_tier(db, user_id, result["tier"])
                return result
            return {"valid": False, "tier": "free", "expires_at": None, "is_trial": False, "message": "IAP 未配置共享密钥"}

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

        # 解析最新一笔订阅交易（按过期时间取最新，不依赖数组顺序）
        latest_info = data.get("latest_receipt_info") or data.get("receipt", {}).get("in_app") or []
        info = max(
            latest_info,
            key=lambda x: int(x.get("expires_date_ms") or 0),
            default={},
        )
        expires_ms = info.get("expires_date_ms") or info.get("expires_date")
        expires_at = self._parse_expiry(expires_ms)
        original_tx = info.get("original_transaction_id")
        # 试用标记（PRD P1-F02 §4）：收据 is_trial_period 为字符串 "true"/"false"
        is_trial = str(info.get("is_trial_period") or "").lower() == "true"
        # 档位必须来自收据内真实交易的 product_id，禁止信任客户端入参（防买低档领高档）
        receipt_product_id = info.get("product_id") or product_id
        tier = _map_tier(receipt_product_id)

        # 收据跨账号复用检查：同一原始交易已绑定其他账号时拒绝（防一收据多账号白嫖）
        if original_tx:
            existing = await subscription_repository.get_by_original_transaction(db, original_tx)
            if existing and existing.user_id != user_id:
                raise AppException(
                    ErrorCode.SUBSCRIPTION_ALREADY_EXISTS,
                    "该收据已绑定其他账号",
                )

        # 写入订阅 + 收据 + 下发权益
        await user_repository.get_or_create_user(db, user_id)
        await subscription_repository.upsert(
            db,
            user_id,
            product_id=receipt_product_id,
            tier=tier,
            expires_at=expires_at,
            original_transaction_id=original_tx,
            is_active=True,
            is_trial=is_trial,
        )
        await subscription_repository.create_receipt(
            db,
            user_id,
            product_id=receipt_product_id,
            receipt_data=receipt_data,
            status="valid",
            transaction_id=original_tx,
        )
        await user_repository.update_tier(db, user_id, tier)

        return {
            "valid": True,
            "tier": tier,
            "expires_at": expires_at,
            "is_trial": is_trial,
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
                "is_trial": False,
            }

        return {
            "tier": sub.tier,
            "is_active": True,
            "expires_at": sub.expires_at,
            "product_id": sub.product_id,
            "auto_renew": True,
            "is_trial": bool(sub.is_trial),
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
        """处理续订：更新过期时间并保持激活。续订成功即试用转正，清除试用标记。"""
        sub = await subscription_repository.get_by_original_transaction(db, original_transaction_id)
        if not sub:
            logger.warning("续订通知：未找到订阅 %s", original_transaction_id)
            return
        sub.expires_at = expires_at
        sub.is_active = True
        sub.is_trial = False
        sub.trial_reminder_sent = False
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

    async def handle_notification(self, db, body: Dict[str, Any]) -> str:
        """处理 App Store Server Notifications V2（实时同步续订/退款/过期）。

        安全：通知主体为 signedPayload(JWS)，必须先验签再解析（防伪造 REFUND/DID_RENEW）；
        并校验 bundleId 与 environment 是否匹配本应用，错配直接拒绝（fail-closed）。
        """
        signed_payload = body.get("signedPayload")
        if not signed_payload or not isinstance(signed_payload, str):
            raise AppException(ErrorCode.IAP_VERIFY_FAILED, "缺少 signedPayload，拒绝处理")

        payload = decode_apple_jws(signed_payload)
        notification_type = payload.get("notificationType", "")
        data = payload.get("data", {}) or {}

        # 校验通知归属：bundleId 必须是本应用（apns_topic 即 bundle id）
        bundle_id = data.get("bundleId")
        if settings.apns_topic and bundle_id and bundle_id != settings.apns_topic:
            raise AppException(ErrorCode.IAP_VERIFY_FAILED, "bundleId 不匹配，拒绝处理")
        # 校验环境（sandbox / production 隔离）
        env = data.get("environment")
        expected_env = "Production" if settings.app_store_env == "production" else "Sandbox"
        if env and env != expected_env:
            raise AppException(ErrorCode.IAP_VERIFY_FAILED, f"环境不匹配（{env}），拒绝处理")

        # Apple 测试通知：验签通过即可直接确认
        if notification_type == "TEST":
            logger.info("收到 Apple 测试通知，验签通过")
            return notification_type

        # signedTransactionInfo 同为 JWS，需验签解码
        signed_tx_raw = data.get("signedTransactionInfo")
        signed_tx = decode_apple_jws(signed_tx_raw) if signed_tx_raw else {}
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
        return {
            "valid": True,
            "tier": tier,
            "expires_at": expires_at,
            "is_trial": False,
            "message": "校验通过（开发态）",
        }

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
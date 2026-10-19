"""
IAP 订阅服务 (P1-F02)。

管理 StoreKit 收据校验、订阅状态查询、退款/续订处理、权益下发。

安全核心：
- 收据必须由服务端调用 App Store Server API 校验，禁止信任客户端
- 校验通过后才写入 subscriptions 并下发权益
- 退款 / 过期通知立即回收权益（降级为 free）
- Life Archive 一次性买断走 user_entitlements，与 tier / subscriptions 完全解耦
  （架构方案 §二 M6 / §九-4）；买断收据不改 tier

未配置 `APP_STORE_SHARED_SECRET` 时（开发态）：
- debug=True 时返回模拟成功，便于本地联调
- 生产环境缺失密钥则拒收并抛出清晰错误
"""
import asyncio
import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.exc import IntegrityError

from backend.config import settings
from backend.core.errors import AppException, ErrorCode
from backend.repositories import (
    entitlement_repository,
    subscription_repository,
    user_repository,
)
from backend.services.delivery_service import DeliveryEvent, emit
from backend.services.journey_service import journey_service

logger = logging.getLogger("closenai.iap")

# 试用时长（天，PRD §5.21：Pro 主订阅 7 天免费试用 / intro offer）。
# 用于由 expires_at 反推试用开始时刻，安排 D5 资产盘点触点。
TRIAL_LENGTH_DAYS = 7

# App Store verifyReceipt 端点
VERIFY_PROD_URL = "https://buy.itunes.apple.com/verifyReceipt"
VERIFY_SANDBOX_URL = "https://sandbox.itunes.apple.com/verifyReceipt"

# Apple 官方根证书（锚定用，DER 的 base64）。
# 来源：https://www.apple.com/certificateauthority/AppleRootCA-G3.cer
# SHA-256 指纹：63:34:3A:BF:B8:9A:6A:03:EB:B5:7E:9B:3F:5F:A7:BE:7C:4F:5C:75:6F:30:17:B3:A8:C4:88:C3:65:3E:91:79
# App Store Server API / Notifications V2 的 JWS x5c 链终止于此根。
_APPLE_ROOT_CA_G3_B64 = (
    "MIICQzCCAcmgAwIBAgIILcX8iNLFS5UwCgYIKoZIzj0EAwMwZzEbMBkGA1UEAwwSQXBwbGUgUm9v"
    "dCBDQSAtIEczMSYwJAYDVQQLDB1BcHBsZSBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTETMBEGA1UE"
    "CgwKQXBwbGUgSW5jLjELMAkGA1UEBhMCVVMwHhcNMTQwNDMwMTgxOTA2WhcNMzkwNDMwMTgxOTA2"
    "WjBnMRswGQYDVQQDDBJBcHBsZSBSb290IENBIC0gRzMxJjAkBgNVBAsMHUFwcGxlIENlcnRpZmlj"
    "YXRpb24gQXV0aG9yaXR5MRMwEQYDVQQKDApBcHBsZSBJbmMuMQswCQYDVQQGEwJVUzB2MBAGByqG"
    "SM49AgEGBSuBBAAiA2IABJjpLz1AcqTtkyJygRMc3RCV8cWjTnHcFBbZDuWmBSp3ZHtfTjjTuxxE"
    "tX/1H7YyYl3J6YRbTzBPEVoA/VhYDKX1DyxNB0cTddqXl5dvMVztK517IDvYuVTZXpmkOlEKMaNC"
    "MEAwHQYDVR0OBBYEFLuw3qFYM4iapIqZ3r6966/ayySrMA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0P"
    "AQH/BAQDAgEGMAoGCCqGSM49BAMDA2gAMGUCMQCD6cHEFl4aXTQY2e3v9GwOAEZLuN+yRhHFD/3m"
    "eoyhpmvOwgPUnPWTxnS4at+qIxUCMG1mihDK1A3UT82NQz60imOlM27jbdoXt2QfyFMm+YhidDkL"
    "F1vLUagM6BgD56KyKA=="
)

# 可信 Apple 根证书 DER 集合（字节级锚定；Apple 换根时需在此追加）
_TRUSTED_APPLE_ROOTS_DER = {base64.b64decode(_APPLE_ROOT_CA_G3_B64)}

# 升级回退处理：同样的收据在沙盒/生产间切换时重试
APPLE_21007 = 21007  # 沙盒收据打到生产端点
APPLE_21008 = 21008  # 生产收据打到沙盒端点

# B7：应用级超时兜底。httpx 自身 timeout=15 在极端网络条件下可能被
# 代理/连接池拖久，叠加 wait_for(10s) 兜底确保 verifyReceipt 不阻塞
# IAP 链路过久，超时返回 IAP_VERIFY_TIMEOUT 而非挂死客户端。
APP_STORE_VERIFY_APP_TIMEOUT = 10  # 秒

# 产品 → 档位映射（与现有 tier 体系 free/pro/pro_plus 对齐）
# 支持 P1-F02 四层体系中的 Pro/Premium 映射到现有档位。
# ⚠️ Life Archive 不在此表：一次性买断走 user_entitlements（架构方案 §九-4），
# 原 `life_archive → pro_plus` 历史别名已删除——买断若映射成订阅档位，
# 用户买一次即永久持有 pro_plus tier，退款/过期链路又只认 subscriptions，
# 权益会永久漂移。买断的判定唯一入口是 _non_consumable_scope。
PRODUCT_TIER_MAP = {
    "pro": "pro",
    "pro_plus": "pro_plus",
    "premium": "pro_plus",
}

# 一次性买断产品（Life Archive 三档，架构方案 §二 M6）：App Store 产品 ID → 权益档位键。
# 档位包含关系 legacy ⊃ all ⊃ 1y（见 entitlement_repository.SCOPE_RANK）。
NON_CONSUMABLE_SCOPES = {
    "life_archive_legacy": "legacy",
    "life_archive_all": "all",
    "life_archive_1y": "1y",
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


def _non_consumable_scope(product_id: str) -> Optional[str]:
    """非消耗品档位键；非买断产品返回 None。

    同样按键长降序匹配：`life_archive_legacy` 必须优先于 `life_archive_all`
    之类的短键，避免跨档误判（误判会发错导出范围）。
    """
    pid = (product_id or "").lower()
    for key, scope in sorted(NON_CONSUMABLE_SCOPES.items(), key=lambda kv: -len(kv[0])):
        if key in pid:
            return scope
    return None


def _collect_transactions(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """收据内全部交易：`latest_receipt_info` + `receipt.in_app` 合并去重。

    两个数组都要取：请求带了 `exclude-old-transactions`，该字段只裁剪自动续期
    订阅历史，一次性买断通常只出现在 `receipt.in_app`；只读前者的话，卸载重装后
    的"恢复购买"会拿不到已购档位。
    """
    rows = list(data.get("latest_receipt_info") or [])
    rows += list((data.get("receipt") or {}).get("in_app") or [])
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for row in rows:
        tx_id = str(row.get("transaction_id") or row.get("original_transaction_id") or "")
        key = tx_id or f"anon-{len(out)}"
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _b64url_decode(data: str) -> bytes:
    """base64url 解码（补齐 padding）。"""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def decode_apple_jws(token: str) -> Dict[str, Any]:
    """解码并验证 Apple JWS（App Store Server Notifications V2 / signedTransactionInfo）。

    验证链：
    1. header.x5c 证书链：每个证书由下一个证书签发（ECDSA/RSA 自适应）
    2. 根证书与 Apple 官方根（Apple Root CA - G3）字节级比对（证书钉扎，防私有 CA 伪造链）
    3. 叶子证书在有效期内
    4. 用叶子证书公钥验签 JWS（ES256）

    任何一步失败都抛 AppException（fail-closed），防止伪造通知升降级订阅。
    """
    try:
        from cryptography import x509
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    except ImportError as exc:  # cryptography 缺失时 fail-closed
        raise AppException(ErrorCode.IAP_VERIFY_FAILED, "服务器缺少 JWS 验签依赖") from exc

    parts = token.split(".")
    if len(parts) != 3:
        raise AppException(ErrorCode.IAP_VERIFY_FAILED, "无效的 JWS 格式")

    try:
        header = json.loads(_b64url_decode(parts[0]))
        chain_b64 = header.get("x5c") or []
        chain_der = [base64.b64decode(c) for c in chain_b64]
        certs = [x509.load_der_x509_certificate(der) for der in chain_der]
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
        # 根证书锚定：x5c 链完全来自请求方，仅验证链内自洽不够——
        # 攻击者可自签 subject 含 "Apple Root CA" 的私有 CA 伪造全链。
        # 必须字节级比对 Apple 官方发布的根证书（证书钉扎）。
        if chain_der[-1] not in _TRUSTED_APPLE_ROOTS_DER:
            raise InvalidSignature("根证书非 Apple 官方根（锚定失败）")
        # 叶子证书有效期
        leaf = certs[0]
        now = datetime.now(timezone.utc)
        if not (leaf.not_valid_before_utc <= now <= leaf.not_valid_after_utc):
            raise InvalidSignature("叶子证书已过期或未生效")
        # JWS 签名
        leaf.public_key().verify(
            _b64url_decode(parts[2]),
            f"{parts[0]}.{parts[1]}".encode(),
            ec.ECDSA(hashes.SHA256()),
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

        三步各留一条交付事件（T1-04）：started / succeeded / failed。
        为什么在 `verify` 这层而不是 `_validate` 里各处埋点：`verify` 是外部唯一入口，
        「付款成功了但后面没出货」的那条链要从这里数起才不会漏掉异常出口——
        `_validate` 抛 AppException（超时、收据跨账号）时根本没有返回体，
        只在 return 分支埋点就会把最该告警的那几种失败数成 0。
        """
        emit(
            DeliveryEvent.IAP_VERIFY_STARTED,
            user_id=user_id,
            source="iap",
            action="verify",
            tier=_map_tier(product_id),
        )
        try:
            result = await self._validate(db, user_id, receipt_data, product_id, is_sandbox)
        except AppException as exc:
            await self._emit_verify_failed(db, user_id, f"app_error_{exc.code}")
            raise
        except Exception as exc:  # noqa: BLE001 - 只为数清失败，异常原样上抛、不改控制流
            await self._emit_verify_failed(db, user_id, f"unexpected_{type(exc).__name__}")
            raise
        if not result.get("valid"):
            # reason 取自 _validate 的机器码，绝不取 message：message 是给人看的文案，
            # 可能带 Apple 状态原文，进了事件就等于把自由文本推到分析平台。
            await self._emit_verify_failed(db, user_id, result.get("reason") or "invalid")
            return result
        user = await user_repository.get_user(db, user_id)
        emit(
            DeliveryEvent.IAP_VERIFY_SUCCEEDED,
            user=user,
            source="iap",
            action="verify",
            result="ok",
            tier=str(result.get("tier") or ""),
        )
        return result

    async def _emit_verify_failed(self, db, user_id: str, reason: str) -> None:
        """收据校验没走通：数一条 failed，同时尽量把 consent 判定拿到。

        用户行可能已经不存在（校验一个不存在账号的收据），那种情况 `emit` 只会写日志、
        回报 `relay=no_user`——不把"没中继"说成"已中继"，与 D-G18 同一口径。
        """
        user = await user_repository.get_user(db, user_id)
        emit(
            DeliveryEvent.IAP_VERIFY_FAILED,
            user=user,
            user_id=user_id,
            source="iap",
            action="verify",
            result="failed",
            reason=reason,
        )

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
                await user_repository.require_user(db, user_id)
                # 买断在开发态同样走 user_entitlements，保持与生产一致的判定链路
                scope = _non_consumable_scope(product_id)
                if scope:
                    row = await entitlement_repository.grant(
                        db,
                        user_id,
                        product_id=product_id,
                        scope=scope,
                        transaction_id=f"dev-{scope}-{user_id}",
                        purchase_date=datetime.now(timezone.utc),
                        original_transaction_id=f"dev-{scope}-{user_id}",
                        environment="Sandbox",
                    )
                    await subscription_repository.create_receipt(
                        db,
                        user_id,
                        product_id=product_id,
                        receipt_data=receipt_data,
                        status="valid",
                        transaction_id=row.transaction_id,
                    )
                    return await self._entitlement_result(db, user_id, [self._entitlement_item(row)])
                result = self._dev_success(user_id, product_id, is_sandbox)
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
            return {
                "valid": False,
                "tier": "free",
                "expires_at": None,
                "is_trial": False,
                # reason 是给交付事件用的机器码；message 给人看，两条通道各留各的口径。
                "reason": "not_configured",
                "message": "IAP 未配置共享密钥",
            }

        url = VERIFY_SANDBOX_URL if is_sandbox else VERIFY_PROD_URL
        payload = {
            "receipt-data": receipt_data,
            "password": settings.app_store_shared_secret,
            "exclude-old-transactions": True,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # B7：应用级超时兜底（10s），httpx 15s 兜底之外再加一层，
                # 防止代理/连接池拖久导致 IAP 链路挂死。
                resp = await asyncio.wait_for(
                    client.post(url, json=payload),
                    timeout=APP_STORE_VERIFY_APP_TIMEOUT,
                )
                data = resp.json()
        except asyncio.TimeoutError as exc:
            logger.error("App Store 校验应用级超时（>%ss）", APP_STORE_VERIFY_APP_TIMEOUT)
            raise AppException(
                ErrorCode.IAP_VERIFY_TIMEOUT,
                "App Store 校验超时，请稍后再试",
            ) from exc
        except httpx.HTTPError as exc:
            logger.error("App Store 校验请求失败: %s", exc)
            return {"valid": False, "reason": "apple_unavailable", "message": "App Store 校验服务不可用"}

        status = data.get("status")

        # 环境切换：沙盒/生产 21007/21008 时换端点重试一次
        if status in (APPLE_21007, APPLE_21008):
            alt_url = VERIFY_PROD_URL if is_sandbox else VERIFY_SANDBOX_URL
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await asyncio.wait_for(
                        client.post(alt_url, json=payload),
                        timeout=APP_STORE_VERIFY_APP_TIMEOUT,
                    )
                    data = resp.json()
                status = data.get("status")
            except asyncio.TimeoutError as exc:
                logger.error(
                    "App Store 校验重试应用级超时（>%ss）", APP_STORE_VERIFY_APP_TIMEOUT
                )
                raise AppException(
                    ErrorCode.IAP_VERIFY_TIMEOUT,
                    "App Store 校验超时，请稍后再试",
                ) from exc
            except httpx.HTTPError:
                return {
                    "valid": False,
                    "reason": "apple_unavailable_on_retry",
                    "message": "App Store 校验服务不可用",
                }

        if status != 0:
            return {
                "valid": False,
                # Apple 的 status 是数字机器码（21007 之类），可以直接进事件；
                # 换成 message 里的自由文本就变成把外部原文往分析平台推。
                "reason": f"apple_status_{status}",
                "message": f"App Store 校验失败 ({status})",
            }

        # 解析收据内全部交易（买断 + 订阅共存）
        transactions = _collect_transactions(data)
        await user_repository.require_user(db, user_id)

        # 一次性买断先发放：与订阅同收据共存时两笔都要处理，
        # 买断只写 user_entitlements，不改 tier、不写 subscriptions（§九-4）。
        entitlements = await self._grant_non_consumables(db, user_id, transactions)
        subscription_tx = [
            t for t in transactions if _non_consumable_scope(t.get("product_id") or "") is None
        ]
        if not subscription_tx:
            if not entitlements:
                return {
                    "valid": False,
                    "reason": "no_transactions",
                    "message": "收据内无有效交易",
                }
            # 收据内只有买断 → 不进入订阅链路（不改 tier），仅留审计收据行
            await subscription_repository.create_receipt(
                db,
                user_id,
                product_id=entitlements[-1]["product_id"],
                receipt_data=receipt_data,
                status="valid",
                transaction_id=entitlements[-1]["transaction_id"],
            )
            return await self._entitlement_result(db, user_id, entitlements)

        # 按过期时间取最新一笔订阅交易（不依赖数组顺序）
        info = max(subscription_tx, key=lambda x: int(x.get("expires_date_ms") or 0))
        expires_ms = info.get("expires_date_ms") or info.get("expires_date")
        expires_at = self._parse_expiry(expires_ms)
        original_tx = info.get("original_transaction_id")
        # 试用标记（PRD P1-F02 §4）：收据 is_trial_period 为字符串 "true"/"false"
        is_trial = str(info.get("is_trial_period") or "").lower() == "true"
        # 档位必须来自收据内真实交易的 product_id，禁止信任客户端入参（防买低档领高档）
        receipt_product_id = info.get("product_id") or product_id
        tier = _map_tier(receipt_product_id)

        # 收据跨账号复用检查：同一原始交易已绑定其他账号时拒绝（防一收据多账号白嫖）
        # 注意：此处检查与下方 upsert 之间存在竞态窗口——两个并发请求可能同时
        # 通过检查后各自尝试写入同一 original_transaction_id。DB 层 partial
        # unique index（ix_subscriptions_original_tx_unique，见迁移
        # 69b435ebef56）兜底，后到者触发 IntegrityError，由下方 SAVEPOINT 捕获后重查。
        if original_tx:
            existing = await subscription_repository.get_by_original_transaction(db, original_tx)
            if existing and existing.user_id != user_id:
                raise AppException(
                    ErrorCode.SUBSCRIPTION_ALREADY_EXISTS,
                    "该收据已绑定其他账号",
                )

        # 写入订阅 + 收据 + 下发权益
        upsert_kwargs = dict(
            user_id=user_id,
            product_id=receipt_product_id,
            tier=tier,
            expires_at=expires_at,
            original_transaction_id=original_tx,
            is_active=True,
            is_trial=is_trial,
        )
        if original_tx:
            # B3：SAVEPOINT 内 upsert，IntegrityError 时回退重查——
            # 已绑定他账号 → 抛 SUBSCRIPTION_ALREADY_EXISTS；
            # 已绑定本账号（同账号并发抢写）→ 幂等再 upsert（命中 existing 分支更新）。
            try:
                async with db.begin_nested():
                    await subscription_repository.upsert(db, **upsert_kwargs)
            except IntegrityError:
                existing = await subscription_repository.get_by_original_transaction(
                    db, original_tx
                )
                if existing and existing.user_id != user_id:
                    raise AppException(
                        ErrorCode.SUBSCRIPTION_ALREADY_EXISTS,
                        "该收据已绑定其他账号",
                    )
                await subscription_repository.upsert(db, **upsert_kwargs)
        else:
            await subscription_repository.upsert(db, **upsert_kwargs)
        await subscription_repository.create_receipt(
            db,
            user_id,
            product_id=receipt_product_id,
            receipt_data=receipt_data,
            status="valid",
            transaction_id=original_tx,
        )
        await user_repository.update_tier(db, user_id, tier)
        # §5.21 D5 触点：试用收据 → 预写第 5 天资产盘点（幂等，失败不阻断购买主流程）
        if is_trial:
            await self._schedule_trial_touchpoint(db, user_id, expires_at)

        return {
            "valid": True,
            "kind": "subscription",
            "tier": tier,
            "expires_at": expires_at,
            "is_trial": is_trial,
            "product_id": receipt_product_id,
            "scope": None,
            "entitlements": entitlements,
            "message": "校验通过",
        }

    async def _schedule_trial_touchpoint(
        self, db, user_id: str, expires_at: Optional[datetime]
    ) -> None:
        """试用 D5 资产盘点触点预写（PRD §5.21 D5，事务边界由调用方控制）。

        试用开始时刻 = expires_at - TRIAL_LENGTH_DAYS；Apple intro offer 一人一次，
        幂等由 journey 仓库 (user, trial_d5, day_no) 查重保证。预写属触达编排而非
        权益本体，失败只告警降级，不阻断购买主流程。
        """
        if expires_at is None:
            return
        try:
            user = await user_repository.get_user(db, user_id)
            if user is None:
                return
            await journey_service.schedule_trial_d5(
                db, user, expires_at - timedelta(days=TRIAL_LENGTH_DAYS)
            )
        except Exception as exc:  # noqa: BLE001 - 触点预写失败不影响权益生效
            logger.warning("试用 D5 触点预写失败 user=%s: %s", user_id, exc)

    async def _grant_non_consumables(
        self, db, user_id: str, transactions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """发放收据内全部 Life Archive 买断权益（恢复购买据此一次补齐三档）。

        幂等：同一 transaction_id 重复校验只更新不新增（重装/重试安全）。
        所有权：同一交易已归属其他账号时拒发——买断没有订阅那样的
        `expires_at` 自然失效，一旦错绑就是永久错发，所以此处 fail-closed。
        """
        granted: List[Dict[str, Any]] = []
        for tx in transactions:
            product_id = tx.get("product_id") or ""
            scope = _non_consumable_scope(product_id)
            if not scope:
                continue
            transaction_id = str(
                tx.get("transaction_id") or tx.get("original_transaction_id") or ""
            )
            if not transaction_id:
                logger.warning("买断收据缺少 transaction_id，跳过 product=%s", product_id)
                continue
            original_tx = tx.get("original_transaction_id") or transaction_id
            existing = await entitlement_repository.get_by_transaction(db, transaction_id)
            if existing is not None and existing.user_id != user_id:
                raise AppException(
                    ErrorCode.SUBSCRIPTION_ALREADY_EXISTS, "该收据已绑定其他账号"
                )
            purchase_date = self._parse_expiry(
                tx.get("purchase_date_ms") or tx.get("purchase_date")
            ) or datetime.now(timezone.utc)
            grant_kwargs = dict(
                user_id=user_id,
                product_id=product_id,
                scope=scope,
                transaction_id=transaction_id,
                purchase_date=purchase_date,
                original_transaction_id=original_tx,
                environment=tx.get("environment"),
            )
            try:
                # transaction_id 上有唯一索引（迁移 f1a3b5c7d9e2），并发双写时
                # 后到者触发 IntegrityError，SAVEPOINT 内回滚后重查复用既有行。
                async with db.begin_nested():
                    row = await entitlement_repository.grant(db, **grant_kwargs)
            except IntegrityError:
                row = await entitlement_repository.get_by_transaction(db, transaction_id)
                if row is None or row.user_id != user_id:
                    raise AppException(
                        ErrorCode.SUBSCRIPTION_ALREADY_EXISTS, "该收据已绑定其他账号"
                    )
            granted.append(self._entitlement_item(row))
        return granted

    def _entitlement_item(self, row) -> Dict[str, Any]:
        return {
            "product_id": row.product_id,
            "scope": row.scope,
            "transaction_id": row.transaction_id,
            "purchase_date": row.purchase_date,
            "environment": row.environment,
        }

    async def _entitlement_result(
        self, db, user_id: str, entitlements: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """买断发放结果。

        `tier` 恒为用户当前订阅档位：买断不产生订阅权益（架构方案 §九-4 角色切分，
        买断只解锁 Life Archive 导出），若在此返回 pro_plus 会让前端误显示订阅已生效。
        """
        user = await user_repository.require_user(db, user_id)
        best = await entitlement_repository.best_scope(db, user_id)
        return {
            "valid": True,
            "kind": "entitlement",
            "tier": user.tier,
            "expires_at": None,
            "is_trial": False,
            "product_id": entitlements[-1]["product_id"] if entitlements else None,
            "scope": best,
            "entitlements": entitlements,
            "message": "买断权益已发放",
        }

    async def entitlements(self, db, user_id: str) -> Dict[str, Any]:
        """查询 Life Archive 买断权益（Paywall 展示已购档位 + 导出门判定）。"""
        await user_repository.require_user(db, user_id)
        rows = await entitlement_repository.list_active(db, user_id)
        return {
            "entitlements": [self._entitlement_item(r) for r in rows],
            "best_scope": await entitlement_repository.best_scope(db, user_id),
            "owned_scopes": [r.scope for r in rows],
        }

    async def get_status(self, db, user_id: str) -> Dict[str, Any]:
        """查询订阅状态（从 subscriptions 表读取真实数据）。"""
        await user_repository.require_user(db, user_id)
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
        """处理退款/撤销：立即回收权益（订阅降级 free，买断置为已撤销）。

        买断与订阅共用 original_transaction_id 口径，但落库分表：只清
        subscriptions 的话，退款用户会永久保留 Life Archive 导出权（M6 要求
        退款即回收）。先撤买断再查订阅，避免订阅缺失时提前 return 漏撤权益。
        """
        now = datetime.now(timezone.utc)
        revoked = await entitlement_repository.revoke_by_original_transaction(
            db, original_transaction_id, now
        )
        if revoked:
            logger.info("退款回收买断 %d 笔 tx=%s", revoked, original_transaction_id)
        sub = await subscription_repository.get_by_original_transaction(db, original_transaction_id)
        if not sub:
            if not revoked:
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
        elif notification_type == "CONSUMPTION_REQUEST":
            # Apple 就消耗型/买断交易询问消耗信息。Life Archive 是一次性交付的
            # 档案导出，无「消耗进度」可报，按合规要求记录后直接确认（不下发/不回收权益）。
            logger.info(
                "收到 CONSUMPTION_REQUEST（买断无消耗口径）tx=%s", original_tx
            )
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
            "kind": "subscription",
            "tier": tier,
            "expires_at": expires_at,
            "is_trial": False,
            "product_id": product_id,
            "scope": None,
            "entitlements": [],
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
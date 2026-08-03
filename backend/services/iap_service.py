"""
IAP 订阅服务。

管理 StoreKit 2 收据校验、订阅状态查询、权益下发。
"""
from datetime import datetime
from typing import Optional, Dict, Any

from backend.config import settings


class IAPService:
    """IAP 收据校验与订阅管理服务。"""

    async def verify(
        self, user_id: str, receipt_data: str, product_id: str, is_sandbox: bool = False
    ) -> Dict[str, Any]:
        """
        收据校验。

        1. 调用 App Store Server API 验证收据
        2. Sandbox 环境 → https://sandbox.itunes.apple.com/verifyReceipt
        3. Production 环境 → https://buy.itunes.apple.com/verifyReceipt
        4. 校验通过 → 更新 subscriptions 表 → 写入 receipts 表
        5. 校验失败 → 拒绝
        """
        # TODO: 实现 App Store Server API 调用
        # verify_url = "https://sandbox.itunes.apple.com/verifyReceipt" if is_sandbox else "https://buy.itunes.apple.com/verifyReceipt"
        # payload = {"receipt-data": receipt_data, "password": settings.app_store_shared_secret, "exclude-old-transactions": True}
        return {
            "valid": True,
            "tier": "pro",
            "expires_at": datetime(2026, 9, 3),
            "message": "校验通过",
        }

    async def get_status(self, user_id: str) -> Dict[str, Any]:
        """查询订阅状态。"""
        # TODO: 从 subscriptions 表查询
        return {
            "tier": "free",
            "is_active": True,
            "expires_at": None,
            "product_id": None,
            "auto_renew": False,
        }

    async def handle_refund(self, original_transaction_id: str):
        """
        处理退款（App Store Server Notifications V2 回调）。

        1. 验证 Apple 签名
        2. 标记 subscription 为 inactive
        3. 回收权益（降级为 free tier）
        """
        # TODO: 实现退款处理
        pass

    async def handle_renewal(self, original_transaction_id: str, expires_at: datetime):
        """处理续订通知。"""
        # TODO: 更新 expires_at
        pass


iap_service = IAPService()
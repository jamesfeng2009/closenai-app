"""
IAP 订阅路由 (P1-F02)。

端点：
    POST /api/v1/iap/verify             — 收据校验
    GET  /api/v1/iap/status             — 订阅状态
    POST /api/v1/iap/notifications      — App Store Server Notifications V2
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.errors import AppException, ErrorCode
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import (
    ReceiptVerifyRequest, ReceiptVerifyResponse, SubscriptionStatusResponse,
)
from backend.services.iap_service import IAPService, iap_service

router = APIRouter()


def get_iap_service() -> IAPService:
    return iap_service


@router.post("/iap/verify")
async def verify_receipt(
    request: ReceiptVerifyRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: IAPService = Depends(get_iap_service),
):
    """
    收据校验。

    客户端购买/恢复购买后，将收据发回后端校验。
    后端调用 App Store Server API 验证，通过后更新订阅状态。
    伪造收据直接拒绝，退款后回收权益。
    """
    result = await service.verify(
        db, user_id, request.receipt_data, request.product_id, request.is_sandbox
    )
    if not result.get("valid"):
        raise AppException(ErrorCode.IAP_VERIFY_FAILED, result.get("message", "收据无效"))
    return success(ReceiptVerifyResponse(**result))


@router.get("/iap/status")
async def subscription_status(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    service: IAPService = Depends(get_iap_service),
):
    """查询当前订阅状态。"""
    status = await service.get_status(db, user_id)
    return success(SubscriptionStatusResponse(**status))


@router.post("/iap/notifications")
async def app_store_notifications(
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: IAPService = Depends(get_iap_service),
):
    """
    App Store Server Notifications V2 回调。

    由 Apple 服务器实时推送：续订 / 退款 / 过期 / 自动续订状态变化。
    处理完成后立即回收或下发权益。
    """
    payload = await request.json()
    notification_type = await service.handle_notification(db, payload)
    return success({"status": "ok", "notification_type": notification_type})
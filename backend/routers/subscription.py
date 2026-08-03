"""
IAP 订阅路由。

端点：
    POST /api/v1/iap/verify  — 收据校验
    GET  /api/v1/iap/status  — 订阅状态
"""
from fastapi import APIRouter, Depends, HTTPException

from backend.models.schemas import (
    ReceiptVerifyRequest, ReceiptVerifyResponse, SubscriptionStatusResponse,
)
from backend.services.iap_service import IAPService, iap_service

router = APIRouter()


def get_iap_service() -> IAPService:
    return iap_service


@router.post("/iap/verify", response_model=ReceiptVerifyResponse)
async def verify_receipt(
    request: ReceiptVerifyRequest,
    service: IAPService = Depends(get_iap_service),
):
    """
    收据校验。

    客户端购买/恢复购买后，将收据发回后端校验。
    后端调用 App Store Server API 验证，通过后更新订阅状态。
    伪造收据直接拒绝，退款后回收权益。
    """
    user_id = "demo-user"
    result = await service.verify(user_id, request.receipt_data, request.product_id, request.is_sandbox)
    if not result.get("valid"):
        raise HTTPException(status_code=400, detail=result.get("message", "收据无效"))
    return ReceiptVerifyResponse(**result)


@router.get("/iap/status", response_model=SubscriptionStatusResponse)
async def subscription_status(
    service: IAPService = Depends(get_iap_service),
):
    """查询当前订阅状态。"""
    user_id = "demo-user"
    status = await service.get_status(user_id)
    return SubscriptionStatusResponse(**status)
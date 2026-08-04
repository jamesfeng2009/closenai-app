"""
推送服务实现（P1-F01）。

自建 APNs / FCM 推送，向用户全部设备推送通知。

发送链路：
    send_to_user → 查询用户全部 push_tokens → 按平台分发 APNs / FCM
    → 成功返回，失败安全降级（记录日志，不抛异常）

推送内容即消息本身（人格化文案），点击进入对话。
"""
import base64
import json
import logging
import time
from typing import Dict, List, Optional

import httpx

from backend.config import settings
from backend.repositories import push_token_repository

logger = logging.getLogger("closenai.push")

# APNs 端点（生产 / 沙盒）
APNS_PROD_URL = "https://api.push.apple.com/3/device"
APNS_SANDBOX_URL = "https://api.sandbox.push.apple.com/3/device"

# FCM legacy 端点（server key 方式）
FCM_LEGACY_URL = "https://fcm.googleapis.com/fcm/send"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign_es256(private_key_pem: str, challenge: str) -> str:
    """用 ES256 对 challenge 签名，返回 JWT 签名段（raw r||s）。"""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    der = key.sign(challenge.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return _b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def _apns_token() -> str:
    """生成 APNs 令牌认证 JWT（ES256）。"""
    header = {"alg": "ES256", "kid": settings.apns_key_id}
    payload = {"iss": settings.apns_team_id, "iat": int(time.time())}
    signing_input = (
        _b64url(json.dumps(header).encode()) + "." + _b64url(json.dumps(payload).encode())
    )
    sig = _sign_es256(settings.apns_private_key, signing_input)
    return signing_input + "." + sig


class PushService:
    """自建推送服务。"""

    async def send_to_user(
        self,
        db,
        user_id: str,
        title: str,
        body: str,
        data: Optional[Dict] = None,
    ) -> Dict[str, int]:
        """向用户全部设备推送。返回 {sent: n, failed: n}。"""
        tokens = await push_token_repository.list_for_user(db, user_id)
        if not tokens:
            return {"sent": 0, "failed": 0}
        sent = 0
        failed = 0
        for tok in tokens:
            ok = False
            if tok.platform == "apns":
                ok = await self.send_apns(tok.device_token, title, body, data)
            elif tok.platform == "fcm":
                ok = await self.send_fcm(tok.device_token, title, body, data)
            else:
                continue
            if ok:
                sent += 1
            else:
                failed += 1
        return {"sent": sent, "failed": failed}

    async def send_apns(
        self,
        device_token: str,
        title: str,
        body: str,
        data: Optional[Dict] = None,
    ) -> bool:
        """发送 APNs 通知（令牌认证）。"""
        if not (settings.apns_team_id and settings.apns_key_id and settings.apns_private_key):
            logger.warning("APNs 未配置，跳过推送")
            return False
        authority = APNS_SANDBOX_URL if settings.debug else APNS_PROD_URL
        url = f"{authority}/{device_token}"
        payload = {
            "aps": {
                "alert": {"title": title, "body": body},
                "sound": "default",
                "content-available": 1,
            }
        }
        if data:
            payload["data"] = data
        headers = {
            "Authorization": f"Bearer {_apns_token()}",
            "apns-topic": settings.apns_topic or "com.closenai.app",
            "apns-push-type": "alert",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 410:
                logger.info("APNs 设备失活 token=%s", device_token[:8])
            return 200 <= resp.status_code < 300
        except httpx.HTTPError as exc:
            logger.warning("APNs 发送失败: %s", exc)
            return False

    async def send_fcm(
        self,
        device_token: str,
        title: str,
        body: str,
        data: Optional[Dict] = None,
    ) -> bool:
        """发送 FCM 通知（legacy server key 或 HTTP v1）。"""
        if not settings.fcm_server_key:
            logger.warning("FCM 未配置，跳过推送")
            return False
        message = {
            "to": device_token,
            "notification": {"title": title, "body": body},
            "priority": "high",
        }
        if data:
            message["data"] = {str(k): str(v) for k, v in data.items()}
        headers = {
            "Authorization": f"key={settings.fcm_server_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(FCM_LEGACY_URL, json=message, headers=headers)
            return 200 <= resp.status_code < 300
        except httpx.HTTPError as exc:
            logger.warning("FCM 发送失败: %s", exc)
            return False


push_service = PushService()
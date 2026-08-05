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

# FCM legacy 端点（server key 方式，已废弃：2024-07 起新项目不可用，仅作回退）
FCM_LEGACY_URL = "https://fcm.googleapis.com/fcm/send"

# FCM HTTP v1 端点模板 / OAuth2 token 端点 / 所需 scope
FCM_V1_URL_TEMPLATE = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
FCM_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
FCM_V1_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

# service account / access_token 进程内缓存（access_token 有效期 1h，提前 5 分钟刷新）
_fcm_sa_cache: Optional[Dict] = None
_fcm_token_cache: Dict[str, object] = {"token": None, "expires_at": 0.0}
# legacy 废弃告警只记一次，避免每次推送刷日志
_legacy_deprecation_warned = False


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


def _sign_rs256(private_key_pem: str, data: str) -> str:
    """用 RSA SHA256 对 data 签名，返回 JWT 签名段（base64url）。"""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    return _b64url(key.sign(data.encode(), padding.PKCS1v15(), hashes.SHA256()))


def _load_fcm_service_account() -> Optional[Dict]:
    """加载 FCM service account JSON（配置内容为 JSON 串或 JSON 文件路径）。

    进程内缓存一次；配置缺失/解析失败返回 None（回退 legacy 或跳过）。
    """
    global _fcm_sa_cache
    if _fcm_sa_cache is not None:
        return _fcm_sa_cache
    raw = (settings.fcm_service_account_json or "").strip()
    if not raw:
        return None
    sa: Optional[Dict] = None
    try:
        sa = json.loads(raw)
    except json.JSONDecodeError:
        # 非 JSON 串：按文件路径处理（私钥不宜直接进 env 时的替代方式）
        try:
            with open(raw, encoding="utf-8") as f:
                sa = json.load(f)
        except OSError as exc:
            logger.warning("FCM service account 加载失败（既非 JSON 也非可读文件）: %s", exc)
    if sa and sa.get("client_email") and sa.get("private_key") and sa.get("project_id"):
        _fcm_sa_cache = sa
        return sa
    logger.warning("FCM service account 缺少 client_email/private_key/project_id，忽略")
    return None


async def _fcm_access_token(sa: Dict) -> Optional[str]:
    """service account JWT（RS256）换 OAuth2 access_token（进程内缓存至过期前 5 分钟）。"""
    now = time.time()
    cached = _fcm_token_cache
    if cached.get("token") and now < float(cached.get("expires_at", 0)) - 300:
        return str(cached["token"])
    iat = int(now)
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": sa["client_email"],
        "scope": FCM_V1_SCOPE,
        "aud": FCM_OAUTH_TOKEN_URL,
        "iat": iat,
        "exp": iat + 3600,
    }
    signing_input = (
        _b64url(json.dumps(header).encode()) + "." + _b64url(json.dumps(claims).encode())
    )
    assertion = signing_input + "." + _sign_rs256(sa["private_key"], signing_input)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                FCM_OAUTH_TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
        if not 200 <= resp.status_code < 300:
            logger.warning("FCM access_token 换取失败 status=%s", resp.status_code)
            return None
        payload = resp.json()
        token = payload.get("access_token")
        if not token:
            return None
        _fcm_token_cache["token"] = token
        _fcm_token_cache["expires_at"] = iat + int(payload.get("expires_in", 3600))
        return token
    except Exception as exc:  # noqa: BLE001 - 签名/网络异常安全降级
        logger.warning("FCM access_token 换取异常: %s", exc)
        return None


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
                ok = await self.send_apns(
                    tok.device_token, title, body, data, db=db, token_id=tok.id
                )
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
        db=None,
        token_id: Optional[str] = None,
    ) -> bool:
        """发送 APNs 通知（令牌认证）。410 失活时清理对应 push_token 记录。"""
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
        try:
            headers = {
                "Authorization": f"Bearer {_apns_token()}",
                "apns-topic": settings.apns_topic or "com.closenai.app",
                "apns-push-type": "alert",
            }
            # APNs provider API 强制 HTTP/2（需 httpx[http2] / h2）
            async with httpx.AsyncClient(timeout=10, http2=True) as client:
                resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 410:
                logger.info("APNs 设备失活 token=%s", device_token[:8])
                # 清理失效 token，避免后续持续向已卸载设备推送
                if db is not None and token_id is not None:
                    try:
                        await push_token_repository.delete(db, token_id)
                    except Exception as exc:  # noqa: BLE001 - 清理失败不阻断推送主流程
                        logger.warning("失效 APNs token 清理失败: %s", exc)
            return 200 <= resp.status_code < 300
        except Exception as exc:  # noqa: BLE001 - 签名/网络/协议异常一律安全降级，不阻断业务
            logger.warning("APNs 发送失败: %s", exc)
            return False

    async def send_fcm(
        self,
        device_token: str,
        title: str,
        body: str,
        data: Optional[Dict] = None,
    ) -> bool:
        """发送 FCM 通知：优先 HTTP v1（service account），未配置时回退 legacy（已废弃）。"""
        sa = _load_fcm_service_account()
        if sa:
            return await self._send_fcm_v1(sa, device_token, title, body, data)
        return await self._send_fcm_legacy(device_token, title, body, data)

    async def _send_fcm_v1(
        self,
        sa: Dict,
        device_token: str,
        title: str,
        body: str,
        data: Optional[Dict] = None,
    ) -> bool:
        """FCM HTTP v1：OAuth2 access_token 鉴权，POST projects/{id}/messages:send。"""
        access_token = await _fcm_access_token(sa)
        if not access_token:
            return False
        message: Dict = {
            "message": {
                "token": device_token,
                "notification": {"title": title, "body": body},
                "android": {"priority": "HIGH"},
            }
        }
        if data:
            message["message"]["data"] = {str(k): str(v) for k, v in data.items()}
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        url = FCM_V1_URL_TEMPLATE.format(project_id=sa["project_id"])
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=message, headers=headers)
            if not 200 <= resp.status_code < 300:
                logger.warning(
                    "FCM v1 发送失败 status=%s body=%s", resp.status_code, resp.text[:200]
                )
                return False
            return True
        except Exception as exc:  # noqa: BLE001 - 网络/协议异常安全降级，不阻断业务
            logger.warning("FCM v1 发送异常: %s", exc)
            return False

    async def _send_fcm_legacy(
        self,
        device_token: str,
        title: str,
        body: str,
        data: Optional[Dict] = None,
    ) -> bool:
        """FCM legacy server key 发送（已废弃，仅作 HTTP v1 未配置时的回退）。"""
        global _legacy_deprecation_warned
        if not settings.fcm_server_key:
            logger.warning("FCM 未配置，跳过推送")
            return False
        if not _legacy_deprecation_warned:
            _legacy_deprecation_warned = True
            logger.warning(
                "FCM legacy server key 已废弃（Google 2024-07 起停用），"
                "请配置 fcm_service_account_json 迁移至 HTTP v1"
            )
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
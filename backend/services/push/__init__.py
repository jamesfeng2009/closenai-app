"""
推送服务（P1-F01）。

自建 APNs（iOS）与 FCM（Android）推送，实现"通知本身就是内容"的人格化推送。
- APNs：令牌认证（ES256 JWT，.p8 私钥）
- FCM：legacy server key 或 HTTP v1

未配置密钥时安全降级（返回 sent=False），不阻塞主流程。
"""
from backend.services.push.service import PushService, push_service

__all__ = ["PushService", "push_service"]
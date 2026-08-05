"""
推送服务（P1-F01）。

自建 APNs（iOS）与 FCM（Android）推送，实现"通知本身就是内容"的人格化推送。
- APNs：令牌认证（ES256 JWT，.p8 私钥）
- FCM：HTTP v1（service account OAuth2）；legacy server key 已废弃，仅作未配置时回退
- 文案：push_copywriter 按场景模板池 + Pro lite LLM 生成（失败回退模板）

未配置密钥时安全降级（返回 sent=False），不阻塞主流程。
"""
from backend.services.push.copy import PushCopywriter, push_copywriter
from backend.services.push.service import PushService, push_service

__all__ = ["PushCopywriter", "push_copywriter", "PushService", "push_service"]

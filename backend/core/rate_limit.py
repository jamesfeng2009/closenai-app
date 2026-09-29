"""
公开端点的进程内固定窗口限流（#38 分享归因链路）。

职责边界：只做「防批量探测」，不做配额计费——配额一律走 services/quota_service.py。
被限的对象是无鉴权、无副作用的公开端点，代价是「一个 IP 打太多次」，不是「用户权益」。

两个刻意的取舍：
1. **单进程内存计数**。多 worker 部署时实际放行量 ≈ limit × worker 数。不引 Redis
   计数器，是为了不给一个反滥用组件引入新的故障面（Redis 不可达时限流该放行还是
   该全拒？两个答案都有人不同意，而现在的组件根本没有这个状态）。
2. **窗口不滑动**。固定窗口的边界效应是最多在窗口切换时放行 2×limit，
   对「慢慢跑字典」这类慢速探测没有影响，实现却小一个数量级。
"""
import time
from typing import Callable, Optional

from fastapi import Request

from backend.core.logging import get_logger

logger = get_logger("closenai.rate_limit")

# 桶数上限：防止有人用海量源地址把计数器变成内存泄漏。超限时清掉已过期的桶，
# 仍超限则整体重置——代价是攻击者可能顺带重置别人的窗口，而这只是让限流短暂失效。
MAX_BUCKETS = 10_000


class FixedWindowLimiter:
    """按 key 计数的固定窗口限流器（非线程安全；FastAPI 单事件循环内使用）。"""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        # key → (窗口起点, 已放行次数)
        self._buckets: dict[str, tuple[float, int]] = {}

    def hit(
        self, key: str, limit: int, window_seconds: int, *, now: Optional[float] = None
    ) -> bool:
        """记一次访问：窗口内未超 limit 返回 True（放行），超限返回 False。"""
        now = self._clock() if now is None else now
        start, count = self._buckets.get(key, (now, 0))
        if now - start >= window_seconds:
            start, count = now, 0
        if count >= limit:
            return False
        self._buckets[key] = (start, count + 1)
        self._prune(now, window_seconds)
        return True

    def reset(self) -> None:
        """清空计数（测试与部署内手动复位用）。"""
        self._buckets.clear()

    def _prune(self, now: float, window_seconds: int) -> None:
        if len(self._buckets) <= MAX_BUCKETS:
            return
        for key, (start, _) in list(self._buckets.items()):
            if now - start >= window_seconds:
                self._buckets.pop(key, None)
        if len(self._buckets) > MAX_BUCKETS:
            logger.warning("限流计数器桶数仍超上限 %s，整体重置", MAX_BUCKETS)
            self._buckets.clear()


def client_ip(request: Request, *, trust_forwarded_for: bool = False) -> str:
    """取用于限流的客户端地址。

    优先级 CF-Connecting-IP → （显式信任代理时）X-Forwarded-For 首跳 → 直连对端。
    XFF 是调用方可控的头：不开关就采信，等于让绕过限流只需要改一个请求头。
    """
    cf = request.headers.get("cf-connecting-ip", "").strip()
    if cf:
        return cf
    if trust_forwarded_for:
        xff = request.headers.get("x-forwarded-for", "")
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"

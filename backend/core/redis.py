"""
Upstash Redis REST 客户端（验证码存储 + 频率限制）。

Upstash Redis 提供 HTTP REST API，无需 redis-py 库。
未配置 redis_url / redis_token 时回退内存 dict（仅开发用，重启丢失，非原子）。

REST 格式（Bearer token 鉴权）：
- GET  {base}/get/{key}                     → {"result": <value|null>}
- POST {base}/set/{key}/{value}?EX={ttl}    → {"result": "OK"}
- POST {base}/del/{key}                     → {"result": <int>}
- POST {base}  body [["INCR",k],["EXPIRE",k,ttl]]  → [{"result":..},..]  (pipeline)

故障语义（Upstash 抖动不应把登录/发码全链路打成裸 500）：
- 限流计数类操作（fail_open=True）：网络/HTTP 异常记 warning 放行，
  限流短暂降级为不拦截，可用性优先
- 验证码写入/读取/删除类操作（默认 fail_open=False）：异常抛
  AppException(INTERNAL_ERROR)（双语文案），而非裸 httpx 异常；
  验证码「校验」因此 fail-closed——Redis 故障时读取直接抛错，
  绝不会误判为验证通过
"""
import time
from typing import Any
from urllib.parse import quote

import httpx

from backend.config import settings
from backend.core.errors import AppException, ErrorCode
from backend.core.logging import get_logger

logger = get_logger("closenai.redis")

# 内存回退（仅开发环境未配置 Redis 时）：key → (value_str, expire_at|0)
_mem_store: dict[str, tuple[str, float]] = {}


def _enabled() -> bool:
    return bool(settings.redis_url and settings.redis_token)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.redis_token}"}


def _on_http_error(op: str, key: str, exc: httpx.HTTPError, fail_open: bool, fallback: Any) -> Any:
    """Redis HTTP 故障统一出口：fail-open 记告警返回兜底值，否则抛双语 500。"""
    if fail_open:
        logger.warning("redis %s 失败，fail-open 放行 key=%s: %s", op, key, exc)
        return fallback
    raise AppException(
        ErrorCode.INTERNAL_ERROR,
        "服务暂时不可用，请稍后重试 / Service temporarily unavailable",
    ) from exc


async def get(key: str, *, fail_open: bool = False) -> str | None:
    """取值；过期或不存在返回 None。fail_open 时 Redis 故障也返回 None（放行）。"""
    if not _enabled():
        entry = _mem_store.get(key)
        if not entry:
            return None
        value, expire_at = entry
        if expire_at and time.time() > expire_at:
            _mem_store.pop(key, None)
            return None
        return value
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{settings.redis_url}/get/{quote(key, safe='')}", headers=_headers()
            )
            resp.raise_for_status()
            result = resp.json().get("result")
            return result if result is not None else None
    except httpx.HTTPError as exc:
        return _on_http_error("get", key, exc, fail_open, None)


async def setex(key: str, ttl_seconds: int, value: str, *, fail_open: bool = False) -> None:
    """设值并带 TTL（秒）。"""
    if not _enabled():
        _mem_store[key] = (value, time.time() + ttl_seconds if ttl_seconds > 0 else 0)
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{settings.redis_url}/set/{quote(key, safe='')}/{quote(value, safe='')}",
                headers=_headers(),
                params={"EX": ttl_seconds},
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        _on_http_error("setex", key, exc, fail_open, None)


async def delete(key: str, *, fail_open: bool = False) -> None:
    """删除 key。"""
    if not _enabled():
        _mem_store.pop(key, None)
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{settings.redis_url}/del/{quote(key, safe='')}", headers=_headers()
            )
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        _on_http_error("delete", key, exc, fail_open, None)


async def incr_with_ttl(key: str, ttl_seconds: int, *, fail_open: bool = False) -> int:
    """原子自增并设置 TTL（pipeline：INCR + EXPIRE）。返回自增后的值。

    注意：每次自增都会续期 TTL（滑动窗口语义）；需要固定窗口请用 incr_fixed_window。
    """
    if not _enabled():
        now = time.time()
        entry = _mem_store.get(key)
        if entry and entry[1] and now > entry[1]:
            entry = None  # 已过期：重新起窗
        cur = int(entry[0]) + 1 if entry and entry[0] else 1
        _mem_store[key] = (str(cur), now + ttl_seconds)
        return cur
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                settings.redis_url,
                headers=_headers(),
                json=[["INCR", key], ["EXPIRE", key, str(ttl_seconds)]],
            )
            resp.raise_for_status()
            data = resp.json()
            # pipeline 返回 [{"result": <int>}, {"result": 1}]
            return int(data[0]["result"])
    except httpx.HTTPError as exc:
        return _on_http_error("incr_with_ttl", key, exc, fail_open, 0)


async def incr_fixed_window(key: str, ttl_seconds: int, *, fail_open: bool = False) -> int:
    """固定窗口自增：首次写入锚定窗口（SET key 0 EX ttl NX）并设置 TTL，
    窗口内 INCR 不续期，窗口到期 key 自动过期解锁。返回自增后的值。

    用于密码登录失败计数等场景：滑动窗口（持续失败持续续期）会让攻击者
    对受害者账号持续提交错误凭证造成永久锁定（DoS），固定窗口到点必解锁。
    """
    if not _enabled():
        now = time.time()
        entry = _mem_store.get(key)
        if entry and (not entry[1] or entry[1] > now):
            # 窗口内：仅自增，不续期
            cur = int(entry[0]) + 1
            _mem_store[key] = (str(cur), entry[1])
            return cur
        # 首次失败 / 窗口已过期：锚定新窗口
        _mem_store[key] = ("1", now + ttl_seconds)
        return 1
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                settings.redis_url,
                headers=_headers(),
                json=[["SET", key, "0", "EX", str(ttl_seconds), "NX"], ["INCR", key]],
            )
            resp.raise_for_status()
            data = resp.json()
            # pipeline 返回 [{"result": "OK"|null}, {"result": <int>}]
            return int(data[1]["result"])
    except httpx.HTTPError as exc:
        return _on_http_error("incr_fixed_window", key, exc, fail_open, 0)

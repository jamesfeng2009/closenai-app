"""
Upstash Redis REST 客户端（验证码存储 + 频率限制）。

Upstash Redis 提供 HTTP REST API，无需 redis-py 库。
未配置 redis_url / redis_token 时回退内存 dict（仅开发用，重启丢失，非原子）。

REST 格式（Bearer token 鉴权）：
- GET  {base}/get/{key}                     → {"result": <value|null>}
- POST {base}/set/{key}/{value}?EX={ttl}    → {"result": "OK"}
- POST {base}/del/{key}                     → {"result": <int>}
- POST {base}  body [["INCR",k],["EXPIRE",k,ttl]]  → [{"result":..},..]  (pipeline)
"""
import time
from urllib.parse import quote

import httpx

from backend.config import settings
from backend.core.logging import get_logger

logger = get_logger("closenai.redis")

# 内存回退（仅开发环境未配置 Redis 时）：key → (value_str, expire_at|0)
_mem_store: dict[str, tuple[str, float]] = {}


def _enabled() -> bool:
    return bool(settings.redis_url and settings.redis_token)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.redis_token}"}


async def get(key: str) -> str | None:
    """取值；过期或不存在返回 None。"""
    if not _enabled():
        entry = _mem_store.get(key)
        if not entry:
            return None
        value, expire_at = entry
        if expire_at and time.time() > expire_at:
            _mem_store.pop(key, None)
            return None
        return value
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(
            f"{settings.redis_url}/get/{quote(key, safe='')}", headers=_headers()
        )
        resp.raise_for_status()
        result = resp.json().get("result")
        return result if result is not None else None


async def setex(key: str, ttl_seconds: int, value: str) -> None:
    """设值并带 TTL（秒）。"""
    if not _enabled():
        _mem_store[key] = (value, time.time() + ttl_seconds if ttl_seconds > 0 else 0)
        return
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.post(
            f"{settings.redis_url}/set/{quote(key, safe='')}/{quote(value, safe='')}",
            headers=_headers(),
            params={"EX": ttl_seconds},
        )
        resp.raise_for_status()


async def delete(key: str) -> None:
    """删除 key。"""
    if not _enabled():
        _mem_store.pop(key, None)
        return
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.post(
            f"{settings.redis_url}/del/{quote(key, safe='')}", headers=_headers()
        )
        resp.raise_for_status()


async def incr_with_ttl(key: str, ttl_seconds: int) -> int:
    """原子自增并设置 TTL（pipeline：INCR + EXPIRE）。返回自增后的值。"""
    if not _enabled():
        entry = _mem_store.get(key)
        cur = int(entry[0]) + 1 if entry and entry[0] else 1
        _mem_store[key] = (str(cur), time.time() + ttl_seconds)
        return cur
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

"""缓存层 —— Redis 实现（懒连接单例）。

供限流（滑动窗口）、短信验证码存储、会话等共用。
结果缓存（同 query+params）后续按 enable_cache 开关接入。
"""

import logging
from typing import Any

import redis.asyncio as aioredis

from ..config import get_settings

logger = logging.getLogger(__name__)


class Cache:
    """缓存接口。生产实现见 RedisCache；保留基类便于测试 mock。"""

    async def get(self, key: str) -> Any:
        return None

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        pass

    async def delete(self, key: str) -> None:
        pass

    async def exists(self, key: str) -> bool:
        return False

    @property
    def client(self) -> Any:
        """底层 redis.asyncio.Redis 客户端（限流滑动窗口等需要原生命令时用）。基类返回 None。"""
        return None


class RedisCache(Cache):
    """redis.asyncio 实装。懒连接，首次 get/set 时建池。"""

    def __init__(self, url: str) -> None:
        self._url = url
        self._redis: aioredis.Redis | None = None

    async def _client(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(
                self._url, decode_responses=True
            )
            logger.info("Redis 连接已建立: %s", self._url)
        return self._redis

    @property
    def client(self) -> aioredis.Redis | None:
        """底层 Redis 客户端（未连接时为 None，需先 await get/set 触发连接）。"""
        return self._redis

    async def get(self, key: str) -> Any:
        r = await self._client()
        return await r.get(key)

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        r = await self._client()
        await r.set(key, value, ex=ttl)

    async def delete(self, key: str) -> None:
        r = await self._client()
        await r.delete(key)

    async def exists(self, key: str) -> bool:
        r = await self._client()
        return bool(await r.exists(key))

    async def incr(self, key: str, ttl: int = 60) -> int:
        """自增并设过期（用于限流计数、短信频控）。"""
        r = await self._client()
        async with r.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, ttl)
            n, _ = await pipe.execute()
        return n

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


_cache: RedisCache | None = None


def get_cache() -> RedisCache:
    """全局单例 RedisCache。"""
    global _cache
    if _cache is None:
        _cache = RedisCache(get_settings().redis_url)
    return _cache

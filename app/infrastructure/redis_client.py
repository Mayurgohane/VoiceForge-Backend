from __future__ import annotations

import json
import time
from typing import Any

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class InMemoryKV:
    """Dev fallback when Redis is unavailable."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._expiry: dict[str, float] = {}

    def _alive(self, key: str) -> bool:
        exp = self._expiry.get(key)
        if exp is not None and exp < time.time():
            self._store.pop(key, None)
            self._expiry.pop(key, None)
            return False
        return key in self._store

    async def get(self, key: str) -> str | None:
        if not self._alive(key):
            return None
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value
        if ex is not None:
            self._expiry[key] = time.time() + ex
        else:
            self._expiry.pop(key, None)

    async def setnx(self, key: str, value: str, ex: int | None = None) -> bool:
        if self._alive(key):
            return False
        await self.set(key, value, ex=ex)
        return True

    async def getdel(self, key: str) -> str | None:
        value = await self.get(key)
        if value is not None:
            await self.delete(key)
        return value

    async def incr(self, key: str, ex: int | None = None) -> int:
        current = 0
        raw = await self.get(key)
        if raw is not None:
            try:
                current = int(raw)
            except ValueError:
                current = 0
        current += 1
        ttl = ex
        if key in self._expiry and ex is None:
            ttl = max(1, int(self._expiry[key] - time.time()))
        await self.set(key, str(current), ex=ttl)
        return current

    async def expire(self, key: str, ttl: int) -> None:
        if self._alive(key):
            self._expiry[key] = time.time() + ttl

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self._expiry.pop(key, None)

    async def ping(self) -> bool:
        return True

    async def info(self, section: str = "memory") -> dict[str, Any]:  # noqa: ARG002
        return {"used_memory_human": f"{len(self._store)} keys", "mode": "memory"}

    async def close(self) -> None:
        self._store.clear()
        self._expiry.clear()


class RedisClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._redis: Redis | InMemoryKV | None = None
        self.using_memory = False

    @property
    def client(self) -> Redis | InMemoryKV:
        if self._redis is None:
            raise RuntimeError("Redis client not initialized")
        return self._redis

    async def connect(self) -> None:
        try:
            redis = Redis.from_url(
                self._settings.redis_url,
                decode_responses=True,
                max_connections=self._settings.redis_max_connections,
                socket_timeout=self._settings.redis_socket_timeout_seconds,
                socket_connect_timeout=self._settings.redis_socket_timeout_seconds,
                health_check_interval=30,
                retry_on_timeout=True,
            )
            await redis.ping()
            self._redis = redis
            self.using_memory = False
            logger.info(
                "redis_connected",
                url=self._settings.redis_url,
                max_connections=self._settings.redis_max_connections,
            )
        except Exception as exc:  # noqa: BLE001
            if not self._settings.redis_optional:
                raise
            logger.warning("redis_unavailable_using_memory", error=str(exc))
            self._redis = InMemoryKV()
            self.using_memory = True

    async def disconnect(self) -> None:
        if self._redis is not None:
            await self._redis.close()
            self._redis = None

    async def healthcheck(self) -> bool:
        retries = max(1, self._settings.redis_health_retries)
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                result = await self.client.ping()
                return bool(result)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt + 1 < retries:
                    await self._try_reconnect()
        if last_error:
            logger.warning("redis_healthcheck_failed", error=str(last_error))
        return False

    async def _try_reconnect(self) -> None:
        if self.using_memory:
            return
        try:
            await self.disconnect()
            await self.connect()
        except Exception as exc:  # noqa: BLE001
            logger.warning("redis_reconnect_failed", error=str(exc))

    async def stats(self) -> dict[str, Any]:
        if self.using_memory:
            return {"mode": "memory", "ok": True}
        try:
            info = await self.client.info("memory")  # type: ignore[misc]
            return {
                "mode": "redis",
                "ok": True,
                "used_memory_human": info.get("used_memory_human"),
                "max_connections": self._settings.redis_max_connections,
            }
        except Exception as exc:  # noqa: BLE001
            return {"mode": "redis", "ok": False, "error": str(exc)}

    async def set_json(self, key: str, value: dict[str, Any], ttl: int | None = None) -> None:
        payload = json.dumps(value, default=str)
        await self.client.set(key, payload, ex=ttl)

    async def get_json(self, key: str) -> dict[str, Any] | None:
        raw = await self.client.get(key)
        if not raw:
            return None
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        return None

    async def delete(self, key: str) -> None:
        await self.client.delete(key)

    async def touch(self, key: str, ttl: int) -> None:
        """Refresh TTL without rewriting the value (session keep-alive)."""
        client = self.client
        if isinstance(client, InMemoryKV):
            await client.expire(key, ttl)
            return
        await client.expire(key, ttl)

    async def setnx(self, key: str, value: str, ex: int | None = None) -> bool:
        client = self.client
        if isinstance(client, InMemoryKV):
            return await client.setnx(key, value, ex=ex)
        result = await client.set(key, value, ex=ex, nx=True)
        return bool(result)

    async def getdel(self, key: str) -> str | None:
        client = self.client
        if isinstance(client, InMemoryKV):
            return await client.getdel(key)
        try:
            return await client.getdel(key)
        except Exception:  # noqa: BLE001
            value = await client.get(key)
            if value is not None:
                await client.delete(key)
            return value

    async def incr_with_ttl(self, key: str, ttl_seconds: int) -> int:
        client = self.client
        if isinstance(client, InMemoryKV):
            exists = await client.get(key)
            value = await client.incr(key, ex=None if exists else ttl_seconds)
            return value
        value = await client.incr(key)
        if value == 1:
            await client.expire(key, ttl_seconds)
        return int(value)

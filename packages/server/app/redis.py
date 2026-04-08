"""Redis client with graceful fallback to in-memory storage.

In development (no Redis), everything works with in-memory dicts.
In production, Redis provides persistence across restarts, shared
state across instances, and pub/sub for WebSocket fan-out.

Usage:
    from app.redis import get_redis, token_store

    redis = await get_redis()      # Redis client or None
    store = token_store()           # TokenStore (Redis or in-memory)
"""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from redis.asyncio import Redis

from app.config import settings

logger = structlog.get_logger()

# Singleton Redis connection
_redis_client: Redis | None = None
_redis_available: bool | None = None  # None = not yet tested


async def get_redis() -> Redis | None:
    """Get the Redis client, or None if Redis is unavailable.

    Lazy-initializes and caches the connection. Tests connectivity
    on first call. If Redis goes down later, callers handle the
    exception — this only gates the initial connection.
    """
    global _redis_client, _redis_available

    if _redis_available is False:
        return None

    if _redis_client is not None:
        return _redis_client

    try:
        client = Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await client.ping()
        _redis_client = client
        _redis_available = True
        logger.info("redis_connected", url=settings.REDIS_URL.split("@")[-1])
        return _redis_client
    except Exception as exc:
        _redis_available = False
        logger.warning("redis_unavailable", error=str(exc), fallback="in-memory")
        return None


async def close_redis() -> None:
    """Close the Redis connection on shutdown."""
    global _redis_client, _redis_available
    if _redis_client:
        await _redis_client.close()
        _redis_client = None
        _redis_available = None


# ---------------------------------------------------------------------------
# Token Store — generic key-value with TTL
# ---------------------------------------------------------------------------

class TokenStore:
    """Abstract interface for token storage with TTL.

    Used by: email verification tokens, password reset tokens,
    invite tokens, OAuth state tokens, rate limiter counters.
    """

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        raise NotImplementedError

    async def get(self, key: str) -> str | None:
        raise NotImplementedError

    async def delete(self, key: str) -> None:
        raise NotImplementedError

    async def exists(self, key: str) -> bool:
        raise NotImplementedError

    async def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        await self.set(key, json.dumps(value), ttl_seconds)

    async def get_json(self, key: str) -> Any | None:
        raw = await self.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def keys_by_prefix(self, prefix: str) -> list[str]:
        raise NotImplementedError


class RedisTokenStore(TokenStore):
    """Redis-backed token store."""

    def __init__(self, client: Redis, namespace: str = "handoff") -> None:
        self._r = client
        self._ns = namespace

    def _key(self, key: str) -> str:
        return f"{self._ns}:{key}"

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._r.setex(self._key(key), ttl_seconds, value)

    async def get(self, key: str) -> str | None:
        return await self._r.get(self._key(key))

    async def delete(self, key: str) -> None:
        await self._r.delete(self._key(key))

    async def exists(self, key: str) -> bool:
        return bool(await self._r.exists(self._key(key)))

    async def keys_by_prefix(self, prefix: str) -> list[str]:
        full_prefix = self._key(prefix)
        keys = []
        async for key in self._r.scan_iter(match=f"{full_prefix}*", count=100):
            # Strip namespace prefix
            keys.append(key[len(self._ns) + 1:])
        return keys


class InMemoryTokenStore(TokenStore):
    """In-memory token store with TTL expiration. For development only."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float]] = {}  # key -> (value, expires_at)

    def _cleanup(self) -> None:
        now = time.monotonic()
        expired = [k for k, (_, exp) in self._store.items() if exp <= now]
        for k in expired:
            del self._store[k]

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._store[key] = (value, time.monotonic() + ttl_seconds)

    async def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def exists(self, key: str) -> bool:
        return await self.get(key) is not None

    async def keys_by_prefix(self, prefix: str) -> list[str]:
        self._cleanup()
        return [k for k in self._store if k.startswith(prefix)]


# Singleton token store
_token_store: TokenStore | None = None


async def token_store() -> TokenStore:
    """Get the token store (Redis-backed or in-memory fallback)."""
    global _token_store
    if _token_store is not None:
        return _token_store

    redis = await get_redis()
    if redis:
        _token_store = RedisTokenStore(redis)
    else:
        _token_store = InMemoryTokenStore()

    return _token_store


# ---------------------------------------------------------------------------
# Rate limiter support — sliding window in Redis
# ---------------------------------------------------------------------------

class RedisRateLimiter:
    """Redis-backed sliding window rate limiter using sorted sets.

    Each key is a sorted set where members are unique request IDs
    and scores are timestamps. To check the rate: count members
    within the window.
    """

    def __init__(self, client: Redis, namespace: str = "handoff:rl") -> None:
        self._r = client
        self._ns = namespace

    async def allow(self, key: str, window_seconds: float, max_requests: int) -> tuple[bool, int, float]:
        """Check if a request is allowed.

        Returns:
            (allowed, remaining, reset_seconds)
        """
        full_key = f"{self._ns}:{key}"
        now = time.time()
        window_start = now - window_seconds

        pipe = self._r.pipeline()
        # Remove expired entries
        pipe.zremrangebyscore(full_key, 0, window_start)
        # Count current entries
        pipe.zcard(full_key)
        # Add this request (optimistically)
        pipe.zadd(full_key, {f"{now}:{id(pipe)}": now})
        # Set TTL on the key
        pipe.expire(full_key, int(window_seconds) + 1)
        results = await pipe.execute()

        count = results[1]  # count BEFORE adding this request

        if count >= max_requests:
            # Over limit — remove the optimistic add
            await self._r.zrem(full_key, f"{now}:{id(pipe)}")
            # Calculate reset time from oldest entry
            oldest = await self._r.zrange(full_key, 0, 0, withscores=True)
            reset = (oldest[0][1] + window_seconds - now) if oldest else window_seconds
            return False, 0, max(0.0, reset)

        remaining = max(0, max_requests - count - 1)
        return True, remaining, 0.0


# ---------------------------------------------------------------------------
# Pub/Sub for WebSocket fan-out across instances
# ---------------------------------------------------------------------------

class RedisPubSub:
    """Redis pub/sub for broadcasting WebSocket messages across server instances."""

    def __init__(self, client: Redis, namespace: str = "handoff:ws") -> None:
        self._r = client
        self._ns = namespace
        self._pubsub = client.pubsub()
        self._handlers: dict[str, list] = {}

    async def publish(self, channel: str, message: dict[str, Any]) -> int:
        """Publish a message to a channel. Returns number of subscribers."""
        full_channel = f"{self._ns}:{channel}"
        return await self._r.publish(full_channel, json.dumps(message))

    async def subscribe(self, channel: str, handler) -> None:
        """Subscribe to a channel with a callback handler."""
        full_channel = f"{self._ns}:{channel}"
        if full_channel not in self._handlers:
            self._handlers[full_channel] = []
            await self._pubsub.subscribe(full_channel)
        self._handlers[full_channel].append(handler)

    async def unsubscribe(self, channel: str) -> None:
        """Unsubscribe from a channel."""
        full_channel = f"{self._ns}:{channel}"
        self._handlers.pop(full_channel, None)
        await self._pubsub.unsubscribe(full_channel)

    async def listen(self) -> None:
        """Start listening for messages. Run this in a background task."""
        async for message in self._pubsub.listen():
            if message["type"] != "message":
                continue
            channel = message["channel"]
            data = json.loads(message["data"])
            for handler in self._handlers.get(channel, []):
                try:
                    await handler(data)
                except Exception as exc:
                    logger.error("pubsub_handler_error", channel=channel, error=str(exc))

    async def close(self) -> None:
        """Clean up pub/sub connection."""
        await self._pubsub.unsubscribe()
        await self._pubsub.close()

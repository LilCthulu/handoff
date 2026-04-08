"""Tests for Redis module — token store and rate limiter abstractions."""

import asyncio
import pytest

from app.redis import InMemoryTokenStore, RedisRateLimiter


class TestInMemoryTokenStore:
    """Test the in-memory fallback token store."""

    async def test_set_and_get(self):
        store = InMemoryTokenStore()
        await store.set("key1", "value1", ttl_seconds=60)
        assert await store.get("key1") == "value1"

    async def test_get_missing_key(self):
        store = InMemoryTokenStore()
        assert await store.get("nonexistent") is None

    async def test_delete(self):
        store = InMemoryTokenStore()
        await store.set("key1", "value1", ttl_seconds=60)
        await store.delete("key1")
        assert await store.get("key1") is None

    async def test_delete_nonexistent(self):
        store = InMemoryTokenStore()
        await store.delete("nonexistent")  # Should not raise

    async def test_exists(self):
        store = InMemoryTokenStore()
        await store.set("key1", "value1", ttl_seconds=60)
        assert await store.exists("key1") is True
        assert await store.exists("nonexistent") is False

    async def test_ttl_expiration(self):
        store = InMemoryTokenStore()
        await store.set("key1", "value1", ttl_seconds=0)  # Immediate expiry
        # The TTL is 0, so monotonic time should have advanced past it
        await asyncio.sleep(0.01)
        assert await store.get("key1") is None

    async def test_set_json_and_get_json(self):
        store = InMemoryTokenStore()
        data = {"user_id": "abc", "scope": ["read", "write"]}
        await store.set_json("json_key", data, ttl_seconds=60)
        result = await store.get_json("json_key")
        assert result == data

    async def test_get_json_missing(self):
        store = InMemoryTokenStore()
        assert await store.get_json("missing") is None

    async def test_keys_by_prefix(self):
        store = InMemoryTokenStore()
        await store.set("verify:abc", "1", ttl_seconds=60)
        await store.set("verify:def", "2", ttl_seconds=60)
        await store.set("reset:ghi", "3", ttl_seconds=60)
        keys = await store.keys_by_prefix("verify:")
        assert sorted(keys) == ["verify:abc", "verify:def"]

    async def test_overwrite_value(self):
        store = InMemoryTokenStore()
        await store.set("key1", "old", ttl_seconds=60)
        await store.set("key1", "new", ttl_seconds=60)
        assert await store.get("key1") == "new"

    async def test_concurrent_access(self):
        store = InMemoryTokenStore()

        async def writer(i):
            await store.set(f"key_{i}", f"val_{i}", ttl_seconds=60)

        await asyncio.gather(*[writer(i) for i in range(100)])
        for i in range(100):
            assert await store.get(f"key_{i}") == f"val_{i}"

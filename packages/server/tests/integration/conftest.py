"""Integration test fixtures — real app, real DB, real middleware.

Uses an in-memory SQLite database and httpx AsyncClient against the
actual FastAPI app via ASGI transport. Nothing is mocked except the
database engine — every middleware, dependency, and handler runs for real.
"""

import base64
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import nacl.signing
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Set env BEFORE any app imports
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-integration-tests")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from app.core.auth import create_agent_token
from app.core.crypto import generate_keypair, canonical_json
from app.database import get_db
from app.models import Base
from app.main import app
import app.database as db_module
import app.middleware.auth as auth_module


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_engine():
    """Create a fresh in-memory SQLite engine for each test."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Yield an async session bound to the test engine."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_engine):
    """httpx AsyncClient wired to the FastAPI app with a test-scoped DB."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    import app.middleware.rate_limit as rl_module
    original_ip_max = rl_module.IP_MAX_REQUESTS
    rl_module.IP_MAX_REQUESTS = 1000  # generous for integration tests

    with patch.object(db_module, "async_session", session_factory), \
         patch.object(auth_module, "async_session", session_factory):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    rl_module.IP_MAX_REQUESTS = original_ip_max
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

class AgentFactory:
    """Creates real agents with Ed25519 keys and JWT tokens."""

    def __init__(self, client: AsyncClient):
        self._client = client

    async def create(
        self,
        name: str | None = None,
        owner_id: str = "test-owner",
        capabilities: list | None = None,
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        """Register a new agent. Returns dict with id, token, keys, headers."""
        private_key, public_key = generate_keypair()

        payload = {
            "name": name or f"test-agent-{uuid.uuid4().hex[:8]}",
            "owner_id": owner_id,
            "public_key": public_key,
            "capabilities": capabilities or [],
            "max_authority": {},
            "metadata": metadata or {},
        }

        resp = await self._client.post("/api/v1/agents/register", json=payload)
        assert resp.status_code == 201, f"Agent registration failed: {resp.text}"
        data = resp.json()

        return {
            "agent": data["agent"],
            "token": data["token"],
            "id": data["agent"]["id"],
            "private_key": private_key,
            "public_key": public_key,
            "headers": {"Authorization": f"Bearer {data['token']}"},
        }

    async def create_pair(self, **kwargs) -> tuple[dict, dict]:
        """Create two agents (delegator + receiver) for handoff testing."""
        delegator = await self.create(name="delegator", **kwargs)
        receiver = await self.create(name="receiver", **kwargs)
        return delegator, receiver


@pytest_asyncio.fixture
async def agent_factory(client: AsyncClient) -> AgentFactory:
    return AgentFactory(client)


# ---------------------------------------------------------------------------
# Crypto helpers
# ---------------------------------------------------------------------------

def sign_bytes(data: bytes, private_key_b64: str) -> str:
    """Sign arbitrary bytes with an Ed25519 private key, return base64 signature."""
    private_key_raw = base64.b64decode(private_key_b64)
    signing_key = nacl.signing.SigningKey(private_key_raw)
    signed = signing_key.sign(data)
    return base64.b64encode(signed.signature).decode()


def sign_claim(claim: dict, private_key_b64: str) -> str:
    """Sign a canonical JSON claim dict, return base64 signature."""
    return sign_bytes(canonical_json(claim), private_key_b64)

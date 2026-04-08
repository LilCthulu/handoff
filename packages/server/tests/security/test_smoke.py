"""Smoke test — verify the security test infrastructure works."""

import pytest


async def test_health(client):
    """Server health check works through test client."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_agent_registration(client, agent_factory):
    """Agent factory can create agents with valid tokens."""
    agent = await agent_factory.create(name="smoke-test-agent")
    assert agent["id"]
    assert agent["token"]
    assert agent["private_key"]
    assert agent["headers"]["Authorization"].startswith("Bearer ")


async def test_authenticated_request(client, agent_factory):
    """Authenticated agent can access protected endpoints."""
    agent = await agent_factory.create()
    resp = await client.get(
        f"/api/v1/agents/{agent['id']}",
        headers=agent["headers"],
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == agent["agent"]["name"]


async def test_unauthenticated_request_rejected(client):
    """Requests without auth token are rejected on protected endpoints."""
    import uuid
    resp = await client.get(f"/api/v1/agents/{uuid.uuid4()}")
    assert resp.status_code == 401

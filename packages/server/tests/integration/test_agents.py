"""Integration tests: Agent lifecycle — register, auth, update, deactivate, rotate keys."""

import uuid

import pytest


class TestAgentRegistration:
    """POST /agents/register"""

    async def test_register_minimal_agent(self, client):
        from app.core.crypto import generate_keypair
        _, pub = generate_keypair()
        resp = await client.post("/api/v1/agents/register", json={
            "name": "minimal",
            "owner_id": "owner1",
            "public_key": pub,
            "capabilities": [],
            "max_authority": {},
            "metadata": {},
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["agent"]["name"] == "minimal"
        assert data["agent"]["status"] == "active"
        assert data["agent"]["trust_score"] == 0.5
        assert "token" in data

    async def test_register_with_capabilities(self, client):
        from app.core.crypto import generate_keypair
        _, pub = generate_keypair()
        resp = await client.post("/api/v1/agents/register", json={
            "name": "capable",
            "owner_id": "owner1",
            "public_key": pub,
            "capabilities": [{"domain": "travel", "actions": ["book_hotel", "search"]}],
            "max_authority": {"max_spend": 1000, "currency": "USD"},
            "metadata": {"version": "1.0"},
        })
        assert resp.status_code == 201
        agent = resp.json()["agent"]
        assert len(agent["capabilities"]) == 1
        assert agent["capabilities"][0]["domain"] == "travel"
        assert agent["max_authority"]["max_spend"] == 1000

    async def test_register_missing_name_rejected(self, client):
        from app.core.crypto import generate_keypair
        _, pub = generate_keypair()
        resp = await client.post("/api/v1/agents/register", json={
            "owner_id": "owner1",
            "public_key": pub,
        })
        assert resp.status_code == 422

    async def test_register_empty_name_rejected(self, client):
        from app.core.crypto import generate_keypair
        _, pub = generate_keypair()
        resp = await client.post("/api/v1/agents/register", json={
            "name": "",
            "owner_id": "owner1",
            "public_key": pub,
            "capabilities": [],
            "max_authority": {},
            "metadata": {},
        })
        assert resp.status_code == 422


class TestAgentChallengeAuth:
    """POST /agents/challenge + POST /agents/authenticate"""

    async def test_challenge_response_flow(self, client, agent_factory):
        agent = await agent_factory.create()

        # Request challenge
        resp = await client.post("/api/v1/agents/challenge", params={"agent_id": agent["id"]})
        assert resp.status_code == 200
        challenge = resp.json()["challenge"]

        # Sign the challenge
        from tests.integration.conftest import sign_bytes
        signature = sign_bytes(challenge.encode(), agent["private_key"])

        # Authenticate
        resp = await client.post("/api/v1/agents/authenticate", json={
            "agent_id": agent["id"],
            "challenge": challenge,
            "signature": signature,
        })
        assert resp.status_code == 200
        assert "token" in resp.json()

    async def test_wrong_signature_rejected(self, client, agent_factory):
        agent = await agent_factory.create()
        resp = await client.post("/api/v1/agents/challenge", params={"agent_id": agent["id"]})
        challenge = resp.json()["challenge"]

        resp = await client.post("/api/v1/agents/authenticate", json={
            "agent_id": agent["id"],
            "challenge": challenge,
            "signature": "aW52YWxpZC1zaWduYXR1cmU=",  # invalid
        })
        assert resp.status_code == 401

    async def test_expired_challenge_rejected(self, client, agent_factory):
        """Using a challenge that doesn't exist (simulates expiry)."""
        agent = await agent_factory.create()
        resp = await client.post("/api/v1/agents/authenticate", json={
            "agent_id": agent["id"],
            "challenge": "nonexistent-challenge",
            "signature": "doesnt-matter",
        })
        assert resp.status_code == 401


class TestAgentProfile:
    """GET/PATCH/DELETE /agents/{id}"""

    async def test_get_own_profile(self, client, agent_factory):
        agent = await agent_factory.create(name="viewer")
        resp = await client.get(f"/api/v1/agents/{agent['id']}", headers=agent["headers"])
        assert resp.status_code == 200
        assert resp.json()["name"] == "viewer"

    async def test_update_own_profile(self, client, agent_factory):
        agent = await agent_factory.create(name="original")
        resp = await client.patch(
            f"/api/v1/agents/{agent['id']}",
            json={"name": "updated", "description": "new desc"},
            headers=agent["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "updated"
        assert resp.json()["description"] == "new desc"

    async def test_cannot_update_other_agent(self, client, agent_factory):
        a, b = await agent_factory.create_pair()
        resp = await client.patch(
            f"/api/v1/agents/{b['id']}",
            json={"name": "pwned"},
            headers=a["headers"],
        )
        assert resp.status_code == 403

    async def test_deactivate_self(self, client, agent_factory):
        agent = await agent_factory.create()
        resp = await client.delete(f"/api/v1/agents/{agent['id']}", headers=agent["headers"])
        assert resp.status_code == 204

        # Verify the agent is now revoked
        resp = await client.get(f"/api/v1/agents/{agent['id']}", headers=agent["headers"])
        assert resp.status_code == 403  # revoked

    async def test_cannot_deactivate_other(self, client, agent_factory):
        a, b = await agent_factory.create_pair()
        resp = await client.delete(f"/api/v1/agents/{b['id']}", headers=a["headers"])
        assert resp.status_code == 403


class TestKeyRotation:
    """POST /agents/{id}/rotate-keys"""

    async def test_rotate_keys_success(self, client, agent_factory):
        agent = await agent_factory.create()
        from app.core.crypto import generate_keypair
        from tests.integration.conftest import sign_bytes

        _, new_pub = generate_keypair()
        # Sign the new public key with the old private key
        sig = sign_bytes(new_pub.encode(), agent["private_key"])

        resp = await client.post(
            f"/api/v1/agents/{agent['id']}/rotate-keys",
            json={"new_public_key": new_pub, "signature": sig},
            headers=agent["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["public_key"] == new_pub

    async def test_rotate_with_bad_signature_rejected(self, client, agent_factory):
        agent = await agent_factory.create()
        from app.core.crypto import generate_keypair
        _, new_pub = generate_keypair()

        resp = await client.post(
            f"/api/v1/agents/{agent['id']}/rotate-keys",
            json={"new_public_key": new_pub, "signature": "aW52YWxpZA=="},
            headers=agent["headers"],
        )
        assert resp.status_code == 400

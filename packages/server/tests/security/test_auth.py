"""Security regression tests for authentication and authorization."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt


class TestTokenValidation:
    """Verify token validation behaves correctly."""

    async def test_no_auth_header(self, client):
        resp = await client.get(f"/api/v1/agents/{uuid.uuid4()}")
        assert resp.status_code == 401

    async def test_empty_bearer(self, client):
        resp = await client.get(
            f"/api/v1/agents/{uuid.uuid4()}",
            headers={"Authorization": "Bearer "},
        )
        assert resp.status_code == 401

    async def test_malformed_bearer(self, client):
        resp = await client.get(
            f"/api/v1/agents/{uuid.uuid4()}",
            headers={"Authorization": "Bearer not-a-jwt-token"},
        )
        assert resp.status_code == 401

    async def test_wrong_auth_scheme(self, client):
        resp = await client.get(
            f"/api/v1/agents/{uuid.uuid4()}",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert resp.status_code == 401

    async def test_expired_token(self, client, agent_factory):
        agent = await agent_factory.create()
        expired_claims = {
            "sub": agent["id"],
            "iss": "handoff-server",
            "iat": datetime.now(timezone.utc) - timedelta(hours=48),
            "exp": datetime.now(timezone.utc) - timedelta(hours=24),
            "jti": str(uuid.uuid4()),
            "scopes": ["negotiate", "handoff", "discover"],
            "authority": {},
            "owner_id": "test-owner",
        }
        import os
        expired_token = jwt.encode(expired_claims, os.environ["JWT_SECRET"], algorithm="HS256")

        resp = await client.get(
            f"/api/v1/agents/{agent['id']}",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401

    async def test_wrong_secret_token(self, client, agent_factory):
        agent = await agent_factory.create()
        forged_claims = {
            "sub": agent["id"],
            "iss": "handoff-server",
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=24),
            "jti": str(uuid.uuid4()),
            "scopes": ["negotiate", "handoff", "discover"],
            "authority": {},
            "owner_id": "test-owner",
        }
        forged_token = jwt.encode(forged_claims, "wrong-secret-key", algorithm="HS256")

        resp = await client.get(
            f"/api/v1/agents/{agent['id']}",
            headers={"Authorization": f"Bearer {forged_token}"},
        )
        assert resp.status_code == 401

    async def test_unsigned_token_rejected(self, client, agent_factory):
        agent = await agent_factory.create()
        import base64, json
        header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=")
        payload = base64.urlsafe_b64encode(json.dumps({
            "sub": agent["id"],
            "iss": "handoff-server",
            "exp": int((datetime.now(timezone.utc) + timedelta(hours=24)).timestamp()),
            "scopes": ["negotiate", "handoff", "discover"],
            "owner_id": "test-owner",
        }).encode()).rstrip(b"=")
        forged = f"{header.decode()}.{payload.decode()}."

        resp = await client.get(
            f"/api/v1/agents/{agent['id']}",
            headers={"Authorization": f"Bearer {forged}"},
        )
        assert resp.status_code == 401


class TestAgentLookup:
    """Verify agent existence is checked during auth."""

    async def test_token_for_nonexistent_agent(self, client):
        from tests.security.conftest import make_fake_token
        ghost_id = str(uuid.uuid4())
        token = make_fake_token(agent_id=ghost_id)

        resp = await client.get(
            f"/api/v1/agents/{ghost_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401


class TestAgentStatusEnforcement:
    """Verify deactivated agents cannot authenticate."""

    async def test_deactivated_agent_rejected(self, client, agent_factory):
        agent = await agent_factory.create()
        headers = agent["headers"]

        resp = await client.delete(f"/api/v1/agents/{agent['id']}", headers=headers)
        assert resp.status_code == 204

        resp = await client.get(f"/api/v1/agents/{agent['id']}", headers=headers)
        assert resp.status_code == 403


class TestAgentIsolation:
    """Verify agents cannot modify other agents' resources."""

    async def test_update_other_agent(self, client, agent_factory):
        agent_a = await agent_factory.create(name="agent-a")
        agent_b = await agent_factory.create(name="agent-b")

        resp = await client.patch(
            f"/api/v1/agents/{agent_b['id']}",
            json={"name": "renamed"},
            headers=agent_a["headers"],
        )
        assert resp.status_code == 403

    async def test_delete_other_agent(self, client, agent_factory):
        agent_a = await agent_factory.create(name="agent-a")
        agent_b = await agent_factory.create(name="agent-b")

        resp = await client.delete(
            f"/api/v1/agents/{agent_b['id']}",
            headers=agent_a["headers"],
        )
        assert resp.status_code == 403

    async def test_rotate_other_agents_keys(self, client, agent_factory):
        from app.core.crypto import generate_keypair
        agent_a = await agent_factory.create(name="agent-a")
        agent_b = await agent_factory.create(name="agent-b")
        _, new_pub = generate_keypair()

        resp = await client.post(
            f"/api/v1/agents/{agent_b['id']}/rotate-keys",
            json={"new_public_key": new_pub, "signature": "fake-sig"},
            headers=agent_a["headers"],
        )
        assert resp.status_code == 403


class TestScopeEnforcement:
    """Verify token scopes are enforced."""

    async def test_limited_scope_token(self, client, agent_factory):
        from app.core.auth import create_agent_token
        agent = await agent_factory.create()
        limited_token = create_agent_token(
            agent_id=uuid.UUID(agent["id"]),
            owner_id="test-owner",
            scopes=["discover"],
        )

        resp = await client.post(
            "/api/v1/negotiations",
            json={
                "responder_id": str(uuid.uuid4()),
                "intent": {"domain": "test", "action": "test"},
            },
            headers={"Authorization": f"Bearer {limited_token}"},
        )
        assert resp.status_code != 201


class TestPublicEndpoints:
    """Verify public endpoints are accessible without auth."""

    async def test_health_is_public(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200

    async def test_register_is_public(self, client):
        from app.core.crypto import generate_keypair
        _, pub = generate_keypair()
        resp = await client.post("/api/v1/agents/register", json={
            "name": "test",
            "owner_id": "test",
            "public_key": pub,
            "capabilities": [],
            "max_authority": {},
            "metadata": {},
        })
        assert resp.status_code == 201

    async def test_dashboard_uses_own_auth(self, client):
        resp = await client.get("/api/v1/dashboard/overview")
        assert resp.status_code != 401 or "Agent" not in resp.text

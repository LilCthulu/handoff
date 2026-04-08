"""Security regression tests for access control and data isolation."""

import uuid
from typing import Any

import pytest


class TestHandoffAccessControl:
    """Verify handoff data isolation between agents."""

    async def _create_handoff(self, client, delegator, receiver) -> dict[str, Any]:
        resp = await client.post(
            "/api/v1/handoffs",
            json={
                "to_agent_id": receiver["id"],
                "context": {"domain": "test", "action": "process", "data": "test-value"},
            },
            headers=delegator["headers"],
        )
        assert resp.status_code == 201, f"Handoff creation failed: {resp.text}"
        return resp.json()

    async def test_participant_can_view_handoff(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        handoff = await self._create_handoff(client, delegator, receiver)

        resp = await client.get(f"/api/v1/handoffs/{handoff['id']}", headers=delegator["headers"])
        assert resp.status_code == 200

        resp = await client.get(f"/api/v1/handoffs/{handoff['id']}", headers=receiver["headers"])
        assert resp.status_code == 200

    async def test_non_participant_cannot_view_handoff(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        outsider = await agent_factory.create(name="outsider")
        handoff = await self._create_handoff(client, delegator, receiver)

        resp = await client.get(f"/api/v1/handoffs/{handoff['id']}", headers=outsider["headers"])
        assert resp.status_code == 403

    async def test_non_participant_cannot_view_chain(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        outsider = await agent_factory.create(name="outsider")
        handoff = await self._create_handoff(client, delegator, receiver)

        resp = await client.get(
            f"/api/v1/handoffs/chain/{handoff['chain_id']}",
            headers=outsider["headers"],
        )
        assert resp.status_code == 403


class TestNegotiationAccessControl:
    """Verify negotiation data isolation between agents."""

    async def _create_negotiation(self, client, initiator, responder) -> dict[str, Any]:
        resp = await client.post(
            "/api/v1/negotiations",
            json={
                "responder_id": responder["id"],
                "intent": {"type": "request", "domain": "test", "action": "process"},
            },
            headers=initiator["headers"],
        )
        assert resp.status_code == 201
        return resp.json()

    async def test_participant_can_view_negotiation(self, client, agent_factory):
        initiator, responder = await agent_factory.create_pair()
        neg = await self._create_negotiation(client, initiator, responder)

        resp = await client.get(f"/api/v1/negotiations/{neg['id']}", headers=initiator["headers"])
        assert resp.status_code == 200

        resp = await client.get(f"/api/v1/negotiations/{neg['id']}", headers=responder["headers"])
        assert resp.status_code == 200

    async def test_non_participant_cannot_view_negotiation(self, client, agent_factory):
        initiator, responder = await agent_factory.create_pair()
        outsider = await agent_factory.create(name="outsider")
        neg = await self._create_negotiation(client, initiator, responder)

        resp = await client.get(f"/api/v1/negotiations/{neg['id']}", headers=outsider["headers"])
        assert resp.status_code == 403

    async def test_non_participant_cannot_view_history(self, client, agent_factory):
        initiator, responder = await agent_factory.create_pair()
        outsider = await agent_factory.create(name="outsider")
        neg = await self._create_negotiation(client, initiator, responder)

        resp = await client.get(f"/api/v1/negotiations/{neg['id']}/history", headers=outsider["headers"])
        assert resp.status_code == 403


class TestScopeEnforcement:
    """Verify token scopes are enforced on protected actions."""

    async def test_negotiate_scope_required(self, client, agent_factory):
        from app.core.auth import create_agent_token
        initiator = await agent_factory.create(name="initiator")
        responder = await agent_factory.create(name="responder")

        limited_token = create_agent_token(
            agent_id=uuid.UUID(initiator["id"]),
            owner_id="test-owner",
            scopes=["discover"],
        )

        resp = await client.post(
            "/api/v1/negotiations",
            json={
                "responder_id": responder["id"],
                "intent": {"type": "request", "domain": "test", "action": "test"},
            },
            headers={"Authorization": f"Bearer {limited_token}"},
        )
        assert resp.status_code == 403

    async def test_handoff_scope_required(self, client, agent_factory):
        from app.core.auth import create_agent_token
        delegator = await agent_factory.create(name="delegator")
        receiver = await agent_factory.create(name="receiver")

        limited_token = create_agent_token(
            agent_id=uuid.UUID(delegator["id"]),
            owner_id="test-owner",
            scopes=["discover", "negotiate"],
        )

        resp = await client.post(
            "/api/v1/handoffs",
            json={
                "to_agent_id": receiver["id"],
                "context": {"domain": "test"},
            },
            headers={"Authorization": f"Bearer {limited_token}"},
        )
        assert resp.status_code == 403


class TestStakeIsolation:
    """Verify agents cannot access other agents' financial data."""

    async def test_cannot_view_other_agent_balance(self, client, agent_factory):
        agent_a = await agent_factory.create(name="agent-a")
        agent_b = await agent_factory.create(name="agent-b")

        resp = await client.get(
            f"/api/v1/stakes/balance/{agent_b['id']}",
            headers=agent_a["headers"],
        )
        assert resp.status_code == 403

    async def test_can_view_own_balance(self, client, agent_factory):
        agent = await agent_factory.create()

        resp = await client.get(
            f"/api/v1/stakes/balance/{agent['id']}",
            headers=agent["headers"],
        )
        assert resp.status_code == 200

    async def test_cannot_view_other_agent_stakes(self, client, agent_factory):
        agent_a = await agent_factory.create(name="agent-a")
        agent_b = await agent_factory.create(name="agent-b")

        resp = await client.get(
            f"/api/v1/stakes/agent/{agent_b['id']}",
            headers=agent_a["headers"],
        )
        assert resp.status_code == 403


class TestAuditTrailAccess:
    """Verify audit trail access controls."""

    async def test_invalid_entity_type_rejected(self, client, agent_factory):
        agent = await agent_factory.create()
        resp = await client.get(
            f"/api/v1/audit/users/{uuid.uuid4()}",
            headers=agent["headers"],
        )
        assert resp.status_code == 400

    async def test_valid_entity_types_accepted(self, client, agent_factory):
        agent = await agent_factory.create()
        resp = await client.get(
            f"/api/v1/audit/agent/{uuid.uuid4()}",
            headers=agent["headers"],
        )
        assert resp.status_code == 200
        assert resp.json() == []


class TestInputValidation:
    """Verify input validation on payloads."""

    async def test_deeply_nested_context_rejected(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()

        nested = {"value": "deep"}
        for _ in range(14):
            nested = {"child": nested}

        resp = await client.post(
            "/api/v1/handoffs",
            json={
                "to_agent_id": receiver["id"],
                "context": nested,
            },
            headers=delegator["headers"],
        )
        assert resp.status_code == 422

    async def test_normal_context_accepted(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()

        resp = await client.post(
            "/api/v1/handoffs",
            json={
                "to_agent_id": receiver["id"],
                "context": {
                    "domain": "travel",
                    "action": "book_hotel",
                    "input": {"city": "Paris", "dates": {"check_in": "2026-05-01", "check_out": "2026-05-05"}},
                },
            },
            headers=delegator["headers"],
        )
        assert resp.status_code == 201


class TestRateLimiting:
    """Verify rate limiting on endpoints."""

    async def test_challenge_rate_limit(self, client, agent_factory):
        agent = await agent_factory.create()

        for i in range(5):
            resp = await client.post(
                "/api/v1/agents/challenge",
                params={"agent_id": agent["id"]},
            )
            assert resp.status_code == 200, f"Challenge {i+1} should succeed"

        resp = await client.post(
            "/api/v1/agents/challenge",
            params={"agent_id": agent["id"]},
        )
        assert resp.status_code == 429


class TestHealthEndpoint:
    """Verify health endpoint response."""

    async def test_health_response_shape(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "extensions_loaded" in data
        assert isinstance(data["extensions_loaded"], int)

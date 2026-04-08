"""Integration tests: Trust scoring API."""

import uuid

import pytest


class TestTrustScores:
    """GET /trust/{agent_id} and /trust/discover/{domain}"""

    async def test_new_agent_gets_default_trust(self, client, agent_factory):
        agent = await agent_factory.create()
        resp = await client.get(
            f"/api/v1/trust/{agent['id']}",
            headers=agent["headers"],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "domains" in data or "score" in data

    async def test_trust_by_domain_no_history(self, client, agent_factory):
        agent = await agent_factory.create()
        resp = await client.get(
            f"/api/v1/trust/{agent['id']}",
            params={"domain": "travel"},
            headers=agent["headers"],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["score"] == 0.5  # default for no history
        assert data["total_handoffs"] == 0

    async def test_trust_after_successful_handoff(self, client, agent_factory):
        """Complete a handoff and verify trust score adjusts."""
        delegator, receiver = await agent_factory.create_pair()

        # Create and complete a handoff
        resp = await client.post("/api/v1/handoffs", json={
            "to_agent_id": receiver["id"],
            "context": {"task": "test", "domain": "payments"},
        }, headers=delegator["headers"])
        handoff = resp.json()

        # Accept
        await client.patch(
            f"/api/v1/handoffs/{handoff['id']}/status",
            json={"status": "in_progress"},
            headers=receiver["headers"],
        )

        # Complete with result
        await client.post(
            f"/api/v1/handoffs/{handoff['id']}/result",
            json={"result": {"success": True}},
            headers=receiver["headers"],
        )
        await client.patch(
            f"/api/v1/handoffs/{handoff['id']}/status",
            json={"status": "completed"},
            headers=receiver["headers"],
        )

        # Check trust — should exist now
        resp = await client.get(
            f"/api/v1/trust/{receiver['id']}",
            headers=delegator["headers"],
        )
        assert resp.status_code == 200

    async def test_discover_trusted_agents(self, client, agent_factory):
        agent = await agent_factory.create()
        resp = await client.get(
            "/api/v1/trust/discover/travel",
            params={"min_score": 0.3},
            headers=agent["headers"],
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_discover_with_limit(self, client, agent_factory):
        agent = await agent_factory.create()
        resp = await client.get(
            "/api/v1/trust/discover/payments",
            params={"min_score": 0.1, "limit": 5},
            headers=agent["headers"],
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

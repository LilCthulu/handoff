"""Integration tests: Discovery and trust endpoints."""

import pytest


class TestDiscovery:
    """GET /discover and /discovery/* endpoints."""

    async def test_discover_active_agents(self, client, agent_factory):
        await agent_factory.create(name="agent-a")
        await agent_factory.create(name="agent-b")
        agent_c = await agent_factory.create(name="agent-c")

        resp = await client.get("/api/v1/discover", headers=agent_c["headers"])
        assert resp.status_code == 200
        agents = resp.json()
        assert len(agents) >= 3

    async def test_discover_with_min_trust(self, client, agent_factory):
        agent = await agent_factory.create()
        resp = await client.get(
            "/api/v1/discover",
            params={"min_trust": 0.9},
            headers=agent["headers"],
        )
        assert resp.status_code == 200
        # All returned agents should have trust >= 0.9
        for a in resp.json():
            assert a["trust_score"] >= 0.9

    async def test_discover_with_limit(self, client, agent_factory):
        for i in range(5):
            await agent_factory.create(name=f"agent-{i}")
        agent = await agent_factory.create(name="querier")

        resp = await client.get(
            "/api/v1/discover",
            params={"limit": 3},
            headers=agent["headers"],
        )
        assert resp.status_code == 200
        assert len(resp.json()) <= 3

    async def test_discovery_stats(self, client, agent_factory):
        agent = await agent_factory.create()

        resp = await client.get("/api/v1/discovery/stats", headers=agent["headers"])
        assert resp.status_code == 200
        stats = resp.json()
        assert "active_agents" in stats
        assert "avg_trust_score" in stats
        assert "active_negotiations" in stats
        assert "active_handoffs" in stats
        assert "completed_handoffs" in stats

    async def test_discovery_domains(self, client, agent_factory):
        await agent_factory.create(
            name="travel-agent",
            capabilities=[{"domain": "travel", "actions": ["book"]}],
        )
        agent = await agent_factory.create()

        resp = await client.get("/api/v1/discovery/domains", headers=agent["headers"])
        assert resp.status_code == 200
        assert "domains" in resp.json()

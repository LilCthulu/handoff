"""Integration tests: Capability contracts and challenges."""

import uuid

import pytest


class TestCapabilityContracts:
    """POST/GET /capabilities"""

    async def test_create_capability_contract(self, client, agent_factory):
        agent = await agent_factory.create(
            capabilities=[{"domain": "payments", "actions": ["charge"]}],
        )

        resp = await client.post("/api/v1/capabilities", json={
            "domain": "payments",
            "action": "charge",
            "version": "1.0.0",
            "input_schema": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number"},
                    "currency": {"type": "string"},
                },
                "required": ["amount", "currency"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string"},
                    "status": {"type": "string"},
                },
            },
            "sla": {"max_latency_ms": 5000, "availability": 0.99},
        }, headers=agent["headers"])
        assert resp.status_code == 201
        cap = resp.json()
        assert cap["domain"] == "payments"
        assert cap["action"] == "charge"

    async def test_list_own_capabilities(self, client, agent_factory):
        agent = await agent_factory.create()

        await client.post("/api/v1/capabilities", json={
            "domain": "test",
            "action": "do",
            "version": "1.0",
        }, headers=agent["headers"])

        resp = await client.get("/api/v1/capabilities/mine", headers=agent["headers"])
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    async def test_discover_capabilities(self, client, agent_factory):
        agent = await agent_factory.create()

        await client.post("/api/v1/capabilities", json={
            "domain": "travel",
            "action": "book_hotel",
            "version": "1.0",
        }, headers=agent["headers"])

        resp = await client.get(
            "/api/v1/capabilities/discover",
            params={"domain": "travel"},
            headers=agent["headers"],
        )
        assert resp.status_code == 200


class TestChallenges:
    """POST /challenges — proof of competence."""

    async def _target_with_contract(self, client, agent_factory, domain="travel", action="book"):
        """Create a target agent with an active capability contract."""
        target = await agent_factory.create(name="target")
        await client.post("/api/v1/capabilities", json={
            "domain": domain,
            "action": action,
            "version": "1.0",
        }, headers=target["headers"])
        return target

    async def test_create_challenge(self, client, agent_factory):
        challenger = await agent_factory.create(name="challenger")
        target = await self._target_with_contract(client, agent_factory)

        resp = await client.post("/api/v1/challenges", json={
            "agent_id": target["id"],
            "domain": "travel",
            "action": "book",
            "challenge_input": {"city": "Paris"},
        }, headers=challenger["headers"])
        assert resp.status_code == 201
        assert resp.json()["status"] == "pending"

    async def test_respond_to_challenge(self, client, agent_factory):
        challenger = await agent_factory.create(name="challenger")
        target = await self._target_with_contract(client, agent_factory, "travel", "search")

        resp = await client.post("/api/v1/challenges", json={
            "agent_id": target["id"],
            "domain": "travel",
            "action": "search",
            "challenge_input": {"query": "hotels in Tokyo"},
        }, headers=challenger["headers"])
        challenge_id = resp.json()["id"]

        # Target responds
        resp = await client.post(
            f"/api/v1/challenges/{challenge_id}/respond",
            json={"response": {"results": [{"name": "Hotel Tokyo", "price": 150}]}},
            headers=target["headers"],
        )
        assert resp.status_code == 200

    async def test_get_pending_challenges(self, client, agent_factory):
        challenger = await agent_factory.create(name="challenger")
        target = await self._target_with_contract(client, agent_factory, "test", "do")

        await client.post("/api/v1/challenges", json={
            "agent_id": target["id"],
            "domain": "test",
            "action": "do",
            "challenge_input": {"data": "test"},
        }, headers=challenger["headers"])

        resp = await client.get("/api/v1/challenges/pending", headers=target["headers"])
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

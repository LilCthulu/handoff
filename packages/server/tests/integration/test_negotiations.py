"""Integration tests: Negotiation lifecycle — create, offer, counter, accept, reject, mediate."""

import uuid

import pytest


class TestNegotiationCreation:
    """POST /negotiations"""

    async def test_create_negotiation(self, client, agent_factory):
        initiator, responder = await agent_factory.create_pair()
        resp = await client.post("/api/v1/negotiations", json={
            "responder_id": responder["id"],
            "intent": {"type": "request", "domain": "travel", "action": "book_hotel"},
        }, headers=initiator["headers"])

        assert resp.status_code == 201
        neg = resp.json()
        assert neg["initiator_id"] == initiator["id"]
        assert neg["responder_id"] == responder["id"]
        assert neg["state"] == "pending"
        assert neg["intent"]["domain"] == "travel"

    async def test_create_with_nonexistent_responder_404(self, client, agent_factory):
        initiator = await agent_factory.create()
        resp = await client.post("/api/v1/negotiations", json={
            "responder_id": str(uuid.uuid4()),
            "intent": {"type": "request", "domain": "test", "action": "test"},
        }, headers=initiator["headers"])
        assert resp.status_code == 404

    async def test_create_with_custom_max_rounds(self, client, agent_factory):
        initiator, responder = await agent_factory.create_pair()
        resp = await client.post("/api/v1/negotiations", json={
            "responder_id": responder["id"],
            "intent": {"type": "request", "domain": "test", "action": "test"},
            "max_rounds": 5,
        }, headers=initiator["headers"])
        assert resp.status_code == 201
        assert resp.json()["max_rounds"] == 5


class TestNegotiationFlow:
    """Full negotiation lifecycle: create → offer → counter → accept."""

    async def _create_neg(self, client, initiator, responder):
        resp = await client.post("/api/v1/negotiations", json={
            "responder_id": responder["id"],
            "intent": {"type": "request", "domain": "travel", "action": "book"},
        }, headers=initiator["headers"])
        assert resp.status_code == 201
        return resp.json()

    async def test_happy_path_offer_and_accept(self, client, agent_factory):
        initiator, responder = await agent_factory.create_pair()
        neg = await self._create_neg(client, initiator, responder)

        # Initiator makes first offer
        resp = await client.post(
            f"/api/v1/negotiations/{neg['id']}/offer",
            json={"terms": {"price": 100, "room": "suite"}},
            headers=initiator["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "negotiating"
        assert resp.json()["current_round"] == 1

        # Responder accepts
        resp = await client.post(
            f"/api/v1/negotiations/{neg['id']}/accept",
            headers=responder["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "agreed"
        assert resp.json()["agreement"] is not None

    async def test_counteroffer_flow(self, client, agent_factory):
        initiator, responder = await agent_factory.create_pair()
        neg = await self._create_neg(client, initiator, responder)

        # Initiator offers
        await client.post(
            f"/api/v1/negotiations/{neg['id']}/offer",
            json={"terms": {"price": 200}},
            headers=initiator["headers"],
        )

        # Responder counters
        resp = await client.post(
            f"/api/v1/negotiations/{neg['id']}/offer",
            json={"terms": {"price": 150}, "concessions": ["lowered price"]},
            headers=responder["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["current_round"] == 2

        # Initiator accepts the counter
        resp = await client.post(
            f"/api/v1/negotiations/{neg['id']}/accept",
            headers=initiator["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "agreed"

    async def test_rejection_flow(self, client, agent_factory):
        initiator, responder = await agent_factory.create_pair()
        neg = await self._create_neg(client, initiator, responder)

        resp = await client.post(
            f"/api/v1/negotiations/{neg['id']}/reject",
            json={"reason": "Too expensive"},
            headers=responder["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "rejected"

    async def test_mediation_request(self, client, agent_factory):
        initiator, responder = await agent_factory.create_pair()
        neg = await self._create_neg(client, initiator, responder)

        # Need at least one offer before requesting mediation
        await client.post(
            f"/api/v1/negotiations/{neg['id']}/offer",
            json={"terms": {"price": 200}},
            headers=initiator["headers"],
        )

        resp = await client.post(
            f"/api/v1/negotiations/{neg['id']}/mediate",
            headers=initiator["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["mediation"] == "active"
        assert "suggestion" in resp.json()


class TestNegotiationHistory:
    """GET /negotiations/{id}/history"""

    async def test_history_tracks_offers(self, client, agent_factory):
        initiator, responder = await agent_factory.create_pair()

        resp = await client.post("/api/v1/negotiations", json={
            "responder_id": responder["id"],
            "intent": {"type": "request", "domain": "test", "action": "test"},
        }, headers=initiator["headers"])
        neg = resp.json()

        # Make an offer
        await client.post(
            f"/api/v1/negotiations/{neg['id']}/offer",
            json={"terms": {"price": 100}},
            headers=initiator["headers"],
        )

        # Check history
        resp = await client.get(
            f"/api/v1/negotiations/{neg['id']}/history",
            headers=initiator["headers"],
        )
        assert resp.status_code == 200
        assert len(resp.json()["offer_history"]) >= 1

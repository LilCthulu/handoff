"""Integration tests: Attestations — create, query, verify."""

import uuid

import pytest

from tests.integration.conftest import sign_claim


class TestAttestations:
    """Attestation lifecycle."""

    async def _completed_handoff(self, client, delegator, receiver):
        resp = await client.post("/api/v1/handoffs", json={
            "to_agent_id": receiver["id"],
            "context": {"domain": "travel"},
        }, headers=delegator["headers"])
        h = resp.json()
        await client.patch(
            f"/api/v1/handoffs/{h['id']}/status",
            json={"status": "in_progress"},
            headers=receiver["headers"],
        )
        await client.patch(
            f"/api/v1/handoffs/{h['id']}/status",
            json={"status": "completed"},
            headers=receiver["headers"],
        )
        return h

    async def test_create_attestation(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._completed_handoff(client, delegator, receiver)

        claim = {"domain": "travel", "quality": "excellent", "on_time": True}
        sig = sign_claim(claim, delegator["private_key"])

        resp = await client.post("/api/v1/attestations", json={
            "handoff_id": h["id"],
            "outcome": "success",
            "rating": 0.95,
            "claim": claim,
            "signature": sig,
        }, headers=delegator["headers"])
        assert resp.status_code == 201
        att = resp.json()
        assert att["verified"] is True
        assert att["outcome"] == "success"
        assert att["domain"] == "travel"

    async def test_invalid_signature_rejected(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._completed_handoff(client, delegator, receiver)

        claim = {"domain": "travel", "quality": "excellent"}

        resp = await client.post("/api/v1/attestations", json={
            "handoff_id": h["id"],
            "outcome": "success",
            "rating": 0.9,
            "claim": claim,
            "signature": "aW52YWxpZC1zaWduYXR1cmU=",  # invalid
        }, headers=delegator["headers"])
        assert resp.status_code == 400  # now rejects invalid signatures

    async def test_only_delegator_can_attest(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._completed_handoff(client, delegator, receiver)

        claim = {"domain": "test"}
        sig = sign_claim(claim, receiver["private_key"])

        resp = await client.post("/api/v1/attestations", json={
            "handoff_id": h["id"],
            "outcome": "success",
            "claim": claim,
            "signature": sig,
        }, headers=receiver["headers"])  # receiver, not delegator
        assert resp.status_code == 403

    async def test_query_agent_attestations(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._completed_handoff(client, delegator, receiver)

        claim = {"domain": "travel", "quality": "good"}
        sig = sign_claim(claim, delegator["private_key"])
        await client.post("/api/v1/attestations", json={
            "handoff_id": h["id"],
            "outcome": "success",
            "rating": 0.8,
            "claim": claim,
            "signature": sig,
        }, headers=delegator["headers"])

        # Query receiver's attestations (public — no auth needed for trust discovery)
        resp = await client.get(f"/api/v1/attestations/agent/{receiver['id']}")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    async def test_attestation_summary(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._completed_handoff(client, delegator, receiver)

        claim = {"domain": "travel"}
        sig = sign_claim(claim, delegator["private_key"])
        await client.post("/api/v1/attestations", json={
            "handoff_id": h["id"],
            "outcome": "success",
            "rating": 0.9,
            "claim": claim,
            "signature": sig,
        }, headers=delegator["headers"])

        resp = await client.get(f"/api/v1/attestations/agent/{receiver['id']}/summary")
        assert resp.status_code == 200
        summary = resp.json()
        assert summary["total_attestations"] >= 1
        assert "travel" in summary["domains"]

    async def test_duplicate_attestation_rejected(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._completed_handoff(client, delegator, receiver)

        claim = {"domain": "travel"}
        sig = sign_claim(claim, delegator["private_key"])

        await client.post("/api/v1/attestations", json={
            "handoff_id": h["id"],
            "outcome": "success",
            "claim": claim,
            "signature": sig,
        }, headers=delegator["headers"])

        resp = await client.post("/api/v1/attestations", json={
            "handoff_id": h["id"],
            "outcome": "success",
            "claim": claim,
            "signature": sig,
        }, headers=delegator["headers"])
        assert resp.status_code == 409

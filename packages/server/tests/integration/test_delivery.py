"""Integration tests: Delivery receipts — submit, acknowledge, verify."""

import uuid

import pytest

from app.core.crypto import hash_payload


class TestDeliveryReceipts:
    """Full delivery receipt lifecycle."""

    async def _completed_handoff_with_result(self, client, delegator, receiver):
        """Create a handoff, move to in_progress, submit result."""
        resp = await client.post("/api/v1/handoffs", json={
            "to_agent_id": receiver["id"],
            "context": {"domain": "test"},
        }, headers=delegator["headers"])
        h = resp.json()

        await client.patch(
            f"/api/v1/handoffs/{h['id']}/status",
            json={"status": "in_progress"},
            headers=receiver["headers"],
        )

        result = {"output": "done", "score": 0.95}
        await client.post(
            f"/api/v1/handoffs/{h['id']}/result",
            json={"result": result},
            headers=receiver["headers"],
        )
        return h, result

    async def test_submit_delivery_receipt(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h, result = await self._completed_handoff_with_result(client, delegator, receiver)

        result_hash = hash_payload(result)
        from tests.integration.conftest import sign_bytes
        sig = sign_bytes(result_hash.encode(), receiver["private_key"])

        resp = await client.post("/api/v1/delivery", json={
            "handoff_id": h["id"],
            "result_hash": result_hash,
            "signature": sig,
        }, headers=receiver["headers"])
        assert resp.status_code == 201
        receipt = resp.json()
        assert receipt["delivered_by"] == receiver["id"]
        assert receipt["delivery_verified"] is True

    async def test_acknowledge_delivery(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h, result = await self._completed_handoff_with_result(client, delegator, receiver)

        result_hash = hash_payload(result)
        from tests.integration.conftest import sign_bytes
        sig = sign_bytes(result_hash.encode(), receiver["private_key"])

        resp = await client.post("/api/v1/delivery", json={
            "handoff_id": h["id"],
            "result_hash": result_hash,
            "signature": sig,
        }, headers=receiver["headers"])
        receipt = resp.json()

        # Delegator acknowledges
        ack_msg = f"{receipt['id']}:True"
        ack_sig = sign_bytes(ack_msg.encode(), delegator["private_key"])

        resp = await client.post(
            f"/api/v1/delivery/{receipt['id']}/acknowledge",
            json={"accepted": True, "signature": ack_sig},
            headers=delegator["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["accepted"] is True
        assert resp.json()["acknowledgment_verified"] is True

    async def test_duplicate_delivery_rejected(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h, result = await self._completed_handoff_with_result(client, delegator, receiver)

        result_hash = hash_payload(result)
        from tests.integration.conftest import sign_bytes
        sig = sign_bytes(result_hash.encode(), receiver["private_key"])

        await client.post("/api/v1/delivery", json={
            "handoff_id": h["id"],
            "result_hash": result_hash,
            "signature": sig,
        }, headers=receiver["headers"])

        # Second attempt should 409
        resp = await client.post("/api/v1/delivery", json={
            "handoff_id": h["id"],
            "result_hash": result_hash,
            "signature": sig,
        }, headers=receiver["headers"])
        assert resp.status_code == 409

    async def test_only_receiver_can_submit(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h, result = await self._completed_handoff_with_result(client, delegator, receiver)

        result_hash = hash_payload(result)
        from tests.integration.conftest import sign_bytes
        sig = sign_bytes(result_hash.encode(), delegator["private_key"])

        resp = await client.post("/api/v1/delivery", json={
            "handoff_id": h["id"],
            "result_hash": result_hash,
            "signature": sig,
        }, headers=delegator["headers"])  # wrong agent
        assert resp.status_code == 403

    async def test_verify_receipt(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h, result = await self._completed_handoff_with_result(client, delegator, receiver)

        result_hash = hash_payload(result)
        from tests.integration.conftest import sign_bytes
        sig = sign_bytes(result_hash.encode(), receiver["private_key"])

        resp = await client.post("/api/v1/delivery", json={
            "handoff_id": h["id"],
            "result_hash": result_hash,
            "signature": sig,
        }, headers=receiver["headers"])
        receipt = resp.json()

        # Verify (requires auth now)
        resp = await client.post(f"/api/v1/delivery/{receipt['id']}/verify", headers=receiver["headers"])
        assert resp.status_code == 200
        assert resp.json()["delivery_verified"] is True

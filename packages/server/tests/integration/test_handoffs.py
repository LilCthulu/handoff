"""Integration tests: Handoff lifecycle — create, status updates, results, rollback, chains."""

import uuid

import pytest


class TestHandoffCreation:
    """POST /handoffs"""

    async def test_create_handoff(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        resp = await client.post("/api/v1/handoffs", json={
            "to_agent_id": receiver["id"],
            "context": {"domain": "travel", "action": "book", "input": {"city": "Paris"}},
        }, headers=delegator["headers"])
        assert resp.status_code == 201
        h = resp.json()
        assert h["from_agent_id"] == delegator["id"]
        assert h["to_agent_id"] == receiver["id"]
        assert h["status"] == "initiated"
        assert h["context"]["input"]["city"] == "Paris"
        assert h["chain_id"] is not None

    async def test_create_with_nonexistent_target_404(self, client, agent_factory):
        delegator = await agent_factory.create()
        resp = await client.post("/api/v1/handoffs", json={
            "to_agent_id": str(uuid.uuid4()),
            "context": {"domain": "test"},
        }, headers=delegator["headers"])
        assert resp.status_code == 404

    async def test_create_with_timeout(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        resp = await client.post("/api/v1/handoffs", json={
            "to_agent_id": receiver["id"],
            "context": {"domain": "test"},
            "timeout_minutes": 30,
        }, headers=delegator["headers"])
        assert resp.status_code == 201
        assert resp.json()["timeout_at"] is not None


class TestHandoffStatusFlow:
    """PATCH /handoffs/{id}/status — full state machine."""

    async def _create_handoff(self, client, delegator, receiver):
        resp = await client.post("/api/v1/handoffs", json={
            "to_agent_id": receiver["id"],
            "context": {"domain": "test", "action": "do_work"},
        }, headers=delegator["headers"])
        return resp.json()

    async def test_initiated_to_in_progress(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._create_handoff(client, delegator, receiver)

        resp = await client.patch(
            f"/api/v1/handoffs/{h['id']}/status",
            json={"status": "in_progress"},
            headers=receiver["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

    async def test_in_progress_to_completed(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._create_handoff(client, delegator, receiver)

        # Move to in_progress
        await client.patch(
            f"/api/v1/handoffs/{h['id']}/status",
            json={"status": "in_progress"},
            headers=receiver["headers"],
        )

        # Complete
        resp = await client.patch(
            f"/api/v1/handoffs/{h['id']}/status",
            json={"status": "completed"},
            headers=receiver["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"
        assert resp.json()["completed_at"] is not None

    async def test_in_progress_to_failed(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._create_handoff(client, delegator, receiver)

        await client.patch(
            f"/api/v1/handoffs/{h['id']}/status",
            json={"status": "in_progress"},
            headers=receiver["headers"],
        )

        resp = await client.patch(
            f"/api/v1/handoffs/{h['id']}/status",
            json={"status": "failed"},
            headers=receiver["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"

    async def test_invalid_transition_rejected(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._create_handoff(client, delegator, receiver)

        # Try to skip to completed from initiated
        resp = await client.patch(
            f"/api/v1/handoffs/{h['id']}/status",
            json={"status": "completed"},
            headers=receiver["headers"],
        )
        assert resp.status_code == 400

    async def test_only_receiver_can_update_status(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._create_handoff(client, delegator, receiver)

        resp = await client.patch(
            f"/api/v1/handoffs/{h['id']}/status",
            json={"status": "in_progress"},
            headers=delegator["headers"],  # delegator, not receiver
        )
        assert resp.status_code == 403


class TestHandoffResults:
    """POST /handoffs/{id}/result"""

    async def _completed_handoff(self, client, delegator, receiver):
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
        return h

    async def test_submit_result(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._completed_handoff(client, delegator, receiver)

        resp = await client.post(
            f"/api/v1/handoffs/{h['id']}/result",
            json={"result": {"booking_id": "BK-123", "confirmed": True}},
            headers=receiver["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["result"]["booking_id"] == "BK-123"
        assert resp.json()["status"] == "completed"

    async def test_only_receiver_can_submit_result(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._completed_handoff(client, delegator, receiver)

        resp = await client.post(
            f"/api/v1/handoffs/{h['id']}/result",
            json={"result": {"status": "done"}},
            headers=delegator["headers"],
        )
        assert resp.status_code == 403


class TestHandoffRollback:
    """POST /handoffs/{id}/rollback"""

    async def test_rollback_in_progress_handoff(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()

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

        resp = await client.post(
            f"/api/v1/handoffs/{h['id']}/rollback",
            headers=delegator["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rolled_back"

    async def test_cannot_rollback_completed(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()

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
        await client.patch(
            f"/api/v1/handoffs/{h['id']}/status",
            json={"status": "completed"},
            headers=receiver["headers"],
        )

        resp = await client.post(
            f"/api/v1/handoffs/{h['id']}/rollback",
            headers=delegator["headers"],
        )
        assert resp.status_code == 400


class TestHandoffChain:
    """GET /handoffs/chain/{chain_id}"""

    async def test_chain_returns_all_handoffs(self, client, agent_factory):
        a = await agent_factory.create(name="a")
        b = await agent_factory.create(name="b")
        c = await agent_factory.create(name="c")

        # A → B
        resp = await client.post("/api/v1/handoffs", json={
            "to_agent_id": b["id"],
            "context": {"domain": "test"},
            "chain_position": 0,
        }, headers=a["headers"])
        h1 = resp.json()
        chain_id = h1["chain_id"]

        # B → C (same chain)
        resp = await client.post("/api/v1/handoffs", json={
            "to_agent_id": c["id"],
            "context": {"domain": "test"},
            "chain_id": chain_id,
            "chain_position": 1,
            "parent_handoff_id": h1["id"],
        }, headers=b["headers"])
        assert resp.status_code == 201

        # Query chain as participant A
        resp = await client.get(f"/api/v1/handoffs/chain/{chain_id}", headers=a["headers"])
        assert resp.status_code == 200
        chain = resp.json()
        assert len(chain) == 2
        assert chain[0]["chain_position"] == 0
        assert chain[1]["chain_position"] == 1

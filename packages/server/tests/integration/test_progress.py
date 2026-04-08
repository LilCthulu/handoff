"""Integration tests: Progress updates and checkpoints."""

import uuid

import pytest


class TestProgressUpdates:
    """POST /progress/handoffs/{id}/update"""

    async def _in_progress_handoff(self, client, delegator, receiver):
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

    async def test_report_progress(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._in_progress_handoff(client, delegator, receiver)

        resp = await client.post(
            f"/api/v1/progress/handoffs/{h['id']}/update",
            json={"phase": "processing", "progress": 0.5, "message": "halfway done"},
            headers=receiver["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["progress"] == 0.5
        assert resp.json()["phase"] == "processing"

    async def test_only_receiver_can_report(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._in_progress_handoff(client, delegator, receiver)

        resp = await client.post(
            f"/api/v1/progress/handoffs/{h['id']}/update",
            json={"phase": "processing", "progress": 1.0},
            headers=delegator["headers"],
        )
        assert resp.status_code == 403

    async def test_progress_on_non_in_progress_rejected(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()

        resp = await client.post("/api/v1/handoffs", json={
            "to_agent_id": receiver["id"],
            "context": {"domain": "test"},
        }, headers=delegator["headers"])
        h = resp.json()  # status = initiated, not in_progress

        resp = await client.post(
            f"/api/v1/progress/handoffs/{h['id']}/update",
            json={"phase": "test", "progress": 0.1},
            headers=receiver["headers"],
        )
        assert resp.status_code == 400

    async def test_get_latest_progress(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._in_progress_handoff(client, delegator, receiver)

        await client.post(
            f"/api/v1/progress/handoffs/{h['id']}/update",
            json={"phase": "step-2", "progress": 0.7},
            headers=receiver["headers"],
        )

        resp = await client.get(
            f"/api/v1/progress/handoffs/{h['id']}/latest",
            headers=delegator["headers"],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["phase"] == "step-2"
        assert data["progress"] == 0.7


class TestCheckpoints:
    """POST/GET checkpoints + resume-from-checkpoint."""

    async def _in_progress_handoff(self, client, delegator, receiver):
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

    async def test_save_and_list_checkpoints(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._in_progress_handoff(client, delegator, receiver)

        # Save two checkpoints
        resp = await client.post(
            f"/api/v1/progress/handoffs/{h['id']}/checkpoint",
            json={"phase": "step-1", "state": {"items_processed": 50}},
            headers=receiver["headers"],
        )
        assert resp.status_code == 201
        assert resp.json()["sequence"] == 1

        resp = await client.post(
            f"/api/v1/progress/handoffs/{h['id']}/checkpoint",
            json={"phase": "step-2", "state": {"items_processed": 100}},
            headers=receiver["headers"],
        )
        assert resp.status_code == 201
        assert resp.json()["sequence"] == 2

        # List checkpoints
        resp = await client.get(
            f"/api/v1/progress/handoffs/{h['id']}/checkpoints",
            headers=receiver["headers"],
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_resume_from_checkpoint(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._in_progress_handoff(client, delegator, receiver)

        # Save checkpoint
        await client.post(
            f"/api/v1/progress/handoffs/{h['id']}/checkpoint",
            json={"phase": "step-1", "state": {"progress": 50}},
            headers=receiver["headers"],
        )

        # Fail the handoff
        await client.patch(
            f"/api/v1/handoffs/{h['id']}/status",
            json={"status": "failed"},
            headers=receiver["headers"],
        )

        # Resume from checkpoint
        resp = await client.post(
            f"/api/v1/progress/handoffs/{h['id']}/resume-from-checkpoint",
            params={"checkpoint_sequence": 1},
            headers=delegator["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["parent_handoff_id"] == h["id"]
        assert resp.json()["resumed_from_checkpoint"] == 1

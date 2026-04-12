"""Tests for SDK HandoffSession."""

import asyncio

import pytest

from handoff_sdk.handoff import HandoffSession
from handoff_sdk.types import HandoffStatus


class FakeClient:
    """Minimal mock of HandoffClient for testing session logic."""

    def __init__(self, responses=None):
        self._responses = responses or {}
        self.calls = []

    async def get(self, path, **kwargs):
        self.calls.append(("GET", path))
        return self._responses.get(path, {})

    async def post(self, path, data=None, **kwargs):
        self.calls.append(("POST", path, data))
        return self._responses.get(path, {})

    async def patch(self, path, data=None, **kwargs):
        self.calls.append(("PATCH", path, data))
        return self._responses.get(path, {})


class TestHandoffSession:
    def test_init_from_data(self):
        data = {
            "id": "h-1",
            "from_agent_id": "agent-a",
            "to_agent_id": "agent-b",
            "status": "initiated",
            "context": {"task": "test"},
            "result": None,
            "chain_id": None,
        }
        session = HandoffSession(FakeClient(), data)
        assert session.id == "h-1"
        assert session.from_agent_id == "agent-a"
        assert session.to_agent_id == "agent-b"
        assert session.status == HandoffStatus.INITIATED
        assert session.context == {"task": "test"}
        assert not session._resolved.is_set()

    def test_init_completed_sets_resolved(self):
        data = {
            "id": "h-2",
            "status": "completed",
            "result": {"done": True},
        }
        session = HandoffSession(FakeClient(), data)
        assert session.status == HandoffStatus.COMPLETED
        assert session._resolved.is_set()

    def test_init_failed_sets_resolved(self):
        data = {"id": "h-3", "status": "failed"}
        session = HandoffSession(FakeClient(), data)
        assert session._resolved.is_set()

    def test_init_rolled_back_sets_resolved(self):
        data = {"id": "h-4", "status": "rolled_back"}
        session = HandoffSession(FakeClient(), data)
        assert session._resolved.is_set()

    def test_chain_id_parsing(self):
        data = {"id": "h-5", "status": "initiated", "chain_id": "chain-abc"}
        session = HandoffSession(FakeClient(), data)
        assert session.chain_id == "chain-abc"

    def test_chain_id_none(self):
        data = {"id": "h-6", "status": "initiated"}
        session = HandoffSession(FakeClient(), data)
        assert session.chain_id is None


class TestHandoffSessionActions:
    @pytest.mark.asyncio
    async def test_start(self):
        client = FakeClient({
            "/api/v1/handoffs/h-1/status": {"status": "in_progress"},
        })
        data = {"id": "h-1", "status": "initiated"}
        session = HandoffSession(client, data)

        await session.start()
        assert session.status == HandoffStatus.IN_PROGRESS
        assert ("PATCH", "/api/v1/handoffs/h-1/status", {"status": "in_progress"}) in client.calls

    @pytest.mark.asyncio
    async def test_complete_with_result(self):
        client = FakeClient({
            "/api/v1/handoffs/h-1/result": {"status": "completed", "result": {"data": 42}},
        })
        data = {"id": "h-1", "status": "in_progress"}
        session = HandoffSession(client, data)

        await session.complete_with_result({"data": 42})
        assert session.status == HandoffStatus.COMPLETED
        assert session.result == {"data": 42}
        assert session._resolved.is_set()

    @pytest.mark.asyncio
    async def test_fail(self):
        client = FakeClient({
            "/api/v1/handoffs/h-1/status": {"status": "failed"},
        })
        data = {"id": "h-1", "status": "in_progress"}
        session = HandoffSession(client, data)

        await session.fail()
        assert session.status == HandoffStatus.FAILED
        assert session._resolved.is_set()

    @pytest.mark.asyncio
    async def test_rollback(self):
        client = FakeClient({
            "/api/v1/handoffs/h-1/rollback": {"status": "rolled_back"},
        })
        data = {"id": "h-1", "status": "in_progress"}
        session = HandoffSession(client, data)

        await session.rollback()
        assert session.status == HandoffStatus.ROLLED_BACK
        assert session._resolved.is_set()

    @pytest.mark.asyncio
    async def test_refresh(self):
        client = FakeClient({
            "/api/v1/handoffs/h-1": {"status": "completed", "result": {"x": 1}},
        })
        data = {"id": "h-1", "status": "initiated"}
        session = HandoffSession(client, data)

        await session.refresh()
        assert session.status == HandoffStatus.COMPLETED
        assert session.result == {"x": 1}

    @pytest.mark.asyncio
    async def test_wait_already_resolved(self):
        data = {"id": "h-1", "status": "completed", "result": {"done": True}}
        session = HandoffSession(FakeClient(), data)

        result = await session.wait(timeout=1.0)
        assert result.status == HandoffStatus.COMPLETED
        assert result.result == {"done": True}

    @pytest.mark.asyncio
    async def test_wait_timeout(self):
        data = {"id": "h-1", "status": "initiated"}
        session = HandoffSession(FakeClient(), data)

        with pytest.raises(asyncio.TimeoutError):
            await session.wait(timeout=0.1)

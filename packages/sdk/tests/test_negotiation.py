"""Tests for SDK NegotiationSession."""

import asyncio

import pytest

from handoff_sdk.negotiation import NegotiationSession
from handoff_sdk.types import NegotiationState


class FakeClient:
    """Minimal mock of HandoffClient for testing negotiation logic."""

    def __init__(self, responses=None):
        self._responses = responses or {}
        self.calls = []

    async def get(self, path, **kwargs):
        self.calls.append(("GET", path))
        return self._responses.get(path, {})

    async def post(self, path, data=None, **kwargs):
        self.calls.append(("POST", path, data))
        return self._responses.get(path, {})


class TestNegotiationSession:
    def test_init(self):
        session = NegotiationSession(FakeClient(), "neg-1", "agent-a")
        assert session.id == "neg-1"
        assert session._agent_id == "agent-a"
        assert session.state == NegotiationState.PENDING
        assert session.current_offer is None
        assert session.agreement is None
        assert not session._resolved.is_set()

    @pytest.mark.asyncio
    async def test_offer(self):
        client = FakeClient({
            "/api/v1/negotiations/neg-1/offer": {
                "state": "negotiating",
                "current_offer": {"price": 500},
                "current_round": 1,
            },
        })
        session = NegotiationSession(client, "neg-1", "agent-a")

        result = await session.offer({"price": 500})
        assert session.state == NegotiationState.NEGOTIATING
        assert session.current_offer == {"price": 500}
        assert ("POST", "/api/v1/negotiations/neg-1/offer", {
            "terms": {"price": 500},
            "concessions": [],
            "conditions": [],
        }) in client.calls

    @pytest.mark.asyncio
    async def test_offer_with_concessions(self):
        client = FakeClient({
            "/api/v1/negotiations/neg-1/offer": {"state": "negotiating", "current_round": 2},
        })
        session = NegotiationSession(client, "neg-1", "agent-a")

        await session.offer(
            {"price": 450},
            concessions=["dropped region filter"],
            conditions=["must complete in 24h"],
        )
        call = client.calls[0]
        assert call[2]["concessions"] == ["dropped region filter"]
        assert call[2]["conditions"] == ["must complete in 24h"]

    @pytest.mark.asyncio
    async def test_accept(self):
        client = FakeClient({
            "/api/v1/negotiations/neg-1/accept": {
                "state": "agreed",
                "agreement": {"price": 450, "sla": "24h"},
            },
        })
        session = NegotiationSession(client, "neg-1", "agent-a")

        await session.accept()
        assert session.state == NegotiationState.AGREED
        assert session.agreement == {"price": 450, "sla": "24h"}
        assert session._resolved.is_set()

    @pytest.mark.asyncio
    async def test_reject(self):
        client = FakeClient({
            "/api/v1/negotiations/neg-1/reject": {"state": "rejected"},
        })
        session = NegotiationSession(client, "neg-1", "agent-a")

        await session.reject(reason="too expensive")
        assert session.state == NegotiationState.REJECTED
        assert session._resolved.is_set()

    @pytest.mark.asyncio
    async def test_reject_without_reason(self):
        client = FakeClient({
            "/api/v1/negotiations/neg-1/reject": {"state": "rejected"},
        })
        session = NegotiationSession(client, "neg-1", "agent-a")

        await session.reject()
        assert ("POST", "/api/v1/negotiations/neg-1/reject", None) in client.calls

    @pytest.mark.asyncio
    async def test_refresh(self):
        client = FakeClient({
            "/api/v1/negotiations/neg-1": {
                "state": "agreed",
                "agreement": {"terms": {"price": 400}},
                "current_round": 3,
            },
        })
        session = NegotiationSession(client, "neg-1", "agent-a")

        await session.refresh()
        assert session.state == NegotiationState.AGREED
        assert session.agreement == {"terms": {"price": 400}}

    @pytest.mark.asyncio
    async def test_history(self):
        client = FakeClient({
            "/api/v1/negotiations/neg-1/history": {
                "offer_history": [
                    {"round": 1, "terms": {"price": 500}},
                    {"round": 2, "terms": {"price": 450}},
                ],
            },
        })
        session = NegotiationSession(client, "neg-1", "agent-a")

        history = await session.history()
        assert len(history) == 2
        assert history[0]["round"] == 1


class TestNegotiationWebSocket:
    def test_handle_ws_offer(self):
        session = NegotiationSession(FakeClient(), "neg-1", "agent-a")
        received = []
        session._on_offer = lambda offer: received.append(offer)

        session._handle_ws_offer({
            "offer_id": "o-1",
            "from_agent": "agent-b",
            "round": 1,
            "offer": {"terms": {"price": 500}, "concessions": [], "conditions": []},
        })

        assert len(received) == 1
        assert received[0].terms == {"price": 500}
        assert received[0].round == 1
        assert session.current_offer == {"price": 500}

    def test_handle_ws_accepted(self):
        session = NegotiationSession(FakeClient(), "neg-1", "agent-a")

        session._handle_ws_accepted({
            "agreement": {"price": 450},
        })

        assert session.state == NegotiationState.AGREED
        assert session.agreement == {"price": 450}
        assert session._resolved.is_set()

    def test_handle_ws_rejected(self):
        session = NegotiationSession(FakeClient(), "neg-1", "agent-a")

        session._handle_ws_rejected({})

        assert session.state == NegotiationState.REJECTED
        assert session._resolved.is_set()

    @pytest.mark.asyncio
    async def test_wait_already_resolved(self):
        session = NegotiationSession(FakeClient(), "neg-1", "agent-a")
        session._handle_ws_accepted({"agreement": {"price": 500}})

        result = await session.wait(timeout=1.0)
        assert result.state == NegotiationState.AGREED
        assert result.agreement == {"price": 500}

    @pytest.mark.asyncio
    async def test_wait_timeout(self):
        session = NegotiationSession(FakeClient(), "neg-1", "agent-a")

        with pytest.raises(asyncio.TimeoutError):
            await session.wait(timeout=0.1)

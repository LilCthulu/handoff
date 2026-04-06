"""Tests for SDK type definitions."""

from handoff_sdk.types import (
    AgentProfile,
    Capability,
    HandoffResult,
    HandoffStatus,
    NegotiationResult,
    NegotiationState,
    Offer,
)


class TestNegotiationState:
    def test_all_states(self):
        states = [s.value for s in NegotiationState]
        assert "created" in states
        assert "pending" in states
        assert "negotiating" in states
        assert "agreed" in states
        assert "executing" in states
        assert "completed" in states
        assert "rejected" in states
        assert "failed" in states

    def test_string_enum(self):
        assert NegotiationState.AGREED == "agreed"
        assert str(NegotiationState.FAILED) == "NegotiationState.FAILED"


class TestHandoffStatus:
    def test_all_statuses(self):
        statuses = [s.value for s in HandoffStatus]
        assert "initiated" in statuses
        assert "in_progress" in statuses
        assert "completed" in statuses
        assert "failed" in statuses
        assert "rolled_back" in statuses


class TestCapability:
    def test_create(self):
        cap = Capability(domain="hotels", actions=["book", "cancel"])
        assert cap.domain == "hotels"
        assert cap.actions == ["book", "cancel"]
        assert cap.constraints == {}

    def test_to_dict(self):
        cap = Capability(domain="hotels", actions=["book"], constraints={"regions": ["asia"]})
        d = cap.to_dict()
        assert d == {"domain": "hotels", "actions": ["book"], "constraints": {"regions": ["asia"]}}


class TestAgentProfile:
    def test_create(self):
        profile = AgentProfile(
            id="test-id", name="test-agent", description="A test agent",
            owner_id="owner", public_key="key", capabilities=[],
            trust_score=0.5, max_authority={}, status="active",
            metadata={}, created_at="2025-01-01", updated_at="2025-01-01",
        )
        assert profile.name == "test-agent"
        assert profile.trust_score == 0.5
        assert profile.last_seen_at is None


class TestOffer:
    def test_create(self):
        offer = Offer(
            id="offer-1", negotiation_id="neg-1", from_agent="agent-1",
            round=1, terms={"price": 500},
        )
        assert offer.round == 1
        assert offer.terms == {"price": 500}
        assert offer.concessions == []
        assert offer.conditions == []


class TestNegotiationResult:
    def test_create(self):
        result = NegotiationResult(
            id="neg-1", state=NegotiationState.AGREED,
            agreement={"terms": {"price": 450}},
            offer_history=[{"round": 1}, {"round": 2}],
            current_round=2,
        )
        assert result.state == NegotiationState.AGREED
        assert result.agreement["terms"]["price"] == 450


class TestHandoffResult:
    def test_create(self):
        result = HandoffResult(
            id="h-1", status=HandoffStatus.COMPLETED,
            result={"confirmation": "ABC123"}, chain_id=None,
        )
        assert result.status == HandoffStatus.COMPLETED

"""Tests for turn enforcement in the negotiation engine."""

import uuid

import pytest

from app.core.negotiation_engine import (
    NegotiationError,
    initiate,
    submit_offer,
)


@pytest.fixture
def active_negotiation():
    """A negotiation in pending state, ready for offers."""
    neg = {
        "id": str(uuid.uuid4()),
        "state": "created",
        "current_offer": None,
        "offer_history": [],
        "agreement": None,
        "current_round": 0,
        "max_rounds": 10,
        "timeout_at": None,
        "metadata": {},
    }
    initiate(neg)
    return neg


class TestTurnEnforcement:
    def test_first_offer_allowed(self, active_negotiation):
        agent_a = str(uuid.uuid4())
        submit_offer(active_negotiation, from_agent_id=agent_a, terms={"price": 300})
        assert active_negotiation["current_round"] == 1

    def test_alternating_offers_allowed(self, active_negotiation):
        agent_a = str(uuid.uuid4())
        agent_b = str(uuid.uuid4())

        submit_offer(active_negotiation, from_agent_id=agent_a, terms={"price": 300})
        submit_offer(active_negotiation, from_agent_id=agent_b, terms={"price": 500})
        submit_offer(active_negotiation, from_agent_id=agent_a, terms={"price": 400})
        assert active_negotiation["current_round"] == 3

    def test_consecutive_offers_from_same_agent_rejected(self, active_negotiation):
        agent_a = str(uuid.uuid4())

        submit_offer(active_negotiation, from_agent_id=agent_a, terms={"price": 300})
        with pytest.raises(NegotiationError, match="consecutive offers"):
            submit_offer(active_negotiation, from_agent_id=agent_a, terms={"price": 350})

    def test_consecutive_offers_dont_advance_round(self, active_negotiation):
        agent_a = str(uuid.uuid4())

        submit_offer(active_negotiation, from_agent_id=agent_a, terms={"price": 300})
        try:
            submit_offer(active_negotiation, from_agent_id=agent_a, terms={"price": 350})
        except NegotiationError:
            pass
        assert active_negotiation["current_round"] == 1  # Round didn't advance

    def test_after_rejection_of_consecutive_other_agent_can_still_offer(self, active_negotiation):
        agent_a = str(uuid.uuid4())
        agent_b = str(uuid.uuid4())

        submit_offer(active_negotiation, from_agent_id=agent_a, terms={"price": 300})
        try:
            submit_offer(active_negotiation, from_agent_id=agent_a, terms={"price": 350})
        except NegotiationError:
            pass
        # Agent B should still be able to counter
        submit_offer(active_negotiation, from_agent_id=agent_b, terms={"price": 500})
        assert active_negotiation["current_round"] == 2

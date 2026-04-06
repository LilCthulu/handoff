"""Tests for the negotiation state machine."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.negotiation_engine import (
    NegotiationError,
    NegotiationState,
    accept_offer,
    begin_execution,
    complete,
    fail,
    initiate,
    reject_negotiation,
    submit_offer,
    validate_transition,
)


class TestValidateTransition:
    def test_valid_transitions(self):
        valid = [
            ("created", "pending"),
            ("pending", "negotiating"),
            ("pending", "rejected"),
            ("negotiating", "negotiating"),
            ("negotiating", "agreed"),
            ("negotiating", "rejected"),
            ("agreed", "executing"),
            ("executing", "completed"),
        ]
        for current, next_state in valid:
            validate_transition(current, next_state)  # Should not raise

    def test_invalid_transitions(self):
        invalid = [
            ("created", "negotiating"),
            ("created", "completed"),
            ("pending", "completed"),
            ("agreed", "rejected"),
            ("completed", "failed"),
            ("rejected", "pending"),
        ]
        for current, next_state in invalid:
            with pytest.raises(NegotiationError):
                validate_transition(current, next_state)

    def test_unknown_state(self):
        with pytest.raises(NegotiationError, match="Unknown state"):
            validate_transition("nonexistent", "pending")

    def test_terminal_states_have_no_transitions(self):
        for state in NegotiationState.TERMINAL:
            assert len(set()) == 0  # TRANSITIONS[state] is empty set
            with pytest.raises(NegotiationError):
                validate_transition(state, "pending")


class TestInitiate:
    def test_created_to_pending(self, negotiation_dict):
        result = initiate(negotiation_dict)
        assert result["state"] == "pending"
        assert result["updated_at"] is not None

    def test_initiate_from_wrong_state_fails(self, negotiation_dict):
        negotiation_dict["state"] = "negotiating"
        with pytest.raises(NegotiationError):
            initiate(negotiation_dict)


class TestSubmitOffer:
    def test_first_offer_from_pending(self, negotiation_dict):
        negotiation_dict["state"] = "pending"
        result = submit_offer(
            negotiation_dict,
            from_agent_id=str(uuid.uuid4()),
            terms={"price": 500},
        )
        assert result["state"] == "negotiating"
        assert result["current_round"] == 1
        assert result["current_offer"]["terms"]["price"] == 500
        assert len(result["offer_history"]) == 1

    def test_counteroffer_in_negotiating(self, negotiation_dict):
        negotiation_dict["state"] = "negotiating"
        negotiation_dict["current_round"] = 1
        result = submit_offer(
            negotiation_dict,
            from_agent_id=str(uuid.uuid4()),
            terms={"price": 450},
            concessions=["Added breakfast"],
        )
        assert result["state"] == "negotiating"
        assert result["current_round"] == 2
        assert result["current_offer"]["concessions"] == ["Added breakfast"]

    def test_max_rounds_exceeded(self, negotiation_dict):
        negotiation_dict["state"] = "negotiating"
        negotiation_dict["current_round"] = 10
        negotiation_dict["max_rounds"] = 10
        with pytest.raises(NegotiationError, match="Max rounds"):
            submit_offer(negotiation_dict, from_agent_id="x", terms={"price": 400})

    def test_timeout_fails_negotiation(self, negotiation_dict):
        negotiation_dict["state"] = "pending"
        negotiation_dict["timeout_at"] = datetime.now(timezone.utc) - timedelta(hours=1)
        with pytest.raises(NegotiationError, match="timed out"):
            submit_offer(negotiation_dict, from_agent_id="x", terms={"price": 400})
        assert negotiation_dict["state"] == "failed"

    def test_offer_from_invalid_state(self, negotiation_dict):
        negotiation_dict["state"] = "agreed"
        with pytest.raises(NegotiationError, match="Cannot submit offer"):
            submit_offer(negotiation_dict, from_agent_id="x", terms={})

    def test_offer_includes_conditions(self, negotiation_dict):
        negotiation_dict["state"] = "pending"
        result = submit_offer(
            negotiation_dict,
            from_agent_id="agent-1",
            terms={"price": 300},
            conditions=["Payment within 24h"],
        )
        assert result["current_offer"]["conditions"] == ["Payment within 24h"]


class TestAcceptOffer:
    def test_accept_from_negotiating(self, negotiation_dict):
        negotiation_dict["state"] = "negotiating"
        negotiation_dict["current_offer"] = {"terms": {"price": 450}, "from_agent": "a1"}
        result = accept_offer(negotiation_dict)
        assert result["state"] == "agreed"
        assert result["agreement"] == negotiation_dict["current_offer"]

    def test_accept_without_offer_fails(self, negotiation_dict):
        negotiation_dict["state"] = "negotiating"
        negotiation_dict["current_offer"] = None
        with pytest.raises(NegotiationError, match="No current offer"):
            accept_offer(negotiation_dict)

    def test_accept_from_wrong_state(self, negotiation_dict):
        negotiation_dict["state"] = "pending"
        negotiation_dict["current_offer"] = {"terms": {}}
        with pytest.raises(NegotiationError):
            accept_offer(negotiation_dict)


class TestReject:
    def test_reject_from_pending(self, negotiation_dict):
        negotiation_dict["state"] = "pending"
        result = reject_negotiation(negotiation_dict, reason="Too expensive")
        assert result["state"] == "rejected"
        assert result["metadata"]["rejection_reason"] == "Too expensive"
        assert result["completed_at"] is not None

    def test_reject_from_negotiating(self, negotiation_dict):
        negotiation_dict["state"] = "negotiating"
        result = reject_negotiation(negotiation_dict)
        assert result["state"] == "rejected"

    def test_reject_from_terminal_state_fails(self, negotiation_dict):
        negotiation_dict["state"] = "completed"
        with pytest.raises(NegotiationError, match="terminal"):
            reject_negotiation(negotiation_dict)


class TestExecutionFlow:
    def test_agreed_to_executing(self, negotiation_dict):
        negotiation_dict["state"] = "agreed"
        result = begin_execution(negotiation_dict)
        assert result["state"] == "executing"

    def test_executing_to_completed(self, negotiation_dict):
        negotiation_dict["state"] = "executing"
        result = complete(negotiation_dict)
        assert result["state"] == "completed"
        assert result["completed_at"] is not None


class TestFail:
    def test_fail_from_pending(self, negotiation_dict):
        negotiation_dict["state"] = "pending"
        result = fail(negotiation_dict, reason="Network error")
        assert result["state"] == "failed"
        assert result["metadata"]["failure_reason"] == "Network error"

    def test_fail_from_executing(self, negotiation_dict):
        negotiation_dict["state"] = "executing"
        result = fail(negotiation_dict)
        assert result["state"] == "failed"

    def test_fail_from_terminal_raises(self, negotiation_dict):
        negotiation_dict["state"] = "rejected"
        with pytest.raises(NegotiationError, match="terminal"):
            fail(negotiation_dict)


class TestFullNegotiationFlow:
    def test_happy_path(self, negotiation_dict):
        """Complete negotiation: created -> pending -> negotiating -> agreed -> executing -> completed"""
        agent_a = str(uuid.uuid4())
        agent_b = str(uuid.uuid4())

        # 1. Initiate
        initiate(negotiation_dict)
        assert negotiation_dict["state"] == "pending"

        # 2. First offer
        submit_offer(negotiation_dict, from_agent_id=agent_a, terms={"price": 300})
        assert negotiation_dict["state"] == "negotiating"
        assert negotiation_dict["current_round"] == 1

        # 3. Counter-offer
        submit_offer(negotiation_dict, from_agent_id=agent_b, terms={"price": 500})
        assert negotiation_dict["current_round"] == 2

        # 4. Final offer
        submit_offer(negotiation_dict, from_agent_id=agent_a, terms={"price": 400})
        assert negotiation_dict["current_round"] == 3

        # 5. Accept
        accept_offer(negotiation_dict)
        assert negotiation_dict["state"] == "agreed"
        assert negotiation_dict["agreement"]["terms"]["price"] == 400

        # 6. Execute
        begin_execution(negotiation_dict)
        assert negotiation_dict["state"] == "executing"

        # 7. Complete
        complete(negotiation_dict)
        assert negotiation_dict["state"] == "completed"

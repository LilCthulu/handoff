"""Tests for the shared negotiation helpers module."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.core.negotiation_helpers import (
    apply_dict_to_negotiation,
    negotiation_to_dict,
    negotiation_to_response,
)


def _mock_negotiation(**overrides):
    """Create a mock Negotiation ORM object."""
    defaults = {
        "id": "test-id",
        "state": "pending",
        "current_offer": None,
        "offer_history": [],
        "agreement": None,
        "current_round": 0,
        "max_rounds": 10,
        "timeout_at": None,
        "metadata_": {},
        "updated_at": datetime.now(timezone.utc),
        "completed_at": None,
        "initiator_id": "init-1",
        "responder_id": "resp-1",
        "mediator_required": False,
        "intent": {"domain": "hotels", "action": "book"},
        "created_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


class TestNegotiationToDict:
    def test_extracts_all_fields(self):
        n = _mock_negotiation(state="negotiating", current_round=3)
        d = negotiation_to_dict(n)
        assert d["state"] == "negotiating"
        assert d["current_round"] == 3
        assert d["max_rounds"] == 10
        assert isinstance(d["offer_history"], list)

    def test_empty_offer_history_becomes_list(self):
        n = _mock_negotiation(offer_history=None)
        d = negotiation_to_dict(n)
        assert d["offer_history"] == []


class TestApplyDict:
    def test_applies_state_change(self):
        n = _mock_negotiation()
        apply_dict_to_negotiation(n, {
            "state": "agreed",
            "current_offer": {"terms": {"price": 400}},
            "offer_history": [{"round": 1}],
            "agreement": {"terms": {"price": 400}},
            "current_round": 1,
            "updated_at": datetime.now(timezone.utc),
            "completed_at": None,
            "metadata": {"key": "value"},
        })
        assert n.state == "agreed"
        assert n.agreement == {"terms": {"price": 400}}
        assert n.metadata_ == {"key": "value"}


class TestNegotiationToResponse:
    def test_includes_all_response_fields(self):
        n = _mock_negotiation()
        resp = negotiation_to_response(n)
        assert "id" in resp
        assert "state" in resp
        assert "initiator_id" in resp
        assert "responder_id" in resp
        assert "intent" in resp
        assert "created_at" in resp

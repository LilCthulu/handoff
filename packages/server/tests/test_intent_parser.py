"""Tests for intent parsing and validation."""

import pytest

from app.core.intent_parser import (
    IntentValidationError,
    check_authority_for_intent,
    match_intent_to_capabilities,
    parse_intent,
    validate_intent,
)
from app.schemas.intent import Intent


class TestParseIntent:
    def test_parse_valid_intent(self, sample_intent):
        intent = parse_intent(sample_intent)
        assert intent.domain == "hotels"
        assert intent.action == "book_room"
        assert intent.priority == "high"

    def test_parse_wrapped_intent(self, sample_intent):
        wrapped = {"intent": sample_intent}
        intent = parse_intent(wrapped)
        assert intent.domain == "hotels"

    def test_parse_missing_domain_fails(self):
        with pytest.raises(IntentValidationError):
            parse_intent({"type": "request", "action": "test"})

    def test_parse_empty_domain_fails(self):
        with pytest.raises(IntentValidationError, match="domain cannot be empty"):
            parse_intent({"type": "request", "domain": "  ", "action": "test"})

    def test_parse_empty_action_fails(self):
        with pytest.raises(IntentValidationError, match="action cannot be empty"):
            parse_intent({"type": "request", "domain": "hotels", "action": "  "})

    def test_parse_invalid_type_fails(self):
        with pytest.raises(IntentValidationError):
            parse_intent({"type": "invalid", "domain": "hotels", "action": "book"})

    def test_parse_negative_budget_fails(self):
        with pytest.raises(IntentValidationError, match="Budget cannot be negative"):
            parse_intent({
                "type": "request", "domain": "hotels", "action": "book",
                "constraints": {"budget_max": -100},
            })

    def test_parse_overlapping_must_nice_fails(self):
        with pytest.raises(IntentValidationError, match="must_have and nice_to_have"):
            parse_intent({
                "type": "request", "domain": "hotels", "action": "book",
                "constraints": {
                    "must_have": ["wifi", "breakfast"],
                    "nice_to_have": ["breakfast", "pool"],
                },
            })

    def test_parse_minimal_intent(self):
        intent = parse_intent({"type": "query", "domain": "flights", "action": "search"})
        assert intent.priority == "medium"
        assert intent.constraints.budget_max is None


class TestMatchCapabilities:
    def test_matching_capability(self):
        intent = parse_intent({
            "type": "request", "domain": "hotels", "action": "book_room",
        })
        caps = [{"domain": "hotels", "actions": ["book_room", "cancel"]}]
        assert match_intent_to_capabilities(intent, caps) is True

    def test_no_matching_domain(self):
        intent = parse_intent({
            "type": "request", "domain": "hotels", "action": "book_room",
        })
        caps = [{"domain": "flights", "actions": ["book_flight"]}]
        assert match_intent_to_capabilities(intent, caps) is False

    def test_no_matching_action(self):
        intent = parse_intent({
            "type": "request", "domain": "hotels", "action": "book_room",
        })
        caps = [{"domain": "hotels", "actions": ["cancel"]}]
        assert match_intent_to_capabilities(intent, caps) is False

    def test_multiple_capabilities(self):
        intent = parse_intent({
            "type": "request", "domain": "hotels", "action": "book_room",
        })
        caps = [
            {"domain": "flights", "actions": ["book"]},
            {"domain": "hotels", "actions": ["book_room"]},
        ]
        assert match_intent_to_capabilities(intent, caps) is True

    def test_empty_capabilities(self):
        intent = parse_intent({
            "type": "request", "domain": "hotels", "action": "book_room",
        })
        assert match_intent_to_capabilities(intent, []) is False


class TestCheckAuthority:
    def test_no_restrictions(self):
        intent = parse_intent({
            "type": "request", "domain": "hotels", "action": "book",
            "constraints": {"budget_max": 1000},
        })
        violations = check_authority_for_intent(intent, {})
        assert violations == []

    def test_domain_allowed(self):
        intent = parse_intent({
            "type": "request", "domain": "hotels", "action": "book",
        })
        violations = check_authority_for_intent(intent, {"allowed_domains": ["hotels", "flights"]})
        assert violations == []

    def test_domain_not_allowed(self):
        intent = parse_intent({
            "type": "request", "domain": "crypto", "action": "trade",
        })
        violations = check_authority_for_intent(intent, {"allowed_domains": ["hotels"]})
        assert len(violations) == 1
        assert "crypto" in violations[0]

    def test_budget_within_authority(self):
        intent = parse_intent({
            "type": "request", "domain": "hotels", "action": "book",
            "constraints": {"budget_max": 1000},
        })
        violations = check_authority_for_intent(intent, {"max_spend": 5000})
        assert violations == []

    def test_budget_exceeds_authority(self):
        intent = parse_intent({
            "type": "request", "domain": "hotels", "action": "book",
            "constraints": {"budget_max": 10000},
        })
        violations = check_authority_for_intent(intent, {"max_spend": 5000})
        assert len(violations) == 1
        assert "exceeds" in violations[0]

"""Tests for the mediation engine."""

import pytest

from app.core.mediator import MediationError, analyze_gap, should_mediate, suggest_compromise


class TestAnalyzeGap:
    def test_budget_gap_over(self):
        result = analyze_gap(
            intent_constraints={"budget_max": 2000},
            latest_offer_terms={"total_price": 2500},
        )
        assert result["budget_gap"]["over_budget"] is True
        assert result["budget_gap"]["amount"] == 500
        assert len(result["suggestions"]) >= 1
        price_suggestion = next(s for s in result["suggestions"] if s["type"] == "price_compromise")
        assert price_suggestion["suggested_price"] == 2250.0  # midpoint

    def test_budget_gap_under(self):
        result = analyze_gap(
            intent_constraints={"budget_max": 3000},
            latest_offer_terms={"total_price": 2000},
        )
        assert result["budget_gap"]["over_budget"] is False

    def test_unmet_must_haves(self):
        result = analyze_gap(
            intent_constraints={"must_have": ["wifi", "breakfast"]},
            latest_offer_terms={"includes": ["wifi"]},
        )
        assert "breakfast" in result["unmet_must_haves"]
        assert any(s["type"] == "must_have_gap" for s in result["suggestions"])

    def test_unmet_nice_to_haves(self):
        result = analyze_gap(
            intent_constraints={"nice_to_have": ["pool", "gym"]},
            latest_offer_terms={"includes": ["gym"]},
        )
        assert "pool" in result["unmet_nice_to_haves"]

    def test_trade_off_suggestion_when_over_budget(self):
        result = analyze_gap(
            intent_constraints={
                "budget_max": 2000,
                "nice_to_have": ["pool"],
            },
            latest_offer_terms={"total_price": 2500, "includes": []},
        )
        trade_offs = [s for s in result["suggestions"] if s["type"] == "trade_off"]
        assert len(trade_offs) >= 1

    def test_no_constraints_no_gaps(self):
        result = analyze_gap(
            intent_constraints={},
            latest_offer_terms={"total_price": 1000, "includes": ["wifi"]},
        )
        assert result["budget_gap"] is None
        assert result["unmet_must_haves"] == []


class TestSuggestCompromise:
    def test_basic_compromise(self):
        history = [
            {"from_agent": "a1", "round": 1, "terms": {"total_price": 1500}},
            {"from_agent": "a2", "round": 2, "terms": {"total_price": 2500}},
        ]
        result = suggest_compromise(
            intent_constraints={"budget_max": 2000},
            offer_history=history,
        )
        assert "suggested_terms" in result
        # Should be midpoint of last price and budget: (2500 + 2000) / 2 = 2250
        assert result["suggested_terms"]["total_price"] == 2250.0
        assert result["confidence"] > 0

    def test_empty_history_raises(self):
        with pytest.raises(MediationError, match="without offer history"):
            suggest_compromise(intent_constraints={}, offer_history=[])

    def test_includes_must_haves(self):
        history = [
            {"from_agent": "a1", "round": 1, "terms": {"total_price": 1000, "includes": ["wifi"]}},
        ]
        result = suggest_compromise(
            intent_constraints={"must_have": ["breakfast"]},
            offer_history=history,
        )
        includes = result["suggested_terms"]["includes"]
        assert "breakfast" in includes
        assert "wifi" in includes

    def test_confidence_high_when_converging(self):
        history = [
            {"from_agent": "a1", "round": 1, "terms": {"total_price": 2000}},
            {"from_agent": "a2", "round": 2, "terms": {"total_price": 2100}},
        ]
        result = suggest_compromise(
            intent_constraints={"budget_max": 2000},
            offer_history=history,
        )
        assert result["confidence"] > 0.9

    def test_confidence_low_when_diverging(self):
        history = [
            {"from_agent": "a1", "round": 1, "terms": {"total_price": 1000}},
            {"from_agent": "a2", "round": 2, "terms": {"total_price": 3000}},
        ]
        result = suggest_compromise(
            intent_constraints={},
            offer_history=history,
        )
        assert result["confidence"] < 0.5


class TestShouldMediate:
    def test_too_early_no_mediation(self):
        assert should_mediate(current_round=2, max_rounds=10, offer_history=[{}, {}]) is False

    def test_insufficient_history(self):
        assert should_mediate(current_round=6, max_rounds=10, offer_history=[{}]) is False

    def test_price_stalling_triggers(self):
        history = [
            {"terms": {"total_price": 2000}},
            {"terms": {"total_price": 2010}},  # < 5% movement
        ]
        assert should_mediate(current_round=6, max_rounds=10, offer_history=history) is True

    def test_rounds_running_out_triggers(self):
        history = [
            {"terms": {"total_price": 1000}},
            {"terms": {"total_price": 2000}},
        ]
        assert should_mediate(current_round=8, max_rounds=10, offer_history=history) is True

    def test_healthy_negotiation_no_mediation(self):
        history = [
            {"terms": {"total_price": 1000}},
            {"terms": {"total_price": 1500}},  # 50% movement, healthy
        ]
        assert should_mediate(current_round=6, max_rounds=10, offer_history=history) is False

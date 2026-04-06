"""Tests for the trust scoring algorithm."""

from datetime import datetime, timedelta, timezone

from app.core.trust_scorer import (
    DECAY_RATE,
    DECAY_THRESHOLD_DAYS,
    DEFAULT_TRUST,
    WEIGHTS,
    compute_from_stats,
    compute_trust_score,
)


class TestComputeTrustScore:
    def test_perfect_agent_scores_high(self):
        score = compute_trust_score(
            negotiation_completion_rate=1.0,
            handoff_success_rate=1.0,
            response_time_score=1.0,
            dispute_rate=0.0,
            longevity_days=365,
            peer_rating=1.0,
        )
        assert score == 1.0

    def test_worst_agent_scores_low(self):
        score = compute_trust_score(
            negotiation_completion_rate=0.0,
            handoff_success_rate=0.0,
            response_time_score=0.0,
            dispute_rate=1.0,
            longevity_days=0,
            peer_rating=0.0,
        )
        assert score == 0.0

    def test_default_trust_for_new_agent(self):
        # New agent with no history — everything at default
        score = compute_trust_score(
            negotiation_completion_rate=0.5,
            handoff_success_rate=0.5,
            response_time_score=0.5,
            dispute_rate=0.0,
            longevity_days=0,
            peer_rating=None,  # No ratings yet
        )
        assert 0.3 < score < 0.7  # Should be near middle range

    def test_weights_sum_to_one(self):
        total = sum(WEIGHTS.values())
        assert abs(total - 1.0) < 1e-10

    def test_score_clamped_between_0_and_1(self):
        # Even with extreme values, score should be in [0, 1]
        score = compute_trust_score(
            negotiation_completion_rate=1.0,
            handoff_success_rate=1.0,
            response_time_score=1.0,
            dispute_rate=0.0,
            longevity_days=10000,
            peer_rating=1.0,
        )
        assert 0.0 <= score <= 1.0

    def test_handoff_rate_has_highest_weight(self):
        # Handoff success rate has 0.30 weight — highest
        base_args = dict(
            negotiation_completion_rate=0.5,
            response_time_score=0.5,
            dispute_rate=0.0,
            longevity_days=180,
            peer_rating=0.5,
        )
        high_handoff = compute_trust_score(handoff_success_rate=1.0, **base_args)
        low_handoff = compute_trust_score(handoff_success_rate=0.0, **base_args)
        assert high_handoff - low_handoff == pytest.approx(WEIGHTS["handoff_success_rate"], abs=0.01)


class TestInactivityDecay:
    def test_no_decay_within_threshold(self):
        recent = datetime.now(timezone.utc) - timedelta(days=DECAY_THRESHOLD_DAYS - 1)
        score_active = compute_trust_score(
            negotiation_completion_rate=0.8,
            handoff_success_rate=0.8,
            response_time_score=0.8,
            dispute_rate=0.1,
            longevity_days=180,
            last_active=recent,
        )
        score_no_time = compute_trust_score(
            negotiation_completion_rate=0.8,
            handoff_success_rate=0.8,
            response_time_score=0.8,
            dispute_rate=0.1,
            longevity_days=180,
        )
        assert score_active == pytest.approx(score_no_time, abs=0.01)

    def test_decay_after_threshold(self):
        long_ago = datetime.now(timezone.utc) - timedelta(days=DECAY_THRESHOLD_DAYS + 60)
        score_decayed = compute_trust_score(
            negotiation_completion_rate=0.9,
            handoff_success_rate=0.9,
            response_time_score=0.9,
            dispute_rate=0.0,
            longevity_days=365,
            last_active=long_ago,
        )
        score_active = compute_trust_score(
            negotiation_completion_rate=0.9,
            handoff_success_rate=0.9,
            response_time_score=0.9,
            dispute_rate=0.0,
            longevity_days=365,
        )
        # Decayed score should be closer to 0.5
        assert score_decayed < score_active
        assert score_decayed > DEFAULT_TRUST * 0.5  # Shouldn't collapse to zero

    def test_decay_pulls_toward_default(self):
        very_long_ago = datetime.now(timezone.utc) - timedelta(days=DECAY_THRESHOLD_DAYS + 200)
        score = compute_trust_score(
            negotiation_completion_rate=1.0,
            handoff_success_rate=1.0,
            response_time_score=1.0,
            dispute_rate=0.0,
            longevity_days=365,
            peer_rating=1.0,
            last_active=very_long_ago,
        )
        # With heavy decay, should approach DEFAULT_TRUST (0.5)
        assert abs(score - DEFAULT_TRUST) < abs(1.0 - DEFAULT_TRUST)


class TestComputeFromStats:
    def test_new_agent_gets_default(self):
        score = compute_from_stats(
            total_negotiations=0,
            completed_negotiations=0,
            total_handoffs=0,
            successful_handoffs=0,
            total_interactions=0,
            disputed_interactions=0,
            avg_response_seconds=0.0,
            registration_date=datetime.now(timezone.utc),
        )
        assert 0.3 < score < 0.7

    def test_experienced_agent(self):
        score = compute_from_stats(
            total_negotiations=100,
            completed_negotiations=90,
            total_handoffs=50,
            successful_handoffs=48,
            total_interactions=150,
            disputed_interactions=3,
            avg_response_seconds=2.0,
            registration_date=datetime.now(timezone.utc) - timedelta(days=365),
            peer_rating=0.85,
        )
        assert score > 0.75

    def test_response_time_scoring(self):
        fast = compute_from_stats(
            total_negotiations=10, completed_negotiations=8,
            total_handoffs=10, successful_handoffs=8,
            total_interactions=20, disputed_interactions=0,
            avg_response_seconds=1.0,
            registration_date=datetime.now(timezone.utc) - timedelta(days=90),
        )
        slow = compute_from_stats(
            total_negotiations=10, completed_negotiations=8,
            total_handoffs=10, successful_handoffs=8,
            total_interactions=20, disputed_interactions=0,
            avg_response_seconds=250.0,
            registration_date=datetime.now(timezone.utc) - timedelta(days=90),
        )
        assert fast > slow


# Import pytest for approx
import pytest

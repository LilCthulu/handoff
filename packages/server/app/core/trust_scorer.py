"""Trust scoring — reputation distilled into a single number.

Trust is earned through action and eroded through inaction.
New agents start at 0.5 — neither trusted nor distrusted.
Every interaction moves the needle. Every silence lets it decay.
"""

from datetime import datetime, timedelta, timezone

import structlog

logger = structlog.get_logger()

# Factor weights — these define what matters
WEIGHTS = {
    "negotiation_completion_rate": 0.25,
    "handoff_success_rate": 0.30,
    "response_time_score": 0.15,
    "dispute_rate": 0.15,
    "longevity_score": 0.10,
    "peer_ratings": 0.05,
}

DEFAULT_TRUST = 0.5
DECAY_THRESHOLD_DAYS = 30
DECAY_RATE = 0.01  # per day of inactivity beyond threshold


def compute_trust_score(
    negotiation_completion_rate: float,
    handoff_success_rate: float,
    response_time_score: float,
    dispute_rate: float,
    longevity_days: int,
    peer_rating: float | None = None,
    last_active: datetime | None = None,
) -> float:
    """Compute an agent's trust score from interaction history.

    All input rates are expected as floats between 0.0 and 1.0.

    Args:
        negotiation_completion_rate: Fraction of negotiations that reached agreement.
        handoff_success_rate: Fraction of handoffs completed successfully.
        response_time_score: Normalized responsiveness (1.0 = instant, 0.0 = timeout).
        dispute_rate: Fraction of interactions with disputes (inverted internally).
        longevity_days: Days since agent registration.
        peer_rating: Average peer rating (0.0 to 1.0), or None if no ratings.
        last_active: When the agent was last active, for decay calculation.

    Returns:
        Trust score between 0.0 and 1.0.
    """
    # Normalize longevity: 0 days = 0.0, 365+ days = 1.0
    longevity_score = min(longevity_days / 365.0, 1.0)

    # Invert dispute rate: 0 disputes = 1.0, all disputes = 0.0
    dispute_score = 1.0 - dispute_rate

    # Use default if no peer ratings
    peer_score = peer_rating if peer_rating is not None else DEFAULT_TRUST

    # Weighted sum
    raw_score = (
        WEIGHTS["negotiation_completion_rate"] * negotiation_completion_rate
        + WEIGHTS["handoff_success_rate"] * handoff_success_rate
        + WEIGHTS["response_time_score"] * response_time_score
        + WEIGHTS["dispute_rate"] * dispute_score
        + WEIGHTS["longevity_score"] * longevity_score
        + WEIGHTS["peer_ratings"] * peer_score
    )

    # Apply inactivity decay
    if last_active:
        days_inactive = (datetime.now(timezone.utc) - last_active).days
        if days_inactive > DECAY_THRESHOLD_DAYS:
            decay_days = days_inactive - DECAY_THRESHOLD_DAYS
            decay = decay_days * DECAY_RATE
            # Decay pulls score toward 0.5
            raw_score = raw_score + (DEFAULT_TRUST - raw_score) * min(decay, 1.0)

    # Clamp to [0.0, 1.0]
    final = max(0.0, min(1.0, raw_score))

    logger.debug(
        "trust_score_computed",
        raw=raw_score,
        final=final,
        negotiation_rate=negotiation_completion_rate,
        handoff_rate=handoff_success_rate,
    )
    return final


def compute_from_stats(
    total_negotiations: int,
    completed_negotiations: int,
    total_handoffs: int,
    successful_handoffs: int,
    total_interactions: int,
    disputed_interactions: int,
    avg_response_seconds: float,
    registration_date: datetime,
    last_active: datetime | None = None,
    peer_rating: float | None = None,
) -> float:
    """Convenience wrapper that computes rates from raw counts.

    Handles zero-division gracefully — new agents with no history
    get the benefit of the doubt at 0.5.
    """
    neg_rate = completed_negotiations / total_negotiations if total_negotiations > 0 else DEFAULT_TRUST
    handoff_rate = successful_handoffs / total_handoffs if total_handoffs > 0 else DEFAULT_TRUST
    dispute_rate = disputed_interactions / total_interactions if total_interactions > 0 else 0.0

    # Response time score: under 5s = 1.0, over 300s = 0.0
    response_score = max(0.0, min(1.0, 1.0 - (avg_response_seconds / 300.0)))

    longevity = (datetime.now(timezone.utc) - registration_date).days

    return compute_trust_score(
        negotiation_completion_rate=neg_rate,
        handoff_success_rate=handoff_rate,
        response_time_score=response_score,
        dispute_rate=dispute_rate,
        longevity_days=longevity,
        peer_rating=peer_rating,
        last_active=last_active,
    )

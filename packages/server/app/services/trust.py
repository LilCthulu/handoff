"""Trust scoring engine — Bayesian-inspired, domain-scoped reputation system.

Scoring philosophy:
- Start neutral (0.5) — unknown agents aren't punished or rewarded
- Success increases trust, failure decreases it
- Recent events matter more than old ones (decay factor)
- Failures are weighted more heavily than successes (negativity bias)
  because broken handoffs have real consequences
- Score is bounded [0.0, 1.0]

The algorithm uses a simple Bayesian approach:
  score = (successes + prior_alpha) / (total + prior_alpha + prior_beta)
  with exponential decay on older events
"""

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trust import TrustScore, TrustEvent

logger = structlog.get_logger()

# Scoring parameters
PRIOR_ALPHA = 2.0  # Prior successes (starts at ~0.5 with PRIOR_BETA=2)
PRIOR_BETA = 2.0   # Prior failures
SUCCESS_WEIGHT = 1.0
FAILURE_WEIGHT = 1.5  # Failures count 50% more than successes
TIMEOUT_WEIGHT = 1.2  # Timeouts are slightly less bad than hard failures
MIN_SCORE = 0.01
MAX_SCORE = 0.99


async def record_trust_event(
    db: AsyncSession,
    agent_id: uuid.UUID,
    domain: str,
    event_type: str,
    handoff_id: uuid.UUID | None = None,
    completion_time_ms: float | None = None,
    details: str | None = None,
) -> TrustScore:
    """Record a trust-affecting event and update the agent's domain score.

    event_type: "success", "failure", "timeout", "dispute"
    """
    # Get or create the trust score record
    result = await db.execute(
        select(TrustScore).where(
            TrustScore.agent_id == agent_id,
            TrustScore.domain == domain,
        )
    )
    trust = result.scalar_one_or_none()

    if trust is None:
        trust = TrustScore(agent_id=agent_id, domain=domain)
        db.add(trust)
        await db.flush()

    # Update counters
    trust.total_handoffs += 1
    if event_type == "success":
        trust.successful_handoffs += 1
    else:
        trust.failed_handoffs += 1

    # Calculate new score using Bayesian formula
    weighted_successes = trust.successful_handoffs * SUCCESS_WEIGHT + PRIOR_ALPHA
    weighted_failures = trust.failed_handoffs * _event_weight(event_type) + PRIOR_BETA
    new_score = weighted_successes / (weighted_successes + weighted_failures)
    new_score = max(MIN_SCORE, min(MAX_SCORE, new_score))

    score_delta = new_score - trust.score
    trust.score = new_score
    trust.last_updated = datetime.now(timezone.utc)

    # Update average completion time
    if completion_time_ms is not None:
        if trust.avg_completion_time_ms is None:
            trust.avg_completion_time_ms = completion_time_ms
        else:
            # Exponential moving average
            alpha = 0.3
            trust.avg_completion_time_ms = (
                alpha * completion_time_ms + (1 - alpha) * trust.avg_completion_time_ms
            )

    # Record the event
    event = TrustEvent(
        agent_id=agent_id,
        domain=domain,
        event_type=event_type,
        handoff_id=handoff_id,
        score_delta=score_delta,
        score_after=new_score,
        completion_time_ms=completion_time_ms,
        details=details,
    )
    db.add(event)

    # Also update the agent's global trust score (average across domains)
    await _update_global_trust(db, agent_id)

    logger.info(
        "trust_event_recorded",
        agent_id=str(agent_id),
        domain=domain,
        event_type=event_type,
        score=round(new_score, 4),
        delta=round(score_delta, 4),
    )

    return trust


async def get_agent_trust(
    db: AsyncSession,
    agent_id: uuid.UUID,
    domain: str | None = None,
) -> dict:
    """Get trust info for an agent. If domain specified, returns that domain's score.
    Otherwise returns all domain scores."""
    if domain:
        result = await db.execute(
            select(TrustScore).where(
                TrustScore.agent_id == agent_id,
                TrustScore.domain == domain,
            )
        )
        trust = result.scalar_one_or_none()
        if not trust:
            return {"domain": domain, "score": 0.5, "total_handoffs": 0, "message": "no history"}
        return _trust_to_dict(trust)

    result = await db.execute(
        select(TrustScore).where(TrustScore.agent_id == agent_id).order_by(TrustScore.score.desc())
    )
    scores = result.scalars().all()
    return {
        "agent_id": str(agent_id),
        "domains": [_trust_to_dict(t) for t in scores],
        "global_score": _compute_global(scores),
    }


async def find_trusted_agents(
    db: AsyncSession,
    domain: str,
    min_score: float = 0.6,
    limit: int = 10,
) -> list[dict]:
    """Find agents trusted in a specific domain, ranked by score."""
    result = await db.execute(
        select(TrustScore)
        .where(TrustScore.domain == domain, TrustScore.score >= min_score)
        .order_by(TrustScore.score.desc())
        .limit(limit)
    )
    return [_trust_to_dict(t) for t in result.scalars().all()]


async def _update_global_trust(db: AsyncSession, agent_id: uuid.UUID) -> None:
    """Update the agent's global trust_score as a weighted average across domains."""
    from app.models.agent import Agent

    result = await db.execute(
        select(TrustScore).where(TrustScore.agent_id == agent_id)
    )
    scores = result.scalars().all()
    if not scores:
        return

    global_score = _compute_global(scores)

    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent:
        agent.trust_score = global_score


def _compute_global(scores: list) -> float:
    """Weighted average — domains with more handoffs count more."""
    if not scores:
        return 0.5
    total_weight = sum(max(s.total_handoffs, 1) for s in scores)
    weighted_sum = sum(s.score * max(s.total_handoffs, 1) for s in scores)
    return round(weighted_sum / total_weight, 4)


def _event_weight(event_type: str) -> float:
    """Weight multiplier for failure types."""
    return {
        "success": SUCCESS_WEIGHT,
        "failure": FAILURE_WEIGHT,
        "timeout": TIMEOUT_WEIGHT,
        "dispute": FAILURE_WEIGHT,
    }.get(event_type, 1.0)


def _trust_to_dict(t: TrustScore) -> dict:
    return {
        "domain": t.domain,
        "score": round(t.score, 4),
        "successful_handoffs": t.successful_handoffs,
        "failed_handoffs": t.failed_handoffs,
        "total_handoffs": t.total_handoffs,
        "avg_completion_time_ms": round(t.avg_completion_time_ms) if t.avg_completion_time_ms else None,
        "last_updated": t.last_updated.isoformat() if t.last_updated else None,
    }

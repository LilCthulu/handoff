"""Domain-scoped trust scores — agents earn trust per capability, not globally.

An agent might be excellent at data-analysis (0.95) but untested at
image-generation (0.5). A single float can't capture this. Domain-scoped
trust lets the network make better delegation decisions.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._compat import GUID


def _utcnow():
    return datetime.now(timezone.utc)


class TrustScore(Base):
    """Per-agent, per-domain trust score with history."""

    __tablename__ = "trust_scores"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    successful_handoffs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_handoffs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_handoffs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_completion_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_updated: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)

    __table_args__ = (
        Index("idx_trust_agent_domain", "agent_id", "domain", unique=True),
        Index("idx_trust_domain_score", "domain", "score"),
    )

    def __repr__(self) -> str:
        return f"<TrustScore agent={self.agent_id} domain={self.domain} score={self.score:.2f}>"


class TrustEvent(Base):
    """Individual trust-affecting event — the audit trail for score changes.

    Every completed or failed handoff generates a TrustEvent. The scoring
    algorithm processes these to update the TrustScore.
    """

    __tablename__ = "trust_events"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)  # success, failure, timeout, dispute
    handoff_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    score_delta: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score_after: Mapped[float] = mapped_column(Float, nullable=False)
    completion_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    details: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)

    __table_args__ = (
        Index("idx_trust_events_agent", "agent_id"),
        Index("idx_trust_events_handoff", "handoff_id"),
    )

"""Task handoffs — the chain of custody when work moves between agents."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base
from app.models._compat import GUID, JSONType


def _utcnow():
    return datetime.now(timezone.utc)


class Handoff(Base):
    """A task handoff from one agent to another, with full context and provenance."""

    __tablename__ = "handoffs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    negotiation_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("negotiations.id"), nullable=True
    )
    from_agent_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("agents.id"), nullable=False
    )
    to_agent_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("agents.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="initiated")
    context: Mapped[dict] = mapped_column(JSONType, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    chain_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    chain_position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_handoff_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("handoffs.id"), nullable=True
    )
    timeout_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Relationships
    negotiation = relationship("Negotiation", lazy="selectin")
    from_agent = relationship("Agent", foreign_keys=[from_agent_id], lazy="selectin")
    to_agent = relationship("Agent", foreign_keys=[to_agent_id], lazy="selectin")
    parent_handoff = relationship("Handoff", remote_side="Handoff.id", lazy="selectin")

    __table_args__ = (
        Index("idx_handoffs_status", "status"),
        Index("idx_handoffs_chain", "chain_id"),
        Index("idx_handoffs_from", "from_agent_id"),
        Index("idx_handoffs_to", "to_agent_id"),
    )

    def __repr__(self) -> str:
        return f"<Handoff {self.id} status={self.status}>"

"""Task handoffs — the chain of custody when work moves between agents."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class Handoff(Base):
    """A task handoff from one agent to another, with full context and provenance."""

    __tablename__ = "handoffs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    negotiation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("negotiations.id"), nullable=True
    )
    from_agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    to_agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("'initiated'")
    )
    context: Mapped[dict] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    chain_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    chain_position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    parent_handoff_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("handoffs.id"), nullable=True
    )
    timeout_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
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

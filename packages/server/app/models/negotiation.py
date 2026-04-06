"""Negotiation sessions — the state machine that governs agent-to-agent deals."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class Negotiation(Base):
    """A negotiation session between two agents, tracking intents, offers, and resolution."""

    __tablename__ = "negotiations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    initiator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    responder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True
    )
    mediator_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    state: Mapped[str] = mapped_column(String(50), nullable=False, server_default=text("'created'"))
    intent: Mapped[dict] = mapped_column(JSONB, nullable=False)
    current_offer: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    offer_history: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    agreement: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    timeout_at: Mapped[datetime | None] = mapped_column(nullable=True)
    max_rounds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("10"))
    current_round: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Relationships
    initiator = relationship("Agent", foreign_keys=[initiator_id], lazy="selectin")
    responder = relationship("Agent", foreign_keys=[responder_id], lazy="selectin")

    __table_args__ = (
        Index("idx_negotiations_state", "state"),
        Index("idx_negotiations_initiator", "initiator_id"),
        Index("idx_negotiations_responder", "responder_id"),
    )

    def __repr__(self) -> str:
        return f"<Negotiation {self.id} state={self.state}>"

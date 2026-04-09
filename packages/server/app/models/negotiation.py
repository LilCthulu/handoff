"""Negotiation sessions — the state machine that governs agent-to-agent deals."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base
from app.models._compat import GUID, JSONType


def _utcnow():
    return datetime.now(timezone.utc)


class Negotiation(Base):
    """A negotiation session between two agents, tracking intents, offers, and resolution."""

    __tablename__ = "negotiations"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    initiator_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("agents.id"), nullable=False
    )
    responder_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("agents.id"), nullable=True
    )
    mediator_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="created")
    intent: Mapped[dict] = mapped_column(JSONType, nullable=False)
    current_offer: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    offer_history: Mapped[dict] = mapped_column(JSONType, nullable=False, default=list)
    agreement: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    timeout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    current_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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

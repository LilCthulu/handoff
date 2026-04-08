"""Agent identity, public keys, capabilities, and trust."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._compat import GUID, JSONType


def _utcnow():
    return datetime.now(timezone.utc)


class Agent(Base):
    """An AI agent registered in the Handoff network."""

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    capabilities: Mapped[dict] = mapped_column(JSONType, nullable=False, default=list)
    trust_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    max_authority: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    metadata_: Mapped[dict] = mapped_column("metadata", JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # org_id for multi-tenant scoping (set by cloud extension)
    org_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index("idx_agents_status", "status"),
        Index("idx_agents_trust", trust_score.desc()),
    )

    def __repr__(self) -> str:
        return f"<Agent {self.name} ({self.id})>"

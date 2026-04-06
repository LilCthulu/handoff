"""Agent identity, public keys, capabilities, and trust."""

import uuid
from datetime import datetime

from sqlalchemy import Float, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class Agent(Base):
    """An AI agent registered in the Handoff network."""

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    capabilities: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    trust_score: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.5"))
    max_authority: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default=text("'active'"))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=text("now()"))
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("idx_agents_capabilities", "capabilities", postgresql_using="gin"),
        Index("idx_agents_status", "status"),
        Index("idx_agents_trust", trust_score.desc()),
    )

    def __repr__(self) -> str:
        return f"<Agent {self.name} ({self.id})>"

"""Immutable audit log — every action leaves a trace. Nothing is forgotten."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._compat import GUID, JSONType


def _utcnow():
    return datetime.now(timezone.utc)


class AuditLog(Base):
    """Immutable audit trail entry for any entity in the system."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("agents.id"), nullable=True
    )
    details: Mapped[dict] = mapped_column(JSONType, nullable=False)
    envelope_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)

    __table_args__ = (
        Index("idx_audit_entity", "entity_type", "entity_id"),
        Index("idx_audit_actor", "actor_agent_id"),
        Index("idx_audit_time", created_at.desc()),
    )

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} on {self.entity_type}/{self.entity_id}>"

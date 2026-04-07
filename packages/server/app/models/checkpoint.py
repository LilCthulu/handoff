"""Handoff checkpoints — saved state for rollback and recovery.

During multi-step handoffs, agents save checkpoints — snapshots of
intermediate state. If the agent fails at step 5, another agent can
resume from checkpoint 4 instead of starting over.

Checkpoints are the difference between "start over" and "pick up where
you left off."
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._compat import GUID, JSONType


def _utcnow():
    return datetime.now(timezone.utc)


class HandoffCheckpoint(Base):
    """A saved state snapshot during handoff execution."""

    __tablename__ = "handoff_checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    handoff_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)  # 1, 2, 3...
    phase: Mapped[str] = mapped_column(String(255), nullable=False)  # human-readable phase name
    state: Mapped[dict] = mapped_column(JSONType, nullable=False)  # serialized checkpoint state
    agent_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)  # who created the checkpoint
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)

    __table_args__ = (
        Index("idx_checkpoint_handoff", "handoff_id"),
        Index("idx_checkpoint_handoff_seq", "handoff_id", "sequence", unique=True),
    )

    def __repr__(self) -> str:
        return f"<Checkpoint {self.handoff_id} #{self.sequence} [{self.phase}]>"

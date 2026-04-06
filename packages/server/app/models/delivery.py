"""Signed delivery receipts — cryptographic proof of result delivery.

When agent B completes a handoff, it signs the result with its private key.
When agent A receives the result, it signs an acknowledgment. Together,
these form an irrefutable record: B delivered X, A received it.

No disputes. No ambiguity. Cryptographic accountability.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._compat import GUID, JSONType


def _utcnow():
    return datetime.now(timezone.utc)


class DeliveryReceipt(Base):
    """A signed delivery receipt for a completed handoff.

    Two signatures:
    1. delivery_signature: Agent B signs the result hash (proves B produced it)
    2. acknowledgment_signature: Agent A signs acceptance (proves A received it)
    """

    __tablename__ = "delivery_receipts"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)

    handoff_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)

    # Delivery (from receiving agent = the one who did the work)
    delivered_by: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    delivery_key_fingerprint: Mapped[str] = mapped_column(String(100), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(100), nullable=False)  # sha256:<hex> of canonical result
    delivery_signature: Mapped[str] = mapped_column(Text, nullable=False)  # base64 Ed25519
    delivered_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)

    # Proof of work (optional — external evidence)
    proof: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # {
    #   "type": "external_api_response",
    #   "data": {"booking_api_response_hash": "sha256:..."},
    #   "timestamp": "2026-04-06T12:00:00Z"
    # }

    # Acknowledgment (from delegating agent = the one who requested the work)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    acknowledgment_key_fingerprint: Mapped[str | None] = mapped_column(String(100), nullable=True)
    accepted: Mapped[bool | None] = mapped_column(nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledgment_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Verification
    delivery_verified: Mapped[bool] = mapped_column(nullable=False, default=False)
    acknowledgment_verified: Mapped[bool] = mapped_column(nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)

    __table_args__ = (
        Index("idx_receipt_handoff", "handoff_id", unique=True),
        Index("idx_receipt_delivered_by", "delivered_by"),
        Index("idx_receipt_acknowledged_by", "acknowledged_by"),
    )

    def __repr__(self) -> str:
        ack = "✓" if self.accepted else ("✗" if self.accepted is False else "?")
        return f"<DeliveryReceipt {self.handoff_id} [{ack}]>"

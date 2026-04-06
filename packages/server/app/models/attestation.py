"""Attestation chain — cryptographic proof of completed work.

When agent B completes a handoff from agent A, A signs an attestation:
"Agent B completed task X satisfactorily at time T." These attestations
are stored on the server and verifiable by any agent. Trust scores are
computed from attestations, not self-reports.

Attestations are the difference between "this agent says it's trustworthy"
and "47 other agents have cryptographically proven it's trustworthy."
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._compat import GUID, JSONType


def _utcnow():
    return datetime.now(timezone.utc)


class Attestation(Base):
    """A signed statement from one agent vouching for another's work.

    The attester (from_agent) signs a structured claim about the subject
    (to_agent) after observing a handoff outcome. The signature is
    verifiable using the attester's public key.
    """

    __tablename__ = "attestations"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)

    # Who is attesting
    attester_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    attester_key_fingerprint: Mapped[str] = mapped_column(String(100), nullable=False)

    # Who is being attested
    subject_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)

    # What is being attested
    handoff_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    outcome: Mapped[str] = mapped_column(String(50), nullable=False)  # "success", "failure", "partial"
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0.0 - 1.0 quality score

    # The signed claim
    claim: Mapped[dict] = mapped_column(JSONType, nullable=False)
    # {
    #   "handoff_id": "...",
    #   "subject_id": "...",
    #   "domain": "hotels.book",
    #   "outcome": "success",
    #   "rating": 0.95,
    #   "completion_time_ms": 1200,
    #   "contract_version": "1.2.0",
    #   "sla_met": true,
    #   "timestamp": "2026-04-06T12:00:00Z"
    # }

    signature: Mapped[str] = mapped_column(Text, nullable=False)  # base64 Ed25519 signature of canonical claim

    # Verification status
    verified: Mapped[bool] = mapped_column(nullable=False, default=False)  # server verified the signature

    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)

    __table_args__ = (
        Index("idx_attestation_subject", "subject_id"),
        Index("idx_attestation_attester", "attester_id"),
        Index("idx_attestation_handoff", "handoff_id", unique=True),
        Index("idx_attestation_domain", "subject_id", "domain"),
    )

    def __repr__(self) -> str:
        return f"<Attestation {self.attester_id} → {self.subject_id} [{self.outcome}]>"


class CapabilityChallenge(Base):
    """A proof-of-competence challenge issued to an agent.

    Before committing resources to a negotiation, the requesting agent or
    the server can challenge an agent to prove it understands a domain.
    The challenge contains a test input; the agent must return a valid
    response shape within a time limit.
    """

    __tablename__ = "capability_challenges"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)

    # Who is being challenged
    agent_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)

    # What capability is being tested
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(255), nullable=False)

    # Challenge details
    challenge_input: Mapped[dict] = mapped_column(JSONType, nullable=False)  # test input conforming to contract schema
    expected_schema: Mapped[dict] = mapped_column(JSONType, nullable=False)  # output schema to validate against
    max_time_ms: Mapped[int] = mapped_column(nullable=False, default=5000)  # time limit

    # Response
    response: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    response_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Outcome
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    # "pending", "passed", "failed", "timeout", "error"
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Who issued the challenge
    issued_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)  # null = server-issued

    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("idx_challenge_agent", "agent_id"),
        Index("idx_challenge_domain", "agent_id", "domain"),
        Index("idx_challenge_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<CapabilityChallenge {self.agent_id} {self.domain}.{self.action} [{self.status}]>"

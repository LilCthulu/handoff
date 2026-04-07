"""Third-party verifiable credentials — external trust signals.

External authorities (auditors, certification bodies, benchmark services)
issue signed credentials vouching for an agent's capabilities. Unlike
peer attestations (agent-to-agent), these come from trusted third parties
with established reputations.

Credentials follow a simplified W3C Verifiable Credential structure:
issuer, subject, claims, proof (signature), expiration.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._compat import GUID, JSONType


def _utcnow():
    return datetime.now(timezone.utc)


class ThirdPartyCredential(Base):
    """A verifiable credential from an external authority.

    Lifecycle:
    1. Submitted via API with issuer signature
    2. Server verifies signature against issuer's registered public key
    3. If valid, credential is stored and contributes to trust scoring
    4. Credentials expire and can be revoked by the issuer
    """

    __tablename__ = "third_party_credentials"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)

    # Issuer (the external authority)
    issuer_id: Mapped[str] = mapped_column(String(255), nullable=False)  # URI or identifier
    issuer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    issuer_key_fingerprint: Mapped[str] = mapped_column(String(100), nullable=False)

    # Subject (the agent being credentialed)
    subject_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)

    # Credential content
    credential_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # Types: "capability_certification", "security_audit", "benchmark_result",
    #        "compliance_attestation", "performance_rating"
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    claims: Mapped[dict] = mapped_column(JSONType, nullable=False)
    # {
    #   "capability": "nlp.summarize",
    #   "benchmark_score": 0.94,
    #   "methodology": "MMLU-agent-v2",
    #   "sample_size": 1000,
    #   "passed_requirements": ["latency_p99 < 2s", "accuracy > 0.9"],
    #   "issued_at": "2026-04-06T12:00:00Z"
    # }

    # Trust weight — how much this credential influences trust scoring
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    # Proof
    signature: Mapped[str] = mapped_column(Text, nullable=False)  # base64 Ed25519 signature
    proof_type: Mapped[str] = mapped_column(String(50), nullable=False, default="Ed25519Signature2020")

    # Verification
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Validity
    issued_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)

    @property
    def is_valid(self) -> bool:
        """Check if credential is currently valid."""
        if self.revoked:
            return False
        if self.expires_at and datetime.now(timezone.utc) > self.expires_at:
            return False
        return self.verified

    __table_args__ = (
        Index("idx_credential_subject", "subject_id"),
        Index("idx_credential_issuer", "issuer_id"),
        Index("idx_credential_domain", "subject_id", "domain"),
        Index("idx_credential_type", "credential_type"),
    )

    def __repr__(self) -> str:
        return f"<ThirdPartyCredential {self.issuer_name} → {self.subject_id} [{self.credential_type}]>"

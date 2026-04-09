"""Capability contracts — typed schemas for agent capabilities.

A capability contract defines:
- What domain and actions the agent handles
- The input schema (what it expects)
- The output schema (what it returns)
- SLA commitments (max latency, availability)
- Version for backwards compatibility

This is what makes Handoff agent-agnostic: any agent, any model, any runtime
can participate if it declares its capabilities in a standard format.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._compat import GUID, JSONType


def _utcnow():
    return datetime.now(timezone.utc)


class CapabilityContract(Base):
    """A versioned capability contract registered by an agent."""

    __tablename__ = "capability_contracts"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0.0")

    # Schema definitions (JSON Schema format)
    input_schema: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    output_schema: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    # SLA commitments
    max_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    availability_target: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0.0-1.0
    max_concurrent: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Obligation declarations — what the agent commits to regarding data handling
    obligations: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    # {
    #   "data_retention": "none_after_completion",  # none_after_completion | 30_days | permanent
    #   "pii_access": "sealed_references_only",     # sealed_references_only | committed_layer | none
    #   "external_apis": ["hyatt-booking-api.com"],  # list of external services the agent calls
    #   "logging": "anonymized",                     # anonymized | full | none
    #   "data_sharing": "none"                       # none | aggregated | third_party
    # }

    # Constraints and metadata
    constraints: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    examples: Mapped[dict] = mapped_column(JSONType, nullable=False, default=list)

    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        Index("idx_cap_agent_domain_action", "agent_id", "domain", "action"),
        Index("idx_cap_domain_action", "domain", "action"),
        Index("idx_cap_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<Capability {self.domain}:{self.action} v{self.version} agent={self.agent_id}>"

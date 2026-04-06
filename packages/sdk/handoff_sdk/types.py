"""Shared type definitions for the Handoff SDK."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from enum import Enum


class NegotiationState(str, Enum):
    """States in the negotiation lifecycle."""
    CREATED = "created"
    PENDING = "pending"
    NEGOTIATING = "negotiating"
    AGREED = "agreed"
    EXECUTING = "executing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class HandoffStatus(str, Enum):
    """States in the handoff lifecycle."""
    INITIATED = "initiated"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class Capability:
    """A single capability declaration."""
    domain: str
    actions: list[str]
    constraints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"domain": self.domain, "actions": self.actions, "constraints": self.constraints}


@dataclass
class AgentProfile:
    """An agent's public profile."""
    id: str
    name: str
    description: str | None
    owner_id: str
    public_key: str
    capabilities: list[dict[str, Any]]
    trust_score: float
    max_authority: dict[str, Any]
    status: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str
    last_seen_at: str | None = None


@dataclass
class Offer:
    """An offer or counteroffer in a negotiation."""
    id: str
    negotiation_id: str
    from_agent: str
    round: int
    terms: dict[str, Any]
    concessions: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    expires_at: str | None = None

    def accept(self) -> None:
        """Accept this offer. Set by the negotiation session."""
        if self._accept_fn:
            self._accept_fn()

    def counter(self, terms: dict[str, Any], concessions: list[str] | None = None, conditions: list[str] | None = None) -> None:
        """Counter this offer with new terms."""
        if self._counter_fn:
            self._counter_fn(terms, concessions or [], conditions or [])

    _accept_fn: Any = field(default=None, repr=False)
    _counter_fn: Any = field(default=None, repr=False)


@dataclass
class NegotiationResult:
    """The result of a completed negotiation."""
    id: str
    state: NegotiationState
    agreement: dict[str, Any] | None
    offer_history: list[dict[str, Any]]
    current_round: int


@dataclass
class HandoffResult:
    """The result of a completed handoff."""
    id: str
    status: HandoffStatus
    result: dict[str, Any] | None
    chain_id: str | None

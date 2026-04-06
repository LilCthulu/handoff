"""Negotiation state machine — the beating heart of agent-to-agent deals.

States: CREATED -> PENDING -> NEGOTIATING -> AGREED -> EXECUTING -> COMPLETED
                    |             |                        |
                    v             v                        v
                 REJECTED       FAILED                   FAILED

Every transition is validated. Every invalid move is rejected.
The state machine does not bend. It does not break.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger()


class NegotiationState:
    """Valid states in the negotiation lifecycle."""

    CREATED = "created"
    PENDING = "pending"
    NEGOTIATING = "negotiating"
    AGREED = "agreed"
    EXECUTING = "executing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"

    ALL = {CREATED, PENDING, NEGOTIATING, AGREED, EXECUTING, COMPLETED, REJECTED, FAILED}
    TERMINAL = {COMPLETED, REJECTED, FAILED}


# Valid state transitions: current_state -> set of allowed next states
TRANSITIONS: dict[str, set[str]] = {
    NegotiationState.CREATED: {NegotiationState.PENDING},
    NegotiationState.PENDING: {NegotiationState.NEGOTIATING, NegotiationState.REJECTED, NegotiationState.FAILED},
    NegotiationState.NEGOTIATING: {NegotiationState.NEGOTIATING, NegotiationState.AGREED, NegotiationState.REJECTED, NegotiationState.FAILED},
    NegotiationState.AGREED: {NegotiationState.EXECUTING, NegotiationState.FAILED},
    NegotiationState.EXECUTING: {NegotiationState.COMPLETED, NegotiationState.FAILED},
    NegotiationState.COMPLETED: set(),
    NegotiationState.REJECTED: set(),
    NegotiationState.FAILED: set(),
}


class NegotiationError(Exception):
    """Raised when a negotiation operation violates protocol."""

    def __init__(self, detail: str, status_code: int = 400) -> None:
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def validate_transition(current_state: str, next_state: str) -> None:
    """Validate that a state transition is allowed.

    Raises:
        NegotiationError: If the transition is invalid.
    """
    if current_state not in TRANSITIONS:
        raise NegotiationError(f"Unknown state: {current_state}")
    if next_state not in TRANSITIONS[current_state]:
        raise NegotiationError(
            f"Invalid transition: {current_state} -> {next_state}. "
            f"Allowed: {TRANSITIONS[current_state]}"
        )


def initiate(negotiation_dict: dict[str, Any]) -> dict[str, Any]:
    """Transition a negotiation from CREATED to PENDING.

    This is the moment the intent reaches the responder.
    """
    validate_transition(negotiation_dict["state"], NegotiationState.PENDING)
    negotiation_dict["state"] = NegotiationState.PENDING
    negotiation_dict["updated_at"] = datetime.now(timezone.utc)
    logger.info("negotiation_initiated", negotiation_id=negotiation_dict.get("id"))
    return negotiation_dict


def submit_offer(
    negotiation_dict: dict[str, Any],
    from_agent_id: str,
    terms: dict[str, Any],
    concessions: list[str] | None = None,
    conditions: list[str] | None = None,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    """Submit an offer or counteroffer.

    Valid from PENDING (first response) or NEGOTIATING (counteroffers).
    """
    current_state = negotiation_dict["state"]

    if current_state == NegotiationState.PENDING:
        next_state = NegotiationState.NEGOTIATING
    elif current_state == NegotiationState.NEGOTIATING:
        next_state = NegotiationState.NEGOTIATING
    else:
        raise NegotiationError(f"Cannot submit offer in state: {current_state}")

    validate_transition(current_state, next_state)

    # Enforce turn order: same agent cannot submit consecutive offers
    last_offer = negotiation_dict.get("current_offer")
    if last_offer and last_offer.get("from_agent") == from_agent_id:
        raise NegotiationError("Cannot submit consecutive offers — wait for the other party to respond")

    # Check round limit
    current_round = negotiation_dict.get("current_round", 0) + 1
    max_rounds = negotiation_dict.get("max_rounds", 10)
    if current_round > max_rounds:
        raise NegotiationError(f"Max rounds ({max_rounds}) exceeded")

    # Check timeout
    timeout_at = negotiation_dict.get("timeout_at")
    if timeout_at and isinstance(timeout_at, datetime) and datetime.now(timezone.utc) > timeout_at:
        negotiation_dict["state"] = NegotiationState.FAILED
        negotiation_dict["updated_at"] = datetime.now(timezone.utc)
        raise NegotiationError("Negotiation timed out")

    offer = {
        "id": str(uuid.uuid4()),
        "negotiation_id": str(negotiation_dict.get("id", "")),
        "from_agent": from_agent_id,
        "round": current_round,
        "terms": terms,
        "concessions": concessions or [],
        "conditions": conditions or [],
        "expires_at": expires_at.isoformat() if expires_at else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Append to history, set as current
    offer_history = negotiation_dict.get("offer_history", [])
    offer_history.append(offer)

    negotiation_dict["state"] = next_state
    negotiation_dict["current_offer"] = offer
    negotiation_dict["offer_history"] = offer_history
    negotiation_dict["current_round"] = current_round
    negotiation_dict["updated_at"] = datetime.now(timezone.utc)

    logger.info(
        "offer_submitted",
        negotiation_id=negotiation_dict.get("id"),
        round=current_round,
        from_agent=from_agent_id,
    )
    return negotiation_dict


def accept_offer(negotiation_dict: dict[str, Any]) -> dict[str, Any]:
    """Accept the current offer, moving to AGREED.

    The deal is struck. What was negotiated becomes the agreement.
    """
    validate_transition(negotiation_dict["state"], NegotiationState.AGREED)

    current_offer = negotiation_dict.get("current_offer")
    if not current_offer:
        raise NegotiationError("No current offer to accept")

    negotiation_dict["state"] = NegotiationState.AGREED
    negotiation_dict["agreement"] = current_offer
    negotiation_dict["updated_at"] = datetime.now(timezone.utc)

    logger.info("negotiation_agreed", negotiation_id=negotiation_dict.get("id"))
    return negotiation_dict


def reject_negotiation(negotiation_dict: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
    """Reject a negotiation, moving to REJECTED."""
    current_state = negotiation_dict["state"]
    if current_state in NegotiationState.TERMINAL:
        raise NegotiationError(f"Negotiation already in terminal state: {current_state}")

    validate_transition(current_state, NegotiationState.REJECTED)

    negotiation_dict["state"] = NegotiationState.REJECTED
    negotiation_dict["updated_at"] = datetime.now(timezone.utc)
    negotiation_dict["completed_at"] = datetime.now(timezone.utc)
    if reason:
        meta = negotiation_dict.get("metadata_", negotiation_dict.get("metadata", {}))
        meta["rejection_reason"] = reason
        if "metadata_" in negotiation_dict:
            negotiation_dict["metadata_"] = meta
        else:
            negotiation_dict["metadata"] = meta

    logger.info("negotiation_rejected", negotiation_id=negotiation_dict.get("id"), reason=reason)
    return negotiation_dict


def begin_execution(negotiation_dict: dict[str, Any]) -> dict[str, Any]:
    """Transition from AGREED to EXECUTING — the handoff is underway."""
    validate_transition(negotiation_dict["state"], NegotiationState.EXECUTING)
    negotiation_dict["state"] = NegotiationState.EXECUTING
    negotiation_dict["updated_at"] = datetime.now(timezone.utc)
    logger.info("negotiation_executing", negotiation_id=negotiation_dict.get("id"))
    return negotiation_dict


def complete(negotiation_dict: dict[str, Any]) -> dict[str, Any]:
    """Mark a negotiation as COMPLETED — the work is done."""
    validate_transition(negotiation_dict["state"], NegotiationState.COMPLETED)
    negotiation_dict["state"] = NegotiationState.COMPLETED
    negotiation_dict["updated_at"] = datetime.now(timezone.utc)
    negotiation_dict["completed_at"] = datetime.now(timezone.utc)
    logger.info("negotiation_completed", negotiation_id=negotiation_dict.get("id"))
    return negotiation_dict


def fail(negotiation_dict: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
    """Mark a negotiation as FAILED."""
    current_state = negotiation_dict["state"]
    if current_state in NegotiationState.TERMINAL:
        raise NegotiationError(f"Negotiation already in terminal state: {current_state}")

    validate_transition(current_state, NegotiationState.FAILED)
    negotiation_dict["state"] = NegotiationState.FAILED
    negotiation_dict["updated_at"] = datetime.now(timezone.utc)
    negotiation_dict["completed_at"] = datetime.now(timezone.utc)
    if reason:
        meta = negotiation_dict.get("metadata_", negotiation_dict.get("metadata", {}))
        meta["failure_reason"] = reason
        if "metadata_" in negotiation_dict:
            negotiation_dict["metadata_"] = meta
        else:
            negotiation_dict["metadata"] = meta

    logger.info("negotiation_failed", negotiation_id=negotiation_dict.get("id"), reason=reason)
    return negotiation_dict

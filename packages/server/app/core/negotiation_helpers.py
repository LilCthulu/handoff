"""Shared helpers for converting between Negotiation ORM and engine dict.

Used by both REST API routes and WebSocket handlers to avoid
logic divergence between the two paths.
"""

from datetime import datetime, timezone
from typing import Any

from app.models.negotiation import Negotiation


def negotiation_to_dict(n: Negotiation) -> dict[str, Any]:
    """Convert ORM instance to dict for the negotiation engine."""
    return {
        "id": str(n.id),
        "state": n.state,
        "current_offer": n.current_offer,
        "offer_history": list(n.offer_history) if n.offer_history else [],
        "agreement": n.agreement,
        "current_round": n.current_round,
        "max_rounds": n.max_rounds,
        "timeout_at": n.timeout_at,
        "metadata": n.metadata_,
        "updated_at": n.updated_at,
        "completed_at": n.completed_at,
    }


def apply_dict_to_negotiation(n: Negotiation, d: dict[str, Any]) -> None:
    """Apply engine dict back to ORM instance."""
    n.state = d["state"]
    n.current_offer = d.get("current_offer")
    n.offer_history = d.get("offer_history", [])
    n.agreement = d.get("agreement")
    n.current_round = d.get("current_round", n.current_round)
    n.updated_at = d.get("updated_at", datetime.now(timezone.utc))
    n.completed_at = d.get("completed_at")
    if "metadata" in d:
        n.metadata_ = d["metadata"]


def negotiation_to_response(n: Negotiation) -> dict[str, Any]:
    """Convert ORM instance to API response dict."""
    return {
        "id": n.id,
        "initiator_id": n.initiator_id,
        "responder_id": n.responder_id,
        "mediator_required": n.mediator_required,
        "state": n.state,
        "intent": n.intent,
        "current_offer": n.current_offer,
        "offer_history": n.offer_history,
        "agreement": n.agreement,
        "timeout_at": n.timeout_at,
        "max_rounds": n.max_rounds,
        "current_round": n.current_round,
        "metadata": n.metadata_,
        "created_at": n.created_at,
        "updated_at": n.updated_at,
        "completed_at": n.completed_at,
    }

"""Negotiation endpoints — where agents meet, argue, and find common ground.

This is the marketplace of the agentic economy. Intents are declared,
offers are exchanged, and agreements are forged. Every round is recorded.
Every transition is enforced.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.negotiation_engine import (
    NegotiationError,
    NegotiationState,
    accept_offer,
    begin_execution,
    fail,
    initiate,
    reject_negotiation,
    submit_offer,
)
from app.core.negotiation_helpers import (
    apply_dict_to_negotiation,
    negotiation_to_dict,
    negotiation_to_response,
)
from app.core.mediator import should_mediate, suggest_compromise
from app.database import get_db
from app.models.agent import Agent
from app.models.audit import AuditLog
from app.models.negotiation import Negotiation
from app.schemas.negotiation import (
    NegotiationCreateRequest,
    NegotiationOfferRequest,
    NegotiationRejectRequest,
    NegotiationResponse,
)
from app.api.deps import get_current_agent, get_agent_id

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/negotiations", tags=["negotiations"])


@router.post("", response_model=NegotiationResponse, status_code=201)
async def create_negotiation(
    req: NegotiationCreateRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Create a new negotiation — the initiator declares intent and names a responder."""
    # Verify responder exists and is active
    responder = await db.get(Agent, req.responder_id)
    if not responder or responder.status != "active":
        raise HTTPException(status_code=404, detail="Responder agent not found or inactive")

    timeout_at = None
    if req.timeout_minutes:
        timeout_at = datetime.now(timezone.utc) + timedelta(minutes=req.timeout_minutes)

    negotiation = Negotiation(
        initiator_id=caller_id,
        responder_id=req.responder_id,
        mediator_required=req.mediator_required,
        intent=req.intent.model_dump(mode="json"),
        max_rounds=req.max_rounds,
        timeout_at=timeout_at,
    )
    db.add(negotiation)
    await db.flush()

    # Transition to PENDING
    neg_dict = negotiation_to_dict(negotiation)
    neg_dict = initiate(neg_dict)
    apply_dict_to_negotiation(negotiation, neg_dict)

    audit = AuditLog(
        entity_type="negotiation",
        entity_id=negotiation.id,
        action="created",
        actor_agent_id=caller_id,
        details={
            "responder_id": str(req.responder_id),
            "intent_domain": req.intent.domain,
            "intent_action": req.intent.action,
        },
    )
    db.add(audit)
    await db.commit()
    await db.refresh(negotiation)

    logger.info("negotiation_created", negotiation_id=str(negotiation.id), initiator=str(caller_id))
    return negotiation_to_response(negotiation)


@router.get("/{negotiation_id}", response_model=NegotiationResponse)
async def get_negotiation(
    negotiation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Get the current state of a negotiation."""
    negotiation = await _get_negotiation_or_404(db, negotiation_id)
    return negotiation_to_response(negotiation)


@router.post("/{negotiation_id}/offer", response_model=NegotiationResponse)
async def submit_negotiation_offer(
    negotiation_id: uuid.UUID,
    req: NegotiationOfferRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Submit an offer or counteroffer. Both sides take turns."""
    negotiation = await _get_negotiation_or_404(db, negotiation_id)
    _verify_participant(negotiation, caller_id)

    try:
        neg_dict = negotiation_to_dict(negotiation)
        neg_dict = submit_offer(
            neg_dict,
            from_agent_id=str(caller_id),
            terms=req.terms,
            concessions=req.concessions,
            conditions=req.conditions,
            expires_at=req.expires_at,
        )
        apply_dict_to_negotiation(negotiation, neg_dict)
    except NegotiationError as e:
        raise HTTPException(status_code=400, detail=e.detail)

    audit = AuditLog(
        entity_type="negotiation",
        entity_id=negotiation.id,
        action="offer_submitted",
        actor_agent_id=caller_id,
        details={
            "round": negotiation.current_round,
            "terms": req.terms,
        },
    )
    db.add(audit)
    await db.commit()
    await db.refresh(negotiation)

    # Check if mediation should be suggested
    if not negotiation.mediator_required and should_mediate(
        negotiation.current_round, negotiation.max_rounds, negotiation.offer_history
    ):
        logger.info("mediation_auto_suggested", negotiation_id=str(negotiation_id))

    return negotiation_to_response(negotiation)


@router.post("/{negotiation_id}/accept", response_model=NegotiationResponse)
async def accept_negotiation(
    negotiation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Accept the current offer — the deal is struck."""
    negotiation = await _get_negotiation_or_404(db, negotiation_id)
    _verify_participant(negotiation, caller_id)

    try:
        neg_dict = negotiation_to_dict(negotiation)
        neg_dict = accept_offer(neg_dict)
        apply_dict_to_negotiation(negotiation, neg_dict)
    except NegotiationError as e:
        raise HTTPException(status_code=400, detail=e.detail)

    audit = AuditLog(
        entity_type="negotiation",
        entity_id=negotiation.id,
        action="accepted",
        actor_agent_id=caller_id,
        details={"agreement": negotiation.agreement},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(negotiation)

    logger.info("negotiation_accepted", negotiation_id=str(negotiation_id), by=str(caller_id))
    return negotiation_to_response(negotiation)


@router.post("/{negotiation_id}/reject", response_model=NegotiationResponse)
async def reject_negotiation_endpoint(
    negotiation_id: uuid.UUID,
    req: NegotiationRejectRequest | None = None,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Reject a negotiation — walk away from the table."""
    negotiation = await _get_negotiation_or_404(db, negotiation_id)
    _verify_participant(negotiation, caller_id)

    reason = req.reason if req else None

    try:
        neg_dict = negotiation_to_dict(negotiation)
        neg_dict = reject_negotiation(neg_dict, reason=reason)
        apply_dict_to_negotiation(negotiation, neg_dict)
    except NegotiationError as e:
        raise HTTPException(status_code=400, detail=e.detail)

    audit = AuditLog(
        entity_type="negotiation",
        entity_id=negotiation.id,
        action="rejected",
        actor_agent_id=caller_id,
        details={"reason": reason},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(negotiation)

    return negotiation_to_response(negotiation)


@router.post("/{negotiation_id}/mediate")
async def request_mediation(
    negotiation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Request mediation for a stalled negotiation."""
    negotiation = await _get_negotiation_or_404(db, negotiation_id)
    _verify_participant(negotiation, caller_id)

    if negotiation.state not in (NegotiationState.NEGOTIATING, NegotiationState.PENDING):
        raise HTTPException(status_code=400, detail=f"Cannot mediate in state: {negotiation.state}")

    negotiation.mediator_required = True
    negotiation.updated_at = datetime.now(timezone.utc)

    # Generate compromise suggestion
    intent_constraints = negotiation.intent.get("constraints", {})
    suggestion = suggest_compromise(intent_constraints, negotiation.offer_history)

    audit = AuditLog(
        entity_type="negotiation",
        entity_id=negotiation.id,
        action="mediation_requested",
        actor_agent_id=caller_id,
        details={"suggestion": suggestion},
    )
    db.add(audit)
    await db.commit()

    logger.info("mediation_requested", negotiation_id=str(negotiation_id))
    return {"mediation": "active", "suggestion": suggestion}


@router.get("/{negotiation_id}/history")
async def get_negotiation_history(
    negotiation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Get the full offer/counteroffer history for a negotiation."""
    negotiation = await _get_negotiation_or_404(db, negotiation_id)
    return {
        "negotiation_id": str(negotiation.id),
        "state": negotiation.state,
        "current_round": negotiation.current_round,
        "max_rounds": negotiation.max_rounds,
        "offer_history": negotiation.offer_history,
    }


# --- Helpers ---

async def _get_negotiation_or_404(db: AsyncSession, negotiation_id: uuid.UUID) -> Negotiation:
    result = await db.execute(select(Negotiation).where(Negotiation.id == negotiation_id))
    negotiation = result.scalar_one_or_none()
    if not negotiation:
        raise HTTPException(status_code=404, detail="Negotiation not found")
    return negotiation


def _verify_participant(negotiation: Negotiation, caller_id: uuid.UUID) -> None:
    """Ensure the caller is a participant in this negotiation."""
    if caller_id not in (negotiation.initiator_id, negotiation.responder_id):
        raise HTTPException(status_code=403, detail="Not a participant in this negotiation")



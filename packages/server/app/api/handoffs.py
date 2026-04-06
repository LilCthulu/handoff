"""Handoff endpoints — the moment trust becomes action.

When agents agree, the work begins. Context is transferred,
chains of custody are established, and every step is recorded.
Failure triggers rollback. Success triggers trust.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.negotiation_engine import NegotiationState, begin_execution, complete, fail
from app.database import get_db
from app.models.agent import Agent
from app.models.audit import AuditLog
from app.models.handoff import Handoff
from app.models.negotiation import Negotiation
from app.schemas.handoff import (
    AuditLogResponse,
    HandoffCreateRequest,
    HandoffResponse,
    HandoffResultRequest,
    HandoffStatusUpdate,
)
from app.api.deps import get_current_agent, get_agent_id

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["handoffs"])


@router.post("/handoffs", response_model=HandoffResponse, status_code=201)
async def create_handoff(
    req: HandoffCreateRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Initiate a handoff — transfer work context to another agent."""
    # Verify target agent
    to_agent = await db.get(Agent, req.to_agent_id)
    if not to_agent or to_agent.status != "active":
        raise HTTPException(status_code=404, detail="Target agent not found or inactive")

    # If linked to a negotiation, transition it to EXECUTING
    if req.negotiation_id:
        result = await db.execute(
            select(Negotiation).where(Negotiation.id == req.negotiation_id)
        )
        negotiation = result.scalar_one_or_none()
        if not negotiation:
            raise HTTPException(status_code=404, detail="Negotiation not found")
        if negotiation.state == NegotiationState.AGREED:
            neg_dict = {
                "id": str(negotiation.id),
                "state": negotiation.state,
                "updated_at": negotiation.updated_at,
            }
            neg_dict = begin_execution(neg_dict)
            negotiation.state = neg_dict["state"]
            negotiation.updated_at = neg_dict["updated_at"]

    # Generate chain_id if this is the first in a chain
    chain_id = req.chain_id or uuid.uuid4()

    timeout_at = None
    if req.timeout_minutes:
        timeout_at = datetime.now(timezone.utc) + timedelta(minutes=req.timeout_minutes)

    handoff = Handoff(
        negotiation_id=req.negotiation_id,
        from_agent_id=caller_id,
        to_agent_id=req.to_agent_id,
        context=req.context,
        chain_id=chain_id,
        chain_position=req.chain_position,
        parent_handoff_id=req.parent_handoff_id,
        timeout_at=timeout_at,
    )
    db.add(handoff)
    await db.flush()

    audit = AuditLog(
        entity_type="handoff",
        entity_id=handoff.id,
        action="initiated",
        actor_agent_id=caller_id,
        details={
            "from_agent": str(caller_id),
            "to_agent": str(req.to_agent_id),
            "chain_id": str(chain_id),
            "chain_position": req.chain_position,
        },
    )
    db.add(audit)
    await db.commit()
    await db.refresh(handoff)

    logger.info(
        "handoff_initiated",
        handoff_id=str(handoff.id),
        from_agent=str(caller_id),
        to_agent=str(req.to_agent_id),
    )
    return _handoff_to_response(handoff)


@router.get("/handoffs/{handoff_id}", response_model=HandoffResponse)
async def get_handoff(
    handoff_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Get handoff status and context."""
    handoff = await _get_handoff_or_404(db, handoff_id)
    return _handoff_to_response(handoff)


@router.patch("/handoffs/{handoff_id}/status", response_model=HandoffResponse)
async def update_handoff_status(
    handoff_id: uuid.UUID,
    req: HandoffStatusUpdate,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Update handoff status. The receiving agent reports progress."""
    handoff = await _get_handoff_or_404(db, handoff_id)

    if caller_id != handoff.to_agent_id:
        raise HTTPException(status_code=403, detail="Only the receiving agent can update status")

    valid_transitions = {
        "initiated": {"in_progress"},
        "in_progress": {"completed", "failed", "rolled_back"},
    }
    allowed = valid_transitions.get(handoff.status, set())
    if req.status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot transition from '{handoff.status}' to '{req.status}'",
        )

    handoff.status = req.status
    handoff.updated_at = datetime.now(timezone.utc)

    if req.status in ("completed", "failed", "rolled_back"):
        handoff.completed_at = datetime.now(timezone.utc)

        # Update linked negotiation
        if handoff.negotiation_id:
            result = await db.execute(
                select(Negotiation).where(Negotiation.id == handoff.negotiation_id)
            )
            negotiation = result.scalar_one_or_none()
            if negotiation and negotiation.state == NegotiationState.EXECUTING:
                neg_dict = {
                    "id": str(negotiation.id),
                    "state": negotiation.state,
                    "metadata": negotiation.metadata_,
                    "updated_at": negotiation.updated_at,
                    "completed_at": negotiation.completed_at,
                }
                if req.status == "completed":
                    neg_dict = complete(neg_dict)
                else:
                    neg_dict = fail(neg_dict, reason=f"Handoff {req.status}")
                negotiation.state = neg_dict["state"]
                negotiation.updated_at = neg_dict["updated_at"]
                negotiation.completed_at = neg_dict.get("completed_at")

    audit = AuditLog(
        entity_type="handoff",
        entity_id=handoff.id,
        action=f"status_{req.status}",
        actor_agent_id=caller_id,
        details={"new_status": req.status},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(handoff)

    logger.info("handoff_status_updated", handoff_id=str(handoff_id), status=req.status)
    return _handoff_to_response(handoff)


@router.post("/handoffs/{handoff_id}/result", response_model=HandoffResponse)
async def submit_handoff_result(
    handoff_id: uuid.UUID,
    req: HandoffResultRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Submit the result of a completed handoff."""
    handoff = await _get_handoff_or_404(db, handoff_id)

    if caller_id != handoff.to_agent_id:
        raise HTTPException(status_code=403, detail="Only the receiving agent can submit results")

    handoff.result = req.result
    handoff.status = "completed"
    handoff.updated_at = datetime.now(timezone.utc)
    handoff.completed_at = datetime.now(timezone.utc)

    audit = AuditLog(
        entity_type="handoff",
        entity_id=handoff.id,
        action="result_submitted",
        actor_agent_id=caller_id,
        details={"result_keys": list(req.result.keys())},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(handoff)

    logger.info("handoff_result_submitted", handoff_id=str(handoff_id))
    return _handoff_to_response(handoff)


@router.post("/handoffs/{handoff_id}/rollback", response_model=HandoffResponse)
async def rollback_handoff(
    handoff_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Rollback a failed handoff."""
    handoff = await _get_handoff_or_404(db, handoff_id)

    if caller_id not in (handoff.from_agent_id, handoff.to_agent_id):
        raise HTTPException(status_code=403, detail="Not a participant in this handoff")

    if handoff.status not in ("in_progress", "failed"):
        raise HTTPException(status_code=400, detail=f"Cannot rollback from status: {handoff.status}")

    previous_status = handoff.status
    handoff.status = "rolled_back"
    handoff.updated_at = datetime.now(timezone.utc)
    handoff.completed_at = datetime.now(timezone.utc)

    audit = AuditLog(
        entity_type="handoff",
        entity_id=handoff.id,
        action="rolled_back",
        actor_agent_id=caller_id,
        details={"previous_status": previous_status},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(handoff)

    logger.info("handoff_rolled_back", handoff_id=str(handoff_id))
    return _handoff_to_response(handoff)


@router.get("/handoffs/chain/{chain_id}", response_model=list[HandoffResponse])
async def get_handoff_chain(
    chain_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> list[dict[str, Any]]:
    """Get the full chain of custody for a multi-hop handoff."""
    result = await db.execute(
        select(Handoff)
        .where(Handoff.chain_id == chain_id)
        .order_by(Handoff.chain_position)
    )
    handoffs = result.scalars().all()
    if not handoffs:
        raise HTTPException(status_code=404, detail="Chain not found")
    return [_handoff_to_response(h) for h in handoffs]


@router.get("/audit/{entity_type}/{entity_id}", response_model=list[AuditLogResponse])
async def get_audit_trail(
    entity_type: str,
    entity_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> list[dict[str, Any]]:
    """Get the full audit trail for any entity."""
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
        .order_by(AuditLog.created_at.desc())
    )
    entries = result.scalars().all()
    return [
        {
            "id": e.id,
            "entity_type": e.entity_type,
            "entity_id": e.entity_id,
            "action": e.action,
            "actor_agent_id": e.actor_agent_id,
            "details": e.details,
            "envelope_signature": e.envelope_signature,
            "created_at": e.created_at,
        }
        for e in entries
    ]


# --- Helpers ---

async def _get_handoff_or_404(db: AsyncSession, handoff_id: uuid.UUID) -> Handoff:
    result = await db.execute(select(Handoff).where(Handoff.id == handoff_id))
    handoff = result.scalar_one_or_none()
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")
    return handoff


def _handoff_to_response(h: Handoff) -> dict[str, Any]:
    return {
        "id": h.id,
        "negotiation_id": h.negotiation_id,
        "from_agent_id": h.from_agent_id,
        "to_agent_id": h.to_agent_id,
        "status": h.status,
        "context": h.context,
        "result": h.result,
        "chain_id": h.chain_id,
        "chain_position": h.chain_position,
        "parent_handoff_id": h.parent_handoff_id,
        "timeout_at": h.timeout_at,
        "created_at": h.created_at,
        "updated_at": h.updated_at,
        "completed_at": h.completed_at,
    }

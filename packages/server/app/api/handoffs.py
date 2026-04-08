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
from app.core.auth import require_scope
from app.api.deps import get_current_agent, get_agent_id
from app.services.contract_enforcement import validate_handoff_input, validate_handoff_result
from app.services.context_privacy import minimize_context

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["handoffs"])


@router.post("/handoffs", response_model=HandoffResponse, status_code=201)
async def create_handoff(
    req: HandoffCreateRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
    claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Initiate a handoff — transfer work context to another agent."""
    require_scope(claims, "handoff")

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

    # Validate input against receiving agent's capability contract
    contract, violations = await validate_handoff_input(db, req.to_agent_id, req.context)
    if violations:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Handoff input violates capability contract",
                "contract_id": str(contract.id) if contract else None,
                "violations": violations,
            },
        )

    # Data minimization: if a contract exists, strip fields not in input_schema
    delivered_context = req.context
    if contract and contract.input_schema:
        delivered_context = minimize_context(req.context, contract.input_schema)
        logger.info(
            "context_minimized",
            original_keys=len(req.context),
            minimized_keys=len(delivered_context),
        )

    # Generate chain_id if this is the first in a chain
    chain_id = req.chain_id or uuid.uuid4()

    timeout_at = None
    if req.timeout_minutes:
        timeout_at = datetime.now(timezone.utc) + timedelta(minutes=req.timeout_minutes)

    handoff = Handoff(
        negotiation_id=req.negotiation_id,
        from_agent_id=caller_id,
        to_agent_id=req.to_agent_id,
        context=delivered_context,
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
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Get handoff status and context. Only participants can view."""
    handoff = await _get_handoff_or_404(db, handoff_id)
    if caller_id not in (handoff.from_agent_id, handoff.to_agent_id):
        raise HTTPException(status_code=403, detail="Not a participant in this handoff")
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

        # Record trust event
        await _record_handoff_trust(db, handoff, req.status)

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

    # Validate result against capability contract output schema
    contract, violations, sla_report = await validate_handoff_result(db, handoff, req.result)
    if violations:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Handoff result violates capability contract output schema",
                "contract_id": str(contract.id) if contract else None,
                "violations": violations,
            },
        )

    handoff.result = req.result
    handoff.status = "completed"
    handoff.updated_at = datetime.now(timezone.utc)
    handoff.completed_at = datetime.now(timezone.utc)

    # Record trust event (with SLA context)
    await _record_handoff_trust(db, handoff, "completed")

    audit_details: dict[str, Any] = {"result_keys": list(req.result.keys())}
    if sla_report:
        audit_details["sla_report"] = sla_report
    if contract:
        audit_details["contract_id"] = str(contract.id)
        audit_details["contract_version"] = contract.version

    audit = AuditLog(
        entity_type="handoff",
        entity_id=handoff.id,
        action="result_submitted",
        actor_agent_id=caller_id,
        details=audit_details,
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
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> list[dict[str, Any]]:
    """Get the full chain of custody for a multi-hop handoff. Must be a participant in at least one."""
    result = await db.execute(
        select(Handoff)
        .where(Handoff.chain_id == chain_id)
        .order_by(Handoff.chain_position)
    )
    handoffs = result.scalars().all()
    if not handoffs:
        raise HTTPException(status_code=404, detail="Chain not found")
    # Caller must be a participant in at least one handoff in the chain
    is_participant = any(
        caller_id in (h.from_agent_id, h.to_agent_id) for h in handoffs
    )
    if not is_participant:
        raise HTTPException(status_code=403, detail="Not a participant in this handoff chain")
    return [_handoff_to_response(h) for h in handoffs]


VALID_AUDIT_ENTITY_TYPES = {"agent", "negotiation", "handoff", "stake", "capability", "attestation", "delivery"}

@router.get("/audit/{entity_type}/{entity_id}", response_model=list[AuditLogResponse])
async def get_audit_trail(
    entity_type: str,
    entity_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> list[dict[str, Any]]:
    """Get the audit trail for an entity. Caller must be a participant."""
    if entity_type not in VALID_AUDIT_ENTITY_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid entity_type. Must be one of: {', '.join(sorted(VALID_AUDIT_ENTITY_TYPES))}")

    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
        .order_by(AuditLog.created_at.desc())
        .limit(200)
    )
    entries = result.scalars().all()

    # Verify the caller is referenced in at least one audit entry for this entity
    if entries:
        caller_involved = any(e.actor_agent_id == caller_id for e in entries)
        if not caller_involved:
            raise HTTPException(status_code=403, detail="Not authorized to view this audit trail")

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


async def _record_handoff_trust(db: AsyncSession, handoff: Handoff, status: str) -> None:
    """Record a trust event for the receiving agent based on handoff outcome."""
    from app.services.trust import record_trust_event

    # Determine the domain from the handoff context or linked negotiation
    domain = "general"
    context = handoff.context or {}
    if "domain" in context:
        domain = context["domain"]
    elif handoff.negotiation_id:
        result = await db.execute(
            select(Negotiation).where(Negotiation.id == handoff.negotiation_id)
        )
        neg = result.scalar_one_or_none()
        if neg and isinstance(neg.intent, dict):
            domain = neg.intent.get("domain", "general")

    # Calculate completion time (handle mixed naive/aware datetimes from SQLite)
    completion_time_ms = None
    if handoff.completed_at and handoff.created_at:
        completed = handoff.completed_at
        created = handoff.created_at
        # Normalize both to aware for safe subtraction
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=timezone.utc)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        delta = completed - created
        completion_time_ms = delta.total_seconds() * 1000

    event_type = "success" if status == "completed" else "failure"
    if status == "rolled_back":
        event_type = "failure"

    await record_trust_event(
        db=db,
        agent_id=handoff.to_agent_id,
        domain=domain,
        event_type=event_type,
        handoff_id=handoff.id,
        completion_time_ms=completion_time_ms,
    )


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

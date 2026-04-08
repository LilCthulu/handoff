"""Handoff progress API — streaming updates and checkpoints.

Agents report progress as they execute work. The server stores
checkpoints, detects stalls, and pushes updates via WebSocket.
The delegating agent sees real-time progress in the dashboard.

Stall detection: if no progress update arrives within the configured
window, the server emits a stall alert. The delegating agent can
then decide to wait, reassign, or rollback.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.database import get_db
from app.models.handoff import Handoff
from app.models.checkpoint import HandoffCheckpoint
from app.models.audit import AuditLog
from app.api.deps import get_current_agent, get_agent_id
from app.websocket.manager import manager

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/progress", tags=["progress"])


# --- Request schemas ---

class ProgressUpdateRequest(BaseModel):
    phase: str  # human-readable phase description
    progress: float = Field(..., ge=0.0, le=1.0)  # 0.0 to 1.0
    message: str | None = None
    details: dict[str, Any] | None = None


class CheckpointRequest(BaseModel):
    phase: str
    state: dict[str, Any]  # serialized intermediate state


# --- Endpoints ---

@router.post("/handoffs/{handoff_id}/update")
async def report_progress(
    handoff_id: uuid.UUID,
    req: ProgressUpdateRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Report progress on an in-progress handoff.

    The receiving agent sends progress updates as it works. These are
    pushed to the delegating agent via WebSocket and stored for the
    dashboard.
    """
    handoff = await _get_handoff(db, handoff_id)
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")

    if caller_id != handoff.to_agent_id:
        raise HTTPException(status_code=403, detail="Only the receiving agent can report progress")

    if handoff.status != "in_progress":
        raise HTTPException(status_code=400, detail=f"Handoff is not in_progress (status: {handoff.status})")

    now = datetime.now(timezone.utc)

    # Store latest progress in handoff context
    if not handoff.context:
        handoff.context = {}
    handoff.context["_progress"] = {
        "phase": req.phase,
        "progress": req.progress,
        "message": req.message,
        "updated_at": now.isoformat(),
    }
    flag_modified(handoff, "context")
    handoff.updated_at = now
    await db.commit()

    # Push to WebSocket — the delegating agent and anyone watching
    progress_event = {
        "type": "handoff.progress",
        "handoff_id": str(handoff_id),
        "phase": req.phase,
        "progress": req.progress,
        "message": req.message,
        "details": req.details,
        "timestamp": now.isoformat(),
    }

    # Send to delegating agent directly
    await manager.send_to_agent(handoff.from_agent_id, progress_event)
    # Broadcast to handoff room (if anyone joined)
    room = f"handoff:{handoff_id}"
    await manager.broadcast_to_room(room, progress_event, exclude=caller_id)

    logger.info(
        "handoff_progress",
        handoff_id=str(handoff_id),
        phase=req.phase,
        progress=req.progress,
    )

    return {
        "handoff_id": str(handoff_id),
        "phase": req.phase,
        "progress": req.progress,
        "timestamp": now.isoformat(),
    }


@router.post("/handoffs/{handoff_id}/checkpoint", status_code=201)
async def save_checkpoint(
    handoff_id: uuid.UUID,
    req: CheckpointRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Save a checkpoint during handoff execution.

    Checkpoints are saved state snapshots. If the executing agent fails,
    another agent can resume from the latest checkpoint instead of
    starting over.
    """
    handoff = await _get_handoff(db, handoff_id)
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")

    if caller_id != handoff.to_agent_id:
        raise HTTPException(status_code=403, detail="Only the receiving agent can save checkpoints")

    if handoff.status != "in_progress":
        raise HTTPException(status_code=400, detail="Handoff must be in_progress to save checkpoints")

    # Get next sequence number
    latest = await db.execute(
        select(HandoffCheckpoint.sequence)
        .where(HandoffCheckpoint.handoff_id == handoff_id)
        .order_by(desc(HandoffCheckpoint.sequence))
        .limit(1)
    )
    last_seq = latest.scalar()
    next_seq = (last_seq or 0) + 1

    checkpoint = HandoffCheckpoint(
        handoff_id=handoff_id,
        sequence=next_seq,
        phase=req.phase,
        state=req.state,
        agent_id=caller_id,
    )
    db.add(checkpoint)

    # Audit
    audit = AuditLog(
        entity_type="handoff",
        entity_id=handoff_id,
        action="checkpoint_saved",
        actor_agent_id=caller_id,
        details={"sequence": next_seq, "phase": req.phase},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(checkpoint)

    # Push checkpoint event via WebSocket
    checkpoint_event = {
        "type": "handoff.checkpoint",
        "handoff_id": str(handoff_id),
        "checkpoint": next_seq,
        "phase": req.phase,
        "timestamp": checkpoint.created_at.isoformat(),
    }
    await manager.send_to_agent(handoff.from_agent_id, checkpoint_event)
    room = f"handoff:{handoff_id}"
    await manager.broadcast_to_room(room, checkpoint_event, exclude=caller_id)

    logger.info(
        "checkpoint_saved",
        handoff_id=str(handoff_id),
        sequence=next_seq,
        phase=req.phase,
    )

    return {
        "id": str(checkpoint.id),
        "handoff_id": str(handoff_id),
        "sequence": next_seq,
        "phase": req.phase,
        "created_at": checkpoint.created_at.isoformat(),
    }


@router.get("/handoffs/{handoff_id}/checkpoints")
async def get_checkpoints(
    handoff_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> list[dict[str, Any]]:
    """Get all checkpoints for a handoff. Must be a participant."""
    handoff = await _get_handoff(db, handoff_id)
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")
    if caller_id not in (handoff.from_agent_id, handoff.to_agent_id):
        raise HTTPException(status_code=403, detail="Not a participant in this handoff")
    result = await db.execute(
        select(HandoffCheckpoint)
        .where(HandoffCheckpoint.handoff_id == handoff_id)
        .order_by(HandoffCheckpoint.sequence)
    )
    checkpoints = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "handoff_id": str(c.handoff_id),
            "sequence": c.sequence,
            "phase": c.phase,
            "state": c.state,
            "agent_id": str(c.agent_id),
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in checkpoints
    ]


@router.get("/handoffs/{handoff_id}/latest")
async def get_latest_progress(
    handoff_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Get the latest progress for a handoff. Must be a participant."""
    handoff = await _get_handoff(db, handoff_id)
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")
    if caller_id not in (handoff.from_agent_id, handoff.to_agent_id):
        raise HTTPException(status_code=403, detail="Not a participant in this handoff")

    # Progress from context
    progress = (handoff.context or {}).get("_progress", {})

    # Latest checkpoint
    latest_cp = await db.execute(
        select(HandoffCheckpoint)
        .where(HandoffCheckpoint.handoff_id == handoff_id)
        .order_by(desc(HandoffCheckpoint.sequence))
        .limit(1)
    )
    checkpoint = latest_cp.scalar_one_or_none()

    # Stall detection
    stall_detected = False
    if handoff.status == "in_progress":
        now = datetime.now(timezone.utc)
        last_update = progress.get("updated_at")
        if last_update:
            from datetime import datetime as dt
            last_dt = dt.fromisoformat(last_update)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            elapsed = (now - last_dt).total_seconds()
            # Stall if no update in 5 minutes
            stall_detected = elapsed > 300
        else:
            # No progress ever reported — check time since handoff started
            if handoff.updated_at:
                updated = handoff.updated_at
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                elapsed = (now - updated).total_seconds()
                stall_detected = elapsed > 300

    return {
        "handoff_id": str(handoff_id),
        "status": handoff.status,
        "phase": progress.get("phase"),
        "progress": progress.get("progress", 0.0),
        "message": progress.get("message"),
        "last_update": progress.get("updated_at"),
        "stall_detected": stall_detected,
        "latest_checkpoint": {
            "sequence": checkpoint.sequence,
            "phase": checkpoint.phase,
            "created_at": checkpoint.created_at.isoformat(),
        } if checkpoint else None,
    }


@router.post("/handoffs/{handoff_id}/resume-from-checkpoint")
async def resume_from_checkpoint(
    handoff_id: uuid.UUID,
    checkpoint_sequence: int = Query(..., ge=1),
    new_agent_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Resume a failed handoff from a specific checkpoint.

    Creates a new handoff with the checkpoint state as context,
    linked to the original via parent_handoff_id. Optionally
    reassigns to a different agent.
    """
    handoff = await _get_handoff(db, handoff_id)
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")

    if caller_id != handoff.from_agent_id:
        raise HTTPException(status_code=403, detail="Only the delegating agent can resume")

    if handoff.status not in ("failed", "rolled_back"):
        raise HTTPException(status_code=400, detail=f"Can only resume from failed/rolled_back (status: {handoff.status})")

    # Get the checkpoint
    result = await db.execute(
        select(HandoffCheckpoint).where(
            HandoffCheckpoint.handoff_id == handoff_id,
            HandoffCheckpoint.sequence == checkpoint_sequence,
        )
    )
    checkpoint = result.scalar_one_or_none()
    if not checkpoint:
        raise HTTPException(status_code=404, detail=f"Checkpoint #{checkpoint_sequence} not found")

    to_agent = new_agent_id or handoff.to_agent_id

    # Create a new handoff from the checkpoint
    new_context = dict(handoff.context or {})
    new_context["_resumed_from"] = {
        "handoff_id": str(handoff_id),
        "checkpoint_sequence": checkpoint_sequence,
        "checkpoint_phase": checkpoint.phase,
    }
    new_context["_checkpoint_state"] = checkpoint.state
    # Remove old progress
    new_context.pop("_progress", None)

    new_handoff = Handoff(
        negotiation_id=handoff.negotiation_id,
        from_agent_id=caller_id,
        to_agent_id=to_agent,
        context=new_context,
        chain_id=handoff.chain_id,
        chain_position=handoff.chain_position + 1,
        parent_handoff_id=handoff_id,
    )
    db.add(new_handoff)
    await db.flush()

    audit = AuditLog(
        entity_type="handoff",
        entity_id=new_handoff.id,
        action="resumed_from_checkpoint",
        actor_agent_id=caller_id,
        details={
            "original_handoff_id": str(handoff_id),
            "checkpoint_sequence": checkpoint_sequence,
            "new_agent_id": str(to_agent),
        },
    )
    db.add(audit)
    await db.commit()
    await db.refresh(new_handoff)

    logger.info(
        "handoff_resumed",
        original=str(handoff_id),
        new=str(new_handoff.id),
        checkpoint=checkpoint_sequence,
    )

    return {
        "id": str(new_handoff.id),
        "from_agent_id": str(new_handoff.from_agent_id),
        "to_agent_id": str(new_handoff.to_agent_id),
        "status": new_handoff.status,
        "parent_handoff_id": str(handoff_id),
        "resumed_from_checkpoint": checkpoint_sequence,
    }


# --- WebSocket handler for handoff progress ---

async def handle_ws_progress(agent_id: uuid.UUID, data: dict[str, Any]) -> None:
    """Handle progress updates received via WebSocket.

    Agents can report progress via WS instead of REST for lower latency.
    """
    handoff_id_str = data.get("handoff_id")
    if not handoff_id_str:
        await manager.send_to_agent(agent_id, {"type": "error", "detail": "Missing handoff_id"})
        return

    handoff_id = uuid.UUID(handoff_id_str)
    phase = data.get("phase", "")
    progress = data.get("progress", 0.0)
    message = data.get("message")

    from app.database import async_session as _async_session
    async with _async_session() as db:
        handoff = await _get_handoff(db, handoff_id)
        if not handoff or agent_id != handoff.to_agent_id:
            return

        if handoff.status != "in_progress":
            return

        now = datetime.now(timezone.utc)
        if not handoff.context:
            handoff.context = {}
        handoff.context["_progress"] = {
            "phase": phase,
            "progress": progress,
            "message": message,
            "updated_at": now.isoformat(),
        }
        flag_modified(handoff, "context")
        handoff.updated_at = now
        await db.commit()

    # Forward to delegating agent
    progress_event = {
        "type": "handoff.progress",
        "handoff_id": str(handoff_id),
        "phase": phase,
        "progress": progress,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await manager.send_to_agent(handoff.from_agent_id, progress_event)
    room = f"handoff:{handoff_id}"
    await manager.broadcast_to_room(room, progress_event, exclude=agent_id)


# --- Helpers ---

async def _get_handoff(db: AsyncSession, handoff_id: uuid.UUID) -> Handoff | None:
    result = await db.execute(select(Handoff).where(Handoff.id == handoff_id))
    return result.scalar_one_or_none()

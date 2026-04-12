"""Stake API — collateral-backed commitment for high-value handoffs.

Agents post stakes to signal confidence and create skin-in-the-game.
Stakes are held in escrow during execution and resolved based on outcome.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.handoff import Handoff
from app.models.stake import AgentStake, AgentBalance
from app.models.audit import AuditLog
from app.core.auth import check_authority, require_scope
from app.api.deps import get_current_agent, get_agent_id

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/stakes", tags=["stakes"])


# --- Request schemas ---

class PostStakeRequest(BaseModel):
    handoff_id: uuid.UUID
    amount: float
    conditions: dict | None = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "handoff_id": "550e8400-e29b-41d4-a716-446655440000",
                "amount": 10.0,
                "conditions": {"min_quality_score": 0.8, "max_latency_ms": 30000},
            }
        }
    }


# --- Endpoints ---

@router.post("", status_code=201)
async def post_stake(
    req: PostStakeRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
    claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Post a stake for a handoff. Locks credits from agent's available balance."""
    require_scope(claims, "handoff")
    check_authority(claims, spend=req.amount)

    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="Stake amount must be positive")

    # Verify handoff exists and agent is the receiver
    handoff = await db.get(Handoff, req.handoff_id)
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")
    if handoff.to_agent_id != caller_id:
        raise HTTPException(status_code=403, detail="Only the receiving agent can post a stake")
    if handoff.status not in ("initiated", "in_progress"):
        raise HTTPException(status_code=400, detail=f"Cannot stake on a {handoff.status} handoff")

    # Check for existing stake on this handoff
    existing = await db.execute(
        select(AgentStake).where(AgentStake.handoff_id == req.handoff_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A stake already exists for this handoff")

    # Get or create agent balance with row-level locking to prevent race conditions
    balance = await _get_or_create_balance(db, caller_id, lock=True)

    if balance.available < req.amount:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient balance. Available: {balance.available}, requested: {req.amount}",
        )

    # Lock the funds
    balance.available -= req.amount
    balance.staked += req.amount
    balance.updated_at = datetime.now(timezone.utc)

    # Create stake
    stake = AgentStake(
        agent_id=caller_id,
        handoff_id=req.handoff_id,
        amount=req.amount,
        status="held",
        conditions=req.conditions,
    )
    db.add(stake)
    await db.flush()

    # Audit
    audit = AuditLog(
        entity_type="stake",
        entity_id=stake.id,
        action="posted",
        actor_agent_id=caller_id,
        details={
            "amount": req.amount,
            "handoff_id": str(req.handoff_id),
            "conditions": req.conditions,
        },
    )
    db.add(audit)
    await db.commit()
    await db.refresh(stake)

    logger.info("stake_posted", stake_id=str(stake.id), agent=str(caller_id), amount=req.amount)

    return _stake_to_response(stake)


@router.get("/agent/{agent_id}")
async def get_agent_stakes(
    agent_id: uuid.UUID,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> list[dict[str, Any]]:
    """List all stakes for an agent. Only the agent themselves can view."""
    if caller_id != agent_id:
        raise HTTPException(status_code=403, detail="Can only view your own stakes")
    query = select(AgentStake).where(AgentStake.agent_id == agent_id)
    if status:
        query = query.where(AgentStake.status == status)
    query = query.order_by(AgentStake.created_at.desc())

    result = await db.execute(query)
    stakes = result.scalars().all()
    return [_stake_to_response(s) for s in stakes]


@router.get("/handoff/{handoff_id}")
async def get_handoff_stake(
    handoff_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Get the stake for a specific handoff. Only handoff participants can view."""
    # Verify caller is a participant in the handoff
    handoff = await db.get(Handoff, handoff_id)
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")
    if caller_id not in (handoff.from_agent_id, handoff.to_agent_id):
        raise HTTPException(status_code=403, detail="Not a participant in this handoff")

    result = await db.execute(
        select(AgentStake).where(AgentStake.handoff_id == handoff_id)
    )
    stake = result.scalar_one_or_none()
    if not stake:
        raise HTTPException(status_code=404, detail="No stake found for this handoff")
    return _stake_to_response(stake)


@router.get("/balance/{agent_id}")
async def get_agent_balance(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Get an agent's credit balance. Only the agent themselves can view."""
    if caller_id != agent_id:
        raise HTTPException(status_code=403, detail="Can only view your own balance")
    balance = await _get_or_create_balance(db, agent_id)
    return {
        "agent_id": str(balance.agent_id),
        "available": balance.available,
        "staked": balance.staked,
        "total_earned": balance.total_earned,
        "total_forfeited": balance.total_forfeited,
    }


@router.post("/{stake_id}/release")
async def release_stake(
    stake_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Release a held stake back to the agent (called on successful handoff completion).

    Only the delegating agent (from_agent) or the system can release a stake.
    """
    stake = await _get_stake_or_404(db, stake_id)
    if stake.status != "held":
        raise HTTPException(status_code=400, detail=f"Cannot release a {stake.status} stake")

    # Verify caller is the delegating agent
    handoff = await db.get(Handoff, stake.handoff_id)
    if not handoff or caller_id != handoff.from_agent_id:
        raise HTTPException(status_code=403, detail="Only the delegating agent can release a stake")

    # Release funds (return to available, don't count as earned — it's the agent's own money)
    balance = await _get_or_create_balance(db, stake.agent_id, lock=True)
    balance.staked -= stake.amount
    balance.available += stake.amount
    balance.updated_at = datetime.now(timezone.utc)

    stake.status = "released"
    stake.resolution_reason = "Handoff completed successfully"
    stake.resolved_at = datetime.now(timezone.utc)

    audit = AuditLog(
        entity_type="stake",
        entity_id=stake.id,
        action="released",
        actor_agent_id=caller_id,
        details={"amount": stake.amount, "agent_id": str(stake.agent_id)},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(stake)

    logger.info("stake_released", stake_id=str(stake.id), agent=str(stake.agent_id), amount=stake.amount)
    return _stake_to_response(stake)


@router.post("/{stake_id}/forfeit")
async def forfeit_stake(
    stake_id: uuid.UUID,
    reason: str = "Handoff failed",
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Forfeit a held stake (called on handoff failure).

    Only the delegating agent (from_agent) or the system can forfeit a stake.
    """
    stake = await _get_stake_or_404(db, stake_id)
    if stake.status != "held":
        raise HTTPException(status_code=400, detail=f"Cannot forfeit a {stake.status} stake")

    handoff = await db.get(Handoff, stake.handoff_id)
    if not handoff or caller_id != handoff.from_agent_id:
        raise HTTPException(status_code=403, detail="Only the delegating agent can forfeit a stake")

    # Forfeit funds
    balance = await _get_or_create_balance(db, stake.agent_id)
    balance.staked -= stake.amount
    balance.total_forfeited += stake.amount
    balance.updated_at = datetime.now(timezone.utc)

    stake.status = "forfeited"
    stake.resolution_reason = reason
    stake.resolved_at = datetime.now(timezone.utc)

    audit = AuditLog(
        entity_type="stake",
        entity_id=stake.id,
        action="forfeited",
        actor_agent_id=caller_id,
        details={"amount": stake.amount, "agent_id": str(stake.agent_id), "reason": reason},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(stake)

    logger.info("stake_forfeited", stake_id=str(stake.id), agent=str(stake.agent_id), amount=stake.amount)
    return _stake_to_response(stake)


@router.get("/{stake_id}")
async def get_stake(
    stake_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Get a single stake by ID. Only handoff participants can view."""
    stake = await _get_stake_or_404(db, stake_id)
    # Verify caller is a participant in the related handoff
    handoff = await db.get(Handoff, stake.handoff_id)
    if not handoff or caller_id not in (handoff.from_agent_id, handoff.to_agent_id):
        raise HTTPException(status_code=403, detail="Not a participant in this handoff")
    return _stake_to_response(stake)


# --- Helpers ---

async def _get_stake_or_404(db: AsyncSession, stake_id: uuid.UUID) -> AgentStake:
    result = await db.execute(select(AgentStake).where(AgentStake.id == stake_id))
    stake = result.scalar_one_or_none()
    if not stake:
        raise HTTPException(status_code=404, detail="Stake not found")
    return stake


async def _get_or_create_balance(db: AsyncSession, agent_id: uuid.UUID, lock: bool = False) -> AgentBalance:
    """Get or create an agent's balance record.

    Args:
        lock: If True, use SELECT FOR UPDATE to prevent race conditions
              on concurrent balance modifications.
    """
    query = select(AgentBalance).where(AgentBalance.agent_id == agent_id)
    if lock:
        query = query.with_for_update()
    result = await db.execute(query)
    balance = result.scalar_one_or_none()
    if not balance:
        balance = AgentBalance(agent_id=agent_id)
        db.add(balance)
        await db.flush()
    return balance


def _stake_to_response(s: AgentStake) -> dict[str, Any]:
    return {
        "id": s.id,
        "agent_id": s.agent_id,
        "handoff_id": s.handoff_id,
        "amount": s.amount,
        "currency": s.currency,
        "status": s.status,
        "conditions": s.conditions,
        "resolution_reason": s.resolution_reason,
        "resolved_at": s.resolved_at,
        "created_at": s.created_at,
    }

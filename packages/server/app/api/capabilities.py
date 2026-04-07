"""Capability contract API — register, discover, and validate agent capabilities.

This is the contract layer that makes agent-to-agent coordination type-safe.
Agents declare what they can do with typed schemas. Other agents discover
capabilities and know exactly what input format to send and what output to expect.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.capability import CapabilityContract
from app.models.audit import AuditLog
from app.api.deps import get_current_agent, get_agent_id

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/capabilities", tags=["capabilities"])


class RegisterCapabilityRequest(BaseModel):
    domain: str
    action: str
    version: str = "1.0.0"
    input_schema: dict = {}
    output_schema: dict = {}
    max_latency_ms: int | None = None
    availability_target: float | None = None
    max_concurrent: int | None = None
    constraints: dict = {}
    description: str | None = None
    examples: list = []


class UpdateCapabilityRequest(BaseModel):
    input_schema: dict | None = None
    output_schema: dict | None = None
    max_latency_ms: int | None = None
    availability_target: float | None = None
    max_concurrent: int | None = None
    constraints: dict | None = None
    description: str | None = None
    examples: list | None = None
    version: str | None = None


@router.post("", status_code=201)
async def register_capability(
    req: RegisterCapabilityRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Register a capability contract for the calling agent.

    If a contract already exists for this agent+domain+action, it's versioned up.
    """
    # Check for existing contract
    result = await db.execute(
        select(CapabilityContract).where(
            CapabilityContract.agent_id == caller_id,
            CapabilityContract.domain == req.domain,
            CapabilityContract.action == req.action,
            CapabilityContract.is_active == True,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Deactivate old version
        existing.is_active = False
        existing.updated_at = datetime.now(timezone.utc)

    contract = CapabilityContract(
        agent_id=caller_id,
        domain=req.domain,
        action=req.action,
        version=req.version,
        input_schema=req.input_schema,
        output_schema=req.output_schema,
        max_latency_ms=req.max_latency_ms,
        availability_target=req.availability_target,
        max_concurrent=req.max_concurrent,
        constraints=req.constraints,
        description=req.description,
        examples=req.examples,
    )
    db.add(contract)
    await db.flush()

    audit = AuditLog(
        entity_type="capability",
        entity_id=contract.id,
        action="registered",
        actor_agent_id=caller_id,
        details={"domain": req.domain, "action": req.action, "version": req.version},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(contract)

    logger.info("capability_registered", agent_id=str(caller_id), domain=req.domain, action=req.action)
    return _contract_to_response(contract)


@router.get("/mine")
async def list_my_capabilities(
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> list[dict[str, Any]]:
    """List all active capability contracts for the calling agent."""
    result = await db.execute(
        select(CapabilityContract)
        .where(CapabilityContract.agent_id == caller_id, CapabilityContract.is_active == True)
        .order_by(CapabilityContract.domain, CapabilityContract.action)
    )
    return [_contract_to_response(c) for c in result.scalars().all()]


@router.get("/discover")
async def discover_capabilities(
    domain: str | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> list[dict[str, Any]]:
    """Discover available capabilities across the network.

    This is how agents find other agents that can do what they need.
    """
    query = select(CapabilityContract).where(CapabilityContract.is_active == True)

    if domain:
        query = query.where(CapabilityContract.domain == domain)
    if action:
        query = query.where(CapabilityContract.action == action)

    query = query.order_by(CapabilityContract.domain, CapabilityContract.action).limit(limit)

    result = await db.execute(query)
    return [_contract_to_response(c) for c in result.scalars().all()]


@router.get("/{capability_id}")
async def get_capability(
    capability_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Get a specific capability contract with full schema details."""
    result = await db.execute(
        select(CapabilityContract).where(CapabilityContract.id == capability_id)
    )
    contract = result.scalar_one_or_none()
    if not contract:
        raise HTTPException(status_code=404, detail="Capability contract not found")
    return _contract_to_response(contract)


@router.patch("/{capability_id}")
async def update_capability(
    capability_id: uuid.UUID,
    req: UpdateCapabilityRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Update a capability contract. Only the owning agent can update."""
    result = await db.execute(
        select(CapabilityContract).where(CapabilityContract.id == capability_id)
    )
    contract = result.scalar_one_or_none()
    if not contract:
        raise HTTPException(status_code=404, detail="Capability contract not found")
    if contract.agent_id != caller_id:
        raise HTTPException(status_code=403, detail="Only the owning agent can update this contract")

    for field in req.model_fields_set:
        setattr(contract, field, getattr(req, field))
    contract.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(contract)
    return _contract_to_response(contract)


@router.delete("/{capability_id}", status_code=204)
async def deactivate_capability(
    capability_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> None:
    """Deactivate a capability contract."""
    result = await db.execute(
        select(CapabilityContract).where(CapabilityContract.id == capability_id)
    )
    contract = result.scalar_one_or_none()
    if not contract:
        raise HTTPException(status_code=404, detail="Capability contract not found")
    if contract.agent_id != caller_id:
        raise HTTPException(status_code=403, detail="Only the owning agent can deactivate this contract")

    contract.is_active = False
    contract.updated_at = datetime.now(timezone.utc)
    await db.commit()


def _contract_to_response(c: CapabilityContract) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "agent_id": str(c.agent_id),
        "domain": c.domain,
        "action": c.action,
        "version": c.version,
        "input_schema": c.input_schema,
        "output_schema": c.output_schema,
        "max_latency_ms": c.max_latency_ms,
        "availability_target": c.availability_target,
        "max_concurrent": c.max_concurrent,
        "constraints": c.constraints,
        "description": c.description,
        "examples": c.examples,
        "is_active": c.is_active,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }

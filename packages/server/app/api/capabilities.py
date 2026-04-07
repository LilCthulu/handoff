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


class ObligationDeclaration(BaseModel):
    """Binding commitments about data handling and external access."""
    data_retention: str = "none_after_completion"  # none_after_completion | 30_days | permanent
    pii_access: str = "sealed_references_only"  # sealed_references_only | committed_layer | none
    external_apis: list[str] = []  # declared external services the agent calls
    logging: str = "anonymized"  # anonymized | full | none
    data_sharing: str = "none"  # none | aggregated | third_party


VALID_DATA_RETENTION = {"none_after_completion", "30_days", "permanent"}
VALID_PII_ACCESS = {"sealed_references_only", "committed_layer", "none"}
VALID_LOGGING = {"anonymized", "full", "none"}
VALID_DATA_SHARING = {"none", "aggregated", "third_party"}


class RegisterCapabilityRequest(BaseModel):
    domain: str
    action: str
    version: str = "1.0.0"
    input_schema: dict = {}
    output_schema: dict = {}
    max_latency_ms: int | None = None
    availability_target: float | None = None
    max_concurrent: int | None = None
    obligations: dict = {}
    constraints: dict = {}
    description: str | None = None
    examples: list = []


class UpdateCapabilityRequest(BaseModel):
    input_schema: dict | None = None
    output_schema: dict | None = None
    max_latency_ms: int | None = None
    availability_target: float | None = None
    max_concurrent: int | None = None
    obligations: dict | None = None
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

    # Validate obligation values if provided
    if req.obligations:
        _validate_obligations(req.obligations)

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
        obligations=req.obligations,
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

    if req.obligations is not None:
        _validate_obligations(req.obligations)

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


def _validate_obligations(obligations: dict) -> None:
    """Validate obligation field values. Raises HTTPException on invalid values."""
    if "data_retention" in obligations and obligations["data_retention"] not in VALID_DATA_RETENTION:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid data_retention. Must be one of: {', '.join(sorted(VALID_DATA_RETENTION))}",
        )
    if "pii_access" in obligations and obligations["pii_access"] not in VALID_PII_ACCESS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid pii_access. Must be one of: {', '.join(sorted(VALID_PII_ACCESS))}",
        )
    if "logging" in obligations and obligations["logging"] not in VALID_LOGGING:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid logging. Must be one of: {', '.join(sorted(VALID_LOGGING))}",
        )
    if "data_sharing" in obligations and obligations["data_sharing"] not in VALID_DATA_SHARING:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid data_sharing. Must be one of: {', '.join(sorted(VALID_DATA_SHARING))}",
        )
    if "external_apis" in obligations:
        if not isinstance(obligations["external_apis"], list):
            raise HTTPException(status_code=400, detail="external_apis must be a list of strings")
        for api in obligations["external_apis"]:
            if not isinstance(api, str):
                raise HTTPException(status_code=400, detail="Each external_apis entry must be a string")


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
        "obligations": c.obligations,
        "constraints": c.constraints,
        "description": c.description,
        "examples": c.examples,
        "is_active": c.is_active,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }

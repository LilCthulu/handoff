"""Context privacy API — sealed references and data minimization.

Agents interact with PII through opaque reference tokens. The actual
values never leave the server. This endpoint resolves tokens when
the agent needs a value (e.g., to make an API call with a real email).

Access is controlled: only the receiving agent on the handoff can
resolve sealed references, and each reference has a TTL and access limit.
"""

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.handoff import Handoff
from app.services.context_privacy import (
    resolve_sealed,
    revoke_sealed,
    seal_value,
    split_context_layers,
    minimize_context,
    generate_pseudonym,
)
from app.api.deps import get_current_agent, get_agent_id

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/context", tags=["context"])


class SealRequest(BaseModel):
    value: Any
    context: str = ""  # label like "user_email"
    ttl_minutes: int = 60


class ResolveRequest(BaseModel):
    token: str
    handoff_id: uuid.UUID  # must be a participant to resolve


class PseudonymRequest(BaseModel):
    identifier: str
    salt: str = ""


@router.post("/seal")
async def seal(
    req: SealRequest,
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Seal a PII value into an opaque reference token.

    The delegating agent seals sensitive values before including them
    in the handoff context. The receiving agent gets tokens, not raw data.
    """
    token = seal_value(req.value, context=req.context, ttl_minutes=req.ttl_minutes, sealed_by=str(caller_id))
    logger.info("pii_sealed", agent=str(caller_id), context=req.context)
    return {"token": token, "ttl_minutes": req.ttl_minutes}


@router.post("/resolve")
async def resolve(
    req: ResolveRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Resolve a sealed reference token to its actual value.

    Only the receiving agent on the associated handoff can resolve.
    Access is logged and rate-limited per token.
    """
    # Verify the caller is the receiving agent on this handoff
    result = await db.execute(select(Handoff).where(Handoff.id == req.handoff_id))
    handoff = result.scalar_one_or_none()
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")

    if caller_id != handoff.to_agent_id:
        raise HTTPException(
            status_code=403,
            detail="Only the receiving agent can resolve sealed references",
        )

    value, success = resolve_sealed(req.token)
    if not success:
        raise HTTPException(
            status_code=404,
            detail="Sealed reference not found, expired, or access limit reached",
        )

    logger.info(
        "sealed_ref_resolved",
        agent=str(caller_id),
        handoff=str(req.handoff_id),
        token=req.token[:20],
    )

    return {"value": value}


@router.post("/revoke")
async def revoke(
    token: str,
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Revoke a sealed reference — delete the PII immediately.

    The delegating agent can revoke references after the handoff
    completes, ensuring PII doesn't persist beyond its purpose.
    """
    success = revoke_sealed(token, caller_id=str(caller_id))
    if not success:
        raise HTTPException(status_code=404, detail="Token not found or not owned by you")

    logger.info("sealed_ref_revoked", agent=str(caller_id), token=token[:20])
    return {"revoked": True}


@router.post("/pseudonym")
async def pseudonym(
    req: PseudonymRequest,
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, str]:
    """Generate a pseudonymous identifier for a real identifier.

    Deterministic: same input + salt always produces the same pseudonym,
    allowing correlation without exposing the real value.
    """
    return {"pseudonym": generate_pseudonym(req.identifier, req.salt)}

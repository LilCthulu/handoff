"""Attestation API — cryptographic proof of work between agents.

After a handoff completes, the delegating agent signs an attestation
vouching for the work quality. These attestations form a verifiable
chain of trust that any agent can audit before entering a negotiation.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import canonical_json, public_key_fingerprint, verify_signature
from app.database import get_db
from app.models.agent import Agent
from app.models.attestation import Attestation
from app.models.handoff import Handoff
from app.api.deps import get_current_agent, get_agent_id

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/attestations", tags=["attestations"])


# --- Request schemas ---

class CreateAttestationRequest(BaseModel):
    handoff_id: uuid.UUID
    outcome: str = Field(..., pattern="^(success|failure|partial)$")
    rating: float | None = Field(None, ge=0.0, le=1.0)
    claim: dict
    signature: str  # base64 Ed25519 signature of canonical claim JSON


class AttestationResponse(BaseModel):
    id: uuid.UUID
    attester_id: uuid.UUID
    subject_id: uuid.UUID
    handoff_id: uuid.UUID
    domain: str
    outcome: str
    rating: float | None
    claim: dict
    signature: str
    verified: bool
    created_at: datetime


# --- Endpoints ---

@router.post("", status_code=201)
async def create_attestation(
    req: CreateAttestationRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Create a signed attestation for a completed handoff.

    The attesting agent (who delegated the work) signs a claim about
    the quality of work performed by the receiving agent. The server
    verifies the signature before storing.
    """
    # Get the handoff
    result = await db.execute(select(Handoff).where(Handoff.id == req.handoff_id))
    handoff = result.scalar_one_or_none()
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")

    # Only the delegating agent can attest
    if caller_id != handoff.from_agent_id:
        raise HTTPException(
            status_code=403,
            detail="Only the delegating agent can create an attestation",
        )

    # Handoff must be completed
    if handoff.status not in ("completed", "failed", "rolled_back"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot attest a handoff with status '{handoff.status}'",
        )

    # Check for duplicate
    existing = await db.execute(
        select(Attestation).where(Attestation.handoff_id == req.handoff_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Attestation already exists for this handoff")

    # Get the attesting agent's public key for verification
    attester = await db.get(Agent, caller_id)
    if not attester:
        raise HTTPException(status_code=404, detail="Attesting agent not found")

    # Verify the signature — reject invalid signatures outright
    import base64
    import nacl.signing
    try:
        claim_bytes = canonical_json(req.claim)
        sig_bytes = base64.b64decode(req.signature)
        public_key_raw = base64.b64decode(attester.public_key)
        verify_key = nacl.signing.VerifyKey(public_key_raw)
        verify_key.verify(claim_bytes, sig_bytes)
        verified = True
    except Exception:
        logger.warning("attestation_signature_invalid", attester=str(caller_id))
        raise HTTPException(
            status_code=400,
            detail="Attestation signature is invalid — claim must be signed with your Ed25519 key",
        )

    # Determine domain from handoff context or claim
    domain = req.claim.get("domain", "general")
    if domain == "general" and handoff.context:
        domain = handoff.context.get("domain", "general")

    fingerprint = public_key_fingerprint(attester.public_key)

    attestation = Attestation(
        attester_id=caller_id,
        attester_key_fingerprint=fingerprint,
        subject_id=handoff.to_agent_id,
        handoff_id=req.handoff_id,
        domain=domain,
        outcome=req.outcome,
        rating=req.rating,
        claim=req.claim,
        signature=req.signature,
        verified=verified,
    )
    db.add(attestation)
    await db.commit()
    await db.refresh(attestation)

    logger.info(
        "attestation_created",
        attester=str(caller_id),
        subject=str(handoff.to_agent_id),
        domain=domain,
        outcome=req.outcome,
        verified=verified,
    )

    return _attestation_to_response(attestation)


@router.get("/agent/{agent_id}")
async def get_agent_attestations(
    agent_id: uuid.UUID,
    domain: str | None = Query(None),
    outcome: str | None = Query(None),
    verified_only: bool = Query(True),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Get attestations received by an agent.

    This is the public trust record — any agent can query another's
    attestation history to decide whether to negotiate with them.
    """
    query = select(Attestation).where(Attestation.subject_id == agent_id)

    if domain:
        query = query.where(Attestation.domain == domain)
    if outcome:
        query = query.where(Attestation.outcome == outcome)
    if verified_only:
        query = query.where(Attestation.verified == True)

    query = query.order_by(desc(Attestation.created_at)).limit(limit)

    result = await db.execute(query)
    attestations = result.scalars().all()
    return [_attestation_to_response(a) for a in attestations]


@router.get("/agent/{agent_id}/summary")
async def get_agent_attestation_summary(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Attestation summary for an agent — aggregated by domain.

    Returns verified attestation counts, average ratings, and
    success/failure ratios per domain. This is the trust profile
    that agents use to evaluate potential partners.
    """
    # Count by domain and outcome (verified only)
    result = await db.execute(
        select(
            Attestation.domain,
            Attestation.outcome,
            func.count(Attestation.id).label("count"),
            func.avg(Attestation.rating).label("avg_rating"),
        )
        .where(Attestation.subject_id == agent_id, Attestation.verified == True)
        .group_by(Attestation.domain, Attestation.outcome)
    )

    rows = result.all()

    # Aggregate into domain summaries
    domains: dict[str, dict[str, Any]] = {}
    for domain, outcome, count, avg_rating in rows:
        if domain not in domains:
            domains[domain] = {"success": 0, "failure": 0, "partial": 0, "total": 0, "avg_rating": None, "ratings": []}
        domains[domain][outcome] = count
        domains[domain]["total"] += count
        if avg_rating is not None:
            domains[domain]["ratings"].append((count, float(avg_rating)))

    # Compute weighted average ratings
    for d in domains.values():
        if d["ratings"]:
            total_weight = sum(c for c, _ in d["ratings"])
            d["avg_rating"] = round(sum(c * r for c, r in d["ratings"]) / total_weight, 3) if total_weight else None
        del d["ratings"]

    # Total attestation count
    total_result = await db.execute(
        select(func.count(Attestation.id))
        .where(Attestation.subject_id == agent_id, Attestation.verified == True)
    )
    total = total_result.scalar() or 0

    return {
        "agent_id": str(agent_id),
        "total_attestations": total,
        "domains": domains,
    }


@router.get("/{attestation_id}")
async def get_attestation(
    attestation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get a single attestation by ID — for independent verification."""
    result = await db.execute(
        select(Attestation).where(Attestation.id == attestation_id)
    )
    attestation = result.scalar_one_or_none()
    if not attestation:
        raise HTTPException(status_code=404, detail="Attestation not found")
    return _attestation_to_response(attestation)


@router.post("/{attestation_id}/verify")
async def verify_attestation(
    attestation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Re-verify an attestation's signature against the attester's current public key.

    Useful for checking attestation validity after key rotation.
    """
    result = await db.execute(
        select(Attestation).where(Attestation.id == attestation_id)
    )
    attestation = result.scalar_one_or_none()
    if not attestation:
        raise HTTPException(status_code=404, detail="Attestation not found")

    # Get attester's current public key
    attester = await db.get(Agent, attestation.attester_id)
    if not attester:
        return {"verified": False, "reason": "Attester agent no longer exists"}

    import base64
    import nacl.signing
    try:
        claim_bytes = canonical_json(attestation.claim)
        sig_bytes = base64.b64decode(attestation.signature)
        public_key_raw = base64.b64decode(attester.public_key)
        verify_key = nacl.signing.VerifyKey(public_key_raw)
        verify_key.verify(claim_bytes, sig_bytes)

        attestation.verified = True
        await db.commit()

        return {"verified": True, "attestation_id": str(attestation_id)}
    except Exception as e:
        return {"verified": False, "reason": "Signature verification failed"}


# --- Helpers ---

def _attestation_to_response(a: Attestation) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "attester_id": str(a.attester_id),
        "attester_key_fingerprint": a.attester_key_fingerprint,
        "subject_id": str(a.subject_id),
        "handoff_id": str(a.handoff_id),
        "domain": a.domain,
        "outcome": a.outcome,
        "rating": a.rating,
        "claim": a.claim,
        "signature": a.signature,
        "verified": a.verified,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }

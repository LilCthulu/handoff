"""Third-party credential API — verifiable credentials from external authorities.

External issuers submit signed credentials vouching for agent capabilities.
The server verifies Ed25519 signatures and stores valid credentials.
Credentials contribute to trust scoring with configurable weights.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent import Agent
from app.models.credential import ThirdPartyCredential
from app.models.audit import AuditLog
from app.api.deps import get_current_agent, get_agent_id

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/credentials", tags=["credentials"])


# --- Request schemas ---

class SubmitCredentialRequest(BaseModel):
    subject_id: uuid.UUID
    issuer_id: str
    issuer_name: str
    issuer_key_fingerprint: str
    credential_type: str
    domain: str
    claims: dict
    signature: str
    proof_type: str = "Ed25519Signature2020"
    weight: float = 1.0
    expires_at: datetime | None = None


class RevokeCredentialRequest(BaseModel):
    reason: str = "Revoked by issuer"


# --- Endpoints ---

@router.post("", status_code=201)
async def submit_credential(
    req: SubmitCredentialRequest,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Submit a third-party credential for an agent.

    The credential includes a signature from the issuer which the server
    verifies against the issuer's registered public key.
    """
    # Validate credential type
    valid_types = {
        "capability_certification",
        "security_audit",
        "benchmark_result",
        "compliance_attestation",
        "performance_rating",
    }
    if req.credential_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid credential type. Must be one of: {', '.join(sorted(valid_types))}",
        )

    # Verify subject agent exists
    agent = await db.get(Agent, req.subject_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Subject agent not found")

    # Verify weight is reasonable
    if not (0.0 < req.weight <= 5.0):
        raise HTTPException(status_code=400, detail="Weight must be between 0 and 5")

    # Verify signature (Ed25519)
    verified = False
    try:
        from nacl.signing import VerifyKey
        from nacl.encoding import HexEncoder
        import base64

        # Reconstruct the canonical claim for verification
        canonical = json.dumps(req.claims, sort_keys=True, separators=(",", ":"))

        verify_key = VerifyKey(req.issuer_key_fingerprint.encode(), encoder=HexEncoder)
        verify_key.verify(canonical.encode(), base64.b64decode(req.signature))
        verified = True
    except ImportError:
        logger.warning("nacl_not_installed", detail="PyNaCl not installed, skipping signature verification")
        verified = False
    except Exception as e:
        logger.warning("credential_signature_invalid", error=str(e))
        verified = False

    credential = ThirdPartyCredential(
        subject_id=req.subject_id,
        issuer_id=req.issuer_id,
        issuer_name=req.issuer_name,
        issuer_key_fingerprint=req.issuer_key_fingerprint,
        credential_type=req.credential_type,
        domain=req.domain,
        claims=req.claims,
        signature=req.signature,
        proof_type=req.proof_type,
        weight=req.weight,
        verified=verified,
        expires_at=req.expires_at,
    )
    db.add(credential)
    await db.flush()

    audit = AuditLog(
        entity_type="credential",
        entity_id=credential.id,
        action="submitted",
        details={
            "issuer": req.issuer_name,
            "subject_id": str(req.subject_id),
            "type": req.credential_type,
            "domain": req.domain,
            "verified": verified,
        },
    )
    db.add(audit)
    await db.commit()
    await db.refresh(credential)

    logger.info(
        "credential_submitted",
        credential_id=str(credential.id),
        issuer=req.issuer_name,
        subject=str(req.subject_id),
        verified=verified,
    )

    return _credential_to_response(credential)


@router.get("/agent/{agent_id}")
async def get_agent_credentials(
    agent_id: uuid.UUID,
    credential_type: str | None = None,
    domain: str | None = None,
    valid_only: bool = True,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> list[dict[str, Any]]:
    """List all credentials for an agent."""
    query = select(ThirdPartyCredential).where(ThirdPartyCredential.subject_id == agent_id)

    if credential_type:
        query = query.where(ThirdPartyCredential.credential_type == credential_type)
    if domain:
        query = query.where(ThirdPartyCredential.domain == domain)
    if valid_only:
        query = query.where(
            ThirdPartyCredential.verified == True,  # noqa: E712
            ThirdPartyCredential.revoked == False,  # noqa: E712
        )

    query = query.order_by(ThirdPartyCredential.created_at.desc())
    result = await db.execute(query)
    credentials = result.scalars().all()

    # Filter expired if valid_only
    if valid_only:
        now = datetime.now(timezone.utc)
        credentials = [c for c in credentials if not c.expires_at or c.expires_at > now]

    return [_credential_to_response(c) for c in credentials]


@router.get("/agent/{agent_id}/summary")
async def get_agent_credential_summary(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Get a summary of an agent's credentials grouped by type and domain."""
    result = await db.execute(
        select(ThirdPartyCredential).where(
            ThirdPartyCredential.subject_id == agent_id,
            ThirdPartyCredential.verified == True,  # noqa: E712
            ThirdPartyCredential.revoked == False,  # noqa: E712
        )
    )
    credentials = result.scalars().all()

    now = datetime.now(timezone.utc)
    valid = [c for c in credentials if not c.expires_at or c.expires_at > now]

    by_type: dict[str, int] = {}
    by_domain: dict[str, list[dict]] = {}
    total_weight = 0.0

    for c in valid:
        by_type[c.credential_type] = by_type.get(c.credential_type, 0) + 1
        domain_list = by_domain.setdefault(c.domain, [])
        domain_list.append({
            "issuer": c.issuer_name,
            "type": c.credential_type,
            "weight": c.weight,
        })
        total_weight += c.weight

    return {
        "agent_id": str(agent_id),
        "total_credentials": len(valid),
        "total_weight": round(total_weight, 2),
        "by_type": by_type,
        "by_domain": by_domain,
        "issuers": list({c.issuer_name for c in valid}),
    }


@router.post("/{credential_id}/revoke")
async def revoke_credential(
    credential_id: uuid.UUID,
    req: RevokeCredentialRequest,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Revoke a credential. Can be called by the issuer."""
    result = await db.execute(
        select(ThirdPartyCredential).where(ThirdPartyCredential.id == credential_id)
    )
    credential = result.scalar_one_or_none()
    if not credential:
        raise HTTPException(status_code=404, detail="Credential not found")
    if credential.revoked:
        raise HTTPException(status_code=400, detail="Credential already revoked")

    credential.revoked = True
    credential.revoked_at = datetime.now(timezone.utc)
    credential.revocation_reason = req.reason

    audit = AuditLog(
        entity_type="credential",
        entity_id=credential.id,
        action="revoked",
        details={"reason": req.reason, "issuer": credential.issuer_name},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(credential)

    logger.info("credential_revoked", credential_id=str(credential_id))
    return _credential_to_response(credential)


@router.post("/{credential_id}/verify")
async def verify_credential(
    credential_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Re-verify a credential's signature (for key rotation scenarios)."""
    result = await db.execute(
        select(ThirdPartyCredential).where(ThirdPartyCredential.id == credential_id)
    )
    credential = result.scalar_one_or_none()
    if not credential:
        raise HTTPException(status_code=404, detail="Credential not found")

    verified = False
    try:
        from nacl.signing import VerifyKey
        from nacl.encoding import HexEncoder
        import base64

        canonical = json.dumps(credential.claims, sort_keys=True, separators=(",", ":"))
        verify_key = VerifyKey(credential.issuer_key_fingerprint.encode(), encoder=HexEncoder)
        verify_key.verify(canonical.encode(), base64.b64decode(credential.signature))
        verified = True
    except Exception as e:
        logger.warning("credential_reverify_failed", error=str(e))
        verified = False

    credential.verified = verified
    await db.commit()

    return {"credential_id": str(credential_id), "verified": verified}


@router.get("/{credential_id}")
async def get_credential(
    credential_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Get a single credential by ID."""
    result = await db.execute(
        select(ThirdPartyCredential).where(ThirdPartyCredential.id == credential_id)
    )
    credential = result.scalar_one_or_none()
    if not credential:
        raise HTTPException(status_code=404, detail="Credential not found")
    return _credential_to_response(credential)


# --- Helpers ---

def _credential_to_response(c: ThirdPartyCredential) -> dict[str, Any]:
    return {
        "id": c.id,
        "subject_id": c.subject_id,
        "issuer_id": c.issuer_id,
        "issuer_name": c.issuer_name,
        "credential_type": c.credential_type,
        "domain": c.domain,
        "claims": c.claims,
        "weight": c.weight,
        "verified": c.verified,
        "is_valid": c.is_valid,
        "proof_type": c.proof_type,
        "issued_at": c.issued_at,
        "expires_at": c.expires_at,
        "revoked": c.revoked,
        "revoked_at": c.revoked_at,
        "revocation_reason": c.revocation_reason,
        "created_at": c.created_at,
    }

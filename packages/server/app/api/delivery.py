"""Delivery receipt API — cryptographic proof of result exchange.

After agent B completes work and submits a result, it signs a delivery
receipt. Agent A then acknowledges receipt by signing acceptance or
rejection. Both signatures are Ed25519 and independently verifiable.

This closes the accountability loop: every result has a signed sender
and a signed receiver. No he-said-she-said.
"""

import base64
import uuid
from datetime import datetime, timezone
from typing import Any

import nacl.signing
import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import canonical_json, hash_payload, public_key_fingerprint
from app.database import get_db
from app.models.agent import Agent
from app.models.delivery import DeliveryReceipt
from app.models.handoff import Handoff
from app.api.deps import get_current_agent, get_agent_id

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/delivery", tags=["delivery"])


# --- Request schemas ---

class SubmitDeliveryRequest(BaseModel):
    handoff_id: uuid.UUID
    result_hash: str  # sha256:<hex> — hash of the canonical result JSON
    signature: str  # base64 Ed25519 signature of result_hash
    proof: dict | None = None  # optional proof of work


class AcknowledgeDeliveryRequest(BaseModel):
    accepted: bool
    rejection_reason: str | None = None
    signature: str  # base64 Ed25519 signature of "{receipt_id}:{accepted}"


# --- Endpoints ---

@router.post("", status_code=201)
async def submit_delivery_receipt(
    req: SubmitDeliveryRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Submit a signed delivery receipt for a completed handoff.

    The receiving agent (who performed the work) signs the result hash
    to prove they produced it. The server verifies the signature.
    """
    # Get the handoff
    result = await db.execute(select(Handoff).where(Handoff.id == req.handoff_id))
    handoff = result.scalar_one_or_none()
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")

    # Only the receiving agent can submit a delivery receipt
    if caller_id != handoff.to_agent_id:
        raise HTTPException(status_code=403, detail="Only the receiving agent can submit a delivery receipt")

    # Handoff must be completed
    if handoff.status != "completed":
        raise HTTPException(status_code=400, detail=f"Cannot deliver a handoff with status '{handoff.status}'")

    # Verify result hash matches the actual result
    if handoff.result:
        expected_hash = hash_payload(handoff.result)
        if req.result_hash != expected_hash:
            raise HTTPException(
                status_code=400,
                detail=f"Result hash mismatch: expected {expected_hash}, got {req.result_hash}",
            )

    # Check for duplicate
    existing = await db.execute(
        select(DeliveryReceipt).where(DeliveryReceipt.handoff_id == req.handoff_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Delivery receipt already exists for this handoff")

    # Get the delivering agent's public key
    agent = await db.get(Agent, caller_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Verify the delivery signature
    verified = _verify_ed25519(req.result_hash.encode(), req.signature, agent.public_key)

    fingerprint = public_key_fingerprint(agent.public_key)

    receipt = DeliveryReceipt(
        handoff_id=req.handoff_id,
        delivered_by=caller_id,
        delivery_key_fingerprint=fingerprint,
        result_hash=req.result_hash,
        delivery_signature=req.signature,
        proof=req.proof,
        delivery_verified=verified,
    )
    db.add(receipt)
    await db.commit()
    await db.refresh(receipt)

    logger.info(
        "delivery_receipt_submitted",
        handoff_id=str(req.handoff_id),
        delivered_by=str(caller_id),
        verified=verified,
    )

    return _receipt_to_response(receipt)


@router.post("/{receipt_id}/acknowledge")
async def acknowledge_delivery(
    receipt_id: uuid.UUID,
    req: AcknowledgeDeliveryRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Acknowledge (accept or reject) a delivery receipt.

    The delegating agent (who requested the work) signs acceptance
    or rejection. This completes the cryptographic accountability chain.
    """
    result = await db.execute(
        select(DeliveryReceipt).where(DeliveryReceipt.id == receipt_id)
    )
    receipt = result.scalar_one_or_none()
    if not receipt:
        raise HTTPException(status_code=404, detail="Delivery receipt not found")

    # Get the handoff to verify caller is the delegating agent
    handoff_result = await db.execute(
        select(Handoff).where(Handoff.id == receipt.handoff_id)
    )
    handoff = handoff_result.scalar_one_or_none()
    if not handoff:
        raise HTTPException(status_code=404, detail="Handoff not found")

    if caller_id != handoff.from_agent_id:
        raise HTTPException(status_code=403, detail="Only the delegating agent can acknowledge delivery")

    if receipt.acknowledged_by is not None:
        raise HTTPException(status_code=409, detail="Delivery already acknowledged")

    # Get the acknowledging agent's public key
    agent = await db.get(Agent, caller_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Verify acknowledgment signature — signs "{receipt_id}:{accepted}"
    ack_message = f"{receipt_id}:{req.accepted}".encode()
    verified = _verify_ed25519(ack_message, req.signature, agent.public_key)

    fingerprint = public_key_fingerprint(agent.public_key)

    receipt.acknowledged_by = caller_id
    receipt.acknowledgment_key_fingerprint = fingerprint
    receipt.accepted = req.accepted
    receipt.rejection_reason = req.rejection_reason
    receipt.acknowledgment_signature = req.signature
    receipt.acknowledgment_verified = verified
    receipt.acknowledged_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(receipt)

    logger.info(
        "delivery_acknowledged",
        receipt_id=str(receipt_id),
        acknowledged_by=str(caller_id),
        accepted=req.accepted,
        verified=verified,
    )

    return _receipt_to_response(receipt)


@router.get("/handoff/{handoff_id}")
async def get_delivery_receipt_by_handoff(
    handoff_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Get the delivery receipt for a handoff."""
    result = await db.execute(
        select(DeliveryReceipt).where(DeliveryReceipt.handoff_id == handoff_id)
    )
    receipt = result.scalar_one_or_none()
    if not receipt:
        raise HTTPException(status_code=404, detail="No delivery receipt for this handoff")
    return _receipt_to_response(receipt)


@router.get("/{receipt_id}")
async def get_delivery_receipt(
    receipt_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Get a delivery receipt by ID."""
    result = await db.execute(
        select(DeliveryReceipt).where(DeliveryReceipt.id == receipt_id)
    )
    receipt = result.scalar_one_or_none()
    if not receipt:
        raise HTTPException(status_code=404, detail="Delivery receipt not found")
    return _receipt_to_response(receipt)


@router.post("/{receipt_id}/verify")
async def verify_delivery_receipt(
    receipt_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Re-verify both signatures on a delivery receipt.

    Useful for auditing — checks signatures against current public keys.
    """
    result = await db.execute(
        select(DeliveryReceipt).where(DeliveryReceipt.id == receipt_id)
    )
    receipt = result.scalar_one_or_none()
    if not receipt:
        raise HTTPException(status_code=404, detail="Delivery receipt not found")

    report: dict[str, Any] = {"receipt_id": str(receipt_id)}

    # Verify delivery signature
    deliverer = await db.get(Agent, receipt.delivered_by)
    if deliverer:
        delivery_ok = _verify_ed25519(
            receipt.result_hash.encode(), receipt.delivery_signature, deliverer.public_key
        )
        receipt.delivery_verified = delivery_ok
        report["delivery_verified"] = delivery_ok
    else:
        report["delivery_verified"] = False
        report["delivery_error"] = "Delivering agent no longer exists"

    # Verify acknowledgment signature (if present)
    if receipt.acknowledged_by and receipt.acknowledgment_signature:
        acknowledger = await db.get(Agent, receipt.acknowledged_by)
        if acknowledger:
            ack_message = f"{receipt_id}:{receipt.accepted}".encode()
            ack_ok = _verify_ed25519(
                ack_message, receipt.acknowledgment_signature, acknowledger.public_key
            )
            receipt.acknowledgment_verified = ack_ok
            report["acknowledgment_verified"] = ack_ok
        else:
            report["acknowledgment_verified"] = False
            report["acknowledgment_error"] = "Acknowledging agent no longer exists"
    else:
        report["acknowledgment_verified"] = None
        report["acknowledgment_note"] = "Not yet acknowledged"

    await db.commit()
    return report


# --- Helpers ---

def _verify_ed25519(message: bytes, signature_b64: str, public_key_b64: str) -> bool:
    """Verify an Ed25519 signature."""
    try:
        sig_bytes = base64.b64decode(signature_b64)
        public_key_raw = base64.b64decode(public_key_b64)
        verify_key = nacl.signing.VerifyKey(public_key_raw)
        verify_key.verify(message, sig_bytes)
        return True
    except Exception:
        return False


def _receipt_to_response(r: DeliveryReceipt) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "handoff_id": str(r.handoff_id),
        "delivered_by": str(r.delivered_by),
        "delivery_key_fingerprint": r.delivery_key_fingerprint,
        "result_hash": r.result_hash,
        "delivery_signature": r.delivery_signature,
        "delivery_verified": r.delivery_verified,
        "proof": r.proof,
        "delivered_at": r.delivered_at.isoformat() if r.delivered_at else None,
        "acknowledged_by": str(r.acknowledged_by) if r.acknowledged_by else None,
        "acknowledgment_key_fingerprint": r.acknowledgment_key_fingerprint,
        "accepted": r.accepted,
        "rejection_reason": r.rejection_reason,
        "acknowledgment_signature": r.acknowledgment_signature,
        "acknowledgment_verified": r.acknowledgment_verified,
        "acknowledged_at": r.acknowledged_at.isoformat() if r.acknowledged_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }

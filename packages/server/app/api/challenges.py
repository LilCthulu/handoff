"""Capability challenge API — proof-of-competence before trust.

Any agent can challenge another to prove it actually handles a domain.
The challenger provides a test input conforming to the target's declared
contract schema; the target must return a valid response within the
time limit. Passed challenges feed into trust scoring.

This prevents agents from registering capabilities they can't deliver.
Talk is cheap — challenges make agents prove it.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.attestation import CapabilityChallenge
from app.models.capability import CapabilityContract
from app.api.deps import get_current_agent, get_agent_id

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/challenges", tags=["challenges"])


# --- Request schemas ---

class IssueChallengeRequest(BaseModel):
    agent_id: uuid.UUID  # who to challenge
    domain: str
    action: str
    challenge_input: dict  # test input conforming to contract input_schema
    max_time_ms: int = Field(5000, ge=500, le=60000)


class RespondChallengeRequest(BaseModel):
    response: dict


class ChallengeResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    domain: str
    action: str
    challenge_input: dict
    expected_schema: dict
    max_time_ms: int
    response: dict | None
    response_time_ms: float | None
    status: str
    failure_reason: str | None
    issued_by: uuid.UUID | None
    created_at: datetime
    completed_at: datetime | None


# --- Endpoints ---

@router.post("", status_code=201)
async def issue_challenge(
    req: IssueChallengeRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Issue a capability challenge to another agent.

    The challenger picks a domain+action and provides test input.
    The challenged agent must respond with valid output within the
    time limit to pass.
    """
    # Can't challenge yourself
    if caller_id == req.agent_id:
        raise HTTPException(status_code=400, detail="Cannot challenge yourself")

    # Find the target agent's active contract for this domain+action
    result = await db.execute(
        select(CapabilityContract).where(
            CapabilityContract.agent_id == req.agent_id,
            CapabilityContract.domain == req.domain,
            CapabilityContract.action == req.action,
            CapabilityContract.is_active == True,
        )
    )
    contract = result.scalar_one_or_none()
    if not contract:
        raise HTTPException(
            status_code=404,
            detail=f"No active capability contract found for agent in {req.domain}.{req.action}",
        )

    # Check for existing pending challenge (prevent spam)
    existing = await db.execute(
        select(CapabilityChallenge).where(
            CapabilityChallenge.agent_id == req.agent_id,
            CapabilityChallenge.domain == req.domain,
            CapabilityChallenge.action == req.action,
            CapabilityChallenge.status == "pending",
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="A pending challenge already exists for this agent+domain+action",
        )

    challenge = CapabilityChallenge(
        agent_id=req.agent_id,
        domain=req.domain,
        action=req.action,
        challenge_input=req.challenge_input,
        expected_schema=contract.output_schema,
        max_time_ms=req.max_time_ms,
        issued_by=caller_id,
    )
    db.add(challenge)
    await db.commit()
    await db.refresh(challenge)

    logger.info(
        "challenge_issued",
        challenger=str(caller_id),
        target=str(req.agent_id),
        domain=req.domain,
        action=req.action,
    )

    return _challenge_to_response(challenge)


@router.get("/pending")
async def get_my_pending_challenges(
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> list[dict[str, Any]]:
    """Get all pending challenges issued to the calling agent.

    Agents should poll this endpoint to discover challenges they need
    to respond to. In production, challenges would also be delivered
    via WebSocket push.
    """
    result = await db.execute(
        select(CapabilityChallenge)
        .where(
            CapabilityChallenge.agent_id == caller_id,
            CapabilityChallenge.status == "pending",
        )
        .order_by(desc(CapabilityChallenge.created_at))
    )
    challenges = result.scalars().all()
    return [_challenge_to_response(c) for c in challenges]


@router.post("/{challenge_id}/respond")
async def respond_to_challenge(
    challenge_id: uuid.UUID,
    req: RespondChallengeRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Submit a response to a capability challenge.

    The server validates the response against the expected output schema
    and checks whether it was submitted within the time limit.
    """
    result = await db.execute(
        select(CapabilityChallenge).where(CapabilityChallenge.id == challenge_id)
    )
    challenge = result.scalar_one_or_none()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    # Only the challenged agent can respond
    if caller_id != challenge.agent_id:
        raise HTTPException(status_code=403, detail="Only the challenged agent can respond")

    # Must be pending
    if challenge.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Challenge is already {challenge.status}",
        )

    now = datetime.now(timezone.utc)
    created = challenge.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    elapsed_ms = (now - created).total_seconds() * 1000

    challenge.response = req.response
    challenge.response_time_ms = round(elapsed_ms, 2)
    challenge.completed_at = now

    # Check time limit
    if elapsed_ms > challenge.max_time_ms:
        challenge.status = "timeout"
        challenge.failure_reason = f"Response took {elapsed_ms:.0f}ms, limit was {challenge.max_time_ms}ms"
        await db.commit()
        await db.refresh(challenge)
        logger.info("challenge_timeout", challenge_id=str(challenge_id), elapsed_ms=elapsed_ms)
        return _challenge_to_response(challenge)

    # Validate response against expected schema
    validation_result = _validate_against_schema(req.response, challenge.expected_schema)
    if validation_result is not None:
        challenge.status = "failed"
        challenge.failure_reason = validation_result
        await db.commit()
        await db.refresh(challenge)
        logger.info("challenge_failed", challenge_id=str(challenge_id), reason=validation_result)
        return _challenge_to_response(challenge)

    # Passed
    challenge.status = "passed"
    await db.commit()
    await db.refresh(challenge)

    logger.info(
        "challenge_passed",
        challenge_id=str(challenge_id),
        agent=str(caller_id),
        domain=challenge.domain,
        action=challenge.action,
        response_time_ms=challenge.response_time_ms,
    )

    return _challenge_to_response(challenge)


@router.get("/agent/{agent_id}")
async def get_agent_challenges(
    agent_id: uuid.UUID,
    status: str | None = Query(None),
    domain: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> list[dict[str, Any]]:
    """Get challenges received by an agent — public competence record."""
    query = select(CapabilityChallenge).where(CapabilityChallenge.agent_id == agent_id)

    if status:
        query = query.where(CapabilityChallenge.status == status)
    if domain:
        query = query.where(CapabilityChallenge.domain == domain)

    query = query.order_by(desc(CapabilityChallenge.created_at)).limit(limit)

    result = await db.execute(query)
    challenges = result.scalars().all()
    return [_challenge_to_response(c) for c in challenges]


@router.get("/agent/{agent_id}/summary")
async def get_agent_challenge_summary(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Challenge performance summary for an agent.

    Shows pass rates and average response times per domain — the
    competence profile that feeds into trust scoring.
    """
    from sqlalchemy import func

    result = await db.execute(
        select(
            CapabilityChallenge.domain,
            CapabilityChallenge.status,
            func.count(CapabilityChallenge.id).label("count"),
            func.avg(CapabilityChallenge.response_time_ms).label("avg_response_ms"),
        )
        .where(CapabilityChallenge.agent_id == agent_id)
        .group_by(CapabilityChallenge.domain, CapabilityChallenge.status)
    )

    rows = result.all()

    domains: dict[str, dict[str, Any]] = {}
    for domain, status, count, avg_ms in rows:
        if domain not in domains:
            domains[domain] = {
                "passed": 0, "failed": 0, "timeout": 0, "pending": 0, "error": 0,
                "total": 0, "avg_response_ms": None, "response_times": [],
            }
        domains[domain][status] = count
        domains[domain]["total"] += count
        if avg_ms is not None and status == "passed":
            domains[domain]["response_times"].append((count, float(avg_ms)))

    for d in domains.values():
        if d["response_times"]:
            total_weight = sum(c for c, _ in d["response_times"])
            d["avg_response_ms"] = round(
                sum(c * ms for c, ms in d["response_times"]) / total_weight, 2
            ) if total_weight else None
        del d["response_times"]
        total = d["total"]
        d["pass_rate"] = round(d["passed"] / total, 3) if total > 0 else 0.0

    return {
        "agent_id": str(agent_id),
        "domains": domains,
    }


@router.get("/{challenge_id}")
async def get_challenge(
    challenge_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Get a single challenge by ID."""
    result = await db.execute(
        select(CapabilityChallenge).where(CapabilityChallenge.id == challenge_id)
    )
    challenge = result.scalar_one_or_none()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    return _challenge_to_response(challenge)


# --- Helpers ---

def _validate_against_schema(response: dict, schema: dict) -> str | None:
    """Validate a response dict against a JSON Schema.

    Returns None if valid, or an error message string if invalid.
    Uses a lightweight structural check — required keys and type matching.
    For production, swap in jsonschema.validate().
    """
    if not schema:
        return None  # no schema to validate against = pass

    try:
        import jsonschema
        jsonschema.validate(instance=response, schema=schema)
        return None
    except ImportError:
        # Fallback: structural check if jsonschema not installed
        return _structural_validate(response, schema)
    except Exception as e:
        return f"Schema validation failed: {str(e)[:200]}"


def _structural_validate(response: dict, schema: dict) -> str | None:
    """Lightweight structural validation without jsonschema dependency."""
    required = schema.get("required", [])
    for key in required:
        if key not in response:
            return f"Missing required key: {key}"

    properties = schema.get("properties", {})
    type_map = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "array": list, "object": dict}

    for key, prop_schema in properties.items():
        if key in response:
            expected_type = prop_schema.get("type")
            if expected_type and expected_type in type_map:
                if not isinstance(response[key], type_map[expected_type]):
                    return f"Key '{key}' expected type {expected_type}, got {type(response[key]).__name__}"

    return None


def _challenge_to_response(c: CapabilityChallenge) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "agent_id": str(c.agent_id),
        "domain": c.domain,
        "action": c.action,
        "challenge_input": c.challenge_input,
        "expected_schema": c.expected_schema,
        "max_time_ms": c.max_time_ms,
        "response": c.response,
        "response_time_ms": c.response_time_ms,
        "status": c.status,
        "failure_reason": c.failure_reason,
        "issued_by": str(c.issued_by) if c.issued_by else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "completed_at": c.completed_at.isoformat() if c.completed_at else None,
    }

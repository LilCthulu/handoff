"""Agent registration, authentication, discovery — the front door of the network.

Every agent that enters the Handoff network passes through here.
They declare who they are, what they can do, and prove it cryptographically.
"""

import base64
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import nacl.signing
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import cast, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import create_agent_token
from app.core.crypto import public_key_fingerprint, verify_signature
from app.config import settings
from app.database import get_db
from app.models.agent import Agent
from app.models.audit import AuditLog
from app.schemas.agent import (
    AgentAuthRequest,
    AgentRegisterRequest,
    AgentRegisteredResponse,
    AgentResponse,
    AgentUpdateRequest,
    DiscoverQuery,
    KeyRotateRequest,
)
from app.api.deps import get_current_agent, get_agent_id

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["agents"])

# Registration hooks: extensions can register callbacks to run after agent creation.
# Each callback receives (agent: Agent, request: AgentRegisterRequest, db: AsyncSession).
_registration_hooks: list[Any] = []


def register_hook(hook: Any) -> None:
    """Register a callback that runs during agent registration.

    The hook receives (agent, request, db) and can modify the agent
    (e.g., set org_id from an API key). Used by the cloud extension.
    """
    _registration_hooks.append(hook)


@router.post("/agents/register", response_model=AgentRegisteredResponse, status_code=201)
async def register_agent(
    req: AgentRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Register a new agent in the network. Returns agent profile + JWT."""
    agent = Agent(
        name=req.name,
        description=req.description,
        owner_id=req.owner_id,
        public_key=req.public_key,
        capabilities=[c.model_dump() for c in req.capabilities],
        max_authority=req.max_authority.model_dump(exclude_none=True),
        metadata_=req.metadata,
    )
    db.add(agent)
    await db.flush()

    # Run registration hooks (e.g., API key → org linking)
    for hook in _registration_hooks:
        await hook(agent, req, db)

    # Audit
    audit = AuditLog(
        entity_type="agent",
        entity_id=agent.id,
        action="registered",
        actor_agent_id=agent.id,
        details={"name": agent.name, "owner_id": agent.owner_id},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(agent)

    token = create_agent_token(
        agent_id=agent.id,
        owner_id=agent.owner_id,
        authority=agent.max_authority,
    )

    logger.info("agent_registered", agent_id=str(agent.id), name=agent.name)

    return {
        "agent": _agent_to_response(agent),
        "token": token,
    }


# In-memory challenge store with expiration (production: use Redis)
_pending_challenges: dict[str, tuple[uuid.UUID, datetime]] = {}
_CHALLENGE_TTL = timedelta(minutes=2)
_MAX_PENDING_CHALLENGES_PER_AGENT = 5
_MAX_PENDING_CHALLENGES_TOTAL = 10_000


@router.post("/agents/challenge")
async def request_challenge(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Issue a cryptographic challenge for authentication.

    The agent must sign this challenge with their Ed25519 private key
    and return it to /agents/authenticate to prove key possession.
    """
    agent = await _get_agent_or_404(db, agent_id)
    if agent.status != "active":
        raise HTTPException(status_code=403, detail=f"Agent is {agent.status}")

    # Purge expired challenges first
    now = datetime.now(timezone.utc)
    expired = [c for c, (_, exp) in _pending_challenges.items() if now > exp]
    for c in expired:
        del _pending_challenges[c]

    # Enforce limits to prevent memory exhaustion
    if len(_pending_challenges) >= _MAX_PENDING_CHALLENGES_TOTAL:
        raise HTTPException(status_code=503, detail="Too many pending challenges — try again shortly")

    agent_challenge_count = sum(1 for _, (aid, _) in _pending_challenges.items() if aid == agent_id)
    if agent_challenge_count >= _MAX_PENDING_CHALLENGES_PER_AGENT:
        raise HTTPException(status_code=429, detail="Too many pending challenges for this agent")

    challenge = secrets.token_urlsafe(32)
    _pending_challenges[challenge] = (agent_id, now + _CHALLENGE_TTL)

    return {"challenge": challenge}


@router.post("/agents/authenticate")
async def authenticate_agent(
    req: AgentAuthRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Authenticate by proving possession of the Ed25519 private key.

    The agent signs a server-issued challenge and submits the signature.
    The server verifies against the agent's registered public key.
    """
    # Validate challenge exists and hasn't expired
    entry = _pending_challenges.pop(req.challenge, None)
    if entry is None:
        raise HTTPException(status_code=401, detail="Invalid or expired challenge")

    expected_agent_id, expires_at = entry
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=401, detail="Challenge expired")
    if expected_agent_id != req.agent_id:
        raise HTTPException(status_code=401, detail="Challenge was not issued for this agent")

    agent = await _get_agent_or_404(db, req.agent_id)
    if agent.status != "active":
        raise HTTPException(status_code=403, detail=f"Agent is {agent.status}")

    # Verify signature: the agent signed the challenge bytes with their private key
    try:
        signature = base64.b64decode(req.signature)
        public_key_raw = base64.b64decode(agent.public_key)
        verify_key = nacl.signing.VerifyKey(public_key_raw)
        verify_key.verify(req.challenge.encode("utf-8"), signature)
    except Exception:
        logger.warning("auth_signature_failed", agent_id=str(req.agent_id))
        raise HTTPException(status_code=401, detail="Signature verification failed")

    agent.last_seen_at = datetime.now(timezone.utc)
    await db.commit()

    token = create_agent_token(
        agent_id=agent.id,
        owner_id=agent.owner_id,
        authority=agent.max_authority,
    )

    logger.info("agent_authenticated", agent_id=str(agent.id))
    return {"token": token}


@router.get("/agents/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Get an agent's public profile."""
    agent = await _get_agent_or_404(db, agent_id)
    return _agent_to_response(agent)


@router.patch("/agents/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: uuid.UUID,
    req: AgentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Update an agent's capabilities or metadata. Agents can only update themselves."""
    if caller_id != agent_id:
        raise HTTPException(status_code=403, detail="Agents can only update themselves")

    agent = await _get_agent_or_404(db, agent_id)

    if req.name is not None:
        agent.name = req.name
    if req.description is not None:
        agent.description = req.description
    if req.capabilities is not None:
        agent.capabilities = [c.model_dump() for c in req.capabilities]
    if req.max_authority is not None:
        agent.max_authority = req.max_authority.model_dump(exclude_none=True)
    if req.metadata is not None:
        agent.metadata_ = req.metadata

    agent.updated_at = datetime.now(timezone.utc)

    audit = AuditLog(
        entity_type="agent",
        entity_id=agent.id,
        action="updated",
        actor_agent_id=caller_id,
        details={"updated_fields": [f for f in req.model_fields_set]},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(agent)

    return _agent_to_response(agent)


@router.delete("/agents/{agent_id}", status_code=204)
async def deactivate_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> None:
    """Deactivate an agent. Agents can only deactivate themselves."""
    if caller_id != agent_id:
        raise HTTPException(status_code=403, detail="Agents can only deactivate themselves")

    agent = await _get_agent_or_404(db, agent_id)
    agent.status = "revoked"
    agent.updated_at = datetime.now(timezone.utc)

    audit = AuditLog(
        entity_type="agent",
        entity_id=agent.id,
        action="deactivated",
        actor_agent_id=caller_id,
        details={"status": "revoked"},
    )
    db.add(audit)
    await db.commit()

    logger.info("agent_deactivated", agent_id=str(agent_id))


@router.post("/agents/{agent_id}/rotate-keys", response_model=AgentResponse)
async def rotate_keys(
    agent_id: uuid.UUID,
    req: KeyRotateRequest,
    db: AsyncSession = Depends(get_db),
    caller_id: uuid.UUID = Depends(get_agent_id),
) -> dict[str, Any]:
    """Rotate an agent's Ed25519 signing keys."""
    if caller_id != agent_id:
        raise HTTPException(status_code=403, detail="Agents can only rotate their own keys")

    agent = await _get_agent_or_404(db, agent_id)

    # Verify the new key is signed by the old key — proof of possession
    old_fingerprint = public_key_fingerprint(agent.public_key)
    new_fingerprint = public_key_fingerprint(req.new_public_key)

    try:
        signature = base64.b64decode(req.signature)
        old_public_raw = base64.b64decode(agent.public_key)
        verify_key = nacl.signing.VerifyKey(old_public_raw)
        # The agent signs the new public key with the old private key
        verify_key.verify(req.new_public_key.encode("utf-8"), signature)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Signature verification failed — new key must be signed by the old key",
        )

    agent.public_key = req.new_public_key
    agent.updated_at = datetime.now(timezone.utc)

    audit = AuditLog(
        entity_type="agent",
        entity_id=agent.id,
        action="keys_rotated",
        actor_agent_id=caller_id,
        details={
            "old_fingerprint": old_fingerprint,
            "new_fingerprint": new_fingerprint,
        },
    )
    db.add(audit)
    await db.commit()
    await db.refresh(agent)

    logger.info("agent_keys_rotated", agent_id=str(agent_id))
    return _agent_to_response(agent)


@router.get("/discover", response_model=list[AgentResponse])
async def discover_agents(
    domain: str | None = Query(None),
    action: str | None = Query(None),
    min_trust: float = Query(0.0, ge=0.0, le=1.0),
    status: str = Query("active"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> list[dict[str, Any]]:
    """Discover agents by capability, trust score, and status."""
    query = select(Agent).where(Agent.status == status, Agent.trust_score >= min_trust)

    if domain and settings.DATABASE_URL.startswith("postgresql"):
        # Use PostgreSQL JSONB containment with parameterized value — no string interpolation
        query = query.where(
            Agent.capabilities.op("@>")(cast([{"domain": domain}], JSONB))
        )

    query = query.order_by(Agent.trust_score.desc()).limit(limit)

    result = await db.execute(query)
    agents = result.scalars().all()

    # Post-filter by domain for non-PostgreSQL backends (SQLite)
    if domain and not settings.DATABASE_URL.startswith("postgresql"):
        agents = [
            a for a in agents
            if any(cap.get("domain") == domain for cap in (a.capabilities or []))
        ]
    if action:
        agents = [
            a for a in agents
            if any(action in cap.get("actions", []) for cap in (a.capabilities or []))
        ]

    return [_agent_to_response(a) for a in agents]


# --- Helpers ---

async def _get_agent_or_404(db: AsyncSession, agent_id: uuid.UUID) -> Agent:
    """Fetch an agent by ID or raise 404."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


def _agent_to_response(agent: Agent) -> dict[str, Any]:
    """Convert an Agent ORM instance to a response dict."""
    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "owner_id": agent.owner_id,
        "public_key": agent.public_key,
        "capabilities": agent.capabilities,
        "trust_score": agent.trust_score,
        "max_authority": agent.max_authority,
        "status": agent.status,
        "metadata": agent.metadata_,
        "created_at": agent.created_at,
        "updated_at": agent.updated_at,
        "last_seen_at": agent.last_seen_at,
    }

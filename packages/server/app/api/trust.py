"""Trust score API — query agent reputation by domain."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.api.deps import get_current_agent
from app.services.trust import get_agent_trust, find_trusted_agents

router = APIRouter(prefix="/api/v1/trust", tags=["trust"])


@router.get("/{agent_id}")
async def get_trust_scores(
    agent_id: uuid.UUID,
    domain: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Get trust scores for an agent, optionally filtered by domain."""
    return await get_agent_trust(db, agent_id, domain)


@router.get("/discover/{domain}")
async def discover_trusted(
    domain: str,
    min_score: float = Query(0.6, ge=0.0, le=1.0),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> list[dict[str, Any]]:
    """Find agents trusted in a specific domain, ranked by score."""
    return await find_trusted_agents(db, domain, min_score, limit)

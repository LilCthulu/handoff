"""Discovery endpoint — separate module for the agent discovery query.

Discovery is already implemented in agents.py via GET /discover.
This module provides additional discovery utilities and the
dashboard-facing stats endpoint.
"""

from typing import Any

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent import Agent
from app.models.handoff import Handoff
from app.models.negotiation import Negotiation
from app.api.deps import get_current_agent

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/discovery", tags=["discovery"])


@router.get("/stats")
async def discovery_stats(
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """Get network-wide statistics for discovery and dashboard."""
    agents_result = await db.execute(
        select(func.count(Agent.id)).where(Agent.status == "active")
    )
    active_agents = agents_result.scalar() or 0

    avg_trust_result = await db.execute(
        select(func.avg(Agent.trust_score)).where(Agent.status == "active")
    )
    avg_trust = avg_trust_result.scalar() or 0.5

    active_neg_result = await db.execute(
        select(func.count(Negotiation.id)).where(
            Negotiation.state.in_(["pending", "negotiating", "agreed", "executing"])
        )
    )
    active_negotiations = active_neg_result.scalar() or 0

    active_handoff_result = await db.execute(
        select(func.count(Handoff.id)).where(
            Handoff.status.in_(["initiated", "in_progress"])
        )
    )
    active_handoffs = active_handoff_result.scalar() or 0

    completed_handoff_result = await db.execute(
        select(func.count(Handoff.id)).where(Handoff.status == "completed")
    )
    completed_handoffs = completed_handoff_result.scalar() or 0

    return {
        "active_agents": active_agents,
        "avg_trust_score": round(float(avg_trust), 3),
        "active_negotiations": active_negotiations,
        "active_handoffs": active_handoffs,
        "completed_handoffs": completed_handoffs,
    }


@router.get("/domains")
async def list_domains(
    db: AsyncSession = Depends(get_db),
    _claims: dict = Depends(get_current_agent),
) -> dict[str, Any]:
    """List all capability domains present in the network."""
    result = await db.execute(
        select(Agent.capabilities).where(Agent.status == "active")
    )
    all_capabilities = result.scalars().all()

    domains: dict[str, int] = {}
    for cap_list in all_capabilities:
        if isinstance(cap_list, list):
            for cap in cap_list:
                domain = cap.get("domain", "unknown")
                domains[domain] = domains.get(domain, 0) + 1

    return {"domains": domains}

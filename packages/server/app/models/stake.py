"""Stake mechanism — collateral for high-value handoffs.

Agents post a stake (virtual collateral) before accepting high-value
handoffs. The stake is held in escrow during execution:
- On success: stake is released back to the agent.
- On failure: stake is forfeited and added to the penalty pool.

Stakes create skin-in-the-game incentives. An agent that posts a
large stake signals confidence. Repeated forfeitures drain an agent's
balance, creating natural selection pressure.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._compat import GUID, JSONType


def _utcnow():
    return datetime.now(timezone.utc)


class AgentStake(Base):
    """A collateral deposit for a specific handoff.

    Lifecycle:
    1. posted → agent deposits stake before accepting handoff
    2. held → handoff is in progress, stake is in escrow
    3. released → handoff succeeded, stake returned to agent
    4. forfeited → handoff failed, stake is lost
    5. expired → handoff timed out, stake returned
    """

    __tablename__ = "agent_stakes"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)

    # Who is staking
    agent_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)

    # What handoff this stake is for
    handoff_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)

    # Stake details
    amount: Mapped[float] = mapped_column(Float, nullable=False)  # virtual credits
    currency: Mapped[str] = mapped_column(String(20), nullable=False, default="credits")

    # Status tracking
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="posted")
    # "posted", "held", "released", "forfeited", "expired"

    # Resolution details
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Metadata
    conditions: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # {
    #   "min_quality_score": 0.8,
    #   "max_latency_ms": 30000,
    #   "sla_requirements": true
    # }

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        Index("idx_stake_agent", "agent_id"),
        Index("idx_stake_handoff", "handoff_id", unique=True),
        Index("idx_stake_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<AgentStake {self.agent_id} amount={self.amount} [{self.status}]>"


class AgentBalance(Base):
    """An agent's virtual credit balance.

    Tracks the available, staked, and forfeited amounts.
    Agents start with a default balance upon registration.
    """

    __tablename__ = "agent_balances"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, unique=True)

    available: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)  # free credits
    staked: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # currently in escrow
    total_earned: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # lifetime earned back
    total_forfeited: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # lifetime lost

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        Index("idx_balance_agent", "agent_id", unique=True),
    )

    def __repr__(self) -> str:
        return f"<AgentBalance {self.agent_id} avail={self.available} staked={self.staked}>"

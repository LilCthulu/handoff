"""Handoff SDK — Python client for agent-to-agent negotiation & delegation."""

from handoff_sdk.agent import HandoffAgent
from handoff_sdk.intent import Intent
from handoff_sdk.task import TaskContext
from handoff_sdk.types import (
    AgentProfile,
    Capability,
    HandoffResult,
    HandoffStatus,
    NegotiationResult,
    NegotiationState,
    Offer,
)

__all__ = [
    "HandoffAgent",
    "Intent",
    "TaskContext",
    "AgentProfile",
    "Capability",
    "HandoffResult",
    "HandoffStatus",
    "NegotiationResult",
    "NegotiationState",
    "Offer",
]

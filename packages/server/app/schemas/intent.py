"""Pydantic schema for the JSON Schema intent language."""

import uuid
from typing import Any

from pydantic import BaseModel, Field


class IntentConstraints(BaseModel):
    """Hard and soft constraints on an intent."""

    budget_max: float | None = None
    currency: str | None = None
    deadline: str | None = None
    must_have: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class Intent(BaseModel):
    """A structured expression of what an agent needs."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    type: str = Field(..., pattern="^(request|offer|inform|query)$")
    domain: str
    action: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    constraints: IntentConstraints = Field(default_factory=IntentConstraints)
    priority: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    fallback_behavior: str = Field(
        default="notify_owner",
        pattern="^(notify_owner|retry|escalate|abort)$",
    )

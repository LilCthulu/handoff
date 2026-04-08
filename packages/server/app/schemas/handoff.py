"""Pydantic schemas for task handoffs and audit trail."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


def _validate_json_depth(obj: Any, max_depth: int = 10, _current: int = 0) -> None:
    """Reject deeply nested JSON to prevent stack overflow / memory abuse."""
    if _current > max_depth:
        raise ValueError(f"JSON nesting exceeds maximum depth of {max_depth}")
    if isinstance(obj, dict):
        for v in obj.values():
            _validate_json_depth(v, max_depth, _current + 1)
    elif isinstance(obj, list):
        for item in obj:
            _validate_json_depth(item, max_depth, _current + 1)


class HandoffCreateRequest(BaseModel):
    """Request body for POST /handoffs."""

    negotiation_id: uuid.UUID | None = None
    to_agent_id: uuid.UUID
    context: dict[str, Any]
    chain_id: uuid.UUID | None = None
    chain_position: int = 0
    parent_handoff_id: uuid.UUID | None = None
    timeout_minutes: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_context_size(self) -> "HandoffCreateRequest":
        import json
        raw = json.dumps(self.context, default=str)
        if len(raw) > 512_000:  # 512 KB max context payload
            raise ValueError("Context payload exceeds 512 KB limit")
        _validate_json_depth(self.context)
        return self


class HandoffStatusUpdate(BaseModel):
    """Request body for PATCH /handoffs/{id}/status."""

    status: str = Field(..., pattern="^(in_progress|completed|failed|rolled_back)$")


class HandoffResultRequest(BaseModel):
    """Request body for POST /handoffs/{id}/result."""

    result: dict[str, Any]


class HandoffResponse(BaseModel):
    """Full handoff state returned by the API."""

    id: uuid.UUID
    negotiation_id: uuid.UUID | None
    from_agent_id: uuid.UUID
    to_agent_id: uuid.UUID
    status: str
    context: dict[str, Any]
    result: dict[str, Any] | None
    chain_id: uuid.UUID | None
    chain_position: int
    parent_handoff_id: uuid.UUID | None
    timeout_at: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class AuditLogResponse(BaseModel):
    """Audit log entry returned by the API."""

    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    action: str
    actor_agent_id: uuid.UUID | None
    details: dict[str, Any]
    envelope_signature: str | None
    created_at: datetime

    model_config = {"from_attributes": True}

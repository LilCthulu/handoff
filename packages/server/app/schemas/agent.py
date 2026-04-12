"""Pydantic schemas for agent registration, profiles, and discovery."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CapabilityConstraints(BaseModel):
    """Domain-specific constraints on a capability."""

    model_config = {"extra": "allow"}


class Capability(BaseModel):
    """A single capability declaration."""

    domain: str = Field(..., min_length=1, description="Capability domain")
    actions: list[str] = Field(..., min_length=1, description="Actions in this domain")
    constraints: dict[str, Any] = Field(default_factory=dict)


class MaxAuthority(BaseModel):
    """Authority limits for an agent."""

    max_spend: float | None = None
    currency: str | None = None
    allowed_domains: list[str] | None = None
    requires_human_approval: list[str] | None = None

    model_config = {"extra": "allow"}


class AgentRegisterRequest(BaseModel):
    """Request body for POST /agents/register."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    owner_id: str = Field(..., min_length=1, max_length=255)
    public_key: str = Field(..., description="Ed25519 public key, base64-encoded")
    capabilities: list[Capability] = Field(default_factory=list)
    max_authority: MaxAuthority = Field(default_factory=MaxAuthority)
    metadata: dict[str, Any] = Field(default_factory=dict)
    api_key: str | None = Field(None, description="Organization API key to link this agent to an org")


class AgentAuthRequest(BaseModel):
    """Request body for POST /agents/authenticate."""

    agent_id: uuid.UUID
    signature: str = Field(..., description="Signature of a server-issued challenge")
    challenge: str


class AgentUpdateRequest(BaseModel):
    """Request body for PATCH /agents/{id}."""

    name: str | None = None
    description: str | None = None
    capabilities: list[Capability] | None = None
    max_authority: MaxAuthority | None = None
    metadata: dict[str, Any] | None = None


class AgentResponse(BaseModel):
    """Agent profile returned by the API."""

    id: uuid.UUID
    name: str
    description: str | None
    owner_id: str
    public_key: str
    capabilities: list[dict[str, Any]]
    trust_score: float
    max_authority: dict[str, Any]
    status: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None

    model_config = {"from_attributes": True}


class AgentRegisteredResponse(BaseModel):
    """Response from successful registration — the agent's identity and first token."""

    agent: AgentResponse
    token: str


class KeyRotateRequest(BaseModel):
    """Request body for POST /agents/{id}/rotate-keys."""

    new_public_key: str = Field(..., description="New Ed25519 public key, base64-encoded")
    signature: str = Field(..., description="Signature of the new key using the old key")


class DiscoverQuery(BaseModel):
    """Query parameters for GET /discover."""

    domain: str | None = None
    action: str | None = None
    min_trust: float = Field(default=0.0, ge=0.0, le=1.0)
    status: str = "active"
    limit: int = Field(default=20, ge=1, le=100)

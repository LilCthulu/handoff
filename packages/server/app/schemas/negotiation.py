"""Pydantic schemas for negotiations, offers, and counteroffers."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.intent import Intent


class OfferTerms(BaseModel):
    """The proposed terms of an offer — domain-specific, so loosely typed."""

    model_config = {"extra": "allow"}


class Offer(BaseModel):
    """An offer or counteroffer within a negotiation."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    negotiation_id: uuid.UUID
    from_agent: uuid.UUID
    round: int = Field(..., ge=1)
    terms: dict[str, Any]
    concessions: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


class NegotiationCreateRequest(BaseModel):
    """Request body for POST /negotiations."""

    responder_id: uuid.UUID
    intent: Intent
    mediator_required: bool = False
    max_rounds: int = Field(default=10, ge=1, le=100)
    timeout_minutes: int | None = Field(default=None, ge=1)


class NegotiationOfferRequest(BaseModel):
    """Request body for POST /negotiations/{id}/offer."""

    terms: dict[str, Any]
    concessions: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


class NegotiationRejectRequest(BaseModel):
    """Request body for POST /negotiations/{id}/reject."""

    reason: str | None = None


class NegotiationResponse(BaseModel):
    """Full negotiation state returned by the API."""

    id: uuid.UUID
    initiator_id: uuid.UUID
    responder_id: uuid.UUID | None
    mediator_required: bool
    state: str
    intent: dict[str, Any]
    current_offer: dict[str, Any] | None
    offer_history: list[dict[str, Any]]
    agreement: dict[str, Any] | None
    timeout_at: datetime | None
    max_rounds: int
    current_round: int
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}

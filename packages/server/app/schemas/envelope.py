"""Pydantic schema for the signed message envelope."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EnvelopeSender(BaseModel):
    """Sender identity in a signed envelope."""

    agent_id: uuid.UUID
    public_key_fingerprint: str = Field(..., pattern=r"^sha256:[a-f0-9]+$")


class EnvelopeRecipient(BaseModel):
    """Recipient identity in a signed envelope."""

    agent_id: uuid.UUID


class Envelope(BaseModel):
    """Cryptographic wrapper for agent-to-agent messages."""

    version: str = "1.0"
    message_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    sender: EnvelopeSender
    recipient: EnvelopeRecipient | None = None
    payload_hash: str = Field(..., pattern=r"^sha256:.+$")
    signature: str


class SignedMessage(BaseModel):
    """A complete signed message: envelope + payload."""

    envelope: Envelope
    payload: dict[str, Any]

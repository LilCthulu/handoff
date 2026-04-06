"""Shared dependencies — the common ground every endpoint stands on."""

import uuid
from typing import Any

import structlog
from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthError, decode_token, extract_agent_id
from app.database import get_db

logger = structlog.get_logger()


async def get_current_agent(authorization: str = Header(...)) -> dict[str, Any]:
    """Extract and validate the agent's JWT from the Authorization header.

    Returns:
        Decoded JWT claims.

    Raises:
        AuthError: If token is missing, malformed, or expired.
    """
    if not authorization.startswith("Bearer "):
        raise AuthError("Authorization header must start with 'Bearer '")

    token = authorization[7:]
    claims = decode_token(token)
    return claims


async def get_agent_id(claims: dict[str, Any] = Depends(get_current_agent)) -> uuid.UUID:
    """Extract agent UUID from validated JWT claims."""
    return extract_agent_id(claims)

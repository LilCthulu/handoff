"""JWT capability token generation and validation.

Tokens are the passport of the agentic economy. They carry identity,
authority, and scope — everything an agent needs to prove who it is
and what it's allowed to do.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from jose import JWTError, jwt

from app.config import settings

logger = structlog.get_logger()

ALGORITHM = "HS256"


class AuthError(Exception):
    """Raised when authentication or authorization fails."""

    def __init__(self, detail: str, status_code: int = 401) -> None:
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def create_agent_token(
    agent_id: uuid.UUID,
    owner_id: str,
    scopes: list[str] | None = None,
    authority: dict[str, Any] | None = None,
) -> str:
    """Issue a JWT capability token for an agent.

    Args:
        agent_id: The agent's UUID.
        owner_id: The owning org/user ID.
        scopes: Permitted actions (defaults to full access).
        authority: Spending limits and domain restrictions.

    Returns:
        Encoded JWT string.
    """
    if scopes is None:
        scopes = ["negotiate", "handoff", "discover"]
    if authority is None:
        authority = {}

    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(agent_id),
        "iss": "handoff-server",
        "iat": now,
        "exp": now + timedelta(hours=settings.JWT_EXPIRY_HOURS),
        "jti": str(uuid.uuid4()),
        "scopes": scopes,
        "authority": authority,
        "owner_id": owner_id,
    }

    return jwt.encode(claims, settings.JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT capability token.

    Args:
        token: The raw JWT string.

    Returns:
        Decoded claims dictionary.

    Raises:
        AuthError: If the token is invalid, expired, or malformed.
    """
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        raise AuthError(f"Invalid token: {e}")


def require_scope(token_claims: dict[str, Any], scope: str) -> None:
    """Verify that a token carries a required scope.

    Args:
        token_claims: Decoded JWT claims.
        scope: The scope to check for.

    Raises:
        AuthError: If the scope is not present.
    """
    scopes = token_claims.get("scopes", [])
    if scope not in scopes:
        raise AuthError(f"Missing required scope: {scope}", status_code=403)


def check_authority(token_claims: dict[str, Any], domain: str | None = None, spend: float | None = None) -> None:
    """Check whether an agent's authority permits an action.

    Args:
        token_claims: Decoded JWT claims.
        domain: The domain being accessed (if applicable).
        spend: The amount being spent (if applicable).

    Raises:
        AuthError: If the action exceeds the agent's authority.
    """
    authority = token_claims.get("authority", {})

    if domain and "allowed_domains" in authority:
        if domain not in authority["allowed_domains"]:
            raise AuthError(f"Domain '{domain}' not in allowed domains", status_code=403)

    if spend is not None and "max_spend" in authority:
        if spend > authority["max_spend"]:
            raise AuthError(
                f"Spend {spend} exceeds max_spend {authority['max_spend']}",
                status_code=403,
            )


def extract_agent_id(token_claims: dict[str, Any]) -> uuid.UUID:
    """Extract the agent UUID from decoded token claims."""
    return uuid.UUID(token_claims["sub"])

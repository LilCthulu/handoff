"""Authentication middleware & signature verification dependency.

Every request passes through here. No exceptions. No shortcuts.
JWT validation, agent status enforcement, and request-level audit logging.

If you can't prove who you are, you don't get in.
If your agent is revoked, you don't get in.
If your signature doesn't match, you don't get in.

Architecture:
- AuthMiddleware (Starlette middleware): JWT + agent status — header-only, no body reads
- verify_signed_envelope (FastAPI dependency): Ed25519 signature verification — reads body safely
"""

import json
import time
import uuid
from typing import Any

import structlog
from fastapi import Depends, HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse
from sqlalchemy import select

from app.core.auth import AuthError, decode_token
from app.core.crypto import verify_signature
from app.database import async_session, get_db
from app.models.agent import Agent

logger = structlog.get_logger()

# Paths that don't require authentication
PUBLIC_PATHS: frozenset[str] = frozenset({
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/docs/oauth2-redirect",
})

# Path prefixes that don't require authentication
PUBLIC_PREFIXES: tuple[str, ...] = (
    "/api/v1/agents/register",
    "/api/v1/agents/challenge",
    "/api/v1/agents/authenticate",
    "/api/v1/attestations/agent/",
    "/api/v1/attestations/summary/",
    "/ws/",
    # Cloud extension routes use session-cookie auth, not JWT.
    # They must bypass the protocol's JWT middleware to reach
    # the cloud extension's own authentication layer.
    "/api/v1/accounts/",
    "/api/v1/dashboard/",
    "/api/v1/billing/",
    "/api/v1/oauth/",
)


class AuthMiddleware(BaseHTTPMiddleware):
    """JWT validation and agent verification on every protected request.

    Enforces:
    1. Valid, unexpired JWT in Authorization header
    2. Agent exists in the database and is not suspended/revoked
    3. Injects validated claims + agent_id into request.state
    4. Logs every authenticated request with timing

    This middleware only reads headers — never the body. Body-level
    verification (signatures) is handled by a FastAPI dependency.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        method = request.method

        # Skip auth for public paths
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        # Extract token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or malformed Authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[7:]

        # Decode and validate JWT
        try:
            claims = decode_token(token)
        except AuthError as e:
            logger.warning(
                "auth_rejected_invalid_token",
                path=path,
                method=method,
                reason=str(e),
                client=_client_ip(request),
            )
            return JSONResponse(
                status_code=401,
                content={"detail": f"Invalid token: {e.detail}"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        agent_id_str = claims.get("sub")
        if not agent_id_str:
            return JSONResponse(status_code=401, content={"detail": "Token missing subject claim"})

        try:
            agent_id = uuid.UUID(agent_id_str)
        except ValueError:
            return JSONResponse(status_code=401, content={"detail": "Invalid agent ID in token"})

        # Verify agent is still active in the database
        try:
            async with async_session() as db:
                result = await db.execute(select(Agent.status).where(Agent.id == agent_id))
                row = result.one_or_none()

                if row is None:
                    logger.warning("auth_rejected_unknown_agent", agent_id=agent_id_str, path=path)
                    return JSONResponse(status_code=401, content={"detail": "Agent not found"})

                agent_status = row[0]
                if agent_status == "revoked":
                    logger.warning("auth_rejected_revoked", agent_id=agent_id_str, path=path)
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Agent has been revoked"},
                    )
                if agent_status == "suspended":
                    logger.warning("auth_rejected_suspended", agent_id=agent_id_str, path=path)
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Agent is suspended"},
                    )
        except Exception:
            logger.exception("auth_db_check_failed", agent_id=agent_id_str)
            return JSONResponse(
                status_code=503,
                content={"detail": "Authentication service temporarily unavailable"},
            )

        # Inject validated claims into request state for downstream handlers
        request.state.agent_claims = claims
        request.state.agent_id = agent_id

        # Log authenticated request with timing
        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = (time.monotonic() - start) * 1000

        logger.info(
            "authenticated_request",
            agent_id=agent_id_str,
            method=method,
            path=path,
            status=response.status_code,
            elapsed_ms=round(elapsed_ms, 1),
        )

        return response


async def verify_signed_envelope(request: Request) -> dict[str, Any] | None:
    """FastAPI dependency: verify Ed25519 signature on signed message envelopes.

    If the request body contains an "envelope" + "payload", verify the
    cryptographic signature against the sender's registered public key.

    Returns the verified payload dict, or None if the body is not a signed envelope.

    Raises HTTPException(403) if signature verification fails.

    Usage in routes:
        @router.post("/my-endpoint")
        async def my_endpoint(verified=Depends(verify_signed_envelope)):
            ...
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type:
        return None

    try:
        body = await request.body()
        if not body:
            return None
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    if "envelope" not in data or "payload" not in data:
        return None

    envelope = data["envelope"]
    sender_info = envelope.get("sender", {})
    sender_id_str = sender_info.get("agent_id")

    if not sender_id_str:
        raise HTTPException(status_code=400, detail="Signed envelope missing sender agent_id")

    try:
        sender_id = uuid.UUID(sender_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid sender agent_id in envelope")

    # Fetch sender's public key
    async with async_session() as db:
        result = await db.execute(
            select(Agent.public_key, Agent.status).where(Agent.id == sender_id)
        )
        row = result.one_or_none()

        if row is None:
            raise HTTPException(status_code=400, detail="Sender agent not found")

        public_key, status = row
        if status != "active":
            raise HTTPException(status_code=403, detail=f"Sender agent is {status}")

    # Verify the cryptographic signature
    if not verify_signature(data, public_key):
        logger.warning(
            "signature_verification_failed",
            sender_id=sender_id_str,
            path=request.url.path,
            client=_client_ip(request),
        )
        raise HTTPException(
            status_code=403,
            detail="Signature verification failed — message may have been tampered with",
        )

    logger.debug("signature_verified", sender_id=sender_id_str, path=request.url.path)
    return data["payload"]


def _client_ip(request: Request) -> str:
    """Extract client IP from the direct connection.

    Uses the direct socket IP rather than X-Forwarded-For to prevent
    spoofing. When behind a reverse proxy, configure the proxy and
    ASGI server to set the correct client IP (e.g., uvicorn --proxy-headers).
    """
    if request.client:
        return request.client.host
    return "unknown"

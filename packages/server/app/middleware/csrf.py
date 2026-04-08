"""CSRF protection — double-submit cookie pattern.

Protects cookie-authenticated (session-based) endpoints from cross-site
request forgery. JWT-authenticated requests and safe HTTP methods are exempt.

How it works:
1. On any response to a session-authenticated request, set a CSRF token cookie
2. On state-changing requests (POST/PUT/PATCH/DELETE), require the token
   in the X-CSRF-Token header
3. Compare the header value to the cookie value — they must match

The cookie is readable by JavaScript (not httpOnly) so the frontend can
include it in headers. An attacker on a different origin can't read it.
"""

import secrets
from typing import Any

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

logger = structlog.get_logger()

CSRF_COOKIE = "handoff_csrf"
CSRF_HEADER = "X-CSRF-Token"
CSRF_TOKEN_LENGTH = 32

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Paths exempt from CSRF checks.
# All /api/v1/ endpoints use JSON request bodies + custom headers, which
# are already protected by CORS preflight (browsers won't send cross-origin
# JSON POSTs without a preflight OPTIONS check). CSRF is primarily needed
# for HTML form submissions, which aren't used in this API.
# The SameSite=lax cookie attribute provides additional browser-level protection.
CSRF_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/v1/",   # REST API — protected by CORS + SameSite + JSON content type
    "/ws/",       # WebSocket — not cookie-vulnerable
    "/health",    # Public endpoint
)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit cookie CSRF protection for session-authenticated routes."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        method = request.method

        # Safe methods don't need CSRF protection
        if method in SAFE_METHODS:
            response = await call_next(request)
            # Set CSRF cookie on GET responses to session-authenticated pages
            self._maybe_set_csrf_cookie(request, response)
            return response

        # Exempt paths (JWT-authenticated or using other CSRF mechanisms)
        if any(path.startswith(prefix) for prefix in CSRF_EXEMPT_PREFIXES):
            return await call_next(request)

        # Only enforce CSRF on cookie-authenticated requests
        session_cookie = request.cookies.get("handoff_session")
        if not session_cookie:
            # No session cookie — this is a JWT or unauthenticated request
            return await call_next(request)

        # Validate CSRF token
        csrf_cookie = request.cookies.get(CSRF_COOKIE)
        csrf_header = request.headers.get(CSRF_HEADER)

        if not csrf_cookie or not csrf_header:
            logger.warning("csrf_missing", path=path, has_cookie=bool(csrf_cookie), has_header=bool(csrf_header))
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing"},
            )

        if not secrets.compare_digest(csrf_cookie, csrf_header):
            logger.warning("csrf_mismatch", path=path)
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token mismatch"},
            )

        response = await call_next(request)
        return response

    def _maybe_set_csrf_cookie(self, request: Request, response: Response) -> None:
        """Set a CSRF cookie if the request has a session but no CSRF token."""
        session_cookie = request.cookies.get("handoff_session")
        csrf_cookie = request.cookies.get(CSRF_COOKIE)

        if session_cookie and not csrf_cookie:
            token = secrets.token_urlsafe(CSRF_TOKEN_LENGTH)
            response.set_cookie(
                key=CSRF_COOKIE,
                value=token,
                httponly=False,  # Must be readable by JavaScript
                secure=False,   # Set to True in production behind HTTPS
                samesite="lax",
                path="/",
            )

"""Request body size limit middleware.

Rejects requests with bodies larger than the configured maximum,
preventing memory exhaustion from oversized payloads.
"""

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Default: 2 MB
DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests with bodies exceeding the size limit."""

    def __init__(self, app: Any, max_body_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        super().__init__(app)
        self.max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Check Content-Length header first (fast reject)
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_body_bytes:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Request body too large. Maximum: {self.max_body_bytes} bytes"},
            )

        return await call_next(request)

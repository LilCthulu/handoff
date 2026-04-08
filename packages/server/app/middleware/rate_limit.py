"""Per-agent rate limiting — protecting the network from abuse.

Every agent gets a budget of requests per time window. Exceed it
and you wait. Persist and your trust score takes the hit.

Uses a sliding window counter backed by in-memory storage (upgradeable
to Redis for multi-instance deployments via the REDIS_URL config).

Defense layers:
1. Per-agent sliding window (authenticated requests)
2. Per-IP sliding window (unauthenticated / registration spam)
3. Global circuit breaker (total request rate across all agents)
4. Penalty escalation (repeated violations shrink the window)
"""

import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from app.config import settings

logger = structlog.get_logger()


@dataclass
class SlidingWindow:
    """Sliding window rate counter."""

    window_seconds: float
    max_requests: int
    timestamps: list[float] = field(default_factory=list)
    violation_count: int = 0

    def allow(self) -> bool:
        """Check if a request is allowed and record it if so."""
        now = time.monotonic()
        cutoff = now - self.window_seconds

        # Purge expired timestamps
        self.timestamps = [t for t in self.timestamps if t > cutoff]

        if len(self.timestamps) >= self.max_requests:
            self.violation_count += 1
            return False

        self.timestamps.append(now)
        return True

    @property
    def remaining(self) -> int:
        """Requests remaining in the current window."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        active = sum(1 for t in self.timestamps if t > cutoff)
        return max(0, self.max_requests - active)

    @property
    def reset_seconds(self) -> float:
        """Seconds until the oldest request in the window expires."""
        if not self.timestamps:
            return 0.0
        now = time.monotonic()
        oldest_in_window = min(t for t in self.timestamps if t > now - self.window_seconds)
        return max(0.0, self.window_seconds - (now - oldest_in_window))


# Default limits
AGENT_WINDOW_SECONDS = 60.0
AGENT_MAX_REQUESTS = 120  # 2 requests/second sustained

IP_WINDOW_SECONDS = 60.0
IP_MAX_REQUESTS = 30  # stricter for unauthenticated

GLOBAL_WINDOW_SECONDS = 10.0
GLOBAL_MAX_REQUESTS = 1000  # circuit breaker

# Penalty: after N violations, halve the agent's rate limit
VIOLATION_PENALTY_THRESHOLD = 5


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Multi-layer rate limiting middleware.

    Layer 1: Per-agent (by JWT subject) — 120 req/min
    Layer 2: Per-IP (for unauthenticated) — 30 req/min
    Layer 3: Global circuit breaker — 1000 req/10s
    """

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self._agent_windows: dict[uuid.UUID, SlidingWindow] = defaultdict(
            lambda: SlidingWindow(AGENT_WINDOW_SECONDS, AGENT_MAX_REQUESTS)
        )
        self._ip_windows: dict[str, SlidingWindow] = defaultdict(
            lambda: SlidingWindow(IP_WINDOW_SECONDS, IP_MAX_REQUESTS)
        )
        self._global_window = SlidingWindow(GLOBAL_WINDOW_SECONDS, GLOBAL_MAX_REQUESTS)
        self._last_cleanup = time.monotonic()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Periodic cleanup of stale windows (every 5 minutes)
        self._maybe_cleanup()

        # Layer 3: Global circuit breaker
        if not self._global_window.allow():
            logger.warning("rate_limit_global_breaker")
            return _rate_limit_response(
                retry_after=self._global_window.reset_seconds,
                detail="Server under heavy load — please retry shortly",
            )

        # Determine identity
        agent_id = getattr(request.state, "agent_id", None) if hasattr(request, "state") else None
        client_ip = _client_ip(request)

        # Layer 1: Per-agent (if authenticated)
        if agent_id:
            window = self._agent_windows[agent_id]

            # Penalty escalation: repeated violators get tighter limits
            if window.violation_count >= VIOLATION_PENALTY_THRESHOLD:
                effective_max = max(10, window.max_requests // 2)
                window.max_requests = effective_max
                logger.warning(
                    "rate_limit_penalty_applied",
                    agent_id=str(agent_id),
                    violations=window.violation_count,
                    new_limit=effective_max,
                )

            if not window.allow():
                logger.warning(
                    "rate_limit_agent",
                    agent_id=str(agent_id),
                    violations=window.violation_count,
                    path=request.url.path,
                )
                return _rate_limit_response(
                    retry_after=window.reset_seconds,
                    remaining=0,
                    limit=window.max_requests,
                )

            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(window.max_requests)
            response.headers["X-RateLimit-Remaining"] = str(window.remaining)
            response.headers["X-RateLimit-Reset"] = str(int(window.reset_seconds))
            return response

        # Layer 2: Per-IP (unauthenticated)
        window = self._ip_windows[client_ip]
        if not window.allow():
            logger.warning("rate_limit_ip", client_ip=client_ip, path=request.url.path)
            return _rate_limit_response(
                retry_after=window.reset_seconds,
                remaining=0,
                limit=window.max_requests,
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(window.max_requests)
        response.headers["X-RateLimit-Remaining"] = str(window.remaining)
        return response

    def _maybe_cleanup(self) -> None:
        """Purge stale windows to prevent memory growth."""
        now = time.monotonic()
        if now - self._last_cleanup < 300:  # every 5 minutes
            return
        self._last_cleanup = now

        # Remove agent windows with no recent activity
        stale_agents = [
            aid for aid, w in self._agent_windows.items()
            if not w.timestamps or (now - max(w.timestamps)) > AGENT_WINDOW_SECONDS * 10
        ]
        for aid in stale_agents:
            del self._agent_windows[aid]

        # Remove IP windows with no recent activity
        stale_ips = [
            ip for ip, w in self._ip_windows.items()
            if not w.timestamps or (now - max(w.timestamps)) > IP_WINDOW_SECONDS * 10
        ]
        for ip in stale_ips:
            del self._ip_windows[ip]

        if stale_agents or stale_ips:
            logger.debug(
                "rate_limit_cleanup",
                purged_agents=len(stale_agents),
                purged_ips=len(stale_ips),
            )


def _rate_limit_response(
    retry_after: float,
    detail: str = "Rate limit exceeded",
    remaining: int = 0,
    limit: int = 0,
) -> JSONResponse:
    """Build a 429 Too Many Requests response with proper headers."""
    headers = {
        "Retry-After": str(max(1, int(retry_after))),
        "X-RateLimit-Remaining": str(remaining),
    }
    if limit:
        headers["X-RateLimit-Limit"] = str(limit)

    return JSONResponse(
        status_code=429,
        content={"detail": detail},
        headers=headers,
    )


def _client_ip(request: Request) -> str:
    """Extract client IP from the direct connection.

    We use the direct client IP for rate limiting rather than
    X-Forwarded-For, which can be spoofed by any client. If running
    behind a reverse proxy, configure the proxy to set the real IP
    and use a trusted-proxy-aware ASGI server (e.g., uvicorn --proxy-headers).
    """
    if request.client:
        return request.client.host
    return "unknown"

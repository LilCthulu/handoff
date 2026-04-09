"""FastAPI application entry point.

This is where everything converges — every route, every middleware,
every connection to the outside world. The single point of ignition.

The open-source core registers only protocol-essential routers.
Proprietary features (dashboard, analytics, enterprise) are loaded
via the extension system — either entry_points or HANDOFF_EXTENSIONS env var.
"""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.auth import AuthError
from app.core.negotiation_engine import NegotiationError
from app.extensions import load_extensions
from app.api.agents import router as agents_router
from app.api.negotiations import router as negotiations_router
from app.api.handoffs import router as handoffs_router
from app.api.discovery import router as discovery_router
from app.api.trust import router as trust_router
from app.api.capabilities import router as capabilities_router
from app.api.attestations import router as attestations_router
from app.api.challenges import router as challenges_router
from app.api.delivery import router as delivery_router
from app.api.progress import router as progress_router
from app.api.context import router as context_router
from app.api.stakes import router as stakes_router
from app.api.credentials import router as credentials_router
from app.websocket.handlers import router as ws_router

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown."""
    logger.info("handoff_server_starting", host=settings.SERVER_HOST, port=settings.SERVER_PORT)
    # Auto-create tables for SQLite dev mode (production uses Alembic migrations)
    if settings.DATABASE_URL.startswith("sqlite"):
        from app.database import create_tables
        await create_tables()
        logger.info("sqlite_tables_created")

    # Initialize Redis (gracefully falls back to in-memory if unavailable)
    from app.redis import get_redis
    await get_redis()

    # Initialize WebSocket pub/sub for cross-instance fan-out
    from app.websocket.manager import manager as ws_manager
    await ws_manager.init_pubsub()

    yield

    # Shutdown
    await ws_manager.close_pubsub()
    from app.redis import close_redis
    await close_redis()
    logger.info("handoff_server_shutting_down")


app = FastAPI(
    title="Handoff",
    description="Universal Agent-to-Agent Negotiation & Delegation Protocol",
    version="0.1.0",
    lifespan=lifespan,
)

# --- Core routers (open-source) ---

app.include_router(agents_router)
app.include_router(negotiations_router)
app.include_router(handoffs_router)
app.include_router(discovery_router)
app.include_router(trust_router)
app.include_router(capabilities_router)
app.include_router(attestations_router)
app.include_router(challenges_router)
app.include_router(delivery_router)
app.include_router(progress_router)
app.include_router(context_router)
app.include_router(stakes_router)
app.include_router(credentials_router)
app.include_router(ws_router)

# --- Extensions (proprietary cloud features loaded at runtime) ---
# Extensions MUST be loaded before middleware — Starlette's BaseHTTPMiddleware
# captures the app's route table at registration time. Routes added after
# middleware wrapping are not visible through the middleware stack.

_loaded_extensions = load_extensions(app)

# --- Middleware ---

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
)

# Starlette add_middleware prepends: last added = outermost.
# Execution order: Auth (outermost) -> RateLimit (inner) -> Route handler
# Auth runs first so request.state.agent_id is set for per-agent rate limiting.
# Unauthenticated requests (public paths) still get per-IP rate limiting.

from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.auth import AuthMiddleware
from app.middleware.body_limit import BodyLimitMiddleware
from app.middleware.csrf import CSRFMiddleware

# Starlette add_middleware prepends: last added = outermost.
# Execution order: BodyLimit -> Auth -> CSRF -> RateLimit -> Route handler
app.add_middleware(RateLimitMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(BodyLimitMiddleware, max_body_bytes=2 * 1024 * 1024)  # 2 MB

# --- Exception handlers ---

@app.exception_handler(AuthError)
async def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(NegotiationError)
async def negotiation_error_handler(request: Request, exc: NegotiationError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    from app.redis import get_redis
    redis = await get_redis()
    return {
        "status": "ok",
        "extensions_loaded": len(_loaded_extensions),
        "redis": "connected" if redis else "unavailable",
    }

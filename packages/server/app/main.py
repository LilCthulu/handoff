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
    yield
    logger.info("handoff_server_shutting_down")


app = FastAPI(
    title="Handoff",
    description="Universal Agent-to-Agent Negotiation & Delegation Protocol",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Security middleware ---
# Starlette add_middleware prepends: last added = outermost.
# Execution order: Auth (outermost) -> RateLimit (inner) -> Route handler
# Auth runs first so request.state.agent_id is set for per-agent rate limiting.
# Unauthenticated requests (public paths) still get per-IP rate limiting.

from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.auth import AuthMiddleware

app.add_middleware(RateLimitMiddleware)  # inner: rate limit with agent_id available
app.add_middleware(AuthMiddleware)       # outer: validate JWT + set request.state.agent_id

# --- Exception handlers ---

@app.exception_handler(AuthError)
async def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(NegotiationError)
async def negotiation_error_handler(request: Request, exc: NegotiationError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


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
app.include_router(ws_router)

# --- Extensions (proprietary cloud features loaded at runtime) ---

_loaded_extensions = load_extensions(app)


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok", "extensions": _loaded_extensions}

# Multi-stage build for production-ready Handoff server.
# Stage 1: install dependencies (cached layer)
# Stage 2: slim runtime image with non-root user

# --- Build stage ---
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build deps (some packages need gcc for C extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .

# --- Runtime stage ---
FROM python:3.12-slim

# Security: non-root user
RUN groupadd -r handoff && useradd -r -g handoff -d /app -s /sbin/nologin handoff

WORKDIR /app

# Install only runtime deps (libpq for asyncpg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

# Own the app directory
RUN chown -R handoff:handoff /app

USER handoff

EXPOSE 8000

# Health check — fail if server doesn't respond within 5s
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Production server with access logging and proxy header support.
# FORWARDED_ALLOW_IPS defaults to the Docker bridge network.
# Set to "*" only when behind a trusted reverse proxy (e.g. nginx).
ENV FORWARDED_ALLOW_IPS="172.17.0.0/16"
CMD uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --proxy-headers \
    --forwarded-allow-ips "${FORWARDED_ALLOW_IPS}" \
    --access-log

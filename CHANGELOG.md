# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Attestation chains with Ed25519 signatures and domain-scoped summaries
- Capability challenges with pass rate tracking
- Stake mechanism with escrow lifecycle
- Third-party credential system with revocation support
- Streaming progress updates with checkpoint/rollback support
- Delivery receipts with cryptographic verification
- Context privacy (public, committed, sealed layers)
- Extension system via Python entry points and environment variable
- Production Docker deployment with PostgreSQL, Redis, and nginx
- CI pipeline with Python 3.11 and 3.12 matrix testing

### Security
- Ed25519 cryptographic identity and message signing
- Challenge-response authentication (no passwords)
- 3-layer rate limiting (per-agent, per-IP, global circuit breaker)
- PII sealing with TTL and access limits
- JWT capability tokens with scoped authority

## [0.1.0] - 2026-03-01

### Added
- Initial release
- Agent registration and discovery
- Negotiation state machine with turn enforcement
- Handoff delegation with result verification
- Bayesian trust scoring (domain-scoped, negativity-biased)
- Python SDK with auto-reauth and WebSocket reconnect
- SQLite development mode (zero-config)
- PostgreSQL production support with Alembic migrations

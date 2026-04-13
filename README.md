<p align="center">
  <h1 align="center">Handoff</h1>
  <p align="center"><strong>The trust layer for AI agent collaboration</strong></p>
  <p align="center">
    <a href="https://github.com/LilCthulu/handoff/actions"><img src="https://github.com/LilCthulu/handoff/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License"></a>
    <img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+">
    <img src="https://img.shields.io/badge/tests-461%20passing-brightgreen.svg" alt="461 tests">
    <a href="https://github.com/LilCthulu/handoff/issues"><img src="https://img.shields.io/github/issues/LilCthulu/handoff.svg" alt="Issues"></a>
    <a href="https://github.com/LilCthulu/handoff/stargazers"><img src="https://img.shields.io/github/stars/LilCthulu/handoff.svg?style=social" alt="Stars"></a>
  </p>
  <p align="center">
    <a href="#quick-start">Quick Start</a> &middot;
    <a href="#sdk">SDK</a> &middot;
    <a href="protocol/spec.md">Protocol Spec</a> &middot;
    <a href="#demo">Demo</a> &middot;
    <a href="#api-reference">API Reference</a>
  </p>
</p>

---

Handoff is an open protocol that lets AI agents discover each other, negotiate tasks, build trust, and hand off work — with cryptographic integrity and full auditability.

Think of it as TCP/IP for agent collaboration. Any agent, any model, any framework. Register, discover, negotiate, delegate, verify.

```python
from handoff_sdk import HandoffAgent, Intent

agent = HandoffAgent(name="my-agent", server="http://localhost:8000")
await agent.register(capabilities=[{"domain": "hotels", "actions": ["book_room"]}])

hotels = await agent.discover(domain="hotels", min_trust=0.7)
negotiation = await agent.negotiate(
    target=hotels[0],
    intent=Intent.request("hotels", "book_room").with_budget(2000, "USD"),
)
```

## Why Handoff

Right now, AI agents are islands. They can talk to humans, but they can't talk to each other — not with trust, accountability, or verifiable results. There's no way for one agent to prove competence, negotiate terms, or be held to commitments.

Handoff fixes this with:

- **Earned trust** — domain-scoped Bayesian scoring with negativity bias. An agent good at translation isn't automatically trusted for payments.
- **Cryptographic identity** — Ed25519 keys, signed envelopes, challenge-response auth. Every message is provably from who it claims.
- **Structured negotiation** — state machine with turn enforcement, offers/counteroffers, mediation, and configurable round limits.
- **Verified delivery** — signed receipts, result validation against contracts, streaming progress with stall detection.
- **Context privacy** — three layers (public, committed, sealed). PII never leaves the server. Pseudonymous identifiers scoped to single handoffs.
- **Capability contracts** — typed JSON Schema input/output, SLA commitments, behavioral obligations, version management.

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL (production) or SQLite (development — zero config)
- Redis (optional — for rate limiting and pub/sub)

### Run the server

```bash
git clone https://github.com/LilCthulu/handoff.git
cd handoff/packages/server
pip install -e ".[dev]"

# Development mode (SQLite, no external dependencies)
uvicorn app.main:app --reload
```

The server auto-creates SQLite tables in development. For production, use PostgreSQL with Alembic migrations:

```bash
cp ../../.env.example .env  # edit DATABASE_URL, JWT_SECRET, etc.
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Docker

```bash
docker-compose up -d    # PostgreSQL + Redis + server
```

### Install the SDK

```bash
pip install handoff-sdk
```

## SDK

The Python SDK handles registration, discovery, negotiation, handoffs, crypto, and WebSocket communication.

### Register an agent

```python
from handoff_sdk import HandoffAgent

agent = HandoffAgent(name="my-agent", server="http://localhost:8000")
profile = await agent.register(
    capabilities=[{
        "domain": "travel",
        "actions": ["search_hotels", "book_room"],
    }],
    description="Travel booking assistant",
)
print(f"Agent ID: {profile.id}, Trust: {profile.trust_score}")
```

Ed25519 keys are generated automatically and persisted to disk. Re-authentication uses challenge-response — no passwords, no tokens to manage.

### Discover agents

```python
hotels = await agent.discover(domain="hotels", min_trust=0.5)
for h in hotels:
    print(f"{h.name} — trust: {h.trust_score}")
```

### Build an intent

Intents are what agents want. The fluent builder makes them easy to construct:

```python
from handoff_sdk import Intent

intent = (
    Intent.request("hotels", "book_room")
    .with_parameters(destination="Tokyo", nights=5, guests=2)
    .with_budget(2000, "USD")
    .with_deadline("2026-03-15T00:00:00Z")
    .must_have("free_cancellation")
    .nice_to_have("late_checkout", "airport_shuttle")
    .with_priority("high")
    .on_failure("escalate")
)
```

### Negotiate

```python
negotiation = await agent.negotiate(target=hotels[0], intent=intent, max_rounds=10)

# Submit offers
await negotiation.offer(
    terms={"price_per_night": 350, "includes": ["wifi", "breakfast"]},
    concessions=["Added breakfast"],
)

# Wait for counter, then accept
await negotiation.refresh()
if negotiation.current_offer:
    await negotiation.accept()
```

### Hand off work

```python
handoff = await agent.handoff(
    to=hotels[0],
    context={
        "task_description": "Book the agreed room",
        "agreed_terms": negotiation.agreement,
        "input_data": {"guest_name": "Jane Smith"},
        "constraints": {"timeout_minutes": 30, "rollback_on_failure": True},
    },
    negotiation_id=negotiation.id,
)

result = await handoff.poll_until_resolved(interval=2, timeout=120)
print(f"Status: {result.status}, Result: {result.result}")
```

### Task execution framework

For agents that receive work, the `@agent.task` decorator handles everything — capability registration, handoff routing, result submission:

```python
@agent.task(domain="hotels", action="book_room")
async def book_room(ctx: TaskContext) -> dict:
    await ctx.report_progress("Checking availability", 0.2)
    # ... do the work ...
    await ctx.report_progress("Confirming reservation", 0.8)
    return {"confirmation": "PH-TYO-2026-78432", "status": "confirmed"}

agent.run()  # Starts listening and auto-routes incoming handoffs
```

## Demo

Two agents negotiate a luxury hotel booking in real time:

```bash
# Terminal 1 — Start the server
cd packages/server && uvicorn app.main:app --reload

# Terminal 2 — Hotel agent (starts at $600/night, min $380, offers concessions)
cd packages/demo && python hotel_agent.py

# Terminal 3 — Travel agent (budget $2000 for 5 nights, negotiates aggressively)
cd packages/demo && python travel_agent.py
```

The travel agent discovers the hotel agent, builds an intent, starts a negotiation. They exchange offers and counteroffers with concessions (breakfast, gym access, late checkout). When they reach agreement, the travel agent initiates a handoff. The hotel agent processes the booking and returns a confirmation.

## Architecture

```
┌─────────────┐                 ┌──────────────┐      ┌────────────┐
│  Agent SDK  │  REST / WS      │              │      │ PostgreSQL │
│  (Python)   │ ───────────────>│   Handoff    │─────>│  (or SQLite│
└─────────────┘                 │   Server     │      │   for dev) │
                                │  (FastAPI)   │      └────────────┘
┌─────────────┐                 │              │      ┌────────────┐
│  Agent SDK  │  REST / WS      │  Ed25519     │─────>│   Redis    │
│  (Python)   │ ───────────────>│  JWT Auth    │      │ (optional) │
└─────────────┘                 │  Trust Algo  │      └────────────┘
                                │  Rate Limit  │
┌─────────────┐                 │  Extensions  │
│  Dashboard  │  REST / SSE     │              │
│  (Next.js)  │ ───────────────>│              │
└─────────────┘                 └──────────────┘
```

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Server** | Python / FastAPI | REST + WebSocket API, state machines, trust scoring, privacy enforcement |
| **Database** | PostgreSQL + asyncpg | Agents, negotiations, handoffs, trust events, audit log |
| **Cache** | Redis | Rate limiting, pub/sub for real-time events |
| **SDK** | Python (`handoff-sdk`) | Client with auto-reauth, WebSocket reconnect, crypto, task framework |
| **Dashboard** | Next.js + Tailwind | Real-time monitoring console (cloud repo) |
| **Crypto** | Ed25519 (PyNaCl) | Message signing, envelope verification, challenge-response |
| **Auth** | JWT (HS256) | Capability tokens with scopes and authority limits |

## Core Concepts

### Trust Scoring

Trust is domain-scoped, Bayesian, and asymmetric. An agent proves itself per domain — trust in `hotels.book` doesn't bleed into `payments.charge`.

New agents start at 0.5 (neutral). Trust updates after every interaction using weighted factors:

| Factor | Weight |
|--------|--------|
| Handoff success rate | 0.30 |
| Negotiation completion rate | 0.25 |
| Response time | 0.15 |
| Dispute rate (inverted) | 0.15 |
| Longevity | 0.10 |
| Peer ratings | 0.05 |

Negativity bias: failures weigh 1.5x more than successes. Inactive agents decay toward 0.5 after 30 days.

### Negotiation State Machine

```
CREATED ──> PENDING ──> NEGOTIATING ──> AGREED ──> EXECUTING ──> COMPLETED
                │             │                         │
                v             v                         v
             REJECTED      FAILED                    FAILED
```

Turn enforcement prevents consecutive offers from the same agent. Max rounds, timeouts, and mediation are configurable.

### Cryptographic Integrity

Every agent message is wrapped in a signed envelope:

- **Sender identity** — agent ID + public key fingerprint
- **Payload hash** — SHA-256 of the message body
- **Timestamp** — prevents replay attacks
- **Ed25519 signature** — over canonical JSON of the envelope

The server verifies signatures against registered public keys. No valid signature, no processing.

### Context Privacy

Three layers protect sensitive data during handoffs:

| Layer | Visibility | Use Case |
|-------|-----------|----------|
| **Public** | Everyone | Agent discovery, capability matching |
| **Committed** | Post-agreement, encrypted to recipient | Task details, agreed terms |
| **Sealed** | Never leaves server | PII — resolved via reference tokens with TTL and access limits |

Data minimization strips context to only what the receiver's contract declares it needs.

### Capability Contracts

Agents publish typed contracts for their capabilities:

```json
{
  "domain": "hotels",
  "action": "book_room",
  "version": "1.2.0",
  "input_schema": { "type": "object", "properties": { "destination": { "type": "string" } } },
  "output_schema": { "type": "object", "properties": { "confirmation": { "type": "string" } } },
  "sla": { "max_latency_ms": 30000, "availability_target": 0.99 },
  "obligations": { "data_retention": "30d", "pii_access": false }
}
```

The server validates inputs and outputs against these schemas and enforces SLA commitments.

### Verified Delivery

Handoff results include:

- **Signed delivery receipts** — the completing agent signs the result with Ed25519
- **Acknowledgment signatures** — the delegating agent signs acceptance
- **Result validation** — server checks output against the capability contract
- **Proof of work** — optional attachments (API responses, transaction hashes)
- **Streaming progress** — REST polling or WebSocket with stall detection
- **Checkpoints** — multi-step tasks support rollback and resume

### Attestation Chains

Agents build verifiable track records through signed attestation chains:

- Each attestation is Ed25519-signed by the attesting agent
- Chain integrity is cryptographically verifiable
- Domain-scoped summaries aggregate attestation history
- Third-party credentials (5 types) with revocation support

### Stake Mechanism

Agents can stake collateral on task completion:

- Escrow lifecycle: `pending` -> `locked` -> `released` / `forfeited`
- Agent balances tracked on-chain
- Stakes are forfeited on failure, released on success
- Adds economic accountability beyond reputation

## API Reference

All endpoints are under `/api/v1/`. Authentication is via `Authorization: Bearer <JWT>` header. Public endpoints (registration, challenge, authentication, discovery) don't require auth.

### Agents

| Method | Endpoint | Description |
|--------|---------|-------------|
| `POST` | `/agents/register` | Register with capabilities and public key (optional `api_key` for org linking) |
| `POST` | `/agents/challenge` | Request a challenge nonce |
| `POST` | `/agents/authenticate` | Submit signed challenge for JWT |
| `GET` | `/agents/{id}` | Get agent profile |
| `PATCH` | `/agents/{id}` | Update agent metadata |
| `DELETE` | `/agents/{id}` | Deregister agent |
| `POST` | `/agents/{id}/rotate-keys` | Rotate Ed25519 keys |

### Discovery

| Method | Endpoint | Description |
|--------|---------|-------------|
| `GET` | `/discover` | Find agents by domain, action, and min trust |

### Negotiations

| Method | Endpoint | Description |
|--------|---------|-------------|
| `POST` | `/negotiations` | Create negotiation with intent and target |
| `GET` | `/negotiations/{id}` | Get negotiation state |
| `POST` | `/negotiations/{id}/offer` | Submit offer or counteroffer |
| `POST` | `/negotiations/{id}/accept` | Accept current offer |
| `POST` | `/negotiations/{id}/reject` | Reject negotiation |
| `POST` | `/negotiations/{id}/mediate` | Request mediation |
| `GET` | `/negotiations/{id}/history` | Get offer history |

### Handoffs

| Method | Endpoint | Description |
|--------|---------|-------------|
| `POST` | `/handoffs` | Initiate task delegation |
| `GET` | `/handoffs/{id}` | Get handoff status |
| `PATCH` | `/handoffs/{id}/status` | Update execution status |
| `POST` | `/handoffs/{id}/result` | Submit completion result |
| `POST` | `/handoffs/{id}/rollback` | Roll back failed handoff |
| `GET` | `/handoffs/chain/{chain_id}` | View delegation chain |

### Trust

| Method | Endpoint | Description |
|--------|---------|-------------|
| `GET` | `/trust/agent/{id}` | Get trust scores by domain |
| `GET` | `/trust/agent/{id}/summary` | Get trust summary |
| `GET` | `/trust/stats` | Network-wide trust statistics |
| `GET` | `/trust/domains` | List all scored domains |

### Capability Contracts

| Method | Endpoint | Description |
|--------|---------|-------------|
| `POST` | `/capabilities` | Register a capability contract |
| `GET` | `/capabilities/mine` | List agent's own capabilities |
| `GET` | `/capabilities/discover` | Discover by domain and action |
| `GET` | `/capabilities/{id}` | Get contract details |
| `PATCH` | `/capabilities/{id}` | Update contract |
| `DELETE` | `/capabilities/{id}` | Remove capability |

### Attestations

| Method | Endpoint | Description |
|--------|---------|-------------|
| `POST` | `/attestations` | Create signed attestation |
| `GET` | `/attestations/agent/{id}` | Get agent's attestation chain |
| `GET` | `/attestations/agent/{id}/summary` | Domain attestation summary |
| `GET` | `/attestations/{id}` | Get single attestation |
| `POST` | `/attestations/{id}/verify` | Verify attestation signature |

### Capability Challenges

| Method | Endpoint | Description |
|--------|---------|-------------|
| `POST` | `/challenges` | Issue a capability challenge |
| `GET` | `/challenges/pending` | List pending challenges |
| `POST` | `/challenges/{id}/respond` | Submit challenge response |
| `GET` | `/challenges/agent/{id}` | Agent's challenge history |
| `GET` | `/challenges/agent/{id}/summary` | Challenge pass rate summary |
| `GET` | `/challenges/{id}` | Get challenge details |

### Context Privacy

| Method | Endpoint | Description |
|--------|---------|-------------|
| `POST` | `/context/seal` | Seal PII with reference token |
| `POST` | `/context/resolve` | Resolve sealed reference (authorized only) |
| `POST` | `/context/revoke` | Revoke a sealed reference |
| `POST` | `/context/pseudonym` | Generate scoped pseudonymous ID |

### Delivery Receipts

| Method | Endpoint | Description |
|--------|---------|-------------|
| `POST` | `/delivery` | Submit signed delivery receipt |
| `POST` | `/delivery/{id}/acknowledge` | Acknowledge receipt |
| `GET` | `/delivery/handoff/{handoff_id}` | Get receipts for handoff |
| `GET` | `/delivery/{id}` | Get single receipt |
| `POST` | `/delivery/{id}/verify` | Verify receipt signature |

### Streaming Progress

| Method | Endpoint | Description |
|--------|---------|-------------|
| `POST` | `/progress/handoffs/{id}/update` | Push progress update |
| `POST` | `/progress/handoffs/{id}/checkpoint` | Create checkpoint |
| `GET` | `/progress/handoffs/{id}/checkpoints` | List checkpoints |
| `GET` | `/progress/handoffs/{id}/latest` | Latest progress |

### Stakes

| Method | Endpoint | Description |
|--------|---------|-------------|
| `POST` | `/stakes` | Create stake (escrow) |
| `GET` | `/stakes/agent/{id}` | Agent's active stakes |
| `GET` | `/stakes/agent/{id}/summary` | Balance and stake summary |

### Third-Party Credentials

| Method | Endpoint | Description |
|--------|---------|-------------|
| `POST` | `/credentials` | Register a credential |
| `GET` | `/credentials/agent/{id}` | Agent's credentials |
| `GET` | `/credentials/agent/{id}/summary` | Credential summary |
| `POST` | `/credentials/{id}/revoke` | Revoke credential |
| `POST` | `/credentials/{id}/verify` | Verify credential signature |
| `GET` | `/credentials/{id}` | Get credential details |

### WebSocket

| Endpoint | Description |
|---------|-------------|
| `ws://host/ws/{token}` | Real-time events — offers, state changes, heartbeat |

### Audit Log

| Method | Endpoint | Description |
|--------|---------|-------------|
| `GET` | `/audit/{entity_type}/{entity_id}` | Audit trail for any entity |

## Extension System

The server loads plugins via Python entry points:

```toml
# pyproject.toml
[project.entry-points."handoff.extensions"]
my_extension = "my_package.routes"
```

Or via environment variable:

```bash
HANDOFF_EXTENSIONS=my_package.routes,another_package.api
```

Each extension module exposes `register(app: FastAPI) -> None` and can add routes, middleware, or startup hooks.

## Project Structure

```
handoff/
├── packages/
│   ├── server/                 # FastAPI backend (protocol core)
│   │   ├── app/
│   │   │   ├── main.py         # App entry, middleware, extension loading
│   │   │   ├── config.py       # pydantic-settings configuration
│   │   │   ├── database.py     # Async SQLAlchemy (PostgreSQL + SQLite)
│   │   │   ├── extensions.py   # Plugin loader (entry_points + env var)
│   │   │   ├── models/         # ORM models (cross-dialect via GUID + JSONType)
│   │   │   ├── schemas/        # Pydantic request/response schemas
│   │   │   ├── api/            # REST route handlers
│   │   │   ├── core/           # Business logic (crypto, auth, trust, negotiation)
│   │   │   ├── middleware/     # Auth, rate limiting
│   │   │   └── websocket/     # Real-time WebSocket handlers
│   │   ├── tests/              # 253 tests (unit + integration + security)
│   │   └── alembic/            # Database migrations
│   ├── sdk/                    # Python SDK (pip install handoff-sdk)
│   │   ├── handoff_sdk/        # Client, crypto, intent builder, task framework
│   │   └── tests/              # SDK tests
│   └── demo/                   # Demo agents (travel + hotel negotiation)
├── protocol/
│   ├── spec.md                 # Formal protocol specification
│   ├── schemas/                # JSON Schema definitions
│   └── examples/               # Example message flows
├── nginx/                      # Production nginx configuration
├── docker-compose.yml          # PostgreSQL + Redis + server
├── .env.example                # Environment variable template
├── SECURITY.md                 # Vulnerability reporting policy
├── CONTRIBUTING.md             # Contribution guidelines
└── LICENSE                     # MIT
```

## Running Tests

```bash
# Server tests
cd packages/server
pip install -e ".[dev]"
pytest tests/ -v

# SDK tests
cd packages/sdk
pip install -e ".[dev]"
pytest tests/ -v
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | — | `postgresql+asyncpg://...` or `sqlite+aiosqlite://` |
| `REDIS_URL` | — | Redis connection string (optional) |
| `JWT_SECRET` | — | Secret key for JWT signing |
| `JWT_EXPIRY_HOURS` | `24` | JWT token lifetime |
| `SERVER_HOST` | `0.0.0.0` | Bind address |
| `SERVER_PORT` | `8000` | Bind port |
| `CORS_ORIGINS` | `http://localhost:3000` | Allowed CORS origins |
| `LOG_LEVEL` | `INFO` | Logging level |
| `HANDOFF_EXTENSIONS` | — | Comma-separated extension module paths |

## Security

- **Identity**: Ed25519 key pairs per agent, challenge-response authentication
- **Message integrity**: Every payload signed, SHA-256 hashed, timestamped
- **Authorization**: JWT capability tokens with scoped authority and spending limits
- **Rate limiting**: 3-layer protection (per-agent, per-IP, global circuit breaker) with penalty escalation
- **Audit trail**: Cryptographically signed, immutable log of every state change
- **Privacy**: Three-layer context model, sealed PII with TTL, pseudonymous identifiers

## Contributing

We welcome contributions. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. Please open an issue before submitting large PRs so we can discuss the approach.

## License

MIT — see [LICENSE](LICENSE).

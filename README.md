# Handoff

**Universal Agent-to-Agent Negotiation & Delegation Protocol**

A production-grade, open-source platform that enables AI agents to discover each other, negotiate tasks, establish trust, and hand off work — with full context, cryptographic integrity, and auditability.

## Quick Start

```bash
# Start infrastructure
docker-compose up -d

# Run database migrations
cd packages/server
alembic upgrade head

# Start the server
uvicorn app.main:app --reload
```

## SDK — 3 Lines to Negotiate

```python
from handoff_sdk import HandoffAgent, Intent

agent = HandoffAgent(name="my-agent", server="http://localhost:8000")
await agent.register(capabilities=[{"domain": "hotels", "actions": ["book_room"]}])

# Discover and negotiate
hotels = await agent.discover(domain="hotels", min_trust=0.5)
intent = Intent.request(domain="hotels", action="book_room").with_budget(2000, "USD")
negotiation = await agent.negotiate(target=hotels[0], intent=intent)
```

Install: `pip install handoff-sdk`

## Demo

Two agents negotiate a luxury hotel booking in real time:

```bash
# Terminal 1: Hotel agent (starts at $600/night, min $380)
python packages/demo/hotel_agent.py

# Terminal 2: Travel agent (budget $2000 for 5 nights, starts at $300/night)
python packages/demo/travel_agent.py
```

The agents discover each other, exchange offers and counteroffers with concessions, reach agreement, then execute a handoff with booking confirmation.

## Architecture

```
┌─────────────┐    REST/WS     ┌──────────────┐     ┌────────────┐
│  Agent SDK  │ ──────────────▶│   Server     │────▶│ PostgreSQL │
│  (Python)   │                │  (FastAPI)   │     └────────────┘
└─────────────┘                │              │     ┌────────────┐
                               │  Ed25519 sig │────▶│   Redis    │
┌─────────────┐    REST/WS     │  JWT auth    │     └────────────┘
│  Dashboard  │ ──────────────▶│  Trust algo  │
│  (Next.js)  │                │  Rate limit  │
└─────────────┘                └──────────────┘
```

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Server | Python / FastAPI | REST + WebSocket API, state machine, trust scoring |
| Database | PostgreSQL + asyncpg | Agents, negotiations, handoffs, audit log |
| Cache | Redis | Rate limiting, pub/sub for real-time events |
| SDK | Python (`handoff-sdk`) | Fluent client with crypto, sessions, WebSocket |
| Dashboard | Next.js + Tailwind | Real-time monitoring (proprietary, in cloud repo) |
| Crypto | Ed25519 (PyNaCl) | Message signing, envelope verification |
| Auth | JWT (HS256) | Capability tokens with scopes and authority limits |

## Core Concepts

### Negotiation State Machine

```
CREATED → PENDING → NEGOTIATING → AGREED → EXECUTING → COMPLETED
              ↓           ↓                      ↓
           REJECTED     FAILED                 FAILED
```

Every state transition is validated. Terminal states (`COMPLETED`, `REJECTED`, `FAILED`) are final.

### Trust Scoring

Agents start at 0.5 (neutral). Trust is computed from 6 weighted factors:

| Factor | Weight |
|--------|--------|
| Handoff success rate | 0.30 |
| Negotiation completion rate | 0.25 |
| Response time | 0.15 |
| Dispute rate (inverted) | 0.15 |
| Longevity | 0.10 |
| Peer ratings | 0.05 |

Inactive agents decay toward 0.5 after 30 days.

### Cryptographic Integrity

Every message is signed with Ed25519. Envelopes include:
- Sender identity and public key fingerprint
- SHA-256 payload hash
- Timestamp and message ID
- Ed25519 signature over canonical JSON

### Intent Language

```python
intent = (
    Intent.request(domain="hotels", action="book_room")
    .with_budget(2000, "USD")
    .must_have("free_cancellation")
    .nice_to_have("late_checkout", "airport_shuttle")
    .with_priority("high")
    .on_failure("escalate")
)
```

## Project Structure

```
handoff/
├── packages/
│   ├── server/              # FastAPI backend
│   │   ├── app/
│   │   │   ├── api/         # REST endpoints (agents, negotiations, handoffs)
│   │   │   ├── core/        # Business logic (crypto, auth, trust, negotiation engine)
│   │   │   ├── middleware/   # Auth + rate limiting middleware
│   │   │   ├── models/      # SQLAlchemy ORM models
│   │   │   ├── schemas/     # Pydantic request/response schemas
│   │   │   └── websocket/   # Real-time WebSocket handlers
│   │   ├── alembic/         # Database migrations
│   │   └── tests/           # Server test suite (105 tests)
│   ├── sdk/                 # Python SDK
│   │   ├── handoff_sdk/     # Client, sessions, crypto, types
│   │   └── tests/           # SDK test suite (31 tests)
│   └── demo/                # Demo agents (travel + hotel)
├── protocol/
│   ├── spec.md              # Formal protocol specification
│   ├── schemas/             # JSON Schema definitions
│   └── examples/            # Example flows (handoff, negotiation, multi-party)
├── docker-compose.yml       # PostgreSQL + Redis + server
└── .env.example             # Environment variable template
```

## API

### Agents
- `POST /agents/register` — Register with capabilities and public key
- `POST /agents/auth` — Authenticate and receive JWT
- `GET /agents/{id}` — Get agent profile
- `GET /agents/discover` — Find agents by domain, trust, capability

### Negotiations
- `POST /negotiations/` — Create with intent and target
- `POST /negotiations/{id}/offer` — Submit offer/counteroffer
- `POST /negotiations/{id}/accept` — Accept current offer
- `POST /negotiations/{id}/reject` — Reject negotiation
- `POST /negotiations/{id}/mediate` — Request AI mediation

### Handoffs
- `POST /handoffs/` — Initiate task delegation
- `PUT /handoffs/{id}/status` — Update execution status
- `PUT /handoffs/{id}/result` — Submit completion result
- `POST /handoffs/{id}/rollback` — Roll back failed handoff
- `GET /handoffs/chain/{chain_id}` — View delegation chain

### WebSocket
- `ws://host/ws/{token}` — Real-time events (offers, state changes, heartbeat)

## Extension System

The server supports plugins via Python entry points:

```toml
# In your extension's pyproject.toml
[project.entry-points."handoff.extensions"]
my_extension = "my_package.routes"
```

Or via environment variable:
```
HANDOFF_EXTENSIONS=my_package.routes,another_package.api
```

Each extension module must expose `register(app: FastAPI) -> None`.

## Running Tests

```bash
# Server tests (105 tests)
cd packages/server
pip install -e ".[dev]"
pytest tests/ -v

# SDK tests (31 tests)
cd packages/sdk
pip install -e ".[dev]"
pytest tests/ -v
```

## Security

- Ed25519 message signing on every payload
- JWT capability tokens with scopes and spending authority
- 3-layer rate limiting (per-agent, per-IP, global circuit breaker)
- Penalty escalation for rate limit violations
- Cryptographically signed audit log entries
- Public key rotation support

## License

MIT — See [LICENSE](LICENSE).

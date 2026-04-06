# Handoff Protocol Specification v1.0

## 1. Overview

The Handoff protocol enables AI agents to discover each other, negotiate tasks, establish trust, and delegate work with full context and auditability. It is designed to be the universal standard for agent-to-agent communication.

## 2. Transport

- **REST API** for CRUD operations (registration, discovery, status queries)
- **WebSocket** for real-time negotiation and handoff events
- All messages use JSON encoding
- All timestamps are ISO 8601 in UTC

## 3. Authentication

### 3.1 Agent Identity

Each agent has an Ed25519 key pair. The public key is registered with the server and serves as the agent's cryptographic identity.

### 3.2 JWT Capability Tokens

Upon registration or authentication, the server issues a JWT containing:
- `sub`: Agent UUID
- `iss`: `handoff-server`
- `scopes`: Permitted actions (`negotiate`, `handoff`, `discover`)
- `authority`: Spending limits and domain restrictions

Tokens are passed via `Authorization: Bearer <token>` header.

### 3.3 Message Signing

Every agent-to-agent message is wrapped in a signed envelope (see `envelope.schema.json`). The envelope includes:
- Sender identity and public key fingerprint
- SHA-256 hash of the payload
- Ed25519 signature over the canonical envelope JSON

Recipients verify the signature against the sender's registered public key.

## 4. Agent Lifecycle

### 4.1 Registration

`POST /api/v1/agents/register`

Agent provides: name, owner_id, public_key, capabilities, max_authority. Server returns: agent_id, JWT token.

### 4.2 Discovery

`GET /api/v1/discover?domain=X&action=Y&min_trust=Z`

Returns agents matching the capability query, sorted by trust score.

### 4.3 Key Rotation

`POST /api/v1/agents/{id}/rotate-keys`

Agent submits a new public key signed by the old key. Server updates the registered key.

## 5. Negotiation Protocol

### 5.1 State Machine

Negotiations follow a strict state machine:

```
CREATED -> PENDING -> NEGOTIATING -> AGREED -> EXECUTING -> COMPLETED
                |          |                        |
                v          v                        v
             REJECTED    FAILED                   FAILED
```

### 5.2 Initiation

`POST /api/v1/negotiations`

Initiator submits an intent (see `intent.schema.json`) targeting a specific agent. The negotiation enters `PENDING` state.

### 5.3 Offer/Counteroffer

Agents exchange offers (see `offer.schema.json`) via REST or WebSocket. Each offer increments the round counter. Negotiations have a configurable `max_rounds` limit.

### 5.4 Resolution

A negotiation resolves when:
- An agent accepts the current offer -> `AGREED`
- An agent rejects -> `REJECTED`
- Max rounds exceeded -> `FAILED`
- Timeout expires -> `FAILED`

### 5.5 Mediation

For complex multi-party negotiations, a mediator can be requested. The mediator (server or designated agent) coordinates offers and can make recommendations.

## 6. Handoff Protocol

### 6.1 Context Transfer

When agents agree on terms, the initiator creates a handoff (see `handoff.schema.json`) containing:
- Task description and agreed terms
- Input data needed for execution
- Partial work state (if any)
- Execution constraints (timeout, rollback policy)
- Provenance chain

### 6.2 Execution

The receiving agent updates handoff status: `initiated` -> `in_progress` -> `completed` or `failed`.

### 6.3 Chain of Custody

Multi-hop handoffs share a `chain_id`. Each hop records `chain_position` and `parent_handoff_id`, creating a full provenance trail.

### 6.4 Rollback

If `rollback_on_failure` is set and the handoff fails, the receiving agent must attempt to undo any partial work.

## 7. Trust Scoring

Trust scores range from 0.0 to 1.0, starting at 0.5 for new agents. Scores are updated after each interaction based on:

| Factor | Weight |
|--------|--------|
| Negotiation completion rate | 0.25 |
| Handoff success rate | 0.30 |
| Response time | 0.15 |
| Dispute rate (inverse) | 0.15 |
| Longevity | 0.10 |
| Peer ratings | 0.05 |

Inactive agents (30+ days) experience trust decay toward 0.5.

## 8. Audit Trail

Every state change produces an immutable audit log entry containing:
- Entity type and ID
- Action performed
- Acting agent
- Full details payload
- Cryptographic signature

## 9. Schemas

All message formats are defined as JSON Schema in the `schemas/` directory:
- `agent.schema.json` — Agent identity and capabilities
- `intent.schema.json` — Intent declaration
- `offer.schema.json` — Offer/counteroffer
- `handoff.schema.json` — Handoff context
- `envelope.schema.json` — Signed message envelope

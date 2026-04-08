# Handoff Protocol Specification v1.0

## 1. Overview

The Handoff protocol enables AI agents to discover each other, verify capabilities, negotiate tasks, establish trust, and delegate work with cryptographic accountability and privacy guarantees. It is designed to be the universal standard for agent-to-agent coordination.

## 2. Transport

- **REST API** for CRUD operations (registration, discovery, status queries)
- **WebSocket** for real-time negotiation and handoff events
- All messages use JSON encoding
- All timestamps are ISO 8601 in UTC

## 3. Authentication

### 3.1 Agent Identity

Each agent has an Ed25519 key pair. The public key is registered with the server and serves as the agent's cryptographic identity.

### 3.2 Challenge-Response Authentication

Agents authenticate by proving key possession:

1. Agent requests a challenge nonce from the server (`POST /agents/challenge`)
2. Agent signs the nonce with its Ed25519 private key
3. Agent submits the signed challenge (`POST /agents/authenticate`)
4. Server verifies the signature against the registered public key
5. Server issues a JWT capability token

### 3.3 JWT Capability Tokens

Upon authentication, the server issues a JWT containing:
- `sub`: Agent UUID
- `iss`: `handoff-server`
- `scopes`: Permitted actions (`negotiate`, `handoff`, `discover`)
- `authority`: Spending limits and domain restrictions

Tokens are passed via `Authorization: Bearer <token>` header.

### 3.4 Key Rotation

`POST /agents/{id}/rotate-keys` — agent submits a new public key signed by the old key. The server updates the registered key after verifying the signature.

### 3.5 Message Signing

Every agent-to-agent message is wrapped in a signed envelope (see `envelope.schema.json`):

| Field | Description |
|-------|-------------|
| `sender_id` | Agent UUID |
| `public_key_fingerprint` | SHA-256 of the sender's registered public key |
| `payload_hash` | SHA-256 of the message body |
| `timestamp` | ISO 8601 — prevents replay attacks |
| `message_id` | Unique ID for deduplication |
| `signature` | Ed25519 signature over canonical JSON of the envelope |

Recipients verify signatures against the sender's registered public key.

## 4. Agent Lifecycle

### 4.1 Registration

`POST /api/v1/agents/register`

Agent provides: name, owner_id, public_key, capabilities, max_authority, description, metadata.
Server returns: agent profile + JWT token.

### 4.2 Discovery

`GET /api/v1/discover?domain=X&action=Y&min_trust=Z`

Returns agents matching the capability query, sorted by trust score descending.

### 4.3 Status Management

Agents can be in states: `active`, `suspended`, `revoked`. Suspended and revoked agents are rejected at the auth middleware layer.

## 5. Negotiation Protocol

### 5.1 State Machine

```
CREATED ──> PENDING ──> NEGOTIATING ──> AGREED ──> EXECUTING ──> COMPLETED
                │             │                         │
                v             v                         v
             REJECTED      FAILED                    FAILED
```

Terminal states (`COMPLETED`, `REJECTED`, `FAILED`) are final and irreversible.

### 5.2 Turn Enforcement

The server enforces alternating turns. An agent cannot submit two consecutive offers — the other party must respond first. This prevents spam offers and ensures genuine negotiation.

### 5.3 Initiation

`POST /api/v1/negotiations`

The initiator submits an intent (see `intent.schema.json`) targeting a specific agent. The negotiation enters `PENDING` state.

### 5.4 Offer/Counteroffer

Agents exchange offers (see `offer.schema.json`) via REST or WebSocket. Each offer includes:
- `terms`: The proposed agreement (domain-specific JSON)
- `concessions`: List of concessions made in this round
- `conditions`: Conditions attached to the offer

Each offer increments the round counter. Negotiations have a configurable `max_rounds` limit.

### 5.5 Resolution

A negotiation resolves when:
- An agent accepts the current offer -> `AGREED`
- An agent rejects -> `REJECTED`
- Max rounds exceeded -> `FAILED`
- Timeout expires -> `FAILED`

### 5.6 Mediation

For complex or stalled negotiations, a mediator can be requested via `POST /negotiations/{id}/mediate`. The mediator analyzes the negotiation history and recommends terms.

## 6. Handoff Protocol

### 6.1 Context Transfer

When agents agree on terms, the initiator creates a handoff containing:
- Task description and agreed terms
- Input data needed for execution
- Execution constraints (timeout, rollback policy)
- Provenance chain (origin agent, delegation path)
- Partial work state (for resumed tasks)

### 6.2 Execution Lifecycle

`initiated` -> `in_progress` -> `completed` or `failed`

The receiving agent updates status as work progresses.

### 6.3 Streaming Progress

During execution, agents can push progress updates:
- `POST /progress/handoffs/{id}/update` — message + completion percentage
- `POST /progress/handoffs/{id}/checkpoint` — create a resumable checkpoint
- `GET /progress/handoffs/{id}/latest` — poll latest progress

The server detects stalled handoffs when progress updates stop arriving.

### 6.4 Chain of Custody

Multi-hop handoffs share a `chain_id`. Each hop records `chain_position` and `parent_handoff_id`, creating a full provenance trail queryable via `GET /handoffs/chain/{chain_id}`.

### 6.5 Rollback

If `rollback_on_failure` is set and the handoff fails, the receiving agent must attempt to undo partial work. Checkpoints enable fine-grained rollback to the last known-good state.

## 7. Trust Scoring

### 7.1 Domain Scoping

Trust is scored per domain, not globally. An agent's trust in `hotels.book` is independent of its trust in `payments.charge`. This prevents reputation bleed across unrelated capabilities.

### 7.2 Score Computation

Trust scores range from 0.0 to 1.0, starting at 0.5 for new agents. Scores are updated after each interaction using Bayesian updates with weighted factors:

| Factor | Weight |
|--------|--------|
| Handoff success rate | 0.30 |
| Negotiation completion rate | 0.25 |
| Response time | 0.15 |
| Dispute rate (inverse) | 0.15 |
| Longevity | 0.10 |
| Peer ratings | 0.05 |

### 7.3 Negativity Bias

Failures carry 1.5x the weight of successes. One betrayal weighs more than one success. This reflects how trust actually works — it's hard to earn and easy to lose.

### 7.4 Inactivity Decay

Agents inactive for 30+ days experience trust decay toward 0.5 (neutral). Past performance matters less as time passes.

## 8. Capability Contracts

### 8.1 Contract Structure

Agents publish typed contracts for their capabilities:

```json
{
  "domain": "hotels",
  "action": "book_room",
  "version": "1.2.0",
  "input_schema": { ... },
  "output_schema": { ... },
  "sla": {
    "max_latency_ms": 30000,
    "availability_target": 0.99
  },
  "obligations": {
    "data_retention": "30d",
    "pii_access": false,
    "external_apis": ["booking-api.example.com"],
    "logging": "anonymized",
    "data_sharing": "none"
  }
}
```

### 8.2 Validation

The server validates handoff inputs and outputs against the contract's JSON schemas. SLA commitments (latency, availability) are tracked and enforced.

### 8.3 Versioning

Contracts are versioned with semver. Discovery can filter by version compatibility.

## 9. Context Privacy

### 9.1 Three-Layer Model

| Layer | Visibility | Encryption | Use Case |
|-------|-----------|------------|----------|
| **Public** | All agents | None | Discovery, capability matching |
| **Committed** | Post-agreement only | Encrypted to recipient | Task details, agreed terms |
| **Sealed** | Never leaves server | AES-256-GCM | PII — resolved via reference tokens |

### 9.2 Sealed References

PII is sealed on the server and replaced with reference tokens:

- `POST /context/seal` — encrypt PII, return reference token
- `POST /context/resolve` — resolve reference (authorized recipients only)
- Each token has a TTL and maximum access count
- Tokens can be revoked: `POST /context/revoke`

### 9.3 Pseudonymous Identifiers

`POST /context/pseudonym` generates a pseudonymous ID scoped to a single handoff. The same user gets different pseudonyms across handoffs, preventing cross-handoff tracking.

### 9.4 Data Minimization

Before context is transferred, the server strips it to only the fields declared in the capability contract's `input_schema`. Everything else is removed.

## 10. Verified Delivery

### 10.1 Delivery Receipts

When an agent completes a handoff, it submits a signed delivery receipt:

- `POST /delivery` — Ed25519-signed receipt with result hash
- The delegating agent acknowledges: `POST /delivery/{id}/acknowledge`
- Receipts are independently verifiable: `POST /delivery/{id}/verify`

### 10.2 Result Validation

The server validates the delivery result against the capability contract's `output_schema`.

### 10.3 Proof of Work

Delivery receipts can include proof-of-work attachments — upstream API responses, transaction hashes, or other evidence that work was actually performed.

## 11. Attestation Chains

### 11.1 Structure

Attestations are Ed25519-signed statements from one agent about another:

- `POST /attestations` — create a signed attestation
- Each attestation links to the previous one, forming a chain
- Chain integrity is cryptographically verifiable

### 11.2 Domain Summaries

`GET /attestations/agent/{id}/summary` returns aggregated attestation data per domain — total count, positive/negative ratio, most recent attestation.

## 12. Stake Mechanism

### 12.1 Escrow Lifecycle

Agents can stake collateral on task completion:

`pending` -> `locked` -> `released` (success) or `forfeited` (failure)

### 12.2 Agent Balances

Each agent has a tracked balance. Stakes are deducted when locked and returned (or forfeited) based on handoff outcome.

## 13. Third-Party Credentials

### 13.1 Credential Types

Agents can register verifiable credentials from external authorities:

- `certification` — formal certification by an authority
- `endorsement` — endorsement from a trusted entity
- `membership` — membership in an organization
- `audit` — audit result from an auditor
- `license` — license to operate in a domain

### 13.2 Verification

Credentials are Ed25519-signed by the issuer. Anyone can verify: `POST /credentials/{id}/verify`.

### 13.3 Trust Integration

Verified credentials contribute to trust scoring with configurable weights per credential type.

## 14. Capability Challenges

### 14.1 Purpose

Before committing to a high-stakes handoff, an agent can challenge another to prove competence:

- `POST /challenges` — issue a timed challenge with a test input
- `POST /challenges/{id}/respond` — submit response within time limit
- Server validates the response against the expected output schema

### 14.2 Scoring

Challenge results (pass/fail, latency) feed into the trust scoring system and are queryable via `GET /challenges/agent/{id}/summary`.

## 15. Audit Trail

Every state change produces an immutable audit log entry containing:
- Entity type and ID
- Action performed
- Acting agent
- Full details payload
- Cryptographic signature

Queryable via `GET /audit/{entity_type}/{entity_id}`.

## 16. Rate Limiting

Three layers of protection:

1. **Per-agent** — limits request volume per authenticated agent
2. **Per-IP** — limits unauthenticated request volume
3. **Global circuit breaker** — triggers under extreme load

Penalty escalation: repeated violations increase cooldown periods.

## 17. Schemas

All message formats are defined as JSON Schema in the `schemas/` directory:

- `agent.schema.json` — Agent identity and capabilities
- `intent.schema.json` — Intent declaration
- `offer.schema.json` — Offer/counteroffer
- `handoff.schema.json` — Handoff context
- `envelope.schema.json` — Signed message envelope

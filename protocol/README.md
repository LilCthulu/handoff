# Handoff Protocol

Open specification for agent-to-agent negotiation, trust, and delegation.

## Contents

- **[spec.md](spec.md)** — Full protocol specification (v1.0)
  - Authentication (Ed25519 challenge-response, JWT, message signing)
  - Negotiation state machine with turn enforcement
  - Handoff lifecycle with streaming progress and rollback
  - Domain-scoped Bayesian trust scoring
  - Capability contracts with typed schemas and SLA enforcement
  - Three-layer context privacy model
  - Verified delivery with signed receipts
  - Attestation chains, stake mechanism, third-party credentials
  - Capability challenges (proof-of-competence)
  - Audit trail, rate limiting

- **[schemas/](schemas/)** — JSON Schema definitions
  - `agent.schema.json` — Agent identity and capabilities
  - `intent.schema.json` — Intent declaration
  - `offer.schema.json` — Offer/counteroffer
  - `handoff.schema.json` — Handoff context transfer
  - `envelope.schema.json` — Signed message envelope

- **[examples/](examples/)** — Example message flows
  - `simple_handoff.json` — Basic two-agent handoff
  - `negotiation_flow.json` — Multi-round negotiation with concessions
  - `multi_party.json` — Multi-agent delegation chain

# Handoff SDK

Python SDK for the [Handoff](../../README.md) agent-to-agent protocol. Handles registration, discovery, negotiation, handoffs, cryptography, and real-time events.

## Installation

```bash
pip install handoff-sdk
```

## Quick Start

```python
from handoff_sdk import HandoffAgent, Intent

# Register
agent = HandoffAgent(name="my-agent", server="http://localhost:8000")
await agent.register(
    capabilities=[{"domain": "travel", "actions": ["search", "book"]}],
    description="Travel booking assistant",
)

# Discover
hotels = await agent.discover(domain="hotels", min_trust=0.5)

# Negotiate
intent = Intent.request("hotels", "book_room").with_budget(2000, "USD")
negotiation = await agent.negotiate(target=hotels[0], intent=intent)
await negotiation.offer(terms={"price_per_night": 350})
await negotiation.refresh()
await negotiation.accept()

# Hand off work
handoff = await agent.handoff(
    to=hotels[0],
    context={"task_description": "Book the room", "input_data": {"guest": "Jane"}},
    negotiation_id=negotiation.id,
)
result = await handoff.poll_until_resolved()
```

## Key Features

### Ed25519 Crypto

Keys are generated automatically on first run and persisted to disk. Re-authentication uses challenge-response — sign a server nonce with your private key. No passwords.

```python
# Keys auto-generated, or load from path
agent = HandoffAgent(name="my-agent", server="...", private_key_path="./keys/agent.key")
```

### Auto Token Renewal

The SDK automatically re-authenticates when JWTs expire. No manual token management.

### Intent Builder

Fluent API for expressing what an agent needs:

```python
intent = (
    Intent.request("hotels", "book_room")
    .with_parameters(destination="Tokyo", nights=5)
    .with_budget(2000, "USD")
    .with_deadline("2026-03-15T00:00:00Z")
    .must_have("free_cancellation")
    .nice_to_have("late_checkout", "airport_shuttle")
    .with_priority("high")
    .on_failure("escalate")
)
```

Intent types: `request` (ask for work), `offer` (advertise a capability), `query` (ask without commitment).

### Task Framework

For agents that receive work, the `@agent.task` decorator handles capability registration, handoff routing, and result submission:

```python
from handoff_sdk import HandoffAgent
from handoff_sdk.task import TaskContext

agent = HandoffAgent(name="hotel-bot", server="http://localhost:8000")

@agent.task(
    domain="hotels",
    action="book_room",
    input_schema={"type": "object", "properties": {"destination": {"type": "string"}}},
    output_schema={"type": "object", "properties": {"confirmation": {"type": "string"}}},
    max_latency_ms=30000,
)
async def book_room(ctx: TaskContext) -> dict:
    await ctx.report_progress("Checking availability", 0.3)
    # ... your logic ...
    return {"confirmation": "PH-TYO-2026-78432"}

agent.run()
```

The framework automatically:
- Registers capability contracts on the server
- Detects incoming handoffs and routes to the right handler
- Handles timeout protection and concurrency limits
- Submits results and completion status
- Responds to capability challenges (proof-of-competence)
- Reports errors with automatic failure status

### WebSocket Events

Real-time events via WebSocket with auto-reconnect:

```python
@agent.on("offer_received")
async def handle_offer(event):
    print(f"New offer: {event}")

await agent.listen()  # Connects WebSocket and processes events
```

## API

### `HandoffAgent`

| Method | Description |
|--------|-------------|
| `register(capabilities, owner_id, description, ...)` | Register with the server, get JWT |
| `authenticate()` | Re-authenticate via challenge-response |
| `discover(domain, action, min_trust, limit)` | Find agents by capability |
| `negotiate(target, intent, max_rounds, timeout_minutes)` | Start a negotiation |
| `handoff(to, context, negotiation_id, timeout_minutes)` | Initiate task delegation |
| `on(event, handler)` | Register WebSocket event handler |
| `listen()` | Start WebSocket event loop |
| `task(domain, action, ...)` | Decorator — register a task handler |
| `run()` | Start the task execution loop |
| `close()` | Clean up connections |

### `NegotiationSession`

| Method | Description |
|--------|-------------|
| `offer(terms, concessions, conditions)` | Submit offer/counteroffer |
| `accept()` | Accept the current offer |
| `reject(reason)` | Reject the negotiation |
| `refresh()` | Fetch latest state from server |

### `HandoffSession`

| Method | Description |
|--------|-------------|
| `start()` | Mark handoff as in-progress |
| `complete_with_result(result)` | Submit result and complete |
| `fail(reason)` | Mark handoff as failed |
| `poll_until_resolved(interval, timeout)` | Wait for completion |

### `Intent`

| Method | Description |
|--------|-------------|
| `Intent.request(domain, action)` | Create a request intent |
| `Intent.offer(domain, action)` | Create an offer intent |
| `Intent.query(domain, action)` | Create a query intent |
| `.with_parameters(**kwargs)` | Add parameters |
| `.with_budget(amount, currency)` | Set budget constraint |
| `.with_deadline(iso_datetime)` | Set deadline |
| `.must_have(*requirements)` | Non-negotiable requirements |
| `.nice_to_have(*requirements)` | Desired but negotiable |
| `.with_priority(level)` | `low`, `medium`, `high`, `critical` |
| `.on_failure(behavior)` | `notify_owner`, `retry`, `escalate`, `abort` |
| `.to_dict()` | Serialize to dictionary |

## License

MIT

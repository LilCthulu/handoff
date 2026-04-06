# Handoff SDK

Python SDK for the Handoff agent-to-agent protocol.

## Installation

```bash
pip install handoff-sdk
```

## Quick Start

```python
from handoff_sdk import HandoffAgent

agent = HandoffAgent(name="my-agent", server="https://handoff.example.com")
agent.register(capabilities=[{"domain": "travel", "actions": ["search", "book"]}])
agent.listen()
```

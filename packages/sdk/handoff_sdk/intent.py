"""Intent builder — fluent API for expressing what an agent needs.

Intents are the spark that starts every negotiation.
This builder makes them easy to construct and hard to get wrong.
"""

import copy
import uuid
from typing import Any, Self


class Intent:
    """Fluent builder for structured intents."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "type": "request",
            "domain": "",
            "action": "",
            "parameters": {},
            "constraints": {
                "must_have": [],
                "nice_to_have": [],
            },
            "priority": "medium",
            "fallback_behavior": "notify_owner",
        }

    # --- Factory methods ---

    @classmethod
    def request(
        cls,
        domain: str,
        action: str,
        parameters: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> "Intent":
        """Create a request intent — the most common type."""
        intent = cls()
        intent._data["type"] = "request"
        intent._data["domain"] = domain
        intent._data["action"] = action
        if parameters:
            intent._data["parameters"] = parameters
        if constraints:
            intent._data["constraints"].update(constraints)
        return intent

    @classmethod
    def offer(cls, domain: str, action: str, parameters: dict[str, Any] | None = None) -> "Intent":
        """Create an offer intent — proactively offering a capability."""
        intent = cls()
        intent._data["type"] = "offer"
        intent._data["domain"] = domain
        intent._data["action"] = action
        if parameters:
            intent._data["parameters"] = parameters
        return intent

    @classmethod
    def query(cls, domain: str, action: str, parameters: dict[str, Any] | None = None) -> "Intent":
        """Create a query intent — asking for information without commitment."""
        intent = cls()
        intent._data["type"] = "query"
        intent._data["domain"] = domain
        intent._data["action"] = action
        if parameters:
            intent._data["parameters"] = parameters
        return intent

    # --- Fluent setters ---

    def with_parameters(self, **kwargs: Any) -> Self:
        """Add parameters to the intent."""
        self._data["parameters"].update(kwargs)
        return self

    def with_budget(self, max_amount: float, currency: str = "USD") -> Self:
        """Set budget constraint."""
        self._data["constraints"]["budget_max"] = max_amount
        self._data["constraints"]["currency"] = currency
        return self

    def with_deadline(self, deadline: str) -> Self:
        """Set a deadline (ISO 8601 datetime string)."""
        self._data["constraints"]["deadline"] = deadline
        return self

    def must_have(self, *requirements: str) -> Self:
        """Add non-negotiable requirements."""
        self._data["constraints"]["must_have"].extend(requirements)
        return self

    def nice_to_have(self, *requirements: str) -> Self:
        """Add desired but negotiable requirements."""
        self._data["constraints"]["nice_to_have"].extend(requirements)
        return self

    def with_priority(self, priority: str) -> Self:
        """Set priority: low, medium, high, critical."""
        self._data["priority"] = priority
        return self

    def on_failure(self, behavior: str) -> Self:
        """Set fallback behavior: notify_owner, retry, escalate, abort."""
        self._data["fallback_behavior"] = behavior
        return self

    # --- Output ---

    def to_dict(self) -> dict[str, Any]:
        """Serialize the intent to a deep-copied dictionary."""
        return copy.deepcopy(self._data)

    def __repr__(self) -> str:
        return f"Intent({self._data['type']} {self._data['domain']}/{self._data['action']})"

"""Intent parsing and validation — translating desire into structure.

An intent is the spark that starts every negotiation.
It must be clear, valid, and actionable. This module ensures it is.
"""

from typing import Any

import structlog

from app.schemas.intent import Intent, IntentConstraints

logger = structlog.get_logger()


class IntentValidationError(Exception):
    """Raised when an intent fails validation."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


VALID_TYPES = {"request", "offer", "inform", "query"}
VALID_PRIORITIES = {"low", "medium", "high", "critical"}
VALID_FALLBACKS = {"notify_owner", "retry", "escalate", "abort"}


def parse_intent(raw: dict[str, Any]) -> Intent:
    """Parse a raw dictionary into a validated Intent.

    Args:
        raw: Dictionary representing an intent, possibly from JSON.

    Returns:
        Validated Intent object.

    Raises:
        IntentValidationError: If the intent is malformed.
    """
    try:
        # If wrapped in {"intent": {...}}, unwrap
        if "intent" in raw and isinstance(raw["intent"], dict):
            raw = raw["intent"]

        intent = Intent(**raw)
        validate_intent(intent)
        return intent

    except IntentValidationError:
        raise
    except Exception as e:
        raise IntentValidationError(f"Failed to parse intent: {e}")


def validate_intent(intent: Intent) -> None:
    """Run semantic validation on a parsed intent.

    Pydantic handles structural validation. This handles business rules.

    Raises:
        IntentValidationError: If business rules are violated.
    """
    if not intent.domain.strip():
        raise IntentValidationError("Intent domain cannot be empty")

    if not intent.action.strip():
        raise IntentValidationError("Intent action cannot be empty")

    # Budget sanity check
    constraints = intent.constraints
    if constraints.budget_max is not None and constraints.budget_max < 0:
        raise IntentValidationError("Budget cannot be negative")

    # Must-have items should not also be in nice-to-have
    must = set(constraints.must_have)
    nice = set(constraints.nice_to_have)
    overlap = must & nice
    if overlap:
        raise IntentValidationError(
            f"Items cannot be both must_have and nice_to_have: {overlap}"
        )

    logger.debug(
        "intent_validated",
        domain=intent.domain,
        action=intent.action,
        priority=intent.priority,
    )


def match_intent_to_capabilities(
    intent: Intent,
    agent_capabilities: list[dict[str, Any]],
) -> bool:
    """Check whether an agent's capabilities can fulfill an intent.

    Args:
        intent: The validated intent.
        agent_capabilities: List of capability declarations from the agent.

    Returns:
        True if at least one capability matches the intent's domain and action.
    """
    for cap in agent_capabilities:
        if cap.get("domain") == intent.domain and intent.action in cap.get("actions", []):
            return True
    return False


def check_authority_for_intent(
    intent: Intent,
    max_authority: dict[str, Any],
) -> list[str]:
    """Check if an intent's constraints fall within an agent's authority.

    Returns:
        List of violation descriptions (empty if all checks pass).
    """
    violations: list[str] = []

    # Domain check
    allowed_domains = max_authority.get("allowed_domains")
    if allowed_domains and intent.domain not in allowed_domains:
        violations.append(f"Domain '{intent.domain}' not in allowed domains: {allowed_domains}")

    # Budget check
    budget = intent.constraints.budget_max
    max_spend = max_authority.get("max_spend")
    if budget is not None and max_spend is not None and budget > max_spend:
        violations.append(f"Budget {budget} exceeds max_spend {max_spend}")

    return violations

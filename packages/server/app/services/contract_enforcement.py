"""Contract enforcement — typed validation for handoff inputs and outputs.

When agents agree on a capability contract, the server enforces it.
Input validated before the handoff is delivered. Output validated before
the handoff is marked as completed. SLA violations tracked and fed into
trust scoring.

This is the difference between "agents promise to behave" and
"the protocol forces correct behavior."
"""

import time
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.capability import CapabilityContract
from app.models.handoff import Handoff

logger = structlog.get_logger()


class ContractViolation(Exception):
    """Raised when handoff data violates a capability contract."""

    def __init__(self, detail: str, violations: list[str] | None = None):
        self.detail = detail
        self.violations = violations or []
        super().__init__(detail)


async def find_contract(
    db: AsyncSession,
    agent_id: Any,
    domain: str,
    action: str,
) -> CapabilityContract | None:
    """Find the active capability contract for an agent+domain+action."""
    result = await db.execute(
        select(CapabilityContract).where(
            CapabilityContract.agent_id == agent_id,
            CapabilityContract.domain == domain,
            CapabilityContract.action == action,
            CapabilityContract.is_active == True,
        )
    )
    return result.scalar_one_or_none()


def validate_against_schema(data: dict, schema: dict, label: str = "data") -> list[str]:
    """Validate data against a JSON Schema. Returns list of violation strings.

    Uses jsonschema if available, falls back to structural validation.
    """
    if not schema:
        return []  # No schema = no enforcement

    try:
        import jsonschema
        validator = jsonschema.Draft7Validator(schema)
        errors = list(validator.iter_errors(data))
        return [f"{label}: {e.message}" for e in errors]
    except ImportError:
        return _structural_validate(data, schema, label)


def _structural_validate(data: dict, schema: dict, label: str) -> list[str]:
    """Lightweight structural validation without jsonschema."""
    violations = []

    required = schema.get("required", [])
    for key in required:
        if key not in data:
            violations.append(f"{label}: missing required field '{key}'")

    properties = schema.get("properties", {})
    type_map = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    for key, prop_schema in properties.items():
        if key in data:
            expected_type = prop_schema.get("type")
            if expected_type and expected_type in type_map:
                if not isinstance(data[key], type_map[expected_type]):
                    violations.append(
                        f"{label}: field '{key}' expected {expected_type}, "
                        f"got {type(data[key]).__name__}"
                    )

    return violations


async def validate_handoff_input(
    db: AsyncSession,
    to_agent_id: Any,
    context: dict[str, Any],
) -> tuple[CapabilityContract | None, list[str]]:
    """Validate handoff context against the receiving agent's capability contract.

    Returns (contract, violations). If no contract found for the domain+action,
    returns (None, []) — enforcement is opt-in per agent.
    """
    domain = context.get("domain", "")
    action = context.get("action", "")

    if not domain or not action:
        return None, []  # No domain+action in context = can't enforce

    contract = await find_contract(db, to_agent_id, domain, action)
    if not contract:
        return None, []  # No contract registered = pass through

    input_data = context.get("input", context)
    violations = validate_against_schema(input_data, contract.input_schema, "input")

    if violations:
        logger.warning(
            "contract_input_violation",
            agent_id=str(to_agent_id),
            domain=domain,
            action=action,
            violations=violations,
        )

    return contract, violations


async def validate_handoff_result(
    db: AsyncSession,
    handoff: Handoff,
    result: dict[str, Any],
) -> tuple[CapabilityContract | None, list[str], dict[str, Any] | None]:
    """Validate handoff result against the capability contract's output schema.

    Also checks SLA compliance (latency).

    Returns (contract, violations, sla_report).
    """
    context = handoff.context or {}
    domain = context.get("domain", "")
    action = context.get("action", "")

    if not domain or not action:
        return None, [], None

    contract = await find_contract(db, handoff.to_agent_id, domain, action)
    if not contract:
        return None, [], None

    # Schema validation
    violations = validate_against_schema(result, contract.output_schema, "output")

    # SLA check — latency
    sla_report: dict[str, Any] = {"sla_met": True, "violations": []}

    if contract.max_latency_ms and handoff.created_at:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        elapsed_ms = (now - handoff.created_at).total_seconds() * 1000

        sla_report["elapsed_ms"] = round(elapsed_ms, 2)
        sla_report["max_latency_ms"] = contract.max_latency_ms

        if elapsed_ms > contract.max_latency_ms:
            sla_report["sla_met"] = False
            sla_report["violations"].append(
                f"Latency {elapsed_ms:.0f}ms exceeds SLA limit of {contract.max_latency_ms}ms"
            )
            logger.warning(
                "sla_latency_violation",
                handoff_id=str(handoff.id),
                elapsed_ms=round(elapsed_ms, 2),
                limit_ms=contract.max_latency_ms,
            )

    if violations:
        logger.warning(
            "contract_output_violation",
            handoff_id=str(handoff.id),
            domain=domain,
            action=action,
            violations=violations,
        )

    return contract, violations, sla_report

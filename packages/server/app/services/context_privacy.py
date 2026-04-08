"""Context privacy — three-layer data model for handoff contexts.

Every handoff context has three layers:

1. **Public** — visible during discovery/negotiation.
   Domain, action, constraints, non-sensitive metadata.

2. **Committed** — encrypted to the receiving agent.
   Shared only after negotiation agreement. Contains the actual
   task input data. Encrypted with the receiver's public key.

3. **Sealed** — PII that never leaves the server.
   The agent receives an opaque reference token (e.g., "sealed:usr_abc123").
   When the agent needs the actual value, it calls a server endpoint
   that resolves the token — but the raw PII is never in the agent's memory.

Data minimization: before delivering context to the receiving agent,
strip any fields not declared in the capability contract's input_schema.
The agent gets only what it needs, nothing more.
"""

import base64
import hashlib
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

import structlog

logger = structlog.get_logger()

# In-memory sealed reference store (production: use encrypted DB or KMS)
_sealed_store: dict[str, dict[str, Any]] = {}


# --- Sealed References ---

def seal_value(value: Any, context: str = "", ttl_minutes: int = 60, sealed_by: str | None = None) -> str:
    """Seal a PII value and return an opaque reference token.

    The value is stored server-side and never sent to agents.
    The token can be resolved via the /api/v1/context/resolve endpoint.

    Args:
        value: The sensitive value to seal (string, dict, etc.)
        context: Optional context label (e.g., "user_email", "phone")
        ttl_minutes: How long the sealed reference is valid

    Returns:
        Opaque reference token like "sealed:ref_abc123def456"
    """
    token = f"ref_{secrets.token_urlsafe(24)}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)

    _sealed_store[token] = {
        "value": value,
        "context": context,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at.isoformat(),
        "access_count": 0,
        "max_accesses": 10,  # limit how many times it can be resolved
        "sealed_by": sealed_by,
    }

    logger.info("value_sealed", context=context, token=token[:12])
    return f"sealed:{token}"


def resolve_sealed(token: str) -> tuple[Any, bool]:
    """Resolve a sealed reference token to its value.

    Returns (value, success). Checks expiration and access limits.
    """
    if token.startswith("sealed:"):
        token = token[7:]

    entry = _sealed_store.get(token)
    if not entry:
        return None, False

    # Check expiration
    expires_at = datetime.fromisoformat(entry["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        del _sealed_store[token]
        logger.warning("sealed_ref_expired", token=token[:12])
        return None, False

    # Check access limit
    if entry["access_count"] >= entry["max_accesses"]:
        logger.warning("sealed_ref_max_accesses", token=token[:12])
        return None, False

    entry["access_count"] += 1
    return entry["value"], True


def revoke_sealed(token: str, caller_id: str | None = None) -> bool:
    """Revoke a sealed reference — PII deleted immediately.

    If caller_id is provided, only the agent who sealed the value can revoke it.
    """
    if token.startswith("sealed:"):
        token = token[7:]
    entry = _sealed_store.get(token)
    if not entry:
        return False
    # Ownership check: if the entry was sealed by someone, only they can revoke
    if caller_id and entry.get("sealed_by") and entry["sealed_by"] != caller_id:
        return False
    del _sealed_store[token]
    return True


# --- Context Layer Processing ---

def split_context_layers(
    context: dict[str, Any],
    sealed_fields: list[str] | None = None,
    ttl_minutes: int = 60,
) -> dict[str, Any]:
    """Split a raw context dict into three privacy layers.

    Args:
        context: The full handoff context
        sealed_fields: List of dot-separated field paths to seal
            (e.g., ["input.user_email", "input.phone"])
        ttl_minutes: TTL for sealed references

    Returns:
        Context dict with sealed values replaced by reference tokens
        and a _privacy metadata block.
    """
    sealed_fields = sealed_fields or []
    processed = dict(context)
    sealed_refs: list[dict[str, str]] = []

    for field_path in sealed_fields:
        parts = field_path.split(".")
        obj = processed
        for part in parts[:-1]:
            if isinstance(obj, dict) and part in obj:
                obj = obj[part]
            else:
                break
        else:
            final_key = parts[-1]
            if isinstance(obj, dict) and final_key in obj:
                original_value = obj[final_key]
                token = seal_value(original_value, context=field_path, ttl_minutes=ttl_minutes)
                obj[final_key] = token
                sealed_refs.append({"field": field_path, "token": token})

    if sealed_refs:
        processed["_privacy"] = {
            "sealed_refs": sealed_refs,
            "sealed_at": datetime.now(timezone.utc).isoformat(),
            "ttl_minutes": ttl_minutes,
        }

    return processed


def minimize_context(
    context: dict[str, Any],
    input_schema: dict[str, Any],
) -> dict[str, Any]:
    """Strip context fields not declared in the contract's input_schema.

    Data minimization: the receiving agent gets only what the contract
    says it needs. Everything else is removed before delivery.

    Preserves: _privacy, _progress, _resumed_from, _checkpoint_state
    metadata keys and top-level keys like domain, action.
    """
    if not input_schema or "properties" not in input_schema:
        return context

    allowed_fields = set(input_schema.get("properties", {}).keys())

    # Always preserve system metadata and top-level routing fields
    preserve_keys = {
        "domain", "action", "constraints", "metadata",
        "_privacy", "_progress", "_resumed_from", "_checkpoint_state",
    }

    minimized = {}
    input_data = context.get("input", {})

    for key, value in context.items():
        if key in preserve_keys:
            minimized[key] = value
        elif key == "input" and isinstance(value, dict):
            # Minimize the input block
            minimized["input"] = {
                k: v for k, v in value.items() if k in allowed_fields
            }
        else:
            # Preserve non-input top-level keys
            minimized[key] = value

    # If there's no "input" key but the contract has properties,
    # filter the top-level context directly
    if "input" not in context and allowed_fields:
        result = {}
        for key, value in context.items():
            if key in preserve_keys or key in allowed_fields:
                result[key] = value
        return result

    return minimized


def generate_pseudonym(identifier: str, salt: str = "") -> str:
    """Generate a deterministic pseudonymous identifier.

    Same input always produces the same pseudonym within the same salt,
    allowing correlation without exposing the real identifier.
    """
    data = f"{identifier}:{salt}".encode()
    digest = hashlib.sha256(data).hexdigest()[:16]
    return f"pseudo:{digest}"

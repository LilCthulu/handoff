"""Key management, signing, and verification for the SDK.

Agents need their own crypto — key generation, storage, signing,
and verification. This module mirrors the server's crypto.py but
is self-contained for the SDK distribution.
"""

import base64
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import nacl.signing


def generate_keypair() -> tuple[str, str]:
    """Generate an Ed25519 key pair.

    Returns:
        Tuple of (private_key_b64, public_key_b64).
    """
    signing_key = nacl.signing.SigningKey.generate()
    private_b64 = base64.b64encode(signing_key.encode()).decode()
    public_b64 = base64.b64encode(signing_key.verify_key.encode()).decode()
    return private_b64, public_b64


def load_or_generate_keys(key_path: str | Path | None = None) -> tuple[str, str]:
    """Load keys from disk or generate and save new ones.

    Args:
        key_path: Path to the private key file. If None, uses ~/.handoff/key.pem.

    Returns:
        Tuple of (private_key_b64, public_key_b64).
    """
    if key_path is None:
        key_path = Path.home() / ".handoff" / "key.pem"
    else:
        key_path = Path(key_path)

    if key_path.exists():
        private_b64 = key_path.read_text().strip()
        private_raw = base64.b64decode(private_b64)
        signing_key = nacl.signing.SigningKey(private_raw)
        public_b64 = base64.b64encode(signing_key.verify_key.encode()).decode()
        return private_b64, public_b64

    # Generate new keys
    private_b64, public_b64 = generate_keypair()

    # Save private key
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(private_b64)
    os.chmod(str(key_path), 0o600)

    return private_b64, public_b64


def public_key_fingerprint(public_key_b64: str) -> str:
    """Compute SHA-256 fingerprint of a public key."""
    raw = base64.b64decode(public_key_b64)
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def canonical_json(obj: dict) -> bytes:
    """Canonical JSON for signing: sorted keys, no whitespace, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def hash_payload(payload: dict) -> str:
    """SHA-256 hash of a canonical JSON payload."""
    return f"sha256:{hashlib.sha256(canonical_json(payload)).hexdigest()}"


def sign_message(
    payload: dict,
    private_key_b64: str,
    sender_agent_id: str,
    sender_public_key_b64: str,
    recipient_agent_id: str | None = None,
) -> dict:
    """Create a signed envelope wrapping a payload."""
    payload_hash = hash_payload(payload)
    fingerprint = public_key_fingerprint(sender_public_key_b64)

    envelope = {
        "version": "1.0",
        "message_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sender": {
            "agent_id": sender_agent_id,
            "public_key_fingerprint": fingerprint,
        },
        "payload_hash": payload_hash,
    }
    if recipient_agent_id:
        envelope["recipient"] = {"agent_id": recipient_agent_id}

    envelope_bytes = canonical_json(envelope)
    private_raw = base64.b64decode(private_key_b64)
    signing_key = nacl.signing.SigningKey(private_raw)
    signed = signing_key.sign(envelope_bytes)
    envelope["signature"] = base64.b64encode(signed.signature).decode()

    return {"envelope": envelope, "payload": payload}


def verify_signature(signed_message: dict, public_key_b64: str) -> bool:
    """Verify an Ed25519 signature on a signed envelope."""
    try:
        envelope = signed_message["envelope"].copy()
        signature_b64 = envelope.pop("signature")
        payload = signed_message["payload"]

        expected_hash = hash_payload(payload)
        if envelope.get("payload_hash") != expected_hash:
            return False

        envelope_bytes = canonical_json(envelope)
        signature = base64.b64decode(signature_b64)
        public_raw = base64.b64decode(public_key_b64)
        verify_key = nacl.signing.VerifyKey(public_raw)
        verify_key.verify(envelope_bytes, signature)
        return True
    except Exception:
        return False

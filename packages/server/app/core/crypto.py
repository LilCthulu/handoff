"""Ed25519 key generation, message signing, and verification.

Every message in the Handoff network is signed. Every signature is verified.
Trust is not assumed — it is proven, cryptographically, every single time.
"""

import base64
import hashlib
import json
import uuid
from datetime import datetime, timezone

import nacl.encoding
import nacl.signing
import structlog

logger = structlog.get_logger()


def generate_keypair() -> tuple[str, str]:
    """Generate an Ed25519 key pair.

    Returns:
        Tuple of (private_key_b64, public_key_b64).
    """
    signing_key = nacl.signing.SigningKey.generate()
    private_b64 = base64.b64encode(signing_key.encode()).decode()
    public_b64 = base64.b64encode(signing_key.verify_key.encode()).decode()
    return private_b64, public_b64


def public_key_fingerprint(public_key_b64: str) -> str:
    """Compute the SHA-256 fingerprint of a public key.

    Args:
        public_key_b64: Base64-encoded Ed25519 public key.

    Returns:
        Fingerprint in the format 'sha256:<hex>'.
    """
    raw = base64.b64decode(public_key_b64)
    digest = hashlib.sha256(raw).hexdigest()
    return f"sha256:{digest}"


def canonical_json(obj: dict) -> bytes:
    """Produce canonical JSON bytes for hashing and signing.

    Canonical form: sorted keys, no whitespace, UTF-8 encoded.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def hash_payload(payload: dict) -> str:
    """Compute SHA-256 hash of a canonical JSON payload.

    Returns:
        Hash in the format 'sha256:<hex>'.
    """
    data = canonical_json(payload)
    digest = hashlib.sha256(data).hexdigest()
    return f"sha256:{digest}"


def sign_message(payload: dict, private_key_b64: str, sender_agent_id: str, sender_public_key_b64: str, recipient_agent_id: str | None = None) -> dict:
    """Create a signed envelope wrapping a payload.

    Args:
        payload: The message content to sign.
        private_key_b64: Sender's base64-encoded Ed25519 private key.
        sender_agent_id: UUID string of the sender.
        sender_public_key_b64: Sender's base64-encoded public key.
        recipient_agent_id: Optional UUID string of the recipient.

    Returns:
        Complete signed message with envelope and payload.
    """
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

    # Sign the canonical envelope
    envelope_bytes = canonical_json(envelope)
    private_key_raw = base64.b64decode(private_key_b64)
    signing_key = nacl.signing.SigningKey(private_key_raw)
    signed = signing_key.sign(envelope_bytes)
    signature_b64 = base64.b64encode(signed.signature).decode()

    envelope["signature"] = signature_b64

    return {"envelope": envelope, "payload": payload}


def verify_signature(signed_message: dict, public_key_b64: str) -> bool:
    """Verify the Ed25519 signature on a signed envelope.

    Args:
        signed_message: The full signed message (envelope + payload).
        public_key_b64: Sender's base64-encoded Ed25519 public key.

    Returns:
        True if signature is valid, False otherwise.
    """
    try:
        envelope = signed_message["envelope"].copy()
        signature_b64 = envelope.pop("signature")
        payload = signed_message["payload"]

        # Verify payload integrity
        expected_hash = hash_payload(payload)
        if envelope.get("payload_hash") != expected_hash:
            logger.warning("payload_hash_mismatch", expected=expected_hash, got=envelope.get("payload_hash"))
            return False

        # Verify signature
        envelope_bytes = canonical_json(envelope)
        signature = base64.b64decode(signature_b64)
        public_key_raw = base64.b64decode(public_key_b64)
        verify_key = nacl.signing.VerifyKey(public_key_raw)
        verify_key.verify(envelope_bytes, signature)
        return True

    except Exception:
        logger.exception("signature_verification_failed")
        return False


def sign_audit_entry(details: dict, private_key_b64: str) -> str:
    """Sign an audit log entry's details for tamper detection.

    Returns:
        Base64-encoded Ed25519 signature.
    """
    data = canonical_json(details)
    private_key_raw = base64.b64decode(private_key_b64)
    signing_key = nacl.signing.SigningKey(private_key_raw)
    signed = signing_key.sign(data)
    return base64.b64encode(signed.signature).decode()

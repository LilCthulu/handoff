"""Tests for the Ed25519 cryptographic module."""

import base64
import uuid

from app.core.crypto import (
    canonical_json,
    generate_keypair,
    hash_payload,
    public_key_fingerprint,
    sign_audit_entry,
    sign_message,
    verify_signature,
)


class TestKeyGeneration:
    def test_generate_keypair_returns_valid_base64(self):
        private, public = generate_keypair()
        # Both should be valid base64
        raw_private = base64.b64decode(private)
        raw_public = base64.b64decode(public)
        assert len(raw_private) == 32  # Ed25519 private key
        assert len(raw_public) == 32   # Ed25519 public key

    def test_generate_keypair_unique(self):
        pair1 = generate_keypair()
        pair2 = generate_keypair()
        assert pair1[0] != pair2[0]
        assert pair1[1] != pair2[1]


class TestFingerprint:
    def test_fingerprint_format(self):
        _, public = generate_keypair()
        fp = public_key_fingerprint(public)
        assert fp.startswith("sha256:")
        assert len(fp) == 7 + 64  # "sha256:" + 64 hex chars

    def test_fingerprint_deterministic(self):
        _, public = generate_keypair()
        assert public_key_fingerprint(public) == public_key_fingerprint(public)

    def test_different_keys_different_fingerprints(self):
        _, pub1 = generate_keypair()
        _, pub2 = generate_keypair()
        assert public_key_fingerprint(pub1) != public_key_fingerprint(pub2)


class TestCanonicalJson:
    def test_sorted_keys(self):
        result = canonical_json({"b": 1, "a": 2})
        assert result == b'{"a":2,"b":1}'

    def test_no_whitespace(self):
        result = canonical_json({"key": "value", "num": 42})
        assert b" " not in result
        assert b"\n" not in result

    def test_deterministic(self):
        obj = {"z": [1, 2], "a": {"nested": True}}
        assert canonical_json(obj) == canonical_json(obj)


class TestHashPayload:
    def test_hash_format(self):
        h = hash_payload({"test": "data"})
        assert h.startswith("sha256:")

    def test_hash_deterministic(self):
        payload = {"key": "value"}
        assert hash_payload(payload) == hash_payload(payload)

    def test_different_payloads_different_hashes(self):
        assert hash_payload({"a": 1}) != hash_payload({"a": 2})


class TestSignAndVerify:
    def test_sign_and_verify_roundtrip(self):
        private, public = generate_keypair()
        agent_id = str(uuid.uuid4())
        payload = {"action": "book_hotel", "price": 500}

        signed = sign_message(payload, private, agent_id, public)

        assert "envelope" in signed
        assert "payload" in signed
        assert signed["payload"] == payload
        assert signed["envelope"]["sender"]["agent_id"] == agent_id
        assert "signature" in signed["envelope"]
        assert signed["envelope"]["version"] == "1.0"
        assert "message_id" in signed["envelope"]
        assert "timestamp" in signed["envelope"]

        assert verify_signature(signed, public) is True

    def test_sign_with_recipient(self):
        private, public = generate_keypair()
        agent_id = str(uuid.uuid4())
        recipient_id = str(uuid.uuid4())
        payload = {"test": True}

        signed = sign_message(payload, private, agent_id, public, recipient_id)
        assert signed["envelope"]["recipient"]["agent_id"] == recipient_id
        assert verify_signature(signed, public) is True

    def test_tampered_payload_fails_verification(self):
        private, public = generate_keypair()
        agent_id = str(uuid.uuid4())
        signed = sign_message({"price": 500}, private, agent_id, public)

        # Tamper with payload
        signed["payload"]["price"] = 100
        assert verify_signature(signed, public) is False

    def test_wrong_key_fails_verification(self):
        private1, public1 = generate_keypair()
        _, public2 = generate_keypair()
        agent_id = str(uuid.uuid4())

        signed = sign_message({"test": True}, private1, agent_id, public1)
        assert verify_signature(signed, public2) is False

    def test_tampered_envelope_fails_verification(self):
        private, public = generate_keypair()
        agent_id = str(uuid.uuid4())
        signed = sign_message({"test": True}, private, agent_id, public)

        # Tamper with envelope timestamp
        signed["envelope"]["timestamp"] = "2020-01-01T00:00:00Z"
        assert verify_signature(signed, public) is False


class TestSignAuditEntry:
    def test_sign_audit_produces_base64(self):
        private, _ = generate_keypair()
        details = {"action": "agent_registered", "agent_id": str(uuid.uuid4())}
        sig = sign_audit_entry(details, private)
        # Should be valid base64
        raw = base64.b64decode(sig)
        assert len(raw) == 64  # Ed25519 signature is 64 bytes

    def test_sign_audit_deterministic_for_same_input(self):
        private, _ = generate_keypair()
        details = {"action": "test"}
        sig1 = sign_audit_entry(details, private)
        sig2 = sign_audit_entry(details, private)
        assert sig1 == sig2

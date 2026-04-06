"""Tests for SDK crypto module."""

import base64
import os
import tempfile
import uuid
from pathlib import Path

from handoff_sdk.crypto import (
    canonical_json,
    generate_keypair,
    hash_payload,
    load_or_generate_keys,
    public_key_fingerprint,
    sign_message,
    verify_signature,
)


class TestKeyGeneration:
    def test_generates_valid_keys(self):
        private, public = generate_keypair()
        assert len(base64.b64decode(private)) == 32
        assert len(base64.b64decode(public)) == 32

    def test_unique_each_time(self):
        p1 = generate_keypair()
        p2 = generate_keypair()
        assert p1[0] != p2[0]


class TestLoadOrGenerateKeys:
    def test_generates_and_saves(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = Path(tmpdir) / "test_key.pem"
            private, public = load_or_generate_keys(key_path)
            assert key_path.exists()
            assert key_path.read_text().strip() == private

    def test_loads_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = Path(tmpdir) / "test_key.pem"
            private1, public1 = load_or_generate_keys(key_path)
            private2, public2 = load_or_generate_keys(key_path)
            assert private1 == private2
            assert public1 == public2


class TestSignVerify:
    def test_roundtrip(self):
        private, public = generate_keypair()
        agent_id = str(uuid.uuid4())
        payload = {"action": "test", "value": 42}

        signed = sign_message(payload, private, agent_id, public)
        assert verify_signature(signed, public) is True

    def test_tampered_payload_fails(self):
        private, public = generate_keypair()
        signed = sign_message({"x": 1}, private, "agent-1", public)
        signed["payload"]["x"] = 999
        assert verify_signature(signed, public) is False

    def test_wrong_key_fails(self):
        priv1, pub1 = generate_keypair()
        _, pub2 = generate_keypair()
        signed = sign_message({"x": 1}, priv1, "agent-1", pub1)
        assert verify_signature(signed, pub2) is False


class TestCrossCompatibility:
    """Verify SDK crypto matches server crypto output format."""

    def test_fingerprint_format(self):
        _, public = generate_keypair()
        fp = public_key_fingerprint(public)
        assert fp.startswith("sha256:")
        assert len(fp) == 71  # sha256: + 64 hex

    def test_hash_format(self):
        h = hash_payload({"test": True})
        assert h.startswith("sha256:")

    def test_canonical_json_sorted(self):
        result = canonical_json({"z": 1, "a": 2})
        assert result == b'{"a":2,"z":1}'

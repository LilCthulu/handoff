"""Integration tests: Third-party credentials API."""

import base64
import json
import uuid

import nacl.signing
import pytest


def _create_signed_credential(agent_id: str, domain: str = "payments"):
    """Generate a valid Ed25519-signed credential for testing."""
    signing_key = nacl.signing.SigningKey.generate()
    verify_key = signing_key.verify_key
    fingerprint = verify_key.encode(encoder=nacl.encoding.HexEncoder).decode()

    claims = {"level": "gold", "auditor": "test-corp"}
    canonical = json.dumps(claims, sort_keys=True, separators=(",", ":"))
    signed = signing_key.sign(canonical.encode())
    signature = base64.b64encode(signed.signature).decode()

    return {
        "subject_id": agent_id,
        "issuer_id": "issuer-001",
        "issuer_name": "TestCorp Auditor",
        "issuer_key_fingerprint": fingerprint,
        "credential_type": "security_audit",
        "domain": domain,
        "claims": claims,
        "signature": signature,
        "weight": 2.0,
    }


class TestCredentials:
    """POST/GET /credentials"""

    async def test_submit_valid_credential(self, client, agent_factory):
        agent = await agent_factory.create()
        payload = _create_signed_credential(agent["id"])

        resp = await client.post(
            "/api/v1/credentials",
            json=payload,
            headers=agent["headers"],
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["verified"] is True
        assert data["domain"] == "payments"
        assert data["credential_type"] == "security_audit"

    async def test_submit_invalid_signature(self, client, agent_factory):
        agent = await agent_factory.create()
        payload = _create_signed_credential(agent["id"])
        payload["signature"] = base64.b64encode(b"bad-sig" * 10).decode()

        resp = await client.post(
            "/api/v1/credentials",
            json=payload,
            headers=agent["headers"],
        )
        assert resp.status_code == 400
        assert "invalid" in resp.json()["detail"].lower()

    async def test_submit_invalid_credential_type(self, client, agent_factory):
        agent = await agent_factory.create()
        payload = _create_signed_credential(agent["id"])
        payload["credential_type"] = "fake_type"

        resp = await client.post(
            "/api/v1/credentials",
            json=payload,
            headers=agent["headers"],
        )
        assert resp.status_code == 400

    async def test_submit_for_nonexistent_agent(self, client, agent_factory):
        agent = await agent_factory.create()
        payload = _create_signed_credential(str(uuid.uuid4()))

        resp = await client.post(
            "/api/v1/credentials",
            json=payload,
            headers=agent["headers"],
        )
        assert resp.status_code == 404

    async def test_get_agent_credentials(self, client, agent_factory):
        agent = await agent_factory.create()
        payload = _create_signed_credential(agent["id"])
        await client.post("/api/v1/credentials", json=payload, headers=agent["headers"])

        resp = await client.get(
            f"/api/v1/credentials/agent/{agent['id']}",
            headers=agent["headers"],
        )
        assert resp.status_code == 200
        creds = resp.json()
        assert len(creds) >= 1

    async def test_get_agent_credential_summary(self, client, agent_factory):
        agent = await agent_factory.create()
        payload = _create_signed_credential(agent["id"])
        await client.post("/api/v1/credentials", json=payload, headers=agent["headers"])

        resp = await client.get(
            f"/api/v1/credentials/agent/{agent['id']}/summary",
            headers=agent["headers"],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_credentials"] >= 1
        assert "by_type" in data
        assert "by_domain" in data

    async def test_revoke_credential(self, client, agent_factory):
        agent = await agent_factory.create()
        payload = _create_signed_credential(agent["id"])
        resp = await client.post("/api/v1/credentials", json=payload, headers=agent["headers"])
        cred_id = resp.json()["id"]

        resp = await client.post(
            f"/api/v1/credentials/{cred_id}/revoke",
            json={"reason": "Key compromised"},
            headers=agent["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["revoked"] is True

    async def test_revoked_credential_excluded_from_valid_list(self, client, agent_factory):
        agent = await agent_factory.create()
        payload = _create_signed_credential(agent["id"])
        resp = await client.post("/api/v1/credentials", json=payload, headers=agent["headers"])
        cred_id = resp.json()["id"]

        # Revoke it
        await client.post(
            f"/api/v1/credentials/{cred_id}/revoke",
            json={"reason": "test"},
            headers=agent["headers"],
        )

        # valid_only=True (default) should exclude it
        resp = await client.get(
            f"/api/v1/credentials/agent/{agent['id']}",
            headers=agent["headers"],
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 0

    async def test_get_single_credential(self, client, agent_factory):
        agent = await agent_factory.create()
        payload = _create_signed_credential(agent["id"])
        resp = await client.post("/api/v1/credentials", json=payload, headers=agent["headers"])
        cred_id = resp.json()["id"]

        resp = await client.get(
            f"/api/v1/credentials/{cred_id}",
            headers=agent["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == cred_id

    async def test_reverify_credential(self, client, agent_factory):
        agent = await agent_factory.create()
        payload = _create_signed_credential(agent["id"])
        resp = await client.post("/api/v1/credentials", json=payload, headers=agent["headers"])
        cred_id = resp.json()["id"]

        resp = await client.post(
            f"/api/v1/credentials/{cred_id}/verify",
            headers=agent["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["verified"] is True

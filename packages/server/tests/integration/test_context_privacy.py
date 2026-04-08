"""Integration tests: Context privacy — seal, resolve, revoke, pseudonyms."""

import uuid

import pytest


class TestSealedReferences:
    """PII sealing and resolution."""

    async def test_seal_and_resolve(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()

        # Delegator seals a value
        resp = await client.post("/api/v1/context/seal", json={
            "value": "user@example.com",
            "context": "user_email",
            "ttl_minutes": 30,
        }, headers=delegator["headers"])
        assert resp.status_code == 200
        token = resp.json()["token"]
        assert token.startswith("sealed:")

        # Create a handoff so receiver can resolve
        resp = await client.post("/api/v1/handoffs", json={
            "to_agent_id": receiver["id"],
            "context": {"domain": "test", "email_ref": token},
        }, headers=delegator["headers"])
        handoff_id = resp.json()["id"]

        # Receiver resolves
        resp = await client.post("/api/v1/context/resolve", json={
            "token": token,
            "handoff_id": handoff_id,
        }, headers=receiver["headers"])
        assert resp.status_code == 200
        assert resp.json()["value"] == "user@example.com"

    async def test_only_receiver_can_resolve(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        outsider = await agent_factory.create(name="outsider")

        resp = await client.post("/api/v1/context/seal", json={
            "value": "sensitive-data",
            "context": "pii",
        }, headers=delegator["headers"])
        token = resp.json()["token"]

        resp = await client.post("/api/v1/handoffs", json={
            "to_agent_id": receiver["id"],
            "context": {"domain": "test"},
        }, headers=delegator["headers"])
        handoff_id = resp.json()["id"]

        # Outsider cannot resolve
        resp = await client.post("/api/v1/context/resolve", json={
            "token": token,
            "handoff_id": handoff_id,
        }, headers=outsider["headers"])
        assert resp.status_code == 403

    async def test_revoke_sealed_reference(self, client, agent_factory):
        agent = await agent_factory.create()

        resp = await client.post("/api/v1/context/seal", json={
            "value": "to-be-deleted",
            "context": "temp",
        }, headers=agent["headers"])
        token = resp.json()["token"]

        # Revoke
        resp = await client.post(
            "/api/v1/context/revoke",
            params={"token": token},
            headers=agent["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["revoked"] is True

    async def test_cannot_revoke_others_token(self, client, agent_factory):
        agent_a = await agent_factory.create(name="a")
        agent_b = await agent_factory.create(name="b")

        resp = await client.post("/api/v1/context/seal", json={
            "value": "secret",
            "context": "pii",
        }, headers=agent_a["headers"])
        token = resp.json()["token"]

        # B cannot revoke A's token
        resp = await client.post(
            "/api/v1/context/revoke",
            params={"token": token},
            headers=agent_b["headers"],
        )
        assert resp.status_code == 404  # not found or not owned


class TestPseudonyms:
    """Pseudonymous identifier generation."""

    async def test_deterministic_pseudonyms(self, client, agent_factory):
        agent = await agent_factory.create()

        resp1 = await client.post("/api/v1/context/pseudonym", json={
            "identifier": "user123",
            "salt": "handoff-abc",
        }, headers=agent["headers"])
        assert resp1.status_code == 200

        resp2 = await client.post("/api/v1/context/pseudonym", json={
            "identifier": "user123",
            "salt": "handoff-abc",
        }, headers=agent["headers"])

        # Same input = same output
        assert resp1.json()["pseudonym"] == resp2.json()["pseudonym"]
        assert resp1.json()["pseudonym"].startswith("pseudo:")

    async def test_different_salt_different_pseudonym(self, client, agent_factory):
        agent = await agent_factory.create()

        resp1 = await client.post("/api/v1/context/pseudonym", json={
            "identifier": "user123",
            "salt": "salt-a",
        }, headers=agent["headers"])

        resp2 = await client.post("/api/v1/context/pseudonym", json={
            "identifier": "user123",
            "salt": "salt-b",
        }, headers=agent["headers"])

        assert resp1.json()["pseudonym"] != resp2.json()["pseudonym"]

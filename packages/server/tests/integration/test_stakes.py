"""Integration tests: Stakes — post, release, forfeit, balance."""

import uuid

import pytest


class TestStakes:
    """Stake lifecycle tests."""

    async def _create_handoff(self, client, delegator, receiver):
        resp = await client.post("/api/v1/handoffs", json={
            "to_agent_id": receiver["id"],
            "context": {"domain": "test"},
        }, headers=delegator["headers"])
        return resp.json()

    async def test_post_stake(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._create_handoff(client, delegator, receiver)

        resp = await client.post("/api/v1/stakes", json={
            "handoff_id": h["id"],
            "amount": 10.0,
        }, headers=receiver["headers"])
        assert resp.status_code == 201
        stake = resp.json()
        assert stake["amount"] == 10.0
        assert stake["status"] == "held"

    async def test_post_stake_insufficient_balance(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._create_handoff(client, delegator, receiver)

        resp = await client.post("/api/v1/stakes", json={
            "handoff_id": h["id"],
            "amount": 999999.0,  # way more than default balance
        }, headers=receiver["headers"])
        assert resp.status_code == 400
        assert "Insufficient" in resp.json()["detail"]

    async def test_only_receiver_can_stake(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._create_handoff(client, delegator, receiver)

        resp = await client.post("/api/v1/stakes", json={
            "handoff_id": h["id"],
            "amount": 5.0,
        }, headers=delegator["headers"])  # delegator, not receiver
        assert resp.status_code == 403

    async def test_release_stake(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._create_handoff(client, delegator, receiver)

        resp = await client.post("/api/v1/stakes", json={
            "handoff_id": h["id"],
            "amount": 10.0,
        }, headers=receiver["headers"])
        stake = resp.json()

        # Delegator releases
        resp = await client.post(
            f"/api/v1/stakes/{stake['id']}/release",
            headers=delegator["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "released"

    async def test_forfeit_stake(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._create_handoff(client, delegator, receiver)

        resp = await client.post("/api/v1/stakes", json={
            "handoff_id": h["id"],
            "amount": 10.0,
        }, headers=receiver["headers"])
        stake = resp.json()

        resp = await client.post(
            f"/api/v1/stakes/{stake['id']}/forfeit",
            params={"reason": "Failed to deliver"},
            headers=delegator["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "forfeited"

    async def test_balance_reflects_stake(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._create_handoff(client, delegator, receiver)

        # Check initial balance
        resp = await client.get(
            f"/api/v1/stakes/balance/{receiver['id']}",
            headers=receiver["headers"],
        )
        initial_available = resp.json()["available"]

        # Post stake
        await client.post("/api/v1/stakes", json={
            "handoff_id": h["id"],
            "amount": 15.0,
        }, headers=receiver["headers"])

        # Check updated balance
        resp = await client.get(
            f"/api/v1/stakes/balance/{receiver['id']}",
            headers=receiver["headers"],
        )
        assert resp.json()["available"] == initial_available - 15.0
        assert resp.json()["staked"] == 15.0

    async def test_duplicate_stake_rejected(self, client, agent_factory):
        delegator, receiver = await agent_factory.create_pair()
        h = await self._create_handoff(client, delegator, receiver)

        await client.post("/api/v1/stakes", json={
            "handoff_id": h["id"],
            "amount": 5.0,
        }, headers=receiver["headers"])

        resp = await client.post("/api/v1/stakes", json={
            "handoff_id": h["id"],
            "amount": 5.0,
        }, headers=receiver["headers"])
        assert resp.status_code == 409

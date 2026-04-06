"""Shared test fixtures for the Handoff server test suite."""

import os
import uuid

import pytest

# Ensure test JWT secret is set before importing app modules
os.environ["JWT_SECRET"] = "test-secret-key-for-testing-only"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///test.db"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"


@pytest.fixture
def agent_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def agent_id_str(agent_id: uuid.UUID) -> str:
    return str(agent_id)


@pytest.fixture
def sample_intent() -> dict:
    return {
        "type": "request",
        "domain": "hotels",
        "action": "book_room",
        "parameters": {"destination": "Tokyo", "nights": 5},
        "constraints": {
            "budget_max": 2000,
            "currency": "USD",
            "must_have": ["wifi"],
            "nice_to_have": ["breakfast"],
        },
        "priority": "high",
    }


@pytest.fixture
def sample_offer_terms() -> dict:
    return {
        "hotel": "Park Hyatt Tokyo",
        "room_type": "deluxe_king",
        "price_per_night": 450,
        "total_price": 2250,
        "currency": "USD",
        "includes": ["wifi", "gym"],
    }


@pytest.fixture
def negotiation_dict(agent_id_str: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "initiator_id": agent_id_str,
        "responder_id": str(uuid.uuid4()),
        "state": "created",
        "intent": {"domain": "hotels", "action": "book_room"},
        "current_offer": None,
        "offer_history": [],
        "agreement": None,
        "current_round": 0,
        "max_rounds": 10,
        "timeout_at": None,
        "metadata": {},
    }

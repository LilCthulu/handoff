"""Tests for the Intent fluent builder."""

from handoff_sdk.intent import Intent


class TestIntentFactory:
    def test_request_creates_intent(self):
        intent = Intent.request(domain="hotels", action="book_room")
        data = intent.to_dict()
        assert data["type"] == "request"
        assert data["domain"] == "hotels"
        assert data["action"] == "book_room"
        assert data["priority"] == "medium"

    def test_offer_creates_intent(self):
        intent = Intent.offer(domain="hotels", action="provide_room")
        data = intent.to_dict()
        assert data["type"] == "offer"
        assert data["domain"] == "hotels"

    def test_query_creates_intent(self):
        intent = Intent.query(domain="flights", action="search")
        data = intent.to_dict()
        assert data["type"] == "query"

    def test_factory_with_parameters(self):
        intent = Intent.request(
            domain="hotels",
            action="book",
            parameters={"destination": "Tokyo", "nights": 5},
        )
        data = intent.to_dict()
        assert data["parameters"]["destination"] == "Tokyo"
        assert data["parameters"]["nights"] == 5

    def test_factory_with_constraints(self):
        intent = Intent.request(
            domain="hotels",
            action="book",
            constraints={"budget_max": 2000},
        )
        data = intent.to_dict()
        assert data["constraints"]["budget_max"] == 2000


class TestIntentFluent:
    def test_chaining(self):
        intent = (
            Intent.request(domain="hotels", action="book_room")
            .with_budget(2000, "USD")
            .must_have("wifi", "breakfast")
            .nice_to_have("pool")
            .with_priority("high")
            .on_failure("escalate")
        )
        data = intent.to_dict()
        assert data["constraints"]["budget_max"] == 2000
        assert data["constraints"]["currency"] == "USD"
        assert "wifi" in data["constraints"]["must_have"]
        assert "breakfast" in data["constraints"]["must_have"]
        assert "pool" in data["constraints"]["nice_to_have"]
        assert data["priority"] == "high"
        assert data["fallback_behavior"] == "escalate"

    def test_with_parameters(self):
        intent = Intent.request(domain="hotels", action="book").with_parameters(
            destination="Paris", guests=2
        )
        data = intent.to_dict()
        assert data["parameters"]["destination"] == "Paris"
        assert data["parameters"]["guests"] == 2

    def test_with_deadline(self):
        intent = Intent.request(domain="hotels", action="book").with_deadline("2025-03-15T00:00:00Z")
        data = intent.to_dict()
        assert data["constraints"]["deadline"] == "2025-03-15T00:00:00Z"

    def test_multiple_must_have_calls_accumulate(self):
        intent = Intent.request(domain="hotels", action="book").must_have("wifi").must_have("breakfast")
        data = intent.to_dict()
        assert data["constraints"]["must_have"] == ["wifi", "breakfast"]


class TestIntentOutput:
    def test_to_dict_has_id(self):
        intent = Intent.request(domain="test", action="test")
        data = intent.to_dict()
        assert "id" in data
        assert len(data["id"]) == 36  # UUID format

    def test_to_dict_is_independent_copy(self):
        intent = Intent.request(domain="test", action="test")
        d1 = intent.to_dict()
        d2 = intent.to_dict()
        d1["domain"] = "modified"
        assert d2["domain"] == "test"

    def test_repr(self):
        intent = Intent.request(domain="hotels", action="book_room")
        assert "request" in repr(intent)
        assert "hotels" in repr(intent)
        assert "book_room" in repr(intent)

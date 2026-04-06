"""Seed demo data into the Handoff database for dashboard testing."""

import asyncio
import uuid
from datetime import datetime, timezone, timedelta

from app.database import async_session, create_tables
from app.models.agent import Agent
from app.models.negotiation import Negotiation
from app.models.handoff import Handoff
from app.models.audit import AuditLog
from app.models.trust import TrustScore, TrustEvent
from app.models.capability import CapabilityContract
from app.models.attestation import Attestation, CapabilityChallenge


async def seed():
    await create_tables()

    async with async_session() as db:
        now = datetime.now(timezone.utc)

        # Create agents
        agents = []
        agent_defs = [
            ("TravelBot", "travel", ["search", "book", "cancel"], 0.92, "Finds flights and hotels"),
            ("HotelAgent", "hotels", ["search", "book", "modify"], 0.87, "Hotel booking specialist"),
            ("PaymentGateway", "payments", ["charge", "refund", "verify"], 0.95, "Handles all payment processing"),
            ("WeatherService", "weather", ["forecast", "alerts", "historical"], 0.78, "Weather data provider"),
            ("NotificationHub", "notifications", ["email", "sms", "push"], 0.85, "Multi-channel notifications"),
            ("AnalyticsEngine", "analytics", ["track", "report", "predict"], 0.90, "Business intelligence"),
        ]

        for name, domain, actions, trust, desc in agent_defs:
            agent = Agent(
                id=uuid.uuid4(),
                name=name,
                description=desc,
                owner_id="demo-owner",
                public_key="demo-key-" + name.lower(),
                capabilities=[{"domain": domain, "actions": actions}],
                trust_score=trust,
                max_authority={"max_spend": 1000, "currency": "USD"},
                status="active",
                metadata_={"version": "1.0", "region": "us-east-1"},
                created_at=now - timedelta(days=7),
                updated_at=now - timedelta(hours=2),
            )
            db.add(agent)
            agents.append(agent)

        await db.flush()

        # --- Trust scores per agent per domain ---
        trust_data = [
            # (agent_idx, domain, score, successes, failures, total, avg_ms)
            (0, "travel", 0.92, 45, 4, 49, 1200),
            (0, "hotels", 0.84, 22, 5, 27, 2100),
            (1, "hotels", 0.87, 38, 6, 44, 1800),
            (1, "travel", 0.71, 12, 5, 17, 2500),
            (2, "payments", 0.95, 120, 6, 126, 340),
            (2, "billing", 0.91, 35, 3, 38, 420),
            (3, "weather", 0.78, 60, 18, 78, 90),
            (3, "analytics", 0.65, 8, 5, 13, 450),
            (4, "notifications", 0.85, 95, 17, 112, 150),
            (4, "alerts", 0.80, 30, 8, 38, 200),
            (5, "analytics", 0.90, 55, 6, 61, 3200),
            (5, "reporting", 0.88, 40, 5, 45, 4500),
        ]

        for aidx, domain, score, succ, fail, total, avg_ms in trust_data:
            ts = TrustScore(
                agent_id=agents[aidx].id,
                domain=domain,
                score=score,
                successful_handoffs=succ,
                failed_handoffs=fail,
                total_handoffs=total,
                avg_completion_time_ms=avg_ms,
                last_updated=now - timedelta(hours=1),
            )
            db.add(ts)

        # --- Trust events (recent history) ---
        event_types = ["success", "success", "success", "failure", "success", "timeout", "success", "success"]
        for i, evt in enumerate(event_types):
            te = TrustEvent(
                agent_id=agents[i % len(agents)].id,
                domain=agent_defs[i % len(agents)][1],
                event_type=evt,
                score_delta=0.01 if evt == "success" else -0.02,
                score_after=agent_defs[i % len(agents)][3],
                completion_time_ms=500 + i * 200 if evt == "success" else None,
                details=f"Handoff {'completed successfully' if evt == 'success' else 'failed: ' + evt}",
                created_at=now - timedelta(hours=i * 3),
            )
            db.add(te)

        # --- Capability contracts ---
        contracts = [
            (0, "travel", "search", "1.2.0", "Search flights by route, dates, and class",
             {"type": "object", "properties": {"origin": {"type": "string"}, "destination": {"type": "string"}, "date": {"type": "string", "format": "date"}, "class": {"type": "string", "enum": ["economy", "business", "first"]}}},
             {"type": "object", "properties": {"flights": {"type": "array"}, "cheapest": {"type": "object"}}},
             800, 0.99),
            (0, "travel", "book", "1.1.0", "Book a flight given a flight ID and passenger details",
             {"type": "object", "properties": {"flight_id": {"type": "string"}, "passenger": {"type": "object"}}},
             {"type": "object", "properties": {"booking_id": {"type": "string"}, "status": {"type": "string"}, "confirmation": {"type": "string"}}},
             2000, 0.98),
            (1, "hotels", "search", "2.0.0", "Search hotels by location, dates, and preferences",
             {"type": "object", "properties": {"location": {"type": "string"}, "checkin": {"type": "string"}, "checkout": {"type": "string"}, "guests": {"type": "integer"}}},
             {"type": "object", "properties": {"hotels": {"type": "array"}, "total_results": {"type": "integer"}}},
             1500, 0.99),
            (1, "hotels", "book", "1.5.0", "Book a hotel room",
             {"type": "object", "properties": {"hotel_id": {"type": "string"}, "room_type": {"type": "string"}, "guest": {"type": "object"}}},
             {"type": "object", "properties": {"reservation_id": {"type": "string"}, "total_price": {"type": "number"}}},
             3000, 0.97),
            (2, "payments", "charge", "3.0.0", "Process a payment charge",
             {"type": "object", "properties": {"amount": {"type": "number"}, "currency": {"type": "string"}, "method": {"type": "string"}, "metadata": {"type": "object"}}},
             {"type": "object", "properties": {"transaction_id": {"type": "string"}, "status": {"type": "string"}, "receipt_url": {"type": "string"}}},
             500, 0.999),
            (2, "payments", "refund", "2.1.0", "Process a refund for a previous charge",
             {"type": "object", "properties": {"transaction_id": {"type": "string"}, "amount": {"type": "number"}, "reason": {"type": "string"}}},
             {"type": "object", "properties": {"refund_id": {"type": "string"}, "status": {"type": "string"}}},
             1000, 0.99),
            (3, "weather", "forecast", "1.0.0", "Get weather forecast for a location",
             {"type": "object", "properties": {"location": {"type": "string"}, "days": {"type": "integer", "maximum": 14}}},
             {"type": "object", "properties": {"forecast": {"type": "array"}, "location": {"type": "object"}}},
             200, 0.995),
            (4, "notifications", "email", "1.3.0", "Send a transactional email",
             {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "template": {"type": "string"}}},
             {"type": "object", "properties": {"message_id": {"type": "string"}, "status": {"type": "string"}}},
             300, 0.99),
            (4, "notifications", "push", "1.0.0", "Send a push notification",
             {"type": "object", "properties": {"user_id": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}}},
             {"type": "object", "properties": {"delivered": {"type": "boolean"}}},
             200, 0.95),
            (5, "analytics", "report", "2.0.0", "Generate an analytics report",
             {"type": "object", "properties": {"metric": {"type": "string"}, "period": {"type": "string"}, "filters": {"type": "object"}}},
             {"type": "object", "properties": {"data": {"type": "array"}, "summary": {"type": "object"}, "chart_url": {"type": "string"}}},
             5000, 0.98),
        ]

        for aidx, domain, action, version, desc, inp, out, latency, avail in contracts:
            cap = CapabilityContract(
                agent_id=agents[aidx].id,
                domain=domain,
                action=action,
                version=version,
                description=desc,
                input_schema=inp,
                output_schema=out,
                max_latency_ms=latency,
                availability_target=avail,
                created_at=now - timedelta(days=5),
                updated_at=now - timedelta(days=1),
            )
            db.add(cap)

        # --- Negotiations ---
        neg_states = [
            ("agreed", 3, True),
            ("negotiating", 2, False),
            ("completed", 5, True),
            ("rejected", 1, True),
            ("pending", 0, False),
        ]

        negotiations = []
        for i, (state, rounds, has_agreement) in enumerate(neg_states):
            a1 = agents[i % len(agents)]
            a2 = agents[(i + 1) % len(agents)]

            offer_history = []
            for r in range(rounds):
                offer_history.append({
                    "round": r + 1,
                    "from_agent": str(a1.id if r % 2 == 0 else a2.id),
                    "terms": {"price": 300 + r * 50, "currency": "USD"},
                    "timestamp": (now - timedelta(hours=rounds - r)).isoformat(),
                })

            neg = Negotiation(
                id=uuid.uuid4(),
                initiator_id=a1.id,
                responder_id=a2.id,
                state=state,
                intent={"domain": a2.capabilities[0]["domain"], "action": "book", "constraints": {"budget": 500}},
                current_offer=offer_history[-1] if offer_history else None,
                offer_history=offer_history,
                agreement={"terms": {"price": 400}, "signed_at": now.isoformat()} if has_agreement else None,
                max_rounds=10,
                current_round=rounds,
                metadata_={"priority": "high" if i == 0 else "normal"},
                created_at=now - timedelta(days=2, hours=i),
                updated_at=now - timedelta(hours=i),
                completed_at=now - timedelta(hours=1) if state in ("agreed", "completed", "rejected") else None,
            )
            db.add(neg)
            negotiations.append(neg)

        await db.flush()

        # --- Handoffs ---
        handoff_statuses = ["completed", "in_progress", "initiated", "completed", "failed"]
        for i, status in enumerate(handoff_statuses):
            a1 = agents[i % len(agents)]
            a2 = agents[(i + 2) % len(agents)]

            ho = Handoff(
                id=uuid.uuid4(),
                negotiation_id=negotiations[i % len(negotiations)].id,
                from_agent_id=a1.id,
                to_agent_id=a2.id,
                status=status,
                context={
                    "task": f"Process booking #{1000 + i}",
                    "domain": agent_defs[(i + 2) % len(agents)][1],
                    "data": {"booking_id": f"BK-{1000 + i}", "amount": 200 + i * 75},
                },
                result={"success": True, "output": "Task completed"} if status == "completed" else None,
                chain_id=uuid.uuid4() if i < 3 else None,
                chain_position=i if i < 3 else 0,
                created_at=now - timedelta(days=1, hours=i),
                updated_at=now - timedelta(hours=i),
                completed_at=now - timedelta(minutes=30) if status == "completed" else None,
            )
            db.add(ho)

        # --- Attestations ---
        # Create attestations for completed handoffs
        attestation_data = [
            # (attester_idx, subject_idx, domain, outcome, rating)
            (0, 2, "payments", "success", 0.95),
            (0, 1, "hotels", "success", 0.88),
            (1, 2, "payments", "success", 0.92),
            (2, 0, "travel", "success", 0.90),
            (2, 4, "notifications", "success", 0.85),
            (3, 0, "travel", "partial", 0.72),
            (4, 5, "analytics", "success", 0.94),
            (5, 1, "hotels", "failure", 0.30),
            (1, 3, "weather", "success", 0.80),
            (0, 5, "analytics", "success", 0.91),
        ]

        for i, (aidx, sidx, domain, outcome, rating) in enumerate(attestation_data):
            att = Attestation(
                attester_id=agents[aidx].id,
                attester_key_fingerprint=f"sha256:demo{aidx:04d}",
                subject_id=agents[sidx].id,
                handoff_id=uuid.uuid4(),  # Synthetic handoff refs
                domain=domain,
                outcome=outcome,
                rating=rating,
                claim={
                    "domain": domain,
                    "outcome": outcome,
                    "rating": rating,
                    "timestamp": (now - timedelta(hours=i * 6)).isoformat(),
                },
                signature="demo-signature-placeholder",
                verified=True,
                created_at=now - timedelta(hours=i * 6),
            )
            db.add(att)

        # --- Capability Challenges ---
        challenge_data = [
            # (agent_idx, domain, action, status, response_time_ms, issuer_idx)
            (0, "travel", "search", "passed", 450.2, 2),
            (1, "hotels", "search", "passed", 820.5, 0),
            (2, "payments", "charge", "passed", 120.8, 0),
            (3, "weather", "forecast", "passed", 65.3, 4),
            (4, "notifications", "email", "passed", 180.1, 2),
            (5, "analytics", "report", "timeout", None, 0),
            (1, "hotels", "book", "failed", 1200.0, 2),
            (3, "weather", "alerts", "pending", None, 5),
        ]

        for i, (aidx, domain, action, status, resp_ms, issuer_idx) in enumerate(challenge_data):
            ch = CapabilityChallenge(
                agent_id=agents[aidx].id,
                domain=domain,
                action=action,
                challenge_input={"test": True, "scenario": f"demo-{i}"},
                expected_schema={"type": "object", "properties": {"result": {"type": "string"}}},
                max_time_ms=5000,
                response={"result": "ok"} if status == "passed" else None,
                response_time_ms=resp_ms,
                status=status,
                failure_reason="Response took too long" if status == "timeout" else ("Schema validation failed" if status == "failed" else None),
                issued_by=agents[issuer_idx].id,
                created_at=now - timedelta(hours=i * 4),
                completed_at=(now - timedelta(hours=i * 4 - 1)) if status in ("passed", "failed", "timeout") else None,
            )
            db.add(ch)

        # --- Audit entries ---
        for i, agent in enumerate(agents):
            audit = AuditLog(
                entity_type="agent",
                entity_id=agent.id,
                action="registered",
                actor_agent_id=agent.id,
                details={"name": agent.name, "owner_id": agent.owner_id},
                created_at=agent.created_at,
            )
            db.add(audit)

        await db.commit()
        print(f"Seeded: {len(agents)} agents, {len(trust_data)} trust scores, {len(contracts)} capabilities, {len(negotiations)} negotiations, {len(handoff_statuses)} handoffs, {len(attestation_data)} attestations, {len(challenge_data)} challenges")


if __name__ == "__main__":
    asyncio.run(seed())

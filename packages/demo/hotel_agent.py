"""Hotel Agent — Agent B in the demo scenario.

Represents a hotel chain's booking agent. Has inventory at the
Park Hyatt Tokyo. Will negotiate starting high and offering
concessions to close deals.

Strategy: start at $600/night, offer concessions (breakfast,
upgrades) to close. Won't go below $380/night (minimum margin).
"""

import asyncio
import sys

from handoff_sdk import HandoffAgent, Intent


SERVER = "http://localhost:8000"

# Pricing strategy
LIST_PRICE = 600
MIN_PRICE = 380
INITIAL_OFFER = LIST_PRICE
NIGHTS = 5

# Concession tiers
CONCESSIONS = [
    {"at_price": 550, "add": "breakfast", "description": "Added complimentary breakfast"},
    {"at_price": 480, "add": "gym", "description": "Added gym access"},
    {"at_price": 420, "add": "late_checkout", "description": "Added late checkout"},
]


async def main() -> None:
    print("=" * 60)
    print("HOTEL AGENT — Park Hyatt Tokyo Demo")
    print("=" * 60)
    print(f"List price: ${LIST_PRICE}/night")
    print(f"Minimum: ${MIN_PRICE}/night")
    print(f"Strategy: Start high, add concessions to close")
    print()

    # --- Register ---
    agent = HandoffAgent(name="park-hyatt-booking", server=SERVER)
    profile = await agent.register(
        capabilities=[{
            "domain": "hotels",
            "actions": ["search_availability", "book_room", "cancel_booking"],
            "constraints": {"regions": ["asia"], "brands": ["park-hyatt"]},
        }],
        owner_id="hyatt-corp",
        description="Park Hyatt Tokyo — luxury hotel booking service",
    )
    print(f"Registered as: {profile.name} (ID: {profile.id})")
    print(f"Trust score: {profile.trust_score}")
    print()

    # --- Listen for negotiations ---
    print("Waiting for incoming negotiations...")
    print("(Start travel_agent.py in another terminal)")
    print()

    # Poll for incoming negotiations
    negotiation_id = None
    initiator_id = None

    for _ in range(60):  # wait up to 2 minutes
        await asyncio.sleep(2)
        try:
            # Check dashboard for negotiations targeting us
            result = await agent._client.get("/api/v1/dashboard/negotiations")
            for neg in result:
                if neg.get("responder_id") == str(agent.agent_id) and neg.get("state") in ("pending", "negotiating"):
                    negotiation_id = neg["id"]
                    initiator_id = neg["initiator_id"]
                    break
            if negotiation_id:
                break
        except Exception:
            continue

    if not negotiation_id:
        print("No incoming negotiations received. Timed out.")
        await agent.close()
        return

    print(f"Incoming negotiation: {negotiation_id}")
    print(f"From agent: {initiator_id}")
    print()

    # --- Get negotiation details ---
    from handoff_sdk.negotiation import NegotiationSession
    negotiation = NegotiationSession(agent._client, negotiation_id, str(agent.agent_id))
    await negotiation.refresh()

    # --- Negotiation loop ---
    current_price = INITIAL_OFFER
    includes = ["wifi"]
    round_num = 0

    while negotiation.state in ("pending", "negotiating"):
        round_num += 1

        # Check if there's a new offer from the travel agent
        await negotiation.refresh()

        if negotiation.state in ("rejected", "failed", "completed", "agreed"):
            break

        if negotiation.current_offer and negotiation.current_offer.get("from_agent") != str(agent.agent_id):
            their_terms = negotiation.current_offer.get("terms", {})
            their_price = their_terms.get("price_per_night", 0)
            their_total = their_terms.get("total_price", their_price * NIGHTS)
            print(f"Round {round_num}: Travel agent offers ${their_price}/night (${their_total} total)")

            # If their offer meets our minimum, accept
            if their_price >= MIN_PRICE:
                print(f"  Offer meets our minimum (${MIN_PRICE}). Accepting.")
                await negotiation.accept()
                break

            # Calculate our counter
            gap = current_price - their_price
            new_price = max(MIN_PRICE, current_price - int(gap * 0.35))

            # Add concessions as price drops
            concessions_made = []
            for tier in CONCESSIONS:
                if new_price <= tier["at_price"] and tier["add"] not in includes:
                    includes.append(tier["add"])
                    concessions_made.append(tier["description"])

            current_price = new_price

            our_terms = {
                "hotel": "Park Hyatt Tokyo",
                "room_type": "deluxe_king",
                "price_per_night": current_price,
                "total_price": current_price * NIGHTS,
                "currency": "USD",
                "cancellation_policy": "free_until_72h_before",
                "includes": list(includes),
                "excludes": [c["add"] for c in CONCESSIONS if c["add"] not in includes] + ["airport_shuttle"],
            }

            print(f"  Counter: ${current_price}/night (${current_price * NIGHTS} total)")
            print(f"  Includes: {', '.join(includes)}")
            if concessions_made:
                print(f"  Concessions: {', '.join(concessions_made)}")

            await negotiation.offer(
                terms=our_terms,
                concessions=concessions_made,
                conditions=["Payment within 24h of agreement"],
            )
        else:
            await asyncio.sleep(2)

        if round_num > 10:
            print("Max rounds reached.")
            break

    # --- Handle handoff ---
    print()
    await negotiation.refresh()

    if negotiation.state == "agreed":
        print("Deal agreed! Waiting for handoff...")

        # Wait for handoff
        handoff_id = None
        for _ in range(30):
            await asyncio.sleep(2)
            try:
                result = await agent._client.get("/api/v1/dashboard/handoffs")
                for h in result:
                    if h.get("to_agent_id") == str(agent.agent_id) and h.get("status") == "initiated":
                        handoff_id = h["id"]
                        break
                if handoff_id:
                    break
            except Exception:
                continue

        if handoff_id:
            print(f"Handoff received: {handoff_id}")

            from handoff_sdk.handoff import HandoffSession
            handoff = HandoffSession(agent._client, {
                "id": handoff_id, "from_agent_id": initiator_id,
                "to_agent_id": str(agent.agent_id), "status": "initiated",
                "context": {}, "result": None, "chain_id": None,
            })

            # Process the booking
            print("Processing booking...")
            await handoff.start()
            await asyncio.sleep(2)  # simulate processing time

            # Complete with result
            await handoff.complete_with_result({
                "status": "confirmed",
                "confirmation_number": "PH-TYO-2025-78432",
                "details": {
                    "hotel": "Park Hyatt Tokyo",
                    "room": f"Deluxe King, Floor 42",
                    "check_in": "2025-03-15T15:00:00+09:00",
                    "check_out": "2025-03-20T11:00:00+09:00",
                    "total_charged": current_price * NIGHTS,
                    "currency": "USD",
                    "includes": includes,
                },
            })
            print("Booking confirmed! Confirmation: PH-TYO-2025-78432")
        else:
            print("No handoff received.")
    else:
        print(f"Negotiation ended in state: {negotiation.state}")

    print()
    print("=" * 60)
    print("HOTEL AGENT — Demo complete")
    print("=" * 60)

    await agent.close()


if __name__ == "__main__":
    asyncio.run(main())

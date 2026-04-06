"""Travel Agent — Agent A in the demo scenario.

Represents a user's personal travel assistant. Wants to book a
5-night hotel stay in Tokyo. Has a budget of $2,000. Will negotiate
aggressively but accept a good deal.

Strategy: start with lowball offer, negotiate up to budget max.
"""

import asyncio
import sys

from handoff_sdk import HandoffAgent, Intent


SERVER = "http://localhost:8000"

# Negotiation strategy
BUDGET_MAX = 2000
INITIAL_OFFER_PER_NIGHT = 300
MAX_PER_NIGHT = 420  # budget_max / 5 nights + small buffer
NIGHTS = 5


async def main() -> None:
    print("=" * 60)
    print("TRAVEL AGENT — Tokyo Hotel Booking Demo")
    print("=" * 60)
    print(f"Budget: ${BUDGET_MAX} for {NIGHTS} nights")
    print(f"Strategy: Start at ${INITIAL_OFFER_PER_NIGHT}/night, max ${MAX_PER_NIGHT}/night")
    print()

    # --- Register ---
    agent = HandoffAgent(name="travel-bot", server=SERVER)
    profile = await agent.register(
        capabilities=[{
            "domain": "travel",
            "actions": ["search_flights", "search_hotels", "plan_itinerary"],
            "constraints": {"max_spend_per_transaction": BUDGET_MAX, "currency": "USD"},
        }],
        owner_id="demo-user",
        description="Personal travel assistant — finds and books the best deals",
    )
    print(f"Registered as: {profile.name} (ID: {profile.id})")
    print(f"Trust score: {profile.trust_score}")
    print()

    # --- Discover hotel agents ---
    print("Discovering hotel agents...")
    hotels = await agent.discover(domain="hotels", min_trust=0.3)
    if not hotels:
        print("No hotel agents found. Start hotel_agent.py first!")
        print("Waiting for hotel agents to register...")
        for _ in range(30):
            await asyncio.sleep(2)
            hotels = await agent.discover(domain="hotels", min_trust=0.3)
            if hotels:
                break
        if not hotels:
            print("Timed out waiting for hotel agents.")
            await agent.close()
            return

    hotel = hotels[0]
    print(f"Found: {hotel.name} (ID: {hotel.id}, trust: {hotel.trust_score})")
    print()

    # --- Build intent ---
    intent = (Intent.request(
        domain="hotels",
        action="book_hotel",
        parameters={
            "destination": "Tokyo",
            "check_in": "2025-03-15",
            "check_out": "2025-03-20",
            "guests": 2,
            "preferences": {
                "star_rating_min": 4,
                "amenities": ["wifi", "breakfast"],
                "location": "Shibuya",
            },
        },
    )
    .with_budget(BUDGET_MAX, "USD")
    .must_have("free_cancellation")
    .nice_to_have("late_checkout", "airport_shuttle")
    .with_priority("high"))

    print(f"Intent: {intent}")
    print()

    # --- Start negotiation ---
    print("Starting negotiation...")
    negotiation = await agent.negotiate(target=hotel, intent=intent, max_rounds=10)
    print(f"Negotiation ID: {negotiation.id}")
    print()

    # --- Negotiation loop ---
    current_offer_per_night = INITIAL_OFFER_PER_NIGHT
    round_num = 0

    while True:
        round_num += 1

        # Submit our offer
        our_terms = {
            "hotel": "Park Hyatt Tokyo",
            "room_type": "deluxe_king",
            "price_per_night": current_offer_per_night,
            "total_price": current_offer_per_night * NIGHTS,
            "currency": "USD",
            "cancellation_policy": "free_until_72h_before",
            "includes": ["wifi"],
        }
        concessions = []
        if round_num > 1:
            concessions.append(f"Raised offer from ${INITIAL_OFFER_PER_NIGHT} to ${current_offer_per_night}/night")

        print(f"Round {round_num}: Offering ${current_offer_per_night}/night (${current_offer_per_night * NIGHTS} total)")
        await negotiation.offer(terms=our_terms, concessions=concessions)

        # Wait for response
        await asyncio.sleep(1)
        await negotiation.refresh()

        if negotiation.state == "rejected":
            print("Hotel agent rejected our negotiation!")
            break

        if negotiation.state == "agreed":
            print("Deal agreed!")
            break

        # Check their counter-offer
        if negotiation.current_offer and negotiation.current_offer.get("from_agent") != str(agent.agent_id):
            their_terms = negotiation.current_offer.get("terms", {})
            their_price = their_terms.get("price_per_night", 0)
            their_total = their_terms.get("total_price", their_price * NIGHTS)
            their_includes = their_terms.get("includes", [])

            print(f"  Hotel countered: ${their_price}/night (${their_total} total)")
            print(f"  Includes: {', '.join(their_includes)}")

            # Decision logic
            if their_total <= BUDGET_MAX:
                print(f"  Within budget! Accepting.")
                await negotiation.accept()
                break

            if their_price <= MAX_PER_NIGHT:
                # Close enough — accept even if slightly over budget
                has_breakfast = "breakfast" in their_includes
                if has_breakfast:
                    print(f"  Slightly over budget but breakfast included. Accepting.")
                    await negotiation.accept()
                    break

            # Counter: move up toward their price
            gap = their_price - current_offer_per_night
            current_offer_per_night = min(MAX_PER_NIGHT, current_offer_per_night + int(gap * 0.4))
            print(f"  Countering: moving up to ${current_offer_per_night}/night")
        else:
            # Waiting for their response
            print("  Waiting for hotel response...")
            await asyncio.sleep(2)
            await negotiation.refresh()

        if round_num >= 8:
            print("Too many rounds. Making final offer at max budget.")
            current_offer_per_night = MAX_PER_NIGHT

    # --- Handoff ---
    print()
    if negotiation.state == "agreed":
        print("Agreement reached! Initiating handoff...")
        agreement = negotiation.agreement or {}
        handoff = await agent.handoff(
            to=hotel,
            context={
                "task_description": "Book the agreed hotel room",
                "agreed_terms": agreement.get("terms", {}),
                "input_data": {
                    "guest_name": "Jane Smith",
                    "guest_email": "jane@example.com",
                },
                "constraints": {
                    "timeout_minutes": 30,
                    "rollback_on_failure": True,
                },
                "provenance": {
                    "origin_agent": str(agent.agent_id),
                    "chain": [str(agent.agent_id), hotel.id],
                },
            },
            negotiation_id=negotiation.id,
            timeout_minutes=30,
        )
        print(f"Handoff initiated: {handoff.id}")

        # Wait for result
        print("Waiting for hotel to process booking...")
        result = await handoff.poll_until_resolved(interval=2, timeout=60)
        print(f"Handoff result: {result.status}")
        if result.result:
            print(f"Booking confirmation: {result.result}")
    else:
        print("Negotiation did not reach agreement.")

    print()
    print("=" * 60)
    print("TRAVEL AGENT — Demo complete")
    print("=" * 60)

    await agent.close()


if __name__ == "__main__":
    asyncio.run(main())

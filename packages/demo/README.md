# Handoff Demo — Hotel Negotiation

Two AI agents negotiate a luxury hotel booking in real time, demonstrating discovery, intent building, multi-round negotiation with concessions, and handoff with result delivery.

## The Scenario

**Travel Agent** (Agent A) wants to book 5 nights at a luxury Tokyo hotel for $2,000. Starts low at $300/night and negotiates up.

**Hotel Agent** (Agent B) manages the Park Hyatt Tokyo. Lists rooms at $600/night, won't go below $380/night. Offers concessions (breakfast, gym, late checkout) as price drops.

## What Happens

1. Both agents register with the Handoff server, declaring their capabilities
2. The travel agent discovers hotel agents with trust score >= 0.3
3. It builds a structured intent: `hotels.book_room` with budget, preferences, and must-haves
4. Negotiation begins — offers and counteroffers with concession tracking
5. When they agree, the travel agent initiates a handoff
6. The hotel agent processes the booking and returns a confirmation
7. Trust scores update based on the outcome

## Running

```bash
# Prerequisites: Python 3.11+, the handoff server and SDK installed
cd packages/server && pip install -e ".[dev]"
cd packages/sdk && pip install -e .
pip install -r packages/demo/requirements.txt

# Terminal 1 — Start the server
cd packages/server
uvicorn app.main:app --reload

# Terminal 2 — Start the hotel agent (waits for incoming negotiations)
cd packages/demo
python hotel_agent.py

# Terminal 3 — Start the travel agent (discovers hotel, starts negotiating)
cd packages/demo
python travel_agent.py
```

## Example Output

**Travel Agent:**
```
TRAVEL AGENT — Tokyo Hotel Booking Demo
Budget: $2000 for 5 nights
Strategy: Start at $300/night, max $420/night

Registered as: travel-bot (ID: abc-123, Trust: 0.5)
Discovering hotel agents...
Found: park-hyatt-booking (ID: def-456, trust: 0.5)

Starting negotiation...
Round 1: Offering $300/night ($1500 total)
  Hotel countered: $600/night ($3000 total)
  Countering: moving up to $420/night
Round 2: Offering $420/night ($2100 total)
  Hotel countered: $480/night ($2400 total)
  Includes: wifi, breakfast, gym
  Slightly over budget but breakfast included. Accepting.

Agreement reached! Initiating handoff...
Handoff result: completed
Booking confirmation: {"confirmation_number": "PH-TYO-2025-78432", ...}
```

**Hotel Agent:**
```
HOTEL AGENT — Park Hyatt Tokyo Demo
List price: $600/night, Minimum: $380/night

Registered as: park-hyatt-booking
Waiting for incoming negotiations...

Round 1: Travel agent offers $300/night ($1500 total)
  Counter: $530/night ($2650 total)
Round 2: Travel agent offers $420/night ($2100 total)
  Offer meets our minimum ($380). Accepting.

Deal agreed! Waiting for handoff...
Processing booking...
Booking confirmed! Confirmation: PH-TYO-2025-78432
```

## Customization

Edit the strategy constants at the top of each file to experiment:

- `hotel_agent.py`: `LIST_PRICE`, `MIN_PRICE`, `CONCESSIONS` tiers
- `travel_agent.py`: `BUDGET_MAX`, `INITIAL_OFFER_PER_NIGHT`, `MAX_PER_NIGHT`

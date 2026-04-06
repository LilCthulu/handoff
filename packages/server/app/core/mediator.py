"""Mediated negotiation logic for complex multi-party deals.

When two agents can't find middle ground alone, the mediator steps in.
It doesn't take sides. It finds common ground, suggests compromises,
and keeps the negotiation moving forward.
"""

from typing import Any

import structlog

logger = structlog.get_logger()


class MediationError(Exception):
    """Raised when mediation cannot proceed."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def analyze_gap(
    intent_constraints: dict[str, Any],
    latest_offer_terms: dict[str, Any],
) -> dict[str, Any]:
    """Analyze the gap between what was asked and what was offered.

    Args:
        intent_constraints: The initiator's original constraints.
        latest_offer_terms: The most recent offer's terms.

    Returns:
        Gap analysis with distance metrics and suggested compromises.
    """
    analysis: dict[str, Any] = {
        "budget_gap": None,
        "unmet_must_haves": [],
        "unmet_nice_to_haves": [],
        "suggestions": [],
    }

    # Budget gap
    budget_max = intent_constraints.get("budget_max")
    total_price = latest_offer_terms.get("total_price")
    if budget_max is not None and total_price is not None:
        gap = total_price - budget_max
        analysis["budget_gap"] = {
            "amount": gap,
            "percentage": (gap / budget_max * 100) if budget_max > 0 else 0,
            "over_budget": gap > 0,
        }
        if gap > 0:
            midpoint = budget_max + (gap / 2)
            analysis["suggestions"].append({
                "type": "price_compromise",
                "suggested_price": round(midpoint, 2),
                "rationale": "Split the difference between budget and offer",
            })

    # Must-have analysis
    must_haves = set(intent_constraints.get("must_have", []))
    includes = set(latest_offer_terms.get("includes", []))
    unmet_must = must_haves - includes
    if unmet_must:
        analysis["unmet_must_haves"] = list(unmet_must)
        analysis["suggestions"].append({
            "type": "must_have_gap",
            "missing": list(unmet_must),
            "rationale": "These are non-negotiable requirements that must be addressed",
        })

    # Nice-to-have analysis
    nice_to_haves = set(intent_constraints.get("nice_to_have", []))
    unmet_nice = nice_to_haves - includes
    if unmet_nice:
        analysis["unmet_nice_to_haves"] = list(unmet_nice)
        if (analysis.get("budget_gap") or {}).get("over_budget"):
            analysis["suggestions"].append({
                "type": "trade_off",
                "drop_nice_to_haves": list(unmet_nice),
                "rationale": "Dropping nice-to-haves could create room for price reduction",
            })

    return analysis


def suggest_compromise(
    intent_constraints: dict[str, Any],
    offer_history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Suggest a compromise based on the full negotiation history.

    Analyzes the trajectory of offers to find convergence potential.

    Args:
        intent_constraints: The initiator's original constraints.
        offer_history: Chronological list of all offers/counteroffers.

    Returns:
        Suggested compromise terms with rationale.
    """
    if not offer_history:
        raise MediationError("Cannot mediate without offer history")

    suggestion: dict[str, Any] = {
        "suggested_terms": {},
        "rationale": [],
        "confidence": 0.0,
    }

    # Track price trajectory
    prices = []
    for offer in offer_history:
        terms = offer.get("terms", {})
        price = terms.get("total_price") or terms.get("price_per_night")
        if price is not None:
            prices.append({"price": price, "from": offer.get("from_agent"), "round": offer.get("round")})

    if len(prices) >= 2:
        first_price = prices[0]["price"]
        last_price = prices[-1]["price"]
        budget_max = intent_constraints.get("budget_max")

        # Find the converging midpoint
        if budget_max is not None:
            suggested_price = (last_price + budget_max) / 2
        else:
            suggested_price = (first_price + last_price) / 2

        suggestion["suggested_terms"]["total_price"] = round(suggested_price, 2)
        suggestion["rationale"].append(
            f"Price trajectory: {first_price} -> {last_price}. "
            f"Suggested midpoint: {suggested_price:.2f}"
        )

        # Confidence based on convergence
        if first_price != 0:
            convergence = 1.0 - abs(first_price - last_price) / first_price
            suggestion["confidence"] = max(0.0, min(1.0, convergence))

    # Collect all includes across offers
    all_includes: set[str] = set()
    for offer in offer_history:
        includes = offer.get("terms", {}).get("includes", [])
        all_includes.update(includes)

    must_haves = set(intent_constraints.get("must_have", []))
    suggestion["suggested_terms"]["includes"] = list(all_includes | must_haves)
    suggestion["rationale"].append(
        f"Include all must-haves plus previously offered items: {all_includes | must_haves}"
    )

    logger.info(
        "compromise_suggested",
        confidence=suggestion["confidence"],
        suggested_price=suggestion["suggested_terms"].get("total_price"),
    )

    return suggestion


def should_mediate(
    current_round: int,
    max_rounds: int,
    offer_history: list[dict[str, Any]],
) -> bool:
    """Determine if mediation should be triggered automatically.

    Mediation is suggested when:
    - We're past the halfway point in rounds
    - Price trajectory shows divergence or stalling
    """
    if current_round < max_rounds // 2:
        return False

    if len(offer_history) < 2:
        return False

    # Check for price stalling (less than 5% movement in last 2 offers)
    recent = offer_history[-2:]
    prices = []
    for offer in recent:
        price = offer.get("terms", {}).get("total_price")
        if price is not None:
            prices.append(price)

    if len(prices) == 2 and prices[0] != 0:
        movement = abs(prices[1] - prices[0]) / prices[0]
        if movement < 0.05:
            logger.info("mediation_suggested", reason="price_stalling", movement=movement)
            return True

    # Past 75% of rounds — time is running out
    if current_round >= max_rounds * 0.75:
        logger.info("mediation_suggested", reason="rounds_running_out", current=current_round, max=max_rounds)
        return True

    return False

"""Negotiation session manager.

Manages the lifecycle of a negotiation from the SDK side —
creating, offering, countering, accepting, rejecting.
Supports both REST polling and real-time WebSocket updates.
"""

import asyncio
from typing import Any, Callable

from handoff_sdk.client import HandoffClient
from handoff_sdk.types import NegotiationResult, NegotiationState, Offer


class NegotiationSession:
    """Manages a single negotiation session."""

    def __init__(self, client: HandoffClient, negotiation_id: str, agent_id: str) -> None:
        self._client = client
        self.id = negotiation_id
        self._agent_id = agent_id
        self.state: NegotiationState = NegotiationState.PENDING
        self.current_offer: dict[str, Any] | None = None
        self.agreement: dict[str, Any] | None = None
        self._offer_history: list[dict[str, Any]] = []
        self._current_round: int = 0
        self._on_offer: Callable | None = None
        self._on_accepted: Callable | None = None
        self._on_rejected: Callable | None = None
        self._resolved = asyncio.Event()

    async def offer(
        self,
        terms: dict[str, Any],
        concessions: list[str] | None = None,
        conditions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Submit an offer or counteroffer."""
        data = {
            "terms": terms,
            "concessions": concessions or [],
            "conditions": conditions or [],
        }
        result = await self._client.post(f"/api/v1/negotiations/{self.id}/offer", data)
        self._update_from_response(result)
        return result

    async def accept(self) -> dict[str, Any]:
        """Accept the current offer."""
        result = await self._client.post(f"/api/v1/negotiations/{self.id}/accept")
        self._update_from_response(result)
        self._resolved.set()
        return result

    async def reject(self, reason: str | None = None) -> dict[str, Any]:
        """Reject the negotiation."""
        data = {"reason": reason} if reason else None
        result = await self._client.post(f"/api/v1/negotiations/{self.id}/reject", data)
        self._update_from_response(result)
        self._resolved.set()
        return result

    async def refresh(self) -> dict[str, Any]:
        """Fetch the latest negotiation state from the server."""
        result = await self._client.get(f"/api/v1/negotiations/{self.id}")
        self._update_from_response(result)
        return result

    async def history(self) -> list[dict[str, Any]]:
        """Get the full offer/counteroffer history."""
        result = await self._client.get(f"/api/v1/negotiations/{self.id}/history")
        return result.get("offer_history", [])

    async def request_mediation(self) -> dict[str, Any]:
        """Request mediation for this negotiation."""
        return await self._client.post(f"/api/v1/negotiations/{self.id}/mediate")

    async def wait(self, timeout: float | None = None) -> NegotiationResult:
        """Wait for the negotiation to reach a terminal state.

        Args:
            timeout: Maximum seconds to wait. None = wait forever.

        Returns:
            NegotiationResult with final state and agreement.
        """
        if timeout is not None:
            await asyncio.wait_for(self._resolved.wait(), timeout=timeout)
        else:
            await self._resolved.wait()

        return NegotiationResult(
            id=self.id,
            state=self.state,
            agreement=self.agreement,
            offer_history=self._offer_history,
            current_round=self._current_round,
        )

    async def poll_until_resolved(self, interval: float = 2.0, timeout: float = 300.0) -> NegotiationResult:
        """Poll the server until the negotiation reaches a terminal state."""
        elapsed = 0.0
        while elapsed < timeout:
            await self.refresh()
            if self.state in (NegotiationState.COMPLETED, NegotiationState.REJECTED, NegotiationState.FAILED, NegotiationState.AGREED):
                self._resolved.set()
                return NegotiationResult(
                    id=self.id,
                    state=self.state,
                    agreement=self.agreement,
                    offer_history=self._offer_history,
                    current_round=self._current_round,
                )
            await asyncio.sleep(interval)
            elapsed += interval

        raise TimeoutError(f"Negotiation {self.id} did not resolve within {timeout}s")

    def on_offer(self, handler: Callable) -> Callable:
        """Register a callback for incoming offers (decorator-style)."""
        self._on_offer = handler
        return handler

    def _handle_ws_offer(self, data: dict[str, Any]) -> None:
        """Handle an incoming offer via WebSocket."""
        offer_data = data.get("offer", {})
        offer = Offer(
            id=data.get("offer_id", ""),
            negotiation_id=self.id,
            from_agent=data.get("from_agent", ""),
            round=data.get("round", 0),
            terms=offer_data.get("terms", {}),
            concessions=offer_data.get("concessions", []),
            conditions=offer_data.get("conditions", []),
        )

        # Wire up action callbacks so offer.accept() and offer.counter() work
        import asyncio

        def _accept_fn() -> None:
            asyncio.create_task(self.accept())

        def _counter_fn(terms: dict, concessions: list, conditions: list) -> None:
            asyncio.create_task(self.offer(terms=terms, concessions=concessions, conditions=conditions))

        offer._accept_fn = _accept_fn
        offer._counter_fn = _counter_fn

        self.current_offer = offer.terms
        self._current_round = offer.round

        if self._on_offer:
            self._on_offer(offer)

    def _handle_ws_accepted(self, data: dict[str, Any]) -> None:
        """Handle acceptance via WebSocket."""
        self.state = NegotiationState.AGREED
        self.agreement = data.get("agreement")
        self._resolved.set()
        if self._on_accepted:
            self._on_accepted(data)

    def _handle_ws_rejected(self, data: dict[str, Any]) -> None:
        """Handle rejection via WebSocket."""
        self.state = NegotiationState.REJECTED
        self._resolved.set()
        if self._on_rejected:
            self._on_rejected(data)

    def _update_from_response(self, data: dict[str, Any]) -> None:
        """Update local state from a server response."""
        self.state = NegotiationState(data.get("state", self.state))
        self.current_offer = data.get("current_offer")
        self.agreement = data.get("agreement")
        self._offer_history = data.get("offer_history", self._offer_history)
        self._current_round = data.get("current_round", self._current_round)

        if self.state in (NegotiationState.COMPLETED, NegotiationState.REJECTED, NegotiationState.FAILED, NegotiationState.AGREED):
            self._resolved.set()

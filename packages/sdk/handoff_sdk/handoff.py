"""Handoff context builder — transferring work between agents.

When agents agree on terms, one hands off work to the other.
This module manages that transfer — context, constraints, chain
of custody, results, and rollback.
"""

import asyncio
from typing import Any

from handoff_sdk.client import HandoffClient
from handoff_sdk.types import HandoffResult, HandoffStatus


class HandoffSession:
    """Manages a single handoff from initiation to completion."""

    def __init__(self, client: HandoffClient, handoff_data: dict[str, Any]) -> None:
        self._client = client
        self.id: str = str(handoff_data["id"])
        self.from_agent_id: str = str(handoff_data.get("from_agent_id", ""))
        self.to_agent_id: str = str(handoff_data.get("to_agent_id", ""))
        self.status: HandoffStatus = HandoffStatus(handoff_data.get("status", "initiated"))
        self.context: dict[str, Any] = handoff_data.get("context", {})
        self.result: dict[str, Any] | None = handoff_data.get("result")
        self.chain_id: str | None = str(handoff_data["chain_id"]) if handoff_data.get("chain_id") else None
        self._resolved = asyncio.Event()
        if self.status in (HandoffStatus.COMPLETED, HandoffStatus.FAILED, HandoffStatus.ROLLED_BACK):
            self._resolved.set()

    async def update_status(self, status: str) -> dict[str, Any]:
        """Update the handoff status (receiving agent)."""
        result = await self._client.patch(
            f"/api/v1/handoffs/{self.id}/status",
            {"status": status},
        )
        self._update_from_response(result)
        return result

    async def start(self) -> dict[str, Any]:
        """Mark handoff as in_progress."""
        return await self.update_status("in_progress")

    async def complete_with_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """Submit result and mark as completed."""
        resp = await self._client.post(
            f"/api/v1/handoffs/{self.id}/result",
            {"result": result},
        )
        self._update_from_response(resp)
        return resp

    async def fail(self) -> dict[str, Any]:
        """Mark handoff as failed."""
        return await self.update_status("failed")

    async def rollback(self) -> dict[str, Any]:
        """Rollback the handoff."""
        result = await self._client.post(f"/api/v1/handoffs/{self.id}/rollback")
        self._update_from_response(result)
        return result

    async def refresh(self) -> dict[str, Any]:
        """Fetch the latest handoff state."""
        result = await self._client.get(f"/api/v1/handoffs/{self.id}")
        self._update_from_response(result)
        return result

    async def wait(self, timeout: float | None = None) -> HandoffResult:
        """Wait for the handoff to complete."""
        if timeout:
            await asyncio.wait_for(self._resolved.wait(), timeout=timeout)
        else:
            await self._resolved.wait()
        return HandoffResult(
            id=self.id,
            status=self.status,
            result=self.result,
            chain_id=self.chain_id,
        )

    async def poll_until_resolved(self, interval: float = 2.0, timeout: float = 300.0) -> HandoffResult:
        """Poll until the handoff reaches a terminal state."""
        elapsed = 0.0
        while elapsed < timeout:
            await self.refresh()
            if self.status in (HandoffStatus.COMPLETED, HandoffStatus.FAILED, HandoffStatus.ROLLED_BACK):
                return HandoffResult(
                    id=self.id,
                    status=self.status,
                    result=self.result,
                    chain_id=self.chain_id,
                )
            await asyncio.sleep(interval)
            elapsed += interval
        raise TimeoutError(f"Handoff {self.id} did not resolve within {timeout}s")

    def _update_from_response(self, data: dict[str, Any]) -> None:
        self.status = HandoffStatus(data.get("status", self.status))
        self.result = data.get("result", self.result)
        if self.status in (HandoffStatus.COMPLETED, HandoffStatus.FAILED, HandoffStatus.ROLLED_BACK):
            self._resolved.set()

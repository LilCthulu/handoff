"""HandoffAgent — the main entry point for the SDK.

Three lines to join the network:

    agent = HandoffAgent(name="my-bot", server="https://handoff.example.com")
    agent.register(capabilities=[{"domain": "travel", "actions": ["search", "book"]}])
    agent.listen()

That's it. You're in.
"""

import asyncio
from typing import Any, Callable
from pathlib import Path

from handoff_sdk.client import HandoffClient
from handoff_sdk.crypto import load_or_generate_keys, sign_message
from handoff_sdk.handoff import HandoffSession
from handoff_sdk.intent import Intent
from handoff_sdk.negotiation import NegotiationSession
from handoff_sdk.types import AgentProfile, Capability


class HandoffAgent:
    """The main SDK class — an agent in the Handoff network.

    Handles registration, discovery, negotiation, handoff, and
    real-time WebSocket communication with the server.
    """

    def __init__(
        self,
        name: str,
        server: str,
        private_key_path: str | Path | None = None,
    ) -> None:
        self.name = name
        self._client = HandoffClient(server)
        self._private_key, self._public_key = load_or_generate_keys(private_key_path)
        self._agent_id: str | None = None
        self._token: str | None = None
        self._owner_id: str | None = None
        self._capabilities: list[dict[str, Any]] = []
        self._event_handlers: dict[str, list[Callable]] = {}
        self._negotiations: dict[str, NegotiationSession] = {}
        self._listening = False

    @property
    def agent_id(self) -> str | None:
        return self._agent_id

    @property
    def public_key(self) -> str:
        return self._public_key

    @property
    def is_registered(self) -> bool:
        return self._agent_id is not None

    # --- Registration & Auth ---

    async def register(
        self,
        capabilities: list[dict[str, Any] | Capability] | None = None,
        owner_id: str = "default",
        max_authority: dict[str, Any] | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentProfile:
        """Register this agent with the Handoff server.

        Returns:
            The agent's profile as returned by the server.
        """
        caps = []
        for c in (capabilities or []):
            if isinstance(c, Capability):
                caps.append(c.to_dict())
            else:
                caps.append(c)

        self._capabilities = caps

        data = {
            "name": self.name,
            "owner_id": owner_id,
            "public_key": self._public_key,
            "capabilities": caps,
            "max_authority": max_authority or {},
            "metadata": metadata or {},
        }
        if description:
            data["description"] = description

        result = await self._client.post("/api/v1/agents/register", data, auth=False)

        agent_data = result["agent"]
        self._agent_id = str(agent_data["id"])
        self._token = result["token"]
        self._owner_id = owner_id
        self._client.set_token(self._token)

        # Wire up auto-renewal: client calls authenticate() on 401
        self._client._reauth_fn = self.authenticate

        return AgentProfile(**agent_data)

    async def authenticate(self) -> str:
        """Re-authenticate via Ed25519 challenge-response and get a fresh token.

        Flow:
        1. Request a challenge nonce from the server
        2. Sign the challenge with our private key
        3. Submit signature to prove key possession
        4. Receive a fresh JWT
        """
        if not self._agent_id:
            raise RuntimeError("Agent not registered. Call register() first.")

        import base64
        import nacl.signing

        # Step 1: Get challenge
        challenge_resp = await self._client.post(
            "/api/v1/agents/challenge",
            {"agent_id": self._agent_id},
            auth=False,
        )
        challenge = challenge_resp["challenge"]

        # Step 2: Sign the challenge
        private_raw = base64.b64decode(self._private_key)
        signing_key = nacl.signing.SigningKey(private_raw)
        signed = signing_key.sign(challenge.encode("utf-8"))
        signature_b64 = base64.b64encode(signed.signature).decode()

        # Step 3: Authenticate with signed challenge
        result = await self._client.post(
            "/api/v1/agents/authenticate",
            {
                "agent_id": self._agent_id,
                "challenge": challenge,
                "signature": signature_b64,
            },
            auth=False,
        )
        self._token = result["token"]
        self._client.set_token(self._token)
        return self._token

    # --- Discovery ---

    async def discover(
        self,
        domain: str | None = None,
        action: str | None = None,
        min_trust: float = 0.0,
        limit: int = 20,
    ) -> list[AgentProfile]:
        """Discover other agents by capability and trust score."""
        params: dict[str, Any] = {"limit": limit}
        if domain:
            params["domain"] = domain
        if action:
            params["action"] = action
        if min_trust > 0:
            params["min_trust"] = min_trust

        results = await self._client.get("/api/v1/discover", params=params)
        return [AgentProfile(**a) for a in results]

    # --- Negotiation ---

    async def negotiate(
        self,
        target: AgentProfile | str,
        intent: Intent | dict[str, Any],
        max_rounds: int = 10,
        timeout_minutes: int | None = None,
    ) -> NegotiationSession:
        """Start a negotiation with another agent.

        Args:
            target: The agent to negotiate with (AgentProfile or agent_id string).
            intent: What this agent wants (Intent builder or raw dict).
            max_rounds: Maximum negotiation rounds.
            timeout_minutes: Optional timeout.

        Returns:
            A NegotiationSession for managing the negotiation.
        """
        target_id = target.id if isinstance(target, AgentProfile) else target
        intent_data = intent.to_dict() if isinstance(intent, Intent) else intent

        data = {
            "responder_id": target_id,
            "intent": intent_data,
            "max_rounds": max_rounds,
        }
        if timeout_minutes:
            data["timeout_minutes"] = timeout_minutes

        result = await self._client.post("/api/v1/negotiations", data)
        session = NegotiationSession(self._client, str(result["id"]), self._agent_id)
        session._update_from_response(result)
        self._negotiations[session.id] = session

        # Wire up WebSocket events if connected
        if self._listening:
            self._client.on_ws_message("negotiate.offer_received", session._handle_ws_offer)
            self._client.on_ws_message("negotiate.accepted", session._handle_ws_accepted)
            self._client.on_ws_message("negotiate.rejected", session._handle_ws_rejected)
            await self._client.ws_send({"type": "room.join", "room": f"negotiation:{session.id}"})

        return session

    # --- Handoff ---

    async def handoff(
        self,
        to: AgentProfile | str,
        context: dict[str, Any],
        negotiation_id: str | None = None,
        timeout_minutes: int | None = None,
        rollback_on_failure: bool = False,
        chain_id: str | None = None,
    ) -> HandoffSession:
        """Initiate a handoff to another agent.

        Args:
            to: The receiving agent.
            context: The work context to transfer.
            negotiation_id: Optional linked negotiation.
            timeout_minutes: Optional timeout.
            rollback_on_failure: Whether to rollback on failure.
            chain_id: Optional chain ID for multi-hop handoffs.

        Returns:
            A HandoffSession for tracking the handoff.
        """
        to_id = to.id if isinstance(to, AgentProfile) else to

        if rollback_on_failure:
            context.setdefault("constraints", {})["rollback_on_failure"] = True

        data: dict[str, Any] = {
            "to_agent_id": to_id,
            "context": context,
        }
        if negotiation_id:
            data["negotiation_id"] = negotiation_id
        if timeout_minutes:
            data["timeout_minutes"] = timeout_minutes
        if chain_id:
            data["chain_id"] = chain_id

        result = await self._client.post("/api/v1/handoffs", data)
        return HandoffSession(self._client, result)

    # --- Event system ---

    def on(self, event: str, handler: Callable | None = None) -> Callable:
        """Register an event handler (can be used as decorator).

        Events: offer_received, accepted, rejected, handoff_received, error
        """
        def decorator(fn: Callable) -> Callable:
            if event not in self._event_handlers:
                self._event_handlers[event] = []
            self._event_handlers[event].append(fn)
            return fn

        if handler:
            return decorator(handler)
        return decorator

    async def _dispatch_event(self, event: str, data: Any) -> None:
        """Dispatch an event to all registered handlers."""
        import structlog
        _logger = structlog.get_logger()
        for handler in self._event_handlers.get(event, []):
            try:
                result = handler(data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                _logger.exception(
                    "event_handler_error",
                    event=event,
                    handler=getattr(handler, "__name__", repr(handler)),
                )

    # --- Listening ---

    async def listen(self) -> None:
        """Connect via WebSocket and start listening for events.

        This is the third line in the 3-line integration:
            agent = HandoffAgent(...)
            agent.register(...)
            agent.listen()  # blocks, listening for incoming negotiations/handoffs
        """
        if not self._agent_id:
            raise RuntimeError("Agent not registered. Call register() first.")

        await self._client.connect_ws()
        self._listening = True

        # Register global WebSocket handlers
        self._client.on_ws_message("negotiate.offer_received", self._on_ws_offer)
        self._client.on_ws_message("negotiate.accepted", self._on_ws_accepted)
        self._client.on_ws_message("negotiate.rejected", self._on_ws_rejected)
        self._client.on_ws_message("error", self._on_ws_error)

    async def stop(self) -> None:
        """Stop listening and disconnect."""
        self._listening = False
        await self._client.disconnect_ws()

    async def close(self) -> None:
        """Close all connections and clean up."""
        await self.stop()
        await self._client.close()

    # --- Internal WS handlers ---

    def _on_ws_offer(self, data: dict[str, Any]) -> None:
        neg_id = data.get("negotiation_id", "")
        session = self._negotiations.get(neg_id)
        if session:
            session._handle_ws_offer(data)
        asyncio.create_task(self._dispatch_event("offer_received", data))

    def _on_ws_accepted(self, data: dict[str, Any]) -> None:
        neg_id = data.get("negotiation_id", "")
        session = self._negotiations.get(neg_id)
        if session:
            session._handle_ws_accepted(data)
        asyncio.create_task(self._dispatch_event("accepted", data))

    def _on_ws_rejected(self, data: dict[str, Any]) -> None:
        neg_id = data.get("negotiation_id", "")
        session = self._negotiations.get(neg_id)
        if session:
            session._handle_ws_rejected(data)
        asyncio.create_task(self._dispatch_event("rejected", data))

    def _on_ws_error(self, data: dict[str, Any]) -> None:
        asyncio.create_task(self._dispatch_event("error", data))

    # --- Signing ---

    def sign(self, payload: dict[str, Any], recipient_id: str | None = None) -> dict[str, Any]:
        """Sign a payload with this agent's private key."""
        if not self._agent_id:
            raise RuntimeError("Agent not registered")
        return sign_message(
            payload, self._private_key, self._agent_id, self._public_key, recipient_id
        )

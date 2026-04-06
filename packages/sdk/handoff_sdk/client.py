"""HTTP + WebSocket client wrapper.

The transport layer — handles REST calls, WebSocket connections,
token management, and automatic reconnection. Every network
operation flows through here.
"""

import asyncio
import json
from typing import Any, Callable

import httpx
import websockets
import structlog

logger = structlog.get_logger()


class HandoffClient:
    """Async HTTP + WebSocket client for the Handoff server."""

    def __init__(self, server: str, timeout: float = 30.0) -> None:
        self._server = server.rstrip("/")
        self._timeout = timeout
        self._token: str | None = None
        self._http: httpx.AsyncClient | None = None
        self._ws: Any = None
        self._ws_task: asyncio.Task | None = None
        self._ws_handlers: dict[str, list[Callable]] = {}
        self._ws_connected = asyncio.Event()
        self._reauth_fn: Callable | None = None  # Set by HandoffAgent for auto-renewal
        self._reauth_in_progress = False

    @property
    def base_url(self) -> str:
        return self._server

    def set_token(self, token: str) -> None:
        """Set the JWT token for authenticated requests."""
        self._token = token

    def _auth_headers(self) -> dict[str, str]:
        if not self._token:
            return {}
        return {"Authorization": f"Bearer {self._token}"}

    async def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self._server,
                timeout=self._timeout,
                headers={"Content-Type": "application/json"},
            )
        return self._http

    # --- HTTP methods ---

    async def _maybe_reauth(self) -> bool:
        """Attempt to re-authenticate if a reauth function is configured.

        Returns True if re-authentication succeeded.
        """
        if not self._reauth_fn or self._reauth_in_progress:
            return False
        self._reauth_in_progress = True
        try:
            await self._reauth_fn()
            return True
        except Exception:
            logger.warning("auto_reauth_failed")
            return False
        finally:
            self._reauth_in_progress = False

    async def post(self, path: str, data: dict[str, Any] | None = None, auth: bool = True) -> dict[str, Any]:
        """Send an authenticated POST request. Auto-renews token on 401."""
        client = await self._ensure_http()
        headers = self._auth_headers() if auth else {}
        resp = await client.post(path, json=data, headers=headers)
        if resp.status_code == 401 and auth and await self._maybe_reauth():
            headers = self._auth_headers()
            resp = await client.post(path, json=data, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def get(self, path: str, params: dict[str, Any] | None = None, auth: bool = True) -> dict[str, Any] | list:
        """Send an authenticated GET request. Auto-renews token on 401."""
        client = await self._ensure_http()
        headers = self._auth_headers() if auth else {}
        resp = await client.get(path, params=params, headers=headers)
        if resp.status_code == 401 and auth and await self._maybe_reauth():
            headers = self._auth_headers()
            resp = await client.get(path, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def patch(self, path: str, data: dict[str, Any] | None = None, auth: bool = True) -> dict[str, Any]:
        """Send an authenticated PATCH request. Auto-renews token on 401."""
        client = await self._ensure_http()
        headers = self._auth_headers() if auth else {}
        resp = await client.patch(path, json=data, headers=headers)
        if resp.status_code == 401 and auth and await self._maybe_reauth():
            headers = self._auth_headers()
            resp = await client.patch(path, json=data, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def delete(self, path: str, auth: bool = True) -> None:
        """Send an authenticated DELETE request. Auto-renews token on 401."""
        client = await self._ensure_http()
        headers = self._auth_headers() if auth else {}
        resp = await client.delete(path, headers=headers)
        if resp.status_code == 401 and auth and await self._maybe_reauth():
            headers = self._auth_headers()
            resp = await client.delete(path, headers=headers)
        resp.raise_for_status()

    # --- WebSocket ---

    async def connect_ws(self) -> None:
        """Establish a WebSocket connection to the server."""
        if not self._token:
            raise RuntimeError("Cannot connect WebSocket without a token. Register or authenticate first.")

        ws_url = self._server.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/ws/{self._token}"

        self._ws = await websockets.connect(ws_url)
        self._ws_connected.set()
        self._ws_task = asyncio.create_task(self._ws_listen())
        logger.info("ws_connected", server=self._server)

    async def disconnect_ws(self) -> None:
        """Close the WebSocket connection."""
        if self._ws_task:
            self._ws_task.cancel()
            self._ws_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._ws_connected.clear()

    async def ws_send(self, message: dict[str, Any]) -> None:
        """Send a message over WebSocket."""
        if not self._ws:
            raise RuntimeError("WebSocket not connected")
        await self._ws.send(json.dumps(message))

    def on_ws_message(self, msg_type: str, handler: Callable) -> None:
        """Register a handler for a specific WebSocket message type."""
        if msg_type not in self._ws_handlers:
            self._ws_handlers[msg_type] = []
        self._ws_handlers[msg_type].append(handler)

    async def _ws_listen(self) -> None:
        """Listen for incoming WebSocket messages and dispatch to handlers.

        Auto-reconnects on connection loss with exponential backoff.
        """
        backoff = 1.0
        max_backoff = 30.0

        while True:
            try:
                async for raw in self._ws:
                    backoff = 1.0  # Reset backoff on successful message
                    try:
                        data = json.loads(raw)
                        msg_type = data.get("type", "")

                        handlers = self._ws_handlers.get(msg_type, [])
                        # Also dispatch to wildcard handlers
                        handlers += self._ws_handlers.get("*", [])

                        for handler in handlers:
                            try:
                                result = handler(data)
                                if asyncio.iscoroutine(result):
                                    await result
                            except Exception:
                                logger.exception("ws_handler_error", msg_type=msg_type)

                    except json.JSONDecodeError:
                        logger.warning("ws_invalid_json", raw=raw[:200])

            except websockets.ConnectionClosed:
                logger.info("ws_connection_closed")
            except asyncio.CancelledError:
                self._ws_connected.clear()
                return  # Cancelled = intentional disconnect, don't reconnect
            except Exception:
                logger.exception("ws_listen_error")

            # Reconnect
            self._ws_connected.clear()
            if not self._token:
                return  # Can't reconnect without a token

            logger.info("ws_reconnecting", backoff=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

            try:
                ws_url = self._server.replace("http://", "ws://").replace("https://", "wss://")
                ws_url = f"{ws_url}/ws/{self._token}"
                self._ws = await websockets.connect(ws_url)
                self._ws_connected.set()
                logger.info("ws_reconnected")
            except Exception:
                logger.warning("ws_reconnect_failed")
                continue

    # --- Lifecycle ---

    async def close(self) -> None:
        """Close all connections."""
        await self.disconnect_ws()
        if self._http:
            await self._http.aclose()
            self._http = None

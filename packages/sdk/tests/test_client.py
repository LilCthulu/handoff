"""Tests for SDK HTTP + WebSocket client."""

import asyncio
import json

import pytest
import httpx

from handoff_sdk.client import HandoffClient


class TestHandoffClient:
    def test_init(self):
        client = HandoffClient("http://localhost:8000")
        assert client.base_url == "http://localhost:8000"

    def test_init_strips_trailing_slash(self):
        client = HandoffClient("http://localhost:8000/")
        assert client.base_url == "http://localhost:8000"

    def test_set_token(self):
        client = HandoffClient("http://localhost:8000")
        assert client._token is None
        client.set_token("test-token")
        assert client._token == "test-token"

    def test_auth_headers_without_token(self):
        client = HandoffClient("http://localhost:8000")
        assert client._auth_headers() == {}

    def test_auth_headers_with_token(self):
        client = HandoffClient("http://localhost:8000")
        client.set_token("my-jwt")
        assert client._auth_headers() == {"Authorization": "Bearer my-jwt"}


class TestClientHTTP:
    @pytest.fixture
    def client(self):
        return HandoffClient("http://localhost:8000")

    @pytest.mark.asyncio
    async def test_post_sends_json(self, client):
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json={"ok": True})
        )
        client._http = httpx.AsyncClient(
            base_url="http://localhost:8000",
            transport=transport,
        )
        result = await client.post("/test", {"key": "value"}, auth=False)
        assert result == {"ok": True}
        await client.close()

    @pytest.mark.asyncio
    async def test_get_sends_params(self, client):
        def handler(req: httpx.Request) -> httpx.Response:
            assert b"domain=travel" in req.url.raw_path
            return httpx.Response(200, json=[{"id": "1"}])

        transport = httpx.MockTransport(handler)
        client._http = httpx.AsyncClient(
            base_url="http://localhost:8000",
            transport=transport,
        )
        result = await client.get("/discover", params={"domain": "travel"}, auth=False)
        assert result == [{"id": "1"}]
        await client.close()

    @pytest.mark.asyncio
    async def test_post_raises_on_error(self, client):
        transport = httpx.MockTransport(
            lambda req: httpx.Response(500, json={"detail": "error"})
        )
        client._http = httpx.AsyncClient(
            base_url="http://localhost:8000",
            transport=transport,
        )
        with pytest.raises(httpx.HTTPStatusError):
            await client.post("/fail", {"x": 1}, auth=False)
        await client.close()

    @pytest.mark.asyncio
    async def test_auto_reauth_on_401(self, client):
        call_count = 0

        def handler(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(401, json={"detail": "expired"})
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        client._http = httpx.AsyncClient(
            base_url="http://localhost:8000",
            transport=transport,
        )
        client.set_token("old-token")

        reauth_called = False

        async def mock_reauth():
            nonlocal reauth_called
            reauth_called = True
            client.set_token("new-token")

        client._reauth_fn = mock_reauth

        result = await client.post("/test", {"x": 1})
        assert result == {"ok": True}
        assert reauth_called
        assert call_count == 2
        await client.close()


class TestClientWebSocket:
    @pytest.mark.asyncio
    async def test_ws_connect_requires_token(self):
        client = HandoffClient("http://localhost:8000")
        with pytest.raises(RuntimeError, match="Cannot connect WebSocket"):
            await client.connect_ws()

    @pytest.mark.asyncio
    async def test_ws_send_requires_connection(self):
        client = HandoffClient("http://localhost:8000")
        with pytest.raises(RuntimeError, match="WebSocket not connected"):
            await client.ws_send({"type": "test"})

    def test_on_ws_message_registers_handler(self):
        client = HandoffClient("http://localhost:8000")
        handler = lambda data: None
        client.on_ws_message("test.event", handler)
        assert "test.event" in client._ws_handlers
        assert handler in client._ws_handlers["test.event"]

    def test_multiple_handlers_per_type(self):
        client = HandoffClient("http://localhost:8000")
        h1 = lambda d: None
        h2 = lambda d: None
        client.on_ws_message("event", h1)
        client.on_ws_message("event", h2)
        assert len(client._ws_handlers["event"]) == 2

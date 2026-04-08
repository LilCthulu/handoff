"""WebSocket connection manager — room-based pub/sub for real-time agent comms.

Every negotiation is a room. Every agent in that room hears every message.
The manager tracks who's connected, who's in which room, and ensures
messages reach the right eyes at the right time.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import WebSocket

logger = structlog.get_logger()


@dataclass
class AgentConnection:
    """A single agent's WebSocket connection and metadata."""

    agent_id: uuid.UUID
    websocket: WebSocket
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    rooms: set[str] = field(default_factory=set)
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ConnectionManager:
    """Manages WebSocket connections, rooms, and message routing.

    Thread-safe via asyncio — all mutations happen in the event loop.
    When Redis is available, broadcasts are published to a shared channel
    so all server instances can relay messages to their local connections.
    """

    def __init__(self) -> None:
        # agent_id -> AgentConnection
        self._connections: dict[uuid.UUID, AgentConnection] = {}
        # room_name -> set of agent_ids
        self._rooms: dict[str, set[uuid.UUID]] = {}
        # Redis pub/sub for cross-instance fan-out
        self._pubsub: Any = None
        self._instance_id: str = uuid.uuid4().hex[:8]

    async def init_pubsub(self) -> None:
        """Initialize Redis pub/sub for cross-instance broadcasting.

        Call this during app startup. If Redis is unavailable, this is a no-op.
        """
        try:
            from app.redis import get_redis, RedisPubSub
            redis = await get_redis()
            if not redis:
                return
            self._pubsub = RedisPubSub(redis)
            await self._pubsub.subscribe("broadcast", self._on_redis_message)
            asyncio.create_task(self._pubsub.listen())
            logger.info("ws_pubsub_initialized", instance=self._instance_id)
        except Exception as exc:
            logger.warning("ws_pubsub_init_failed", error=str(exc))

    async def close_pubsub(self) -> None:
        """Shut down Redis pub/sub. Call during app shutdown."""
        if self._pubsub:
            await self._pubsub.close()
            self._pubsub = None

    async def _on_redis_message(self, data: dict[str, Any]) -> None:
        """Handle broadcast messages from other server instances."""
        if data.get("instance") == self._instance_id:
            return  # Skip our own messages

        msg_type = data.get("type")
        if msg_type == "room":
            room = data.get("room", "")
            message = data.get("message", {})
            exclude_str = data.get("exclude")
            exclude = uuid.UUID(exclude_str) if exclude_str else None
            await self._local_broadcast_to_room(room, message, exclude)
        elif msg_type == "agent":
            try:
                agent_id = uuid.UUID(data["agent_id"])
                await self.send_to_agent(agent_id, data.get("message", {}))
            except (KeyError, ValueError):
                pass
        elif msg_type == "all":
            await self._local_broadcast_all(data.get("message", {}))

    @property
    def active_connections(self) -> int:
        return len(self._connections)

    async def connect(self, agent_id: uuid.UUID, websocket: WebSocket) -> AgentConnection:
        """Accept a WebSocket connection and register the agent."""
        await websocket.accept()

        # If agent already connected, close old connection
        if agent_id in self._connections:
            old = self._connections[agent_id]
            try:
                await old.websocket.close(code=1000, reason="Reconnected from another session")
            except Exception:
                pass
            self._remove_from_all_rooms(agent_id)

        conn = AgentConnection(agent_id=agent_id, websocket=websocket)
        self._connections[agent_id] = conn

        logger.info("ws_connected", agent_id=str(agent_id), active=self.active_connections)
        return conn

    async def disconnect(self, agent_id: uuid.UUID) -> None:
        """Remove an agent's connection and clean up rooms."""
        if agent_id in self._connections:
            self._remove_from_all_rooms(agent_id)
            del self._connections[agent_id]
            logger.info("ws_disconnected", agent_id=str(agent_id), active=self.active_connections)

    def join_room(self, agent_id: uuid.UUID, room: str) -> None:
        """Add an agent to a room (e.g., a negotiation session)."""
        if room not in self._rooms:
            self._rooms[room] = set()
        self._rooms[room].add(agent_id)

        if agent_id in self._connections:
            self._connections[agent_id].rooms.add(room)

        logger.debug("ws_room_joined", agent_id=str(agent_id), room=room)

    def leave_room(self, agent_id: uuid.UUID, room: str) -> None:
        """Remove an agent from a room."""
        if room in self._rooms:
            self._rooms[room].discard(agent_id)
            if not self._rooms[room]:
                del self._rooms[room]

        if agent_id in self._connections:
            self._connections[agent_id].rooms.discard(room)

    async def send_to_agent(self, agent_id: uuid.UUID, message: dict[str, Any]) -> bool:
        """Send a message directly to a specific agent.

        Returns True if sent, False if agent not connected.
        """
        conn = self._connections.get(agent_id)
        if not conn:
            return False

        try:
            await conn.websocket.send_json(message)
            return True
        except Exception:
            logger.warning("ws_send_failed", agent_id=str(agent_id))
            await self.disconnect(agent_id)
            return False

    async def _local_broadcast_to_room(
        self,
        room: str,
        message: dict[str, Any],
        exclude: uuid.UUID | None = None,
    ) -> int:
        """Broadcast to local WebSocket connections only (no Redis publish)."""
        agent_ids = self._rooms.get(room, set()).copy()
        if exclude:
            agent_ids.discard(exclude)

        sent = 0
        failed: list[uuid.UUID] = []

        for agent_id in agent_ids:
            success = await self.send_to_agent(agent_id, message)
            if success:
                sent += 1
            else:
                failed.append(agent_id)

        for aid in failed:
            await self.disconnect(aid)

        return sent

    async def broadcast_to_room(
        self,
        room: str,
        message: dict[str, Any],
        exclude: uuid.UUID | None = None,
    ) -> int:
        """Broadcast a message to all agents in a room, across all instances.

        Args:
            room: The room name (typically a negotiation ID).
            message: JSON-serializable message.
            exclude: Optional agent_id to exclude (e.g., the sender).

        Returns:
            Number of agents the message was sent to on this instance.
        """
        sent = await self._local_broadcast_to_room(room, message, exclude)

        # Fan out to other instances via Redis
        if self._pubsub:
            try:
                await self._pubsub.publish("broadcast", {
                    "type": "room",
                    "instance": self._instance_id,
                    "room": room,
                    "message": message,
                    "exclude": str(exclude) if exclude else None,
                })
            except Exception as exc:
                logger.warning("ws_pubsub_publish_failed", error=str(exc))

        return sent

    async def _local_broadcast_all(self, message: dict[str, Any]) -> int:
        """Broadcast to all local connections only (no Redis publish)."""
        sent = 0
        failed: list[uuid.UUID] = []

        for agent_id in list(self._connections.keys()):
            success = await self.send_to_agent(agent_id, message)
            if success:
                sent += 1
            else:
                failed.append(agent_id)

        for aid in failed:
            await self.disconnect(aid)

        return sent

    async def broadcast_all(self, message: dict[str, Any]) -> int:
        """Broadcast a message to every connected agent, across all instances."""
        sent = await self._local_broadcast_all(message)

        if self._pubsub:
            try:
                await self._pubsub.publish("broadcast", {
                    "type": "all",
                    "instance": self._instance_id,
                    "message": message,
                })
            except Exception as exc:
                logger.warning("ws_pubsub_publish_failed", error=str(exc))

        return sent

    def update_heartbeat(self, agent_id: uuid.UUID) -> None:
        """Update the last heartbeat timestamp for an agent."""
        conn = self._connections.get(agent_id)
        if conn:
            conn.last_heartbeat = datetime.now(timezone.utc)

    def get_room_members(self, room: str) -> set[uuid.UUID]:
        """Get all agent IDs in a room."""
        return self._rooms.get(room, set()).copy()

    def get_agent_rooms(self, agent_id: uuid.UUID) -> set[str]:
        """Get all rooms an agent is in."""
        conn = self._connections.get(agent_id)
        return conn.rooms.copy() if conn else set()

    def is_connected(self, agent_id: uuid.UUID) -> bool:
        """Check if an agent is currently connected."""
        return agent_id in self._connections

    def _remove_from_all_rooms(self, agent_id: uuid.UUID) -> None:
        """Remove an agent from every room they're in."""
        conn = self._connections.get(agent_id)
        if not conn:
            return

        for room in list(conn.rooms):
            if room in self._rooms:
                self._rooms[room].discard(agent_id)
                if not self._rooms[room]:
                    del self._rooms[room]
        conn.rooms.clear()


# Singleton — one manager for the entire server process
manager = ConnectionManager()

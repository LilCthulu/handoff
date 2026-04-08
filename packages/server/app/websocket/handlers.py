"""WebSocket message handlers — real-time negotiation, handoff, and heartbeat.

Messages arrive as JSON, are validated, dispatched to the right handler,
and broadcast to the right rooms. The WebSocket layer is the nervous
system — REST is memory, WebSocket is reflex.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthError, decode_token
from app.core.negotiation_engine import (
    NegotiationError,
    NegotiationState,
    accept_offer,
    reject_negotiation,
    submit_offer,
)
from app.core.negotiation_helpers import (
    apply_dict_to_negotiation,
    negotiation_to_dict,
)
from app.database import get_db, async_session
from app.models.audit import AuditLog
from app.models.negotiation import Negotiation
from app.websocket.manager import manager

logger = structlog.get_logger()

router = APIRouter()


@router.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str) -> None:
    """Main WebSocket endpoint. Agents connect with their JWT in the URL path.

    Message protocol:
        Client -> Server:
            { "type": "negotiate.offer", "negotiation_id": "...", "offer": {...} }
            { "type": "negotiate.accept", "negotiation_id": "..." }
            { "type": "negotiate.reject", "negotiation_id": "...", "reason": "..." }
            { "type": "negotiate.counter", "negotiation_id": "...", "offer": {...} }
            { "type": "room.join", "room": "..." }
            { "type": "room.leave", "room": "..." }
            { "type": "heartbeat" }

        Server -> Client:
            { "type": "negotiate.offer_received", ... }
            { "type": "negotiate.accepted", ... }
            { "type": "negotiate.rejected", ... }
            { "type": "negotiate.timeout", ... }
            { "type": "error", "detail": "..." }
            { "type": "heartbeat.ack" }
    """
    # Authenticate
    try:
        claims = decode_token(token)
        agent_id = uuid.UUID(claims["sub"])
    except (AuthError, KeyError, ValueError) as e:
        await websocket.close(code=4001, reason=f"Authentication failed: {e}")
        return

    conn = await manager.connect(agent_id, websocket)

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            try:
                if msg_type == "heartbeat":
                    await _handle_heartbeat(agent_id)
                elif msg_type == "room.join":
                    await _handle_room_join(agent_id, data)
                elif msg_type == "room.leave":
                    _handle_room_leave(agent_id, data)
                elif msg_type.startswith("negotiate."):
                    await _handle_negotiation(agent_id, msg_type, data)
                elif msg_type == "handoff.progress":
                    from app.api.progress import handle_ws_progress
                    await handle_ws_progress(agent_id, data)
                else:
                    await manager.send_to_agent(agent_id, {
                        "type": "error",
                        "detail": f"Unknown message type: {msg_type}",
                    })
            except Exception:
                logger.exception("ws_handler_error", agent_id=str(agent_id), msg_type=msg_type)
                await manager.send_to_agent(agent_id, {
                    "type": "error",
                    "detail": "Internal error processing message",
                })

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("ws_unexpected_error", agent_id=str(agent_id))
    finally:
        await manager.disconnect(agent_id)


# --- Heartbeat ---

async def _handle_heartbeat(agent_id: uuid.UUID) -> None:
    """Respond to heartbeat with ack."""
    manager.update_heartbeat(agent_id)
    await manager.send_to_agent(agent_id, {
        "type": "heartbeat.ack",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# --- Room management ---

async def _handle_room_join(agent_id: uuid.UUID, data: dict[str, Any]) -> None:
    """Join a room. Validates that the agent is a participant in the underlying entity."""
    room = data.get("room", "")
    if not room:
        return

    # Validate participation for entity-scoped rooms
    if room.startswith("negotiation:") or room.startswith("handoff:"):
        entity_type, _, entity_id_str = room.partition(":")
        try:
            entity_id = uuid.UUID(entity_id_str)
        except ValueError:
            await manager.send_to_agent(agent_id, {"type": "error", "detail": "Invalid room ID"})
            return

        async with async_session() as db:
            if entity_type == "negotiation":
                neg = await _get_negotiation(db, entity_id)
                if not neg or agent_id not in (neg.initiator_id, neg.responder_id):
                    await manager.send_to_agent(agent_id, {"type": "error", "detail": "Not a participant in this negotiation"})
                    return
            elif entity_type == "handoff":
                from app.models.handoff import Handoff
                result = await db.execute(select(Handoff).where(Handoff.id == entity_id))
                handoff = result.scalar_one_or_none()
                if not handoff or agent_id not in (handoff.from_agent_id, handoff.to_agent_id):
                    await manager.send_to_agent(agent_id, {"type": "error", "detail": "Not a participant in this handoff"})
                    return

    manager.join_room(agent_id, room)


def _handle_room_leave(agent_id: uuid.UUID, data: dict[str, Any]) -> None:
    """Leave a room."""
    room = data.get("room", "")
    if room:
        manager.leave_room(agent_id, room)


# --- Negotiation handlers ---

async def _handle_negotiation(agent_id: uuid.UUID, msg_type: str, data: dict[str, Any]) -> None:
    """Route negotiation messages to the appropriate handler."""
    negotiation_id_str = data.get("negotiation_id")
    if not negotiation_id_str:
        await manager.send_to_agent(agent_id, {
            "type": "error",
            "detail": "Missing negotiation_id",
        })
        return

    try:
        negotiation_id = uuid.UUID(negotiation_id_str)
    except ValueError:
        await manager.send_to_agent(agent_id, {
            "type": "error",
            "detail": "Invalid negotiation_id",
        })
        return

    # Auto-join the negotiation room
    room = f"negotiation:{negotiation_id}"
    manager.join_room(agent_id, room)

    if msg_type == "negotiate.offer" or msg_type == "negotiate.counter":
        await _handle_offer(agent_id, negotiation_id, data, room)
    elif msg_type == "negotiate.accept":
        await _handle_accept(agent_id, negotiation_id, room)
    elif msg_type == "negotiate.reject":
        await _handle_reject(agent_id, negotiation_id, data, room)
    else:
        await manager.send_to_agent(agent_id, {
            "type": "error",
            "detail": f"Unknown negotiation message type: {msg_type}",
        })


async def _handle_offer(
    agent_id: uuid.UUID,
    negotiation_id: uuid.UUID,
    data: dict[str, Any],
    room: str,
) -> None:
    """Process an offer/counteroffer via WebSocket."""
    offer_data = data.get("offer", {})
    terms = offer_data.get("terms", {})
    concessions = offer_data.get("concessions", [])
    conditions = offer_data.get("conditions", [])

    if not terms:
        await manager.send_to_agent(agent_id, {
            "type": "error",
            "detail": "Offer must include terms",
        })
        return

    async with async_session() as db:
        negotiation = await _get_negotiation(db, negotiation_id)
        if not negotiation:
            await manager.send_to_agent(agent_id, {
                "type": "error",
                "detail": "Negotiation not found",
            })
            return

        if agent_id not in (negotiation.initiator_id, negotiation.responder_id):
            await manager.send_to_agent(agent_id, {
                "type": "error",
                "detail": "Not a participant in this negotiation",
            })
            return

        try:
            neg_dict = negotiation_to_dict(negotiation)
            neg_dict = submit_offer(
                neg_dict,
                from_agent_id=str(agent_id),
                terms=terms,
                concessions=concessions,
                conditions=conditions,
            )
            apply_dict_to_negotiation(negotiation, neg_dict)

            audit = AuditLog(
                entity_type="negotiation",
                entity_id=negotiation.id,
                action="offer_submitted_ws",
                actor_agent_id=agent_id,
                details={"round": negotiation.current_round, "terms": terms},
            )
            db.add(audit)
            await db.commit()

        except NegotiationError as e:
            await manager.send_to_agent(agent_id, {
                "type": "error",
                "detail": e.detail,
            })
            return

    # Broadcast to room
    await manager.broadcast_to_room(room, {
        "type": "negotiate.offer_received",
        "negotiation_id": str(negotiation_id),
        "from_agent": str(agent_id),
        "round": negotiation.current_round,
        "offer": {"terms": terms, "concessions": concessions, "conditions": conditions},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, exclude=agent_id)

    # Confirm to sender
    await manager.send_to_agent(agent_id, {
        "type": "negotiate.offer_sent",
        "negotiation_id": str(negotiation_id),
        "round": negotiation.current_round,
    })


async def _handle_accept(
    agent_id: uuid.UUID,
    negotiation_id: uuid.UUID,
    room: str,
) -> None:
    """Process an acceptance via WebSocket."""
    async with async_session() as db:
        negotiation = await _get_negotiation(db, negotiation_id)
        if not negotiation:
            await manager.send_to_agent(agent_id, {"type": "error", "detail": "Negotiation not found"})
            return

        if agent_id not in (negotiation.initiator_id, negotiation.responder_id):
            await manager.send_to_agent(agent_id, {"type": "error", "detail": "Not a participant"})
            return

        try:
            neg_dict = negotiation_to_dict(negotiation)
            neg_dict = accept_offer(neg_dict)
            apply_dict_to_negotiation(negotiation, neg_dict)

            audit = AuditLog(
                entity_type="negotiation",
                entity_id=negotiation.id,
                action="accepted_ws",
                actor_agent_id=agent_id,
                details={"agreement": negotiation.agreement},
            )
            db.add(audit)
            await db.commit()

        except NegotiationError as e:
            await manager.send_to_agent(agent_id, {"type": "error", "detail": e.detail})
            return

    # Broadcast acceptance
    await manager.broadcast_to_room(room, {
        "type": "negotiate.accepted",
        "negotiation_id": str(negotiation_id),
        "accepted_by": str(agent_id),
        "agreement": negotiation.agreement,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


async def _handle_reject(
    agent_id: uuid.UUID,
    negotiation_id: uuid.UUID,
    data: dict[str, Any],
    room: str,
) -> None:
    """Process a rejection via WebSocket."""
    reason = data.get("reason")

    async with async_session() as db:
        negotiation = await _get_negotiation(db, negotiation_id)
        if not negotiation:
            await manager.send_to_agent(agent_id, {"type": "error", "detail": "Negotiation not found"})
            return

        if agent_id not in (negotiation.initiator_id, negotiation.responder_id):
            await manager.send_to_agent(agent_id, {"type": "error", "detail": "Not a participant"})
            return

        try:
            neg_dict = negotiation_to_dict(negotiation)
            neg_dict = reject_negotiation(neg_dict, reason=reason)
            apply_dict_to_negotiation(negotiation, neg_dict)

            audit = AuditLog(
                entity_type="negotiation",
                entity_id=negotiation.id,
                action="rejected_ws",
                actor_agent_id=agent_id,
                details={"reason": reason},
            )
            db.add(audit)
            await db.commit()

        except NegotiationError as e:
            await manager.send_to_agent(agent_id, {"type": "error", "detail": e.detail})
            return

    # Broadcast rejection
    await manager.broadcast_to_room(room, {
        "type": "negotiate.rejected",
        "negotiation_id": str(negotiation_id),
        "rejected_by": str(agent_id),
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# --- Helpers ---

async def _get_negotiation(db: AsyncSession, negotiation_id: uuid.UUID) -> Negotiation | None:
    result = await db.execute(select(Negotiation).where(Negotiation.id == negotiation_id))
    return result.scalar_one_or_none()



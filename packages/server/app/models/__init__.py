"""SQLAlchemy ORM models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


# Import all models so Alembic and relationships resolve correctly.
from app.models.agent import Agent  # noqa: E402, F401
from app.models.negotiation import Negotiation  # noqa: E402, F401
from app.models.handoff import Handoff  # noqa: E402, F401
from app.models.audit import AuditLog  # noqa: E402, F401
from app.models.trust import TrustScore, TrustEvent  # noqa: E402, F401
from app.models.capability import CapabilityContract  # noqa: E402, F401
from app.models.attestation import Attestation, CapabilityChallenge  # noqa: E402, F401
from app.models.delivery import DeliveryReceipt  # noqa: E402, F401
from app.models.checkpoint import HandoffCheckpoint  # noqa: E402, F401

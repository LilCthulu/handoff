"""Cross-dialect column types that work on both PostgreSQL and SQLite.

PostgreSQL uses native JSONB and UUID types for performance.
SQLite stores JSON as TEXT and UUIDs as CHAR(36) strings.
"""

import json
import uuid

from sqlalchemy import String, Text, TypeDecorator
from sqlalchemy.types import JSON
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


class GUID(TypeDecorator):
    """UUID type that works on both PostgreSQL (native UUID) and SQLite (CHAR 36)."""

    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(value)
        if isinstance(value, uuid.UUID):
            return str(value)
        return str(uuid.UUID(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


# Use SQLAlchemy's built-in JSON type — it works on both PG (as JSONB) and SQLite (as TEXT).
JSONType = JSON().with_variant(JSON(), "sqlite")

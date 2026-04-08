"""Add cloud extension tables — users, organizations, sessions, API keys,
usage tracking, webhooks, email queue.

These tables are created by the cloud extension (handoff-cloud) and are
only needed when running the proprietary cloud layer. The open-source
server operates without them.

Revision ID: 003
Revises: 002
Create Date: 2026-04-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("email_encrypted", sa.Text(), nullable=False),
        sa.Column("email_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("name_encrypted", sa.Text(), nullable=False),
        sa.Column("email_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("mfa_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("mfa_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("oauth_provider", sa.String(50), nullable=True),
        sa.Column("oauth_provider_id", sa.String(255), nullable=True),
        sa.Column("failed_login_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_ip", sa.String(45), nullable=True),
    )
    op.create_index("idx_users_email_hash", "users", ["email_hash"], unique=True)
    op.create_index("idx_users_oauth", "users", ["oauth_provider", "oauth_provider_id"])

    # --- organizations ---
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("plan", sa.String(50), server_default=sa.text("'starter'"), nullable=False),
        sa.Column("stripe_customer_id", sa.String(255), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(255), nullable=True),
        sa.Column("usage_negotiations", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("usage_handoffs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("usage_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settings", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_orgs_slug", "organizations", ["slug"], unique=True)

    # --- org_memberships ---
    op.create_table(
        "org_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("role", sa.String(50), server_default=sa.text("'viewer'"), nullable=False),
        sa.Column("invited_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invited_email", sa.String(255), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_membership_user_org", "org_memberships", ["user_id", "org_id"], unique=True)
    op.create_index("idx_membership_org", "org_memberships", ["org_id"])

    # --- sessions ---
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_active_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_session_user", "sessions", ["user_id"])
    op.create_index("idx_session_expires", "sessions", ["expires_at"])

    # --- org_api_keys ---
    op.create_table(
        "org_api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("key_prefix", sa.String(11), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_api_key_hash", "org_api_keys", ["key_hash"], unique=True)
    op.create_index("idx_api_key_org", "org_api_keys", ["org_id"])

    # --- usage_records ---
    op.create_table(
        "usage_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("api_calls", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("negotiations", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("handoffs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("agents_registered", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("org_id", "period", name="uq_usage_org_period"),
    )
    op.create_index("idx_usage_org_period", "usage_records", ["org_id", "period"])

    # --- webhooks ---
    op.create_table(
        "webhooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("description", sa.String(255), server_default=sa.text("''"), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("secret_prefix", sa.String(8), nullable=False),
        sa.Column("events", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("failure_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_webhook_org", "webhooks", ["org_id"])

    # --- email_queue ---
    op.create_table(
        "email_queue",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("to_email", sa.String(255), nullable=False),
        sa.Column("template", sa.String(100), nullable=False),
        sa.Column("context", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(50), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_email_status", "email_queue", ["status"])
    op.create_index("idx_email_retry", "email_queue", ["next_retry_at"])


def downgrade() -> None:
    op.drop_table("email_queue")
    op.drop_table("webhooks")
    op.drop_table("usage_records")
    op.drop_table("org_api_keys")
    op.drop_table("sessions")
    op.drop_table("org_memberships")
    op.drop_table("organizations")
    op.drop_table("users")

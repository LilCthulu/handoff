"""Initial schema — agents, negotiations, handoffs, audit_log.

Revision ID: 001
Revises:
Create Date: 2026-04-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- agents ---
    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("trust_score", sa.Float(), server_default=sa.text("0.5"), nullable=False),
        sa.Column("max_authority", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(50), server_default=sa.text("'active'"), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_agents_capabilities", "agents", ["capabilities"], postgresql_using="gin")
    op.create_index("idx_agents_status", "agents", ["status"])
    op.create_index("idx_agents_trust", "agents", [sa.text("trust_score DESC")])

    # --- negotiations ---
    op.create_table(
        "negotiations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("initiator_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("responder_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("mediator_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("state", sa.String(50), server_default=sa.text("'created'"), nullable=False),
        sa.Column("intent", postgresql.JSONB(), nullable=False),
        sa.Column("current_offer", postgresql.JSONB(), nullable=True),
        sa.Column("offer_history", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("agreement", postgresql.JSONB(), nullable=True),
        sa.Column("timeout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_rounds", sa.Integer(), server_default=sa.text("10"), nullable=False),
        sa.Column("current_round", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_negotiations_state", "negotiations", ["state"])
    op.create_index("idx_negotiations_initiator", "negotiations", ["initiator_id"])
    op.create_index("idx_negotiations_responder", "negotiations", ["responder_id"])

    # --- handoffs ---
    op.create_table(
        "handoffs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("negotiation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("negotiations.id"), nullable=True),
        sa.Column("from_agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("to_agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("status", sa.String(50), server_default=sa.text("'initiated'"), nullable=False),
        sa.Column("context", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("chain_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("chain_position", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("parent_handoff_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("handoffs.id"), nullable=True),
        sa.Column("timeout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_handoffs_status", "handoffs", ["status"])
    op.create_index("idx_handoffs_chain", "handoffs", ["chain_id"])
    op.create_index("idx_handoffs_from", "handoffs", ["from_agent_id"])
    op.create_index("idx_handoffs_to", "handoffs", ["to_agent_id"])

    # --- audit_log ---
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("actor_agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("envelope_signature", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_audit_entity", "audit_log", ["entity_type", "entity_id"])
    op.create_index("idx_audit_actor", "audit_log", ["actor_agent_id"])
    op.create_index("idx_audit_time", "audit_log", [sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("handoffs")
    op.drop_table("negotiations")
    op.drop_table("agents")

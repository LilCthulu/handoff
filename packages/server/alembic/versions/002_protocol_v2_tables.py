"""Add protocol v2 tables — trust, capabilities, attestations, delivery,
stakes, credentials, challenges, checkpoints. Also adds org_id to agents.

Revision ID: 002
Revises: 001
Create Date: 2026-04-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- agents: add org_id column ---
    op.add_column("agents", sa.Column("org_id", sa.String(255), nullable=True))

    # --- trust_scores ---
    op.create_table(
        "trust_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("score", sa.Float(), server_default=sa.text("0.5"), nullable=False),
        sa.Column("successful_handoffs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed_handoffs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("total_handoffs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("avg_completion_time_ms", sa.Float(), nullable=True),
        sa.Column("last_updated", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_trust_agent_domain", "trust_scores", ["agent_id", "domain"], unique=True)
    op.create_index("idx_trust_domain_score", "trust_scores", ["domain", "score"])

    # --- trust_events ---
    op.create_table(
        "trust_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("handoff_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("score_delta", sa.Float(), server_default=sa.text("0.0"), nullable=False),
        sa.Column("score_after", sa.Float(), nullable=False),
        sa.Column("completion_time_ms", sa.Float(), nullable=True),
        sa.Column("details", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_trust_events_agent", "trust_events", ["agent_id"])
    op.create_index("idx_trust_events_handoff", "trust_events", ["handoff_id"])

    # --- capability_contracts ---
    op.create_table(
        "capability_contracts",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("action", sa.String(255), nullable=False),
        sa.Column("version", sa.String(50), server_default=sa.text("'1.0.0'"), nullable=False),
        sa.Column("input_schema", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("output_schema", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("max_latency_ms", sa.Integer(), nullable=True),
        sa.Column("availability_target", sa.Float(), nullable=True),
        sa.Column("max_concurrent", sa.Integer(), nullable=True),
        sa.Column("obligations", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("constraints", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("examples", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_cap_agent_domain_action", "capability_contracts", ["agent_id", "domain", "action"])
    op.create_index("idx_cap_domain_action", "capability_contracts", ["domain", "action"])
    op.create_index("idx_cap_active", "capability_contracts", ["is_active"])

    # --- attestations ---
    op.create_table(
        "attestations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("attester_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attester_key_fingerprint", sa.String(100), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("handoff_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("outcome", sa.String(50), nullable=False),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("claim", postgresql.JSONB(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_attestation_subject", "attestations", ["subject_id"])
    op.create_index("idx_attestation_attester", "attestations", ["attester_id"])
    op.create_index("idx_attestation_handoff", "attestations", ["handoff_id"], unique=True)
    op.create_index("idx_attestation_domain", "attestations", ["subject_id", "domain"])

    # --- capability_challenges ---
    op.create_table(
        "capability_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("action", sa.String(255), nullable=False),
        sa.Column("challenge_input", postgresql.JSONB(), nullable=False),
        sa.Column("expected_schema", postgresql.JSONB(), nullable=False),
        sa.Column("max_time_ms", sa.Integer(), server_default=sa.text("5000"), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=True),
        sa.Column("response_time_ms", sa.Float(), nullable=True),
        sa.Column("status", sa.String(50), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("issued_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_challenge_agent", "capability_challenges", ["agent_id"])
    op.create_index("idx_challenge_domain", "capability_challenges", ["agent_id", "domain"])
    op.create_index("idx_challenge_status", "capability_challenges", ["status"])

    # --- delivery_receipts ---
    op.create_table(
        "delivery_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("handoff_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delivered_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delivery_key_fingerprint", sa.String(100), nullable=False),
        sa.Column("result_hash", sa.String(100), nullable=False),
        sa.Column("delivery_signature", sa.Text(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("proof", postgresql.JSONB(), nullable=True),
        sa.Column("acknowledged_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("acknowledgment_key_fingerprint", sa.String(100), nullable=True),
        sa.Column("accepted", sa.Boolean(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("acknowledgment_signature", sa.Text(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("acknowledgment_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_receipt_handoff", "delivery_receipts", ["handoff_id"], unique=True)
    op.create_index("idx_receipt_delivered_by", "delivery_receipts", ["delivered_by"])
    op.create_index("idx_receipt_acknowledged_by", "delivery_receipts", ["acknowledged_by"])

    # --- handoff_checkpoints ---
    op.create_table(
        "handoff_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("handoff_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(255), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_checkpoint_handoff", "handoff_checkpoints", ["handoff_id"])
    op.create_index("idx_checkpoint_handoff_seq", "handoff_checkpoints", ["handoff_id", "sequence"], unique=True)

    # --- agent_stakes ---
    op.create_table(
        "agent_stakes",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("handoff_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(20), server_default=sa.text("'credits'"), nullable=False),
        sa.Column("status", sa.String(50), server_default=sa.text("'posted'"), nullable=False),
        sa.Column("resolution_reason", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conditions", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_stake_agent", "agent_stakes", ["agent_id"])
    op.create_index("idx_stake_handoff", "agent_stakes", ["handoff_id"], unique=True)
    op.create_index("idx_stake_status", "agent_stakes", ["status"])

    # --- agent_balances ---
    op.create_table(
        "agent_balances",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("available", sa.Float(), server_default=sa.text("100.0"), nullable=False),
        sa.Column("staked", sa.Float(), server_default=sa.text("0.0"), nullable=False),
        sa.Column("total_earned", sa.Float(), server_default=sa.text("0.0"), nullable=False),
        sa.Column("total_forfeited", sa.Float(), server_default=sa.text("0.0"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_balance_agent", "agent_balances", ["agent_id"], unique=True)

    # --- third_party_credentials ---
    op.create_table(
        "third_party_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("issuer_id", sa.String(255), nullable=False),
        sa.Column("issuer_name", sa.String(255), nullable=False),
        sa.Column("issuer_key_fingerprint", sa.String(100), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_type", sa.String(100), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("claims", postgresql.JSONB(), nullable=False),
        sa.Column("weight", sa.Float(), server_default=sa.text("1.0"), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("proof_type", sa.String(50), server_default=sa.text("'Ed25519Signature2020'"), nullable=False),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_credential_subject", "third_party_credentials", ["subject_id"])
    op.create_index("idx_credential_issuer", "third_party_credentials", ["issuer_id"])
    op.create_index("idx_credential_domain", "third_party_credentials", ["subject_id", "domain"])
    op.create_index("idx_credential_type", "third_party_credentials", ["credential_type"])


def downgrade() -> None:
    op.drop_table("third_party_credentials")
    op.drop_table("agent_balances")
    op.drop_table("agent_stakes")
    op.drop_table("handoff_checkpoints")
    op.drop_table("delivery_receipts")
    op.drop_table("capability_challenges")
    op.drop_table("attestations")
    op.drop_table("capability_contracts")
    op.drop_table("trust_events")
    op.drop_table("trust_scores")
    op.drop_column("agents", "org_id")

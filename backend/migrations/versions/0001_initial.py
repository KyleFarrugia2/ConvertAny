"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-13
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)

    op.create_table(
        "uploads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("chunk_bytes", sa.Integer(), nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=False),
        sa.Column("received_chunks", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("storage_key", sa.String(512)),
        sa.Column("s3_upload_id", sa.String(1024)),
        sa.Column("probe", sa.JSON()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_uploads_session_id", "uploads", ["session_id"])
    op.create_index("ix_uploads_expires_at", "uploads", ["expires_at"])

    op.create_table(
        "analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("adapter", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_analyses_session_id", "analyses", ["session_id"])
    op.create_index("ix_analyses_expires_at", "analyses", ["expires_at"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_id", sa.String(36)),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("source_type", sa.String(10), nullable=False),
        sa.Column("upload_id", sa.String(36), sa.ForeignKey("uploads.id", ondelete="SET NULL")),
        sa.Column("analysis_id", sa.String(36), sa.ForeignKey("analyses.id", ondelete="SET NULL")),
        sa.Column("source_url", sa.Text()),
        sa.Column("source_item", sa.JSON()),
        sa.Column("source_title", sa.String(512), nullable=False),
        sa.Column("source_info", sa.JSON()),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("queue", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("stages", sa.JSON(), nullable=False),
        sa.Column("progress", sa.Float()),
        sa.Column("message", sa.Text()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("error_retryable", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("manual_retries", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(128)),
        sa.Column("lease_until", sa.DateTime()),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("output_key", sa.String(512)),
        sa.Column("output_filename", sa.String(255)),
        sa.Column("output_size", sa.BigInteger()),
        sa.Column("output_info", sa.JSON()),
        sa.Column("pipeline_kind", sa.String(64), nullable=False),
        sa.Column("media_seconds", sa.Float()),
        sa.Column("processing_seconds", sa.Float()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime()),
        sa.UniqueConstraint("session_id", "idempotency_key", name="uq_jobs_session_idem"),
    )
    op.create_index("ix_jobs_session_id", "jobs", ["session_id"])
    op.create_index("ix_jobs_batch_id", "jobs", ["batch_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_lease_until", "jobs", ["lease_until"])
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])
    op.create_index("ix_jobs_expires_at", "jobs", ["expires_at"])
    op.create_index("ix_jobs_claim", "jobs", ["status", "queue", "created_at"])

    op.create_table(
        "reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("contact", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    for t in ("reports", "jobs", "analyses", "uploads", "sessions"):
        op.drop_table(t)

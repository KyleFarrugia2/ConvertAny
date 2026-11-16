"""Database schema. Keep in sync with migrations/versions/*.py."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow


class AnonSession(Base):
    """Anonymous browser session. Only a SHA-256 of the token is stored."""

    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Upload(Base):
    __tablename__ = "uploads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    chunk_bytes: Mapped[int] = mapped_column(Integer)
    total_chunks: Mapped[int] = mapped_column(Integer)
    received_chunks: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="initiated")  # initiated|complete|rejected|deleted
    storage_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    s3_upload_id: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    probe: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class Analysis(Base):
    """Result of analyzing a URL. Stores normalized metadata only (no signed media URLs)."""

    __tablename__ = "analyses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    adapter: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("session_id", "idempotency_key", name="uq_jobs_session_idem"),
        Index("ix_jobs_claim", "status", "queue", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    batch_id: Mapped[Optional[str]] = mapped_column(String(36), index=True, nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    source_type: Mapped[str] = mapped_column(String(10))  # upload | url
    upload_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("uploads.id", ondelete="SET NULL"), nullable=True)
    analysis_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("analyses.id", ondelete="SET NULL"), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # page URL (never a signed media URL)
    source_item: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    source_title: Mapped[str] = mapped_column(String(512), default="")
    source_info: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    options: Mapped[dict] = mapped_column(JSON)
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    queue: Mapped[str] = mapped_column(String(10), default="cpu")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="queued")
    stages: Mapped[list] = mapped_column(JSON, default=list)
    progress: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_retryable: Mapped[bool] = mapped_column(Boolean, default=False)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    manual_retries: Mapped[int] = mapped_column(Integer, default=0)
    worker_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)

    output_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    output_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    output_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    output_info: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    pipeline_kind: Mapped[str] = mapped_column(String(64), default="")
    media_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    processing_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)


class Report(Base):
    """Abuse / copyright reports submitted through the site."""

    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[str] = mapped_column(Text, default="")
    contact: Mapped[str] = mapped_column(String(320), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


JSONDict = dict[str, Any]

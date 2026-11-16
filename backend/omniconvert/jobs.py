"""Job service shared by the API and workers."""
from __future__ import annotations

import statistics
import uuid
from datetime import timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import utcnow
from .errors import CATALOG, OmniError
from .models import Analysis, Job, Upload
from .media.options import ConvertOptions
from .media.planner import Plan, build_plan

ACTIVE = ("pending", "analyzing", "queued", "processing")
TERMINAL = ("completed", "failed", "cancelled", "expired")


def history_speed(db: Session, pipeline_kind: str) -> Optional[tuple[float, int]]:
    """Median media-seconds per wall-second for recent similar completed jobs."""
    rows = db.execute(
        select(Job.media_seconds, Job.processing_seconds)
        .where(Job.pipeline_kind == pipeline_kind, Job.status == "completed", Job.processing_seconds > 0, Job.media_seconds > 0)
        .order_by(Job.finished_at.desc())
        .limit(30)
    ).all()
    if not rows:
        return None
    speeds = [m / p for m, p in rows if p]
    return (statistics.median(speeds), len(speeds))


def resolve_source(db: Session, session_id: str, source: dict) -> dict:
    """Validate a job source reference and return normalized source context."""
    if source.get("upload_id"):
        up = db.get(Upload, source["upload_id"])
        if not up or up.session_id != session_id or up.status != "complete":
            raise OmniError("upload_not_found")
        return {"type": "upload", "upload": up, "media": up.probe or {}, "title": up.filename.rsplit(".", 1)[0], "thumbnail": None}
    if source.get("analysis_id"):
        an = db.get(Analysis, source["analysis_id"])
        if not an or an.session_id != session_id:
            raise OmniError("job_not_found", "That link analysis has expired. Analyze the link again.")
        entry_id = str(source.get("entry_id") or "")
        entries = an.result.get("entries") or []
        entry = next((e for e in entries if str(e.get("id")) == entry_id), None)
        if entry is None and len(entries) == 1 and not entry_id:
            entry = entries[0]
        if entry is None:
            raise OmniError("invalid_settings", "Select an item from the analyzed link.")
        media = entry.get("media") or {"duration": entry.get("duration"), "has_video": True, "has_audio": True}
        return {"type": "url", "analysis": an, "entry": entry, "media": media, "title": entry.get("title") or an.result.get("title") or "", "thumbnail": entry.get("thumbnail")}
    raise OmniError("invalid_settings", "Missing source.")


def make_plan(db: Session, ctx: dict, options: ConvertOptions) -> Plan:
    plan = build_plan(options, ctx["media"], source_type=ctx["type"], title=ctx["title"])
    hist = history_speed(db, plan.pipeline_kind)
    if hist:
        plan = build_plan(options, ctx["media"], source_type=ctx["type"], title=ctx["title"], history_speed=hist)
    return plan


def check_capacity(db: Session, session_id: str, adding: int) -> None:
    s = get_settings()
    active = db.scalar(select(func.count()).select_from(Job).where(Job.session_id == session_id, Job.status.in_(ACTIVE))) or 0
    if active + adding > s.max_active_jobs_per_session:
        raise OmniError("too_many_jobs")
    depth = db.scalar(select(func.count()).select_from(Job).where(Job.status.in_(("queued", "pending")))) or 0
    if depth + adding > s.max_queue_depth:
        raise OmniError("server_busy")


def create_job(db: Session, session_id: str, ctx: dict, options: ConvertOptions, plan: Plan, *, batch_id: str | None, idem: str | None) -> Job:
    job = Job(
        id=str(uuid.uuid4()),
        session_id=session_id,
        batch_id=batch_id,
        idempotency_key=idem,
        source_type=ctx["type"],
        upload_id=ctx["upload"].id if ctx["type"] == "upload" else None,
        analysis_id=ctx["analysis"].id if ctx["type"] == "url" else None,
        source_url=(ctx["entry"].get("url") or ctx["analysis"].url) if ctx["type"] == "url" else None,
        source_item={k: ctx["entry"].get(k) for k in ("id", "title", "url", "thumbnail", "duration")} | {"adapter": ctx["analysis"].adapter} if ctx["type"] == "url" else None,
        source_title=(ctx["title"] or "")[:500],
        source_info=ctx["media"],
        options=options.model_dump(),
        plan={"summary": plan.summary, "estimate": plan.estimate, "notes": plan.notes, "warnings": plan.warnings, "filename": plan.filename},
        queue=plan.queue,
        status="queued",
        stage="queued",
        stages=plan.stages,
        pipeline_kind=plan.pipeline_kind,
        media_seconds=plan.duration,
    )
    db.add(job)
    return job


def queue_position(db: Session, job: Job) -> Optional[int]:
    if job.status != "queued":
        return None
    return (db.scalar(
        select(func.count()).select_from(Job).where(Job.status == "queued", Job.queue == job.queue, Job.created_at < job.created_at)
    ) or 0) + 1


def serialize(job: Job, db: Optional[Session] = None) -> dict:
    err = None
    if job.error_code:
        spec = CATALOG.get(job.error_code, CATALOG["processing_failed"])
        err = {"code": job.error_code, "message": job.error_message or spec.message, "action": spec.action, "retryable": job.error_retryable}
    s = get_settings()
    out = None
    if job.status == "completed" and job.output_info:
        i = job.output_info
        v, a = i.get("video") or {}, i.get("audio") or {}
        out = {
            "filename": job.output_filename,
            "size": job.output_size,
            "format": job.options.get("output", "").upper(),
            "duration": i.get("duration"),
            "width": v.get("width"), "height": v.get("height"), "fps": v.get("fps"),
            "video_codec": v.get("codec"),
            "audio_codec": a.get("codec"), "sample_rate": a.get("sample_rate"), "channels": a.get("channels"),
            "audio_bitrate": a.get("bit_rate") or (i.get("bit_rate") if not v else None),
            "upscaled": bool((job.plan.get("summary") or {}).get("upscale")),
            "interpolated": (job.options.get("fps_mode") == "interpolate"),
        }
    return {
        "id": job.id,
        "batch_id": job.batch_id,
        "status": job.status,
        "stage": job.stage,
        "stages": job.stages,
        "progress": job.progress,
        "message": job.message,
        "error": err,
        "source": {
            "type": job.source_type,
            "title": job.source_title,
            "thumbnail": (job.source_item or {}).get("thumbnail"),
            "platform": (job.source_item or {}).get("adapter"),
        },
        "options": job.options,
        "summary": (job.plan or {}).get("summary"),
        "estimate": (job.plan or {}).get("estimate"),
        "notes": (job.plan or {}).get("notes", []),
        "warnings": (job.plan or {}).get("warnings", []),
        "output": out,
        "queue_position": queue_position(db, job) if db is not None else None,
        "attempts": job.attempts,
        "can_cancel": job.status in ACTIVE,
        "can_retry": job.status in ("failed", "cancelled") and job.manual_retries < s.max_manual_retries and job.error_code not in ("invalid_settings", "unsupported_input"),
        "created_at": job.created_at.isoformat() + "Z" if job.created_at else None,
        "started_at": job.started_at.isoformat() + "Z" if job.started_at else None,
        "finished_at": job.finished_at.isoformat() + "Z" if job.finished_at else None,
        "expires_at": job.expires_at.isoformat() + "Z" if job.expires_at else None,
    }


def get_owned_job(db: Session, session_id: str, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if not job or job.session_id != session_id:  # never reveal existence of others' jobs
        raise OmniError("job_not_found")
    return job


def expiry_from_now() -> object:
    return utcnow() + timedelta(seconds=get_settings().file_ttl_seconds)

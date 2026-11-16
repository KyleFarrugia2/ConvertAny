"""Media worker.

Run with:  python -m omniconvert.worker.main [--queues cpu,gpu] [--concurrency N]

* Claims jobs from the database with an atomic conditional update (plus
  ``FOR UPDATE SKIP LOCKED`` on PostgreSQL), so many workers can share a queue.
* Holds a renewable lease per job. If a worker dies, the reaper re-queues the
  job after the lease expires (bounded by ``max_auto_attempts``).
* Polls for cancellation, kills the media process, and removes partial files.
* Never marks a job complete unless the output passed verification.
* Periodically deletes expired outputs, uploads, analyses, and scratch dirs.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import socket
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import and_, or_, select, update

from ..adapters.base import FetchRequest
from ..adapters.registry import get_adapter
from ..config import get_settings
from ..db import is_postgres, session_scope, utcnow
from ..errors import OmniError
from ..jobs import expiry_from_now
from ..logging_setup import setup_logging
from ..media.capabilities import get_capabilities
from ..media.enhancement import run_external
from ..media.ffmpeg import Cancelled, build_onepass, run_process
from ..media.options import ConvertOptions
from ..media.planner import build_plan
from ..media.probe import inspect_media
from ..media.verify import verify_output
from ..models import Analysis, AnonSession, Job, Upload
from ..security.ssrf import redact_url
from ..storage import get_storage

log = logging.getLogger("omniconvert.worker")
STOP = threading.Event()


class JobContext:
    """Per-job helper: throttled progress writes, lease renewal, cancellation."""

    def __init__(self, job_id: str, worker_id: str):
        self.job_id = job_id
        self.worker_id = worker_id
        self.cancelled = threading.Event()
        self._stop = threading.Event()
        self._last_write = 0.0
        self._stage: Optional[str] = None
        self._monitor = threading.Thread(target=self._watch, daemon=True)
        self._monitor.start()

    def _watch(self) -> None:
        s = get_settings()
        last_lease = 0.0
        while not self._stop.wait(1.0):
            try:
                with session_scope() as db:
                    job = db.get(Job, self.job_id)
                    if job is None or job.cancel_requested:
                        self.cancelled.set()
                    if job is not None and time.monotonic() - last_lease > min(10.0, s.worker_lease_seconds / 3):
                        job.lease_until = utcnow() + timedelta(seconds=s.worker_lease_seconds)
                        last_lease = time.monotonic()
            except Exception:
                log.exception("lease/cancel monitor error")

    def close(self) -> None:
        self._stop.set()

    def should_cancel(self) -> bool:
        return self.cancelled.is_set() or STOP.is_set()

    def set_stage(self, stage: str, progress: Optional[float] = None, message: Optional[str] = None, force: bool = False) -> None:
        now = time.monotonic()
        if not force and stage == self._stage and now - self._last_write < 0.75:
            return
        self._stage, self._last_write = stage, now
        with session_scope() as db:
            vals = {"stage": stage, "progress": None if progress is None else round(float(progress), 4)}
            if message is not None:
                vals["message"] = message
            db.execute(update(Job).where(Job.id == self.job_id, Job.worker_id == self.worker_id).values(**vals))


# ----------------------------------------------------------------------------
# claiming
# ----------------------------------------------------------------------------
def claim_job(worker_id: str, queues: list[str]) -> Optional[str]:
    s = get_settings()
    now = utcnow()
    with session_scope() as db:
        q = (
            select(Job.id)
            .where(Job.status == "queued", Job.queue.in_(queues), or_(Job.lease_until.is_(None), Job.lease_until < now))
            .order_by(Job.created_at)
            .limit(5)
        )
        if is_postgres():
            q = q.with_for_update(skip_locked=True)
        for job_id in db.scalars(q).all():
            res = db.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "queued")
                .values(status="processing", worker_id=worker_id, attempts=Job.attempts + 1, started_at=now,
                        lease_until=now + timedelta(seconds=s.worker_lease_seconds), progress=None, message=None)
            )
            if res.rowcount == 1:
                return job_id
    return None


def reap_stale() -> None:
    """Re-queue jobs whose worker vanished (lease expired)."""
    s = get_settings()
    now = utcnow()
    with session_scope() as db:
        stale = db.scalars(select(Job).where(Job.status == "processing", Job.lease_until < now)).all()
        for job in stale:
            if job.cancel_requested:
                job.status, job.stage, job.finished_at, job.expires_at = "cancelled", "cancelled", now, expiry_from_now()
            elif job.attempts < s.max_auto_attempts:
                job.status, job.stage, job.progress = "queued", "queued", None
                job.worker_id, job.lease_until = None, None
                job.message = "A worker stopped unexpectedly; the job was re-queued automatically."
                log.warning("re-queued stale job", extra={"event": "job_requeued", "job": job.id, "attempts": job.attempts})
            else:
                job.status, job.stage = "failed", "failed"
                job.error_code, job.error_retryable = "worker_lost", True
                job.finished_at, job.expires_at = now, expiry_from_now()


# ----------------------------------------------------------------------------
# processing
# ----------------------------------------------------------------------------
def _finish(job_id: str, _owner: str, **vals) -> None:
    with session_scope() as db:
        db.execute(update(Job).where(Job.id == job_id, Job.worker_id == _owner).values(**vals))


def process_job(job_id: str, worker_id: str) -> None:
    s = get_settings()
    st = get_storage()
    # Unique per attempt: a crashed attempt's leftovers can never collide.
    scratch = s.scratch_root / f"job-{job_id}-{uuid.uuid4().hex[:8]}"
    scratch.mkdir(parents=True)
    ctx = JobContext(job_id, worker_id)
    t0 = time.monotonic()
    with session_scope() as db:
        job = db.get(Job, job_id)
        opts = ConvertOptions(**job.options)
        source_type, upload_id, item, attempts = job.source_type, job.upload_id, job.source_item or {}, job.attempts
        source_url = job.source_url
        title = job.source_title
    try:
        if shutil.disk_usage(scratch).free < s.worker_min_free_disk_bytes:
            raise OmniError("insufficient_disk")
        # ---------------- obtain source ----------------
        if source_type == "url":
            ctx.set_stage("analyzing", None, force=True)
            adapter = get_adapter(item.get("adapter") or "")
            fetched_once = {"v": False}

            def on_fetch_progress(frac):
                if not fetched_once["v"]:
                    fetched_once["v"] = True
                    ctx.set_stage("fetching", frac, force=True)
                else:
                    ctx.set_stage("fetching", frac)

            target_short = None if opts.resolution == "original" else int(opts.resolution)
            category = {"mp4": "video", "webm": "video", "mov": "video", "mkv": "video", "gif": "gif"}.get(opts.output, "audio")
            if category == "video" and not opts.include_audio:
                category = "gif"  # video-only fetch
            src_path = adapter.fetch(FetchRequest(
                url=source_url or "", item=item, dest_dir=scratch / "src", category=category,
                target_short=target_short if opts.upscale == "none" else None, prefer_container=opts.output,
                max_bytes=s.max_url_download_bytes, on_progress=on_fetch_progress, should_cancel=ctx.should_cancel,
            ))
            media = inspect_media(src_path)
            src_cm = None
        else:
            with session_scope() as db:
                up = db.get(Upload, upload_id)
                if not up or up.status != "complete":
                    raise OmniError("job_expired", "The uploaded source file has expired. Upload it again.")
                media, key = up.probe, up.storage_key
            src_cm = st.local_copy(key, scratch / "src")
            src_path = src_cm.__enter__()

        try:
            # ---------------- plan against the real source ----------------
            plan = build_plan(opts, media, source_type=source_type, worker=True, title=title)
            if not plan.valid:
                raise OmniError("invalid_settings", "; ".join(plan.errors.values()), fields=plan.errors)
            with session_scope() as db:
                job = db.get(Job, job_id)
                job.stages = plan.stages
                job.pipeline_kind = plan.pipeline_kind
                job.media_seconds = plan.duration
                job.source_info = media
                job.plan = {**(job.plan or {}), "summary": plan.summary, "notes": plan.notes, "warnings": plan.warnings, "filename": plan.filename}
            out_path = scratch / f"output.{plan.ext}"

            # ---------------- convert ----------------
            if plan.external:
                run_external(plan, src_path, out_path, scratch / "work", strip_metadata=opts.edit.strip_metadata,
                             set_stage=lambda st_, f: ctx.set_stage(st_, f), should_cancel=ctx.should_cancel)
            else:
                stage = "interpolating" if plan.interp else "converting"
                ctx.set_stage(stage, 0.0, force=True)
                run_process(build_onepass(plan, src_path, out_path, opts.edit.strip_metadata), expected_seconds=plan.duration,
                            on_progress=lambda f: ctx.set_stage(stage, f), should_cancel=ctx.should_cancel)
        finally:
            if src_cm is not None:
                src_cm.__exit__(None, None, None)

        # ---------------- verify & store ----------------
        if ctx.should_cancel():
            raise Cancelled()
        ctx.set_stage("finalizing", None, force=True)
        info = verify_output(out_path, plan)
        out_key = f"outputs/{job_id}/{plan.filename}"
        st.put_file(out_key, out_path)
        elapsed = time.monotonic() - t0
        _finish(job_id, worker_id, status="completed", stage="ready", progress=1.0, output_key=out_key, output_filename=plan.filename,
                output_size=info["size"], output_info=info, finished_at=utcnow(), expires_at=expiry_from_now(),
                processing_seconds=round(elapsed, 3), lease_until=None, message=None, error_code=None)
        log.info("job completed", extra={"event": "job_completed", "job": job_id, "kind": plan.pipeline_kind, "seconds": round(elapsed, 2), "bytes": info["size"]})
    except Cancelled:
        _finish(job_id, worker_id, status="cancelled", stage="cancelled", progress=None, finished_at=utcnow(), expires_at=expiry_from_now(), lease_until=None,
                message=None if not STOP.is_set() else "Worker shutting down")
        if STOP.is_set():
            # Shutdown, not a user cancel: hand the job back to the queue.
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job and not job.cancel_requested:
                    job.status, job.stage, job.attempts, job.message, job.finished_at, job.worker_id = "queued", "queued", max(0, job.attempts - 1), "Re-queued after a worker restart.", None, None
        log.info("job cancelled", extra={"event": "job_cancelled", "job": job_id})
    except OmniError as e:
        temporary = e.retryable and e.code not in ("cancelled",)
        if temporary and attempts < s.max_auto_attempts:
            backoff = 15 * attempts
            _finish(job_id, worker_id, status="queued", stage="queued", progress=None, worker_id=None,
                    lease_until=utcnow() + timedelta(seconds=backoff),
                    message=f"Temporary problem ({e.detail or e.spec.message}) — retrying automatically in {backoff}s.")
            log.warning("job retry scheduled", extra={"event": "job_retry", "job": job_id, "code": e.code, "attempt": attempts})
        else:
            _finish(job_id, worker_id, status="failed", stage="failed", progress=None, error_code=e.code,
                    error_message=e.detail or e.spec.message, error_retryable=e.retryable, finished_at=utcnow(), expires_at=expiry_from_now(), lease_until=None)
            log.warning("job failed", extra={"event": "job_failed", "job": job_id, "code": e.code, "url": redact_url(source_url)})
    except Exception:
        log.exception("unexpected job error", extra={"event": "job_error", "job": job_id})
        if attempts < s.max_auto_attempts:
            _finish(job_id, worker_id, status="queued", stage="queued", worker_id=None, lease_until=utcnow() + timedelta(seconds=15 * attempts),
                    message="Unexpected error — retrying automatically.")
        else:
            _finish(job_id, worker_id, status="failed", stage="failed", error_code="processing_failed", error_retryable=True,
                    finished_at=utcnow(), expires_at=expiry_from_now(), lease_until=None)
    finally:
        ctx.close()
        shutil.rmtree(scratch, ignore_errors=True)  # partial outputs never survive


# ----------------------------------------------------------------------------
# cleanup
# ----------------------------------------------------------------------------
def cleanup_expired() -> dict:
    s = get_settings()
    st = get_storage()
    now = utcnow()
    stats = {"jobs_expired": 0, "jobs_purged": 0, "uploads": 0, "analyses": 0, "sessions": 0, "scratch": 0}
    with session_scope() as db:
        for job in db.scalars(select(Job).where(Job.status.in_(("completed", "failed", "cancelled")), Job.expires_at < now)).all():
            st.delete_prefix(f"outputs/{job.id}")
            job.status, job.stage, job.output_key = "expired", "expired", None
            stats["jobs_expired"] += 1
        # minimal retention: drop expired job records a day after expiry
        for job in db.scalars(select(Job).where(Job.status == "expired", Job.expires_at < now - timedelta(days=1))).all():
            db.delete(job)
            stats["jobs_purged"] += 1
        active_upload_ids = set(db.scalars(select(Job.upload_id).where(Job.status.in_(("queued", "processing", "pending")), Job.upload_id.is_not(None))).all())
        for up in db.scalars(select(Upload).where(Upload.status != "deleted", Upload.expires_at < now)).all():
            if up.id in active_upload_ids:
                continue
            if up.s3_upload_id and up.status == "initiated":
                try:
                    st.abort_multipart(up.storage_key, up.s3_upload_id)
                except Exception:
                    pass
            st.delete_prefix(f"uploads/{up.id}")
            up.status, up.probe = "deleted", None
            stats["uploads"] += 1
        for an in db.scalars(select(Analysis).where(Analysis.expires_at < now)).all():
            db.execute(update(Job).where(Job.analysis_id == an.id).values(analysis_id=None))
            db.delete(an)
            stats["analyses"] += 1
        stale_sessions = db.scalars(select(AnonSession).where(AnonSession.last_seen < now - timedelta(days=30))).all()
        for sess in stale_sessions:
            has_jobs = db.scalar(select(Job.id).where(Job.session_id == sess.id).limit(1))
            if not has_jobs:
                db.execute(update(Upload).where(Upload.session_id == sess.id).values(status="deleted"))
                db.delete(sess)
                stats["sessions"] += 1
        processing = set(db.scalars(select(Job.id).where(Job.status == "processing")).all())
    cutoff = time.time() - 3600
    for p in s.scratch_root.iterdir():
        try:
            if p.name.startswith("job-") and p.name[4:40] in processing:
                continue
            if p.stat().st_mtime < cutoff:
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
                stats["scratch"] += 1
        except FileNotFoundError:
            pass
    if any(stats.values()):
        log.info("cleanup", extra={"event": "cleanup", **stats})
    return stats


# ----------------------------------------------------------------------------
# main loop
# ----------------------------------------------------------------------------
def worker_loop(worker_id: str, queues: list[str]) -> None:
    s = get_settings()
    while not STOP.is_set():
        try:
            job_id = claim_job(worker_id, queues)
        except Exception:
            log.exception("claim failed")
            job_id = None
        if job_id:
            log.info("job claimed", extra={"event": "job_claimed", "job": job_id, "worker": worker_id})
            process_job(job_id, worker_id)
        else:
            STOP.wait(s.worker_poll_seconds)


def housekeeping_loop() -> None:
    s = get_settings()
    last_cleanup = 0.0
    while not STOP.wait(10):
        try:
            reap_stale()
            if time.monotonic() - last_cleanup > s.cleanup_interval_seconds:
                cleanup_expired()
                last_cleanup = time.monotonic()
        except Exception:
            log.exception("housekeeping error")


def main(argv: Optional[list[str]] = None) -> None:
    setup_logging()
    s = get_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--queues", default=s.worker_queues)
    ap.add_argument("--concurrency", type=int, default=s.worker_concurrency)
    args = ap.parse_args(argv)
    queues = [q.strip() for q in args.queues.split(",") if q.strip()]
    caps = get_capabilities()
    if "gpu" in queues and not (caps.rife.available or caps.realesrgan.available):
        log.warning("gpu queue requested but no GPU enhancement tool passed its self-test; jobs needing it are never planned")
    host = socket.gethostname()

    def _sig(*_):
        log.info("shutdown requested")
        STOP.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    reap_stale()
    threads = [threading.Thread(target=housekeeping_loop, name="housekeeping", daemon=True)]
    for i in range(max(1, args.concurrency)):
        wid = f"{host}:{os.getpid()}:{i}:{uuid.uuid4().hex[:6]}"
        threads.append(threading.Thread(target=worker_loop, args=(wid, queues), name=f"worker-{i}", daemon=True))
    for t in threads:
        t.start()
    log.info("worker started", extra={"event": "worker_start", "queues": queues, "concurrency": args.concurrency,
                                        "rife": caps.rife.available, "realesrgan": caps.realesrgan.available})
    try:
        while not STOP.wait(1):
            pass
    finally:
        for t in threads[1:]:
            t.join(timeout=30)


if __name__ == "__main__":
    main()

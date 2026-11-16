"""OmniConvert HTTP API."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..adapters.registry import find_adapter, get_adapter
from ..config import get_settings
from ..db import get_db, session_scope, utcnow
from ..errors import OmniError
from ..jobs import (ACTIVE, check_capacity, create_job, expiry_from_now, get_owned_job, make_plan, resolve_source, serialize)
from ..logging_setup import setup_logging
from ..media.capabilities import capability_matrix, get_capabilities
from ..media.options import ConvertOptions
from ..media.planner import sanitize_filename
from ..media.probe import inspect_media
from ..models import Analysis, AnonSession, Job, Report, Upload
from ..security.cors import cookie_params, cors_enabled
from ..security.ratelimit import limiter
from ..security.ssrf import redact_url, validate_public_url
from ..security.tokens import hash_token, make_download_token, new_session_token, verify_download_token
from ..storage import get_storage

log = logging.getLogger("omniconvert.api")
COOKIE = "oc_session"
CSRF_HEADER = "x-omniconvert"

app = FastAPI(title="OmniConvert API", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json")


# ----------------------------------------------------------------------------
# errors
# ----------------------------------------------------------------------------
@app.exception_handler(OmniError)
async def _omni_error(_: Request, exc: OmniError):
    return JSONResponse(status_code=exc.spec.status, content={"error": exc.to_dict()})


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError):
    fields = {}
    for e in exc.errors():
        loc = [str(p) for p in e.get("loc", []) if p not in ("body",)]
        fields[".".join(loc) or "request"] = e.get("msg", "invalid")
    err = OmniError("invalid_settings", "Some settings are invalid.", fields=fields)
    return JSONResponse(status_code=422, content={"error": err.to_dict()})


# ----------------------------------------------------------------------------
# middleware: anonymous session, CSRF, rate limit
# ----------------------------------------------------------------------------
def _client_ip(request: Request) -> str:
    # Only trust X-Forwarded-For when explicitly deployed behind a proxy.
    if os.environ.get("OC_TRUST_PROXY") == "1":
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _load_or_create_session(token: Optional[str]) -> tuple[str, Optional[str]]:
    with session_scope() as db:
        if token:
            sess = db.scalar(select(AnonSession).where(AnonSession.token_hash == hash_token(token)))
            if sess:
                if (utcnow() - sess.last_seen) > timedelta(minutes=5):
                    sess.last_seen = utcnow()
                return sess.id, None
        new_token = new_session_token()
        sess = AnonSession(id=str(uuid.uuid4()), token_hash=hash_token(new_token))
        db.add(sess)
        return sess.id, new_token


@app.middleware("http")
async def session_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path.startswith(("/api/health", "/api/docs", "/api/openapi")):
        return await call_next(request)
    s = get_settings()
    ip = _client_ip(request)
    try:
        limiter.check("general", ip, s.rate_general_per_min)
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
            raise OmniError("csrf")
    except OmniError as e:
        return JSONResponse(status_code=e.spec.status, content={"error": e.to_dict()})
    sid, new_token = await run_in_threadpool(_load_or_create_session, request.cookies.get(COOKIE))
    request.state.session_id = sid
    request.state.ip = ip
    response = await call_next(request)
    if new_token:
        response.set_cookie(COOKIE, new_token, **cookie_params(s))
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


def sid(request: Request) -> str:
    return request.state.session_id


def _public_url(path: str) -> str:
    base = get_settings().public_api_base.strip().rstrip("/")
    return f"{base}{path}" if base else path


# ----------------------------------------------------------------------------
# health & capabilities
# ----------------------------------------------------------------------------
@app.get("/api/health")
def health():
    s = get_settings()
    out = {"ok": True}
    try:
        with session_scope() as db:
            out["queued"] = db.scalar(select(func.count()).select_from(Job).where(Job.status == "queued")) or 0
            out["processing"] = db.scalar(select(func.count()).select_from(Job).where(Job.status == "processing")) or 0
            out["db"] = "ok"
    except Exception:
        out.update(ok=False, db="error")
    caps = get_capabilities()
    out["ffmpeg"] = bool(caps.encoders)
    out["overloaded"] = out.get("queued", 0) >= s.max_queue_depth
    out["free_disk_bytes"] = shutil.disk_usage(s.scratch_root).free
    return JSONResponse(out, status_code=200 if out["ok"] and out["ffmpeg"] else 503)


@app.get("/api/capabilities")
def capabilities():
    return capability_matrix()


# ----------------------------------------------------------------------------
# URL analysis
# ----------------------------------------------------------------------------
class AnalyzeBody(BaseModel):
    url: str = Field(max_length=2048)


def _public_analysis(an: Analysis) -> dict:
    r = dict(an.result)
    return {"id": an.id, **r}


@app.post("/api/analyze")
async def analyze(body: AnalyzeBody, request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    limiter.check("analyze", request.state.ip, s.rate_analyze_per_min)
    url = await run_in_threadpool(validate_public_url, body.url)
    adapter = find_adapter(url)
    if adapter.effective_status == "temporarily_unavailable":
        raise OmniError("source_unavailable", f"{adapter.name} is temporarily unavailable due to repeated upstream errors.")
    try:
        result = await asyncio.wait_for(run_in_threadpool(adapter.analyze, url), timeout=s.analysis_timeout_seconds)
    except asyncio.TimeoutError:
        raise OmniError("source_unavailable", "The source took too long to respond.")
    log.info("analyzed", extra={"event": "analyze", "adapter": adapter.id, "url": redact_url(url), "kind": result.get("kind")})
    an = Analysis(id=str(uuid.uuid4()), session_id=sid(request), url=url, adapter=adapter.id, result=result,
                  expires_at=utcnow() + timedelta(seconds=s.file_ttl_seconds))
    db.add(an)
    db.flush()
    return _public_analysis(an)


@app.post("/api/analyze/{analysis_id}/entries/{entry_id}/resolve")
async def resolve_entry(analysis_id: str, entry_id: str, request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    limiter.check("analyze", request.state.ip, s.rate_analyze_per_min * 3)
    an = db.get(Analysis, analysis_id)
    if not an or an.session_id != sid(request):
        raise OmniError("job_not_found", "That analysis has expired. Analyze the link again.")
    entries = list(an.result.get("entries") or [])
    idx = next((i for i, e in enumerate(entries) if str(e.get("id")) == entry_id), None)
    if idx is None:
        raise OmniError("invalid_settings", "Unknown item.")
    entry = entries[idx]
    if entry.get("resolved"):
        return entry
    url = await run_in_threadpool(validate_public_url, entry.get("url") or "")
    adapter = get_adapter(an.adapter)
    try:
        full = await asyncio.wait_for(run_in_threadpool(adapter.resolve_entry, url), timeout=s.analysis_timeout_seconds)
    except asyncio.TimeoutError:
        raise OmniError("source_unavailable", "The source took too long to respond.")
    full["id"] = entry_id
    entries[idx] = full
    an.result = {**an.result, "entries": entries}
    return full


# ----------------------------------------------------------------------------
# uploads (chunked, resumable)
# ----------------------------------------------------------------------------
class UploadInit(BaseModel):
    filename: str = Field(max_length=255)
    size: int = Field(gt=0)


ACCEPTED_EXT = {"mp4", "mov", "mkv", "webm", "avi", "m4v", "mp3", "wav", "aac", "m4a", "flac", "ogg", "oga", "opus"}


def _upload_dto(up: Upload) -> dict:
    return {"id": up.id, "filename": up.filename, "size": up.size_bytes, "chunk_bytes": up.chunk_bytes, "total_chunks": up.total_chunks,
            "received_chunks": sorted(up.received_chunks or []), "status": up.status, "media": up.probe,
            "transport": "s3" if up.s3_upload_id else "api"}


@app.post("/api/uploads")
def init_upload(body: UploadInit, request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    limiter.check("upload", request.state.ip, s.rate_upload_per_min)
    if body.size > s.max_upload_bytes:
        raise OmniError("file_too_large", f"Files are limited to {s.max_upload_bytes / 1024**3:.1f} GB.")
    ext = body.filename.rsplit(".", 1)[-1].lower() if "." in body.filename else ""
    if ext and ext not in ACCEPTED_EXT:
        # Extension is only a first hint; real validation happens on the content.
        raise OmniError("unsupported_input")
    uid = str(uuid.uuid4())
    chunk = s.upload_chunk_bytes
    up = Upload(id=uid, session_id=sid(request), filename=sanitize_filename(body.filename, ext or "bin").rsplit(".", 1)[0] + (f".{ext}" if ext else ""),
                size_bytes=body.size, chunk_bytes=chunk, total_chunks=max(1, -(-body.size // chunk)), received_chunks=[],
                storage_key=f"uploads/{uid}/source", expires_at=utcnow() + timedelta(seconds=s.upload_ttl_seconds))
    st = get_storage()
    if st.kind == "s3":
        up.s3_upload_id = st.create_multipart(up.storage_key)
    else:
        st.create_sparse(up.storage_key, body.size)
    db.add(up)
    db.flush()
    return _upload_dto(up)


def _owned_upload(db: Session, request: Request, upload_id: str) -> Upload:
    up = db.get(Upload, upload_id)
    if not up or up.session_id != sid(request) or up.status == "deleted":
        raise OmniError("upload_not_found")
    return up


@app.get("/api/uploads/{upload_id}")
def get_upload(upload_id: str, request: Request, db: Session = Depends(get_db)):
    return _upload_dto(_owned_upload(db, request, upload_id))


@app.put("/api/uploads/{upload_id}/chunks/{index}")
async def put_chunk(upload_id: str, index: int, request: Request):
    def load():
        with session_scope() as db:
            up = _owned_upload(db, request, upload_id)
            return up.status, up.total_chunks, up.chunk_bytes, up.size_bytes, up.storage_key, bool(up.s3_upload_id)

    status, total, chunk, size, key, is_s3 = await run_in_threadpool(load)
    if is_s3 or status != "initiated":
        raise OmniError("invalid_settings", "This upload no longer accepts data.")
    if not 0 <= index < total:
        raise OmniError("invalid_settings", "Chunk index out of range.")
    expected = min(chunk, size - index * chunk)
    try:
        await get_storage().write_chunk_at(key, index * chunk, request.stream(), expected)
    except ValueError:
        raise OmniError("invalid_settings", "Chunk size mismatch; retry this part.")

    def mark():
        with session_scope() as db:
            up = db.get(Upload, upload_id)
            if up and index not in (up.received_chunks or []):
                up.received_chunks = sorted(set(up.received_chunks or []) | {index})
            return len(up.received_chunks or []) if up else 0

    received = await run_in_threadpool(mark)
    return {"index": index, "received": received, "total": total}


@app.post("/api/uploads/{upload_id}/parts/{index}/url")
def part_url(upload_id: str, index: int, request: Request, db: Session = Depends(get_db)):
    up = _owned_upload(db, request, upload_id)
    if not 0 <= index < up.total_chunks or up.status != "initiated":
        raise OmniError("invalid_settings", "Invalid part.")
    st = get_storage()
    if st.kind == "s3":
        return {"url": st.presigned_part(up.storage_key, up.s3_upload_id, index, 3600), "method": "PUT", "direct": True}
    return {"url": f"/api/uploads/{up.id}/chunks/{index}", "method": "PUT", "direct": False}


class CompleteBody(BaseModel):
    etags: Optional[list[str]] = None


@app.post("/api/uploads/{upload_id}/complete")
async def complete_upload(upload_id: str, body: CompleteBody, request: Request):
    def run():
        st = get_storage()
        with session_scope() as db:
            up = _owned_upload(db, request, upload_id)
            if up.status == "complete":
                return _upload_dto(up)
            if up.status == "rejected":
                raise OmniError(up.error_code or "unsupported_input")
            if st.kind == "s3":
                if not body.etags or len(body.etags) != up.total_chunks:
                    raise OmniError("upload_incomplete")
                actual = st.complete_multipart(up.storage_key, up.s3_upload_id, body.etags)
            else:
                if len(up.received_chunks or []) != up.total_chunks:
                    raise OmniError("upload_incomplete")
                actual = st.size(up.storage_key)
            if actual != up.size_bytes:
                raise OmniError("upload_incomplete", "Uploaded size doesn't match the file size.")
            key, scratch = up.storage_key, get_settings().scratch_root / f"probe-{up.id}"
        try:
            with st.local_copy(key, scratch) as path:
                info = inspect_media(path)
        except OmniError as e:
            with session_scope() as db:
                up = db.get(Upload, upload_id)
                up.status, up.error_code = "rejected", e.code
            st.delete_prefix(f"uploads/{upload_id}")
            raise
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        with session_scope() as db:
            up = db.get(Upload, upload_id)
            up.status, up.probe = "complete", info
            return _upload_dto(up)

    return await run_in_threadpool(run)


@app.delete("/api/uploads/{upload_id}")
def delete_upload(upload_id: str, request: Request, db: Session = Depends(get_db)):
    up = _owned_upload(db, request, upload_id)
    in_use = db.scalar(select(func.count()).select_from(Job).where(Job.upload_id == up.id, Job.status.in_(ACTIVE))) or 0
    if in_use:
        return {"deleted": False, "reason": "in_use"}
    st = get_storage()
    if up.s3_upload_id and up.status == "initiated":
        st.abort_multipart(up.storage_key, up.s3_upload_id)
    st.delete_prefix(f"uploads/{up.id}")
    up.status = "deleted"
    up.probe = None
    return {"deleted": True}


# ----------------------------------------------------------------------------
# planning & jobs
# ----------------------------------------------------------------------------
class SourceRef(BaseModel):
    upload_id: Optional[str] = None
    analysis_id: Optional[str] = None
    entry_id: Optional[str] = None


class PlanBody(BaseModel):
    source: SourceRef
    options: ConvertOptions


@app.post("/api/plan")
def plan(body: PlanBody, request: Request, db: Session = Depends(get_db)):
    ctx = resolve_source(db, sid(request), body.source.model_dump())
    p = make_plan(db, ctx, body.options)
    return {
        "valid": p.valid, "errors": p.errors, "warnings": p.warnings, "notes": p.notes, "stages": p.stages,
        "summary": p.summary, "estimate": p.estimate, "filename": p.filename, "queue": p.queue,
        "stream_copy": p.stream_copy, "interp": p.interp, "upscale": p.upscale,
        "out_width": p.out_width, "out_height": p.out_height,
    }


class JobItem(BaseModel):
    source: SourceRef
    options: ConvertOptions
    client_id: Optional[str] = Field(default=None, max_length=64)


class JobsBody(BaseModel):
    items: list[JobItem] = Field(min_length=1)
    idempotency_key: Optional[str] = Field(default=None, max_length=100)


@app.post("/api/jobs")
def create_jobs(body: JobsBody, request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    session_id = sid(request)
    limiter.check("jobs", request.state.ip, s.rate_jobs_per_min)
    url_items = sum(1 for i in body.items if i.source.analysis_id)
    if len(body.items) > s.max_files_per_batch or url_items > s.max_url_items_per_batch:
        raise OmniError("batch_too_large", f"Up to {s.max_files_per_batch} items per batch, including at most {s.max_url_items_per_batch} link items.")
    # Idempotency: replaying the same key returns the originally created jobs.
    if body.idempotency_key:
        existing = db.scalars(select(Job).where(Job.session_id == session_id, Job.idempotency_key.like(f"{body.idempotency_key}:%"))).all()
        if existing:
            return {"batch_id": existing[0].batch_id, "results": [{"client_id": None, "job": serialize(j, db)} for j in existing], "replayed": True}
    check_capacity(db, session_id, len(body.items))
    batch_id = str(uuid.uuid4())
    results = []
    for n, item in enumerate(body.items):
        try:
            ctx = resolve_source(db, session_id, item.source.model_dump())
            p = make_plan(db, ctx, item.options)
            if not p.valid:
                raise OmniError("invalid_settings", next(iter(p.errors.values())), fields=p.errors)
            idem = f"{body.idempotency_key}:{n}" if body.idempotency_key else None
            job = create_job(db, session_id, ctx, item.options, p, batch_id=batch_id, idem=idem)
            db.flush()
            results.append({"client_id": item.client_id, "job": serialize(job, db)})
        except OmniError as e:
            results.append({"client_id": item.client_id, "error": e.to_dict()})
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise OmniError("invalid_settings", "Duplicate submission.")
    log.info("jobs created", extra={"event": "jobs_created", "batch": batch_id, "count": sum(1 for r in results if "job" in r)})
    return {"batch_id": batch_id, "results": results}


@app.get("/api/jobs")
def list_jobs(request: Request, db: Session = Depends(get_db)):
    jobs = db.scalars(select(Job).where(Job.session_id == sid(request)).order_by(Job.created_at.desc()).limit(200)).all()
    return {"jobs": [serialize(j, db) for j in jobs]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    return serialize(get_owned_job(db, sid(request), job_id), db)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    job = get_owned_job(db, sid(request), job_id)
    if job.status not in ACTIVE:
        raise OmniError("not_cancellable")
    if job.status in ("queued", "pending"):
        job.status, job.stage, job.progress = "cancelled", "cancelled", None
        job.finished_at = utcnow()
        job.expires_at = expiry_from_now()
        job.error_code = None
    else:
        job.cancel_requested = True  # worker kills the process and cleans up
    return serialize(job, db)


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    job = get_owned_job(db, sid(request), job_id)
    if job.status not in ("failed", "cancelled"):
        raise OmniError("not_retryable")
    if job.error_code in ("invalid_settings", "unsupported_input"):
        raise OmniError("not_retryable", "This item can't succeed with the same settings. Change the settings and convert again.")
    if job.manual_retries >= s.max_manual_retries:
        raise OmniError("retry_limit")
    if job.upload_id:
        up = db.get(Upload, job.upload_id)
        if not up or up.status != "complete":
            raise OmniError("job_expired", "The uploaded source file has expired. Upload it again.")
    check_capacity(db, sid(request), 1)
    job.manual_retries += 1
    job.attempts = 0
    job.status, job.stage, job.progress, job.message = "queued", "queued", None, None
    job.error_code = job.error_message = None
    job.cancel_requested = False
    job.lease_until = None
    job.finished_at = None
    job.created_at = utcnow()
    return serialize(job, db)


def _delete_job_files(db: Session, job: Job) -> None:
    st = get_storage()
    st.delete_prefix(f"outputs/{job.id}")
    if job.upload_id:
        others = db.scalar(select(func.count()).select_from(Job).where(Job.upload_id == job.upload_id, Job.id != job.id, Job.status.in_(ACTIVE))) or 0
        if not others:
            up = db.get(Upload, job.upload_id)
            if up and up.status != "deleted":
                st.delete_prefix(f"uploads/{up.id}")
                up.status, up.probe = "deleted", None


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    """'Delete now': removes output, source upload (if unused), and the job record."""
    job = get_owned_job(db, sid(request), job_id)
    if job.status == "processing":
        job.cancel_requested = True
        job.message = "Deleting after cancellation"
    _delete_job_files(db, job)
    if job.status != "processing":
        db.delete(job)
    else:
        job.output_key = None
        job.expires_at = utcnow()
    return {"deleted": True}


# ----------------------------------------------------------------------------
# downloads
# ----------------------------------------------------------------------------
@app.post("/api/jobs/{job_id}/download-link")
def download_link(job_id: str, request: Request, db: Session = Depends(get_db)):
    job = get_owned_job(db, sid(request), job_id)
    if job.status == "expired":
        raise OmniError("job_expired")
    if job.status != "completed" or not job.output_key:
        raise OmniError("invalid_settings", "This conversion hasn't finished yet.")
    token = make_download_token("job", job.id, sid(request), ttl=900)
    return {"url": _public_url(f"/api/download?t={token}"), "expires_in": 900}


@app.post("/api/batches/{batch_id}/zip-link")
def zip_link(batch_id: str, request: Request, db: Session = Depends(get_db)):
    count = db.scalar(select(func.count()).select_from(Job).where(Job.batch_id == batch_id, Job.session_id == sid(request), Job.status == "completed")) or 0
    if not count:
        raise OmniError("job_not_found", "No completed items in this batch yet.")
    token = make_download_token("zip", batch_id, sid(request), ttl=900)
    return {"url": _public_url(f"/api/download?t={token}"), "expires_in": 900, "count": count}


def _content_disposition(name: str) -> str:
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_").replace('"', "")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


@app.get("/api/download")
def download(t: str, request: Request, db: Session = Depends(get_db)):
    parsed = verify_download_token(t, sid(request))
    if not parsed:
        raise OmniError("job_not_found", "This download link is invalid, expired, or belongs to another browser session.")
    kind, obj_id = parsed
    st = get_storage()
    if kind == "job":
        job = get_owned_job(db, sid(request), obj_id)
        if job.status != "completed" or not job.output_key:
            raise OmniError("job_expired")
        url = st.presigned_get(job.output_key, job.output_filename, 300)
        if url:
            return RedirectResponse(url, status_code=302)
        return FileResponse(st.path(job.output_key), headers={"Content-Disposition": _content_disposition(job.output_filename)},
                            media_type="application/octet-stream")
    if kind == "zip":
        jobs = db.scalars(select(Job).where(Job.batch_id == obj_id, Job.session_id == sid(request), Job.status == "completed")).all()
        if not jobs:
            raise OmniError("job_expired")
        # Stream objects into a temporary ZIP on disk (never in memory), store-only
        # since media is already compressed, then stream it out and delete it.
        tmp = tempfile.NamedTemporaryFile(prefix="zip-", suffix=".zip", dir=get_settings().scratch_root, delete=False)
        tmp.close()
        used: set[str] = set()
        with zipfile.ZipFile(tmp.name, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for j in jobs:
                name = j.output_filename or f"{j.id}.bin"
                base, dot, ext = name.rpartition(".")
                k = 1
                while name in used:
                    name = f"{base} ({k}).{ext}"
                    k += 1
                used.add(name)
                with st.open(j.output_key) as src, zf.open(name, "w", force_zip64=True) as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
        return FileResponse(tmp.name, media_type="application/zip", headers={"Content-Disposition": _content_disposition("omniconvert-batch.zip")},
                            background=BackgroundTask(lambda: os.unlink(tmp.name)))
    raise OmniError("job_not_found")


# ----------------------------------------------------------------------------
# abuse / copyright reports
# ----------------------------------------------------------------------------
class ReportBody(BaseModel):
    kind: str = Field(pattern="^(copyright|abuse|security|other)$")
    url: str = Field(default="", max_length=2048)
    details: str = Field(min_length=10, max_length=5000)
    contact: str = Field(default="", max_length=320)


@app.post("/api/reports")
def report(body: ReportBody, request: Request, db: Session = Depends(get_db)):
    limiter.check("report", request.state.ip, 5)
    db.add(Report(id=str(uuid.uuid4()), kind=body.kind, url=body.url, details=body.details, contact=body.contact))
    return {"received": True}


@app.on_event("startup")
def _startup():
    setup_logging()
    get_capabilities()
    log.info("api started", extra={"event": "startup", "storage": get_settings().storage_backend})


def _install_cors() -> None:
    s = get_settings()
    if not cors_enabled(s):
        return
    origins = [o.strip() for o in s.cors_origins.split(",") if o.strip()]
    regex = s.cors_origin_regex.strip() or None
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
        allow_headers=["*"],
        expose_headers=["ETag", "Content-Disposition"],
        max_age=600,
    )


_install_cors()

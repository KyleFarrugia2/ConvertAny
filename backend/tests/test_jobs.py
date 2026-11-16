"""Job lifecycle: batches, cancellation, retry, expiry, deletion, isolation,
idempotency, and worker crash recovery."""
from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import time
import zipfile
from datetime import timedelta

from conftest import BACKEND, TMP, convert, ffprobe, make_client, run_worker_until_idle, upload


def test_batch_mixed_success_and_failure_with_zip(client, samples):
    good1 = upload(client, samples["mov"])
    good2 = upload(client, samples["wav"])
    doomed = upload(client, samples["silent"])
    items = [
        {"source": {"upload_id": good1["id"]}, "options": {"output": "mp3"}, "client_id": "a"},
        {"source": {"upload_id": good2["id"]}, "options": {"output": "flac"}, "client_id": "b"},
        {"source": {"upload_id": doomed["id"]}, "options": {"output": "mp4", "quality": "small"}, "client_id": "c"},
        {"source": {"upload_id": doomed["id"]}, "options": {"output": "mp3"}, "client_id": "invalid"},  # no audio -> rejected up front
    ]
    r = client.post("/api/jobs", json={"items": items})
    body = r.json()
    assert r.status_code == 200
    by = {x["client_id"]: x for x in body["results"]}
    assert "error" in by["invalid"] and by["invalid"]["error"]["code"] == "invalid_settings"
    # make job c fail at processing time: its source disappears before the worker runs
    from omniconvert.db import session_scope
    from omniconvert.models import Upload
    with session_scope() as db:
        db.get(Upload, doomed["id"]).status = "deleted"
    run_worker_until_idle()
    states = {cid: client.get(f"/api/jobs/{by[cid]['job']['id']}").json() for cid in "abc"}
    assert states["a"]["status"] == "completed" and states["b"]["status"] == "completed"
    assert states["c"]["status"] == "failed" and states["c"]["error"]["code"] == "job_expired"
    assert states["c"]["error"]["action"]
    link = client.post(f"/api/batches/{body['batch_id']}/zip-link").json()
    assert link["count"] == 2
    z = zipfile.ZipFile(io.BytesIO(client.get(link["url"]).content))
    names = z.namelist()
    assert len(names) == 2 and any(n.endswith(".mp3") for n in names) and any(n.endswith(".flac") for n in names)


def test_idempotent_submission(client, samples):
    up = upload(client, samples["wav"])
    payload = {"items": [{"source": {"upload_id": up["id"]}, "options": {"output": "mp3"}}], "idempotency_key": "abc-123"}
    a = client.post("/api/jobs", json=payload).json()
    b = client.post("/api/jobs", json=payload).json()
    assert b.get("replayed") and a["results"][0]["job"]["id"] == b["results"][0]["job"]["id"]
    run_worker_until_idle()


def test_cancel_running_job_cleans_up(client, samples):
    from omniconvert.worker.main import claim_job, process_job
    from omniconvert.config import get_settings

    up = upload(client, samples["long"])
    r = client.post("/api/jobs", json={"items": [{"source": {"upload_id": up["id"]}, "options": {
        "output": "mp4", "fps_mode": "interpolate", "target_fps": 60, "quality": "high"}}]})
    job_id = r.json()["results"][0]["job"]["id"]
    jid = claim_job("cancel-worker", ["cpu"])
    assert jid == job_id
    t = threading.Thread(target=process_job, args=(jid, "cancel-worker"))
    t.start()
    for _ in range(100):
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["stage"] == "interpolating" and (j["progress"] or 0) > 0:
            break
        time.sleep(0.2)
    assert j["progress"] is not None and 0 < j["progress"] < 1  # real measured progress
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    t.join(timeout=60)
    j = client.get(f"/api/jobs/{job_id}").json()
    assert j["status"] == "cancelled" and j["output"] is None
    assert not list(get_settings().scratch_root.glob(f"job-{job_id}*"))
    # retry is allowed after cancellation and is bounded
    j = client.post(f"/api/jobs/{job_id}/retry").json()
    assert j["status"] == "queued"
    client.post(f"/api/jobs/{job_id}/cancel")


def test_cancel_queued_job(client, samples):
    up = upload(client, samples["wav"])
    jid = client.post("/api/jobs", json={"items": [{"source": {"upload_id": up["id"]}, "options": {"output": "mp3"}}]}).json()["results"][0]["job"]["id"]
    assert client.post(f"/api/jobs/{jid}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/jobs/{jid}/cancel").status_code == 409


def test_expiry_and_delete_now(client, samples):
    from omniconvert.db import session_scope, utcnow
    from omniconvert.models import Job
    from omniconvert.storage import get_storage
    from omniconvert.worker.main import cleanup_expired

    up = upload(client, samples["wav"])
    job = convert(client, up["id"], output="mp3")
    with session_scope() as db:
        key = db.get(Job, job["id"]).output_key
        db.get(Job, job["id"]).expires_at = utcnow() - timedelta(seconds=1)
    assert get_storage().exists(key)
    stats = cleanup_expired()
    assert stats["jobs_expired"] >= 1
    assert not get_storage().exists(key)
    j = client.get(f"/api/jobs/{job['id']}").json()
    assert j["status"] == "expired"
    r = client.post(f"/api/jobs/{job['id']}/download-link")
    assert r.status_code == 410 and r.json()["error"]["code"] == "job_expired"

    # Delete now: output + unused source upload + record removed immediately
    job2 = convert(client, up["id"], output="flac")
    with session_scope() as db:
        key2 = db.get(Job, job2["id"]).output_key
    assert client.delete(f"/api/jobs/{job2['id']}").json()["deleted"]
    assert not get_storage().exists(key2)
    assert not get_storage().exists(f"uploads/{up['id']}/source")
    assert client.get(f"/api/jobs/{job2['id']}").status_code == 404


def test_download_access_isolation(client, samples):
    up = upload(client, samples["wav"])
    job = convert(client, up["id"], output="mp3")
    link = client.post(f"/api/jobs/{job['id']}/download-link").json()["url"]
    other = make_client()
    # knowing the job id or even a valid download URL is not enough
    assert other.get(f"/api/jobs/{job['id']}").status_code == 404
    assert other.post(f"/api/jobs/{job['id']}/download-link").status_code == 404
    assert other.get(link).status_code == 404
    assert other.delete(f"/api/jobs/{job['id']}").status_code == 404
    r = other.post("/api/jobs", json={"items": [{"source": {"upload_id": up["id"]}, "options": {"output": "mp3"}}]})
    assert r.json()["results"][0]["error"]["code"] == "upload_not_found"
    assert job["id"] not in [j["id"] for j in other.get("/api/jobs").json()["jobs"]]
    # tampered token
    assert client.get(link[:-3] + "abc").status_code == 404
    # owner still works
    assert client.get(link).status_code == 200


def test_csrf_header_required(client):
    c = make_client()
    c.headers.pop("x-omniconvert")
    r = c.post("/api/analyze", json={"url": "https://example.com/a.mp4"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf"


def test_lease_expiry_requeues_then_bounds_attempts(client, samples):
    from omniconvert.db import session_scope, utcnow
    from omniconvert.models import Job
    from omniconvert.worker.main import claim_job, reap_stale

    up = upload(client, samples["wav"])
    jid = client.post("/api/jobs", json={"items": [{"source": {"upload_id": up["id"]}, "options": {"output": "mp3"}}]}).json()["results"][0]["job"]["id"]
    for attempt in range(1, 4):
        assert claim_job(f"crashy-{attempt}", ["cpu"]) == jid
        with session_scope() as db:  # simulate a crashed worker: lease lapses
            db.get(Job, jid).lease_until = utcnow() - timedelta(seconds=1)
        reap_stale()
        st = client.get(f"/api/jobs/{jid}").json()
        if attempt < 3:
            assert st["status"] == "queued" and "re-queued" in st["message"]
    assert st["status"] == "failed" and st["error"]["code"] == "worker_lost" and st["can_retry"]


def test_worker_process_killed_mid_job_recovers(client, samples):
    """Kill a real worker process mid-conversion; a fresh worker finishes the job."""
    up = upload(client, samples["long"])
    jid = client.post("/api/jobs", json={"items": [{"source": {"upload_id": up["id"]}, "options": {
        "output": "mp4", "quality": "high", "fps_mode": "interpolate", "target_fps": 60, "edit": {"trim_end": 6}}}]}).json()["results"][0]["job"]["id"]
    env = dict(os.environ, OC_WORKER_LEASE_SECONDS="3", OC_WORKER_CONCURRENCY="1")
    p = subprocess.Popen([sys.executable, "-m", "omniconvert.worker.main", "--queues", "cpu"], cwd=str(BACKEND), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(150):
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] == "processing" and j["stage"] == "interpolating":
            break
        time.sleep(0.2)
    assert j["status"] == "processing"
    p.kill()  # hard crash: no cleanup, no status update
    p.wait()
    time.sleep(4)
    p2 = subprocess.Popen([sys.executable, "-m", "omniconvert.worker.main", "--queues", "cpu"], cwd=str(BACKEND), env=env,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(600):
            j = client.get(f"/api/jobs/{jid}").json()
            if j["status"] in ("completed", "failed"):
                break
            time.sleep(0.25)
    finally:
        p2.terminate()
        p2.wait(timeout=30)
    assert j["status"] == "completed", j
    assert j["attempts"] == 2
    assert j["output"]["fps"] == 60


def test_capabilities_and_health(client):
    caps = client.get("/api/capabilities").json()
    assert caps["formats"]["mp4"]["available"] and caps["formats"]["mp3"]["recommended"]
    assert {s["id"] for s in caps["sources"]} >= {"youtube", "instagram", "facebook", "tiktok", "x", "direct"}
    assert caps["enhancement"]["interpolation"]["minterpolate"]["available"]
    h = client.get("/api/health")
    assert h.status_code == 200 and h.json()["db"] == "ok"

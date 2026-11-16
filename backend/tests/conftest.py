"""Test fixtures. Everything runs against real FFmpeg/FFprobe and a real
(temporary) database + storage; nothing in the media pipeline is mocked."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp(prefix="oc-test-"))
os.environ.update({
    "OC_DATABASE_URL": f"sqlite:///{(TMP / 'test.db').as_posix()}",
    "OC_STORAGE_ROOT": str(TMP / "storage"),
    "OC_SCRATCH_ROOT": str(TMP / "scratch"),
    "OC_SECRET_KEY": "test-secret-key",
    "OC_UPLOAD_CHUNK_BYTES": str(256 * 1024),
    "OC_RATE_GENERAL_PER_MIN": "100000",
    "OC_RATE_JOBS_PER_MIN": "100000",
    "OC_RATE_UPLOAD_PER_MIN": "100000",
    "OC_RATE_ANALYZE_PER_MIN": "100000",
    "OC_WORKER_MIN_FREE_DISK_BYTES": str(50 * 1024**2),
})
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

SAMPLES = TMP / "samples"


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def ffprobe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], capture_output=True, check=True)
    return json.loads(out.stdout)


@pytest.fixture(scope="session", autouse=True)
def _setup_db():
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.upgrade(cfg, "head")
    yield
    shutil.rmtree(TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def samples() -> dict[str, Path]:
    """Authorized synthetic sample media generated locally with FFmpeg."""
    SAMPLES.mkdir(parents=True, exist_ok=True)
    s = {}
    # 6 s 1280x720 30 fps H.264 + AAC stereo in a MOV container
    s["mov"] = SAMPLES / "clip.mov"
    ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
       "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", "-b:a", "160k", str(s["mov"]))
    # portrait 720x1280 webm (vp9/opus)
    s["portrait"] = SAMPLES / "portrait.webm"
    ff("-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30", "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000",
       "-t", "3", "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "500k", "-c:a", "libopus", str(s["portrait"]))
    # 4 s WAV stereo 44.1k
    s["wav"] = SAMPLES / "tone.wav"
    ff("-f", "lavfi", "-i", "sine=frequency=523:sample_rate=44100", "-t", "4", "-ac", "2", "-c:a", "pcm_s16le", str(s["wav"]))
    # silent video (no audio track)
    s["silent"] = SAMPLES / "silent.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(s["silent"]))
    # 24 fps moving test clip for interpolation proofs (a box moving smoothly)
    s["motion"] = SAMPLES / "motion.mp4"
    ff("-f", "lavfi", "-i", "color=c=black:size=320x240:rate=24",
       "-f", "lavfi", "-i", "color=c=white:size=40x40:rate=24",
       "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
       "-filter_complex", "[0:v][1:v]overlay=x='mod(t*160,280)':y=100:shortest=1[v]",
       "-map", "[v]", "-map", "2:a", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "12", "-c:a", "aac", str(s["motion"]))
    # longer clip used for cancellation tests
    s["long"] = SAMPLES / "long.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
       "-t", "40", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(s["long"]))
    # not media at all, but with a media extension
    s["fake"] = SAMPLES / "notvideo.mp4"
    s["fake"].write_bytes(b"This is plain text pretending to be a video.\n" * 100)
    # HLS playlist disguised as .mp4 that points at a cloud metadata endpoint (SSRF attempt)
    s["hls"] = SAMPLES / "evil.mp4"
    s["hls"].write_text("#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nhttp://169.254.169.254/latest/meta-data/\n#EXT-X-ENDLIST\n")
    return s


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from omniconvert.api.main import app

    with TestClient(app) as c:
        c.headers.update({"x-omniconvert": "1"})
        yield c


def make_client():
    from fastapi.testclient import TestClient

    from omniconvert.api.main import app

    c = TestClient(app)
    c.headers.update({"x-omniconvert": "1"})
    return c


def upload(client, path: Path, name: str | None = None) -> dict:
    data = path.read_bytes()
    r = client.post("/api/uploads", json={"filename": name or path.name, "size": len(data)})
    assert r.status_code == 200, r.text
    up = r.json()
    cb = up["chunk_bytes"]
    # send chunks out of order to exercise offset writes/resumability
    order = list(range(up["total_chunks"]))[::-1]
    for i in order:
        rr = client.put(f"/api/uploads/{up['id']}/chunks/{i}", content=data[i * cb:(i + 1) * cb])
        assert rr.status_code == 200, rr.text
    r = client.post(f"/api/uploads/{up['id']}/complete", json={})
    return r.json() | {"_status": r.status_code}


def run_worker_until_idle(timeout: float = 300) -> None:
    """Process queued jobs in-process using the real worker code."""
    from omniconvert.worker.main import claim_job, process_job

    deadline = time.time() + timeout
    while time.time() < deadline:
        jid = claim_job("test-worker", ["cpu", "gpu"])
        if not jid:
            return
        process_job(jid, "test-worker")
    raise TimeoutError("worker did not finish")


def convert(client, upload_id: str, **options) -> dict:
    r = client.post("/api/jobs", json={"items": [{"source": {"upload_id": upload_id}, "options": options}]})
    assert r.status_code == 200, r.text
    res = r.json()["results"][0]
    assert "job" in res, res
    run_worker_until_idle()
    return client.get(f"/api/jobs/{res['job']['id']}").json()


def download(client, job_id: str, dest: Path) -> Path:
    link = client.post(f"/api/jobs/{job_id}/download-link").json()["url"]
    r = client.get(link)
    assert r.status_code == 200, r.text
    dest.write_bytes(r.content)
    return dest

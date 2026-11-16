"""Proves motion interpolation actually synthesizes intermediate frames.

A file reporting 60/120 FPS proves nothing (frame duplication does that too).
We decode the output and hash every frame: duplicated output has ~24/60 unique
frames; interpolated output must have (nearly) every frame unique. We also
check that synthesized frames differ from *every* source frame, i.e. they are
genuinely new images and not reordered copies.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import TMP, convert, download, ffprobe, upload


def frame_hashes(path: Path, scale: str = "320:240") -> list[str]:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-vf", f"scale={scale},format=gray", "-f", "framemd5", "-"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line.rsplit(",", 1)[-1].strip() for line in out.splitlines() if line and not line.startswith("#")]


def _assert_interpolated(src: Path, out: Path, target: int):
    info = ffprobe(out)
    v = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    a = [s for s in info["streams"] if s["codec_type"] == "audio"][0]
    num, den = map(int, v["avg_frame_rate"].split("/"))
    assert abs(num / den - target) < 0.5
    assert abs(float(info["format"]["duration"]) - 2.0) < 0.15  # duration preserved
    assert abs(float(a["duration"]) - float(v["duration"])) < 0.15  # audio in sync
    hashes = frame_hashes(out)
    src_hashes = set(frame_hashes(src))
    unique_ratio = len(set(hashes)) / len(hashes)
    novel = [h for h in hashes if h not in src_hashes]
    # duplication would give unique_ratio ~ 24/target and novel ~ 0
    assert unique_ratio > 0.9, unique_ratio
    assert len(novel) / len(hashes) > 0.5, len(novel) / len(hashes)
    print(f"\n[interp-proof] target={target} frames={len(hashes)} unique_ratio={unique_ratio:.3f} novel_ratio={len(novel)/len(hashes):.3f}")
    return unique_ratio


def test_standard_conversion_only_duplicates(client, samples):
    """Control experiment: the same check FAILS for plain frame-rate conversion."""
    up = upload(client, samples["motion"])
    job = convert(client, up["id"], output="mp4", fps_mode="standard", target_fps=60, quality="original", video_codec="h264")
    out = download(client, job["id"], TMP / "std60.mp4")
    hashes = frame_hashes(out)
    print(f"\n[interp-proof] standard-60 frames={len(hashes)} unique_ratio={len(set(hashes))/len(hashes):.3f}")
    assert len(set(hashes)) / len(hashes) < 0.5


def test_minterpolate_60fps_generates_intermediate_frames(client, samples):
    up = upload(client, samples["motion"])
    p = client.post("/api/plan", json={"source": {"upload_id": up["id"]}, "options": {"output": "mp4", "fps_mode": "interpolate", "target_fps": 60}}).json()
    assert p["valid"] and p["interp"]["engine"] in ("minterpolate", "rife") and p["interp"]["scene_detection"]
    assert p["summary"]["fps_in"] == 24 and p["summary"]["fps_out"] == 60
    job = convert(client, up["id"], output="mp4", fps_mode="interpolate", target_fps=60, quality="original", video_codec="h264")
    assert job["status"] == "completed", job
    assert job["output"]["interpolated"]
    _assert_interpolated(samples["motion"], download(client, job["id"], TMP / "i60.mp4"), 60)


def test_minterpolate_120fps_generates_intermediate_frames(client, samples):
    up = upload(client, samples["motion"])
    job = convert(client, up["id"], output="mp4", fps_mode="interpolate", target_fps=120, quality="original", video_codec="h264", interp_engine="minterpolate")
    assert job["status"] == "completed", job
    _assert_interpolated(samples["motion"], download(client, job["id"], TMP / "i120.mp4"), 120)


def test_interpolation_rules(client, samples):
    up = upload(client, samples["mov"])  # already 30 fps
    p = client.post("/api/plan", json={"source": {"upload_id": up["id"]}, "options": {"output": "mp4", "fps_mode": "interpolate", "target_fps": 30}}).json()
    assert not p["valid"]
    caps = client.get("/api/capabilities").json()["enhancement"]["interpolation"]
    p = client.post("/api/plan", json={"source": {"upload_id": up["id"]}, "options": {"output": "mp4", "fps_mode": "interpolate", "target_fps": 60, "interp_engine": "rife"}}).json()
    if not caps["rife"]["available"]:
        assert not p["valid"] and "interp_engine" in p["errors"]  # never pretends AI is active
    # preview clips limit expensive work
    p = client.post("/api/plan", json={"source": {"upload_id": up["id"]}, "options": {"output": "mp4", "fps_mode": "interpolate", "target_fps": 120, "preview": True}}).json()
    assert p["valid"] and p["summary"]["duration"] <= 4


def test_rife_interpolation_if_installed(client, samples):
    caps = client.get("/api/capabilities").json()["enhancement"]["interpolation"]
    if not caps["rife"]["available"]:
        pytest.skip(f"RIFE not installed: {caps['rife']['reason']}")
    up = upload(client, samples["motion"])
    for target in (60, 120):
        job = convert(client, up["id"], output="mp4", fps_mode="interpolate", target_fps=target, interp_engine="rife", quality="original", video_codec="h264")
        assert job["status"] == "completed", job
        _assert_interpolated(samples["motion"], download(client, job["id"], TMP / f"rife{target}.mp4"), target)

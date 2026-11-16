"""Output verification: a job is only marked complete when the output exists,
probes cleanly, has the expected streams/geometry/duration, and decodes."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..config import get_settings
from ..errors import OmniError
from .capabilities import PROBE_TO_CODEC
from .planner import Plan
from .probe import normalize, run_ffprobe

FULL_DECODE_MAX_SECONDS = 900


def _decode_ok(path: Path, start: float | None = None, dur: float | None = None) -> bool:
    s = get_settings()
    args = [s.ffmpeg_path, "-v", "error", "-xerror", "-nostdin", "-protocol_whitelist", "file"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    if dur:
        args += ["-t", f"{dur:.3f}"]
    args += ["-i", str(path), "-map", "0", "-f", "null", "-"]
    try:
        r = subprocess.run(args, capture_output=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return False
    return r.returncode == 0


def verify_output(path: Path, plan: Plan) -> dict:
    if not path.exists() or path.stat().st_size < 64:
        raise OmniError("output_invalid", "The converter produced no output.")
    try:
        info = normalize(run_ffprobe(path))
    except OmniError:
        raise OmniError("output_invalid")
    problems = []
    if plan.include_video and not info["has_video"]:
        problems.append("missing video stream")
    if plan.include_audio and not info["has_audio"]:
        problems.append("missing audio stream")
    if not plan.include_audio and info["has_audio"]:
        problems.append("unexpected audio stream")
    if plan.category == "audio" and info["has_video"]:
        problems.append("unexpected video stream")
    v = info.get("video") or {}
    if plan.include_video and v and plan.category == "video" and not plan.video_copy:
        if (v.get("width"), v.get("height")) != (plan.out_width, plan.out_height):
            problems.append(f"geometry {v.get('width')}x{v.get('height')} != {plan.out_width}x{plan.out_height}")
        if plan.vcodec and PROBE_TO_CODEC.get(v.get("codec") or "") != plan.vcodec:
            problems.append(f"video codec {v.get('codec')}")
        if plan.fps_mode != "original" and plan.fps_out and v.get("fps") and abs(v["fps"] - plan.fps_out) > 0.6:
            problems.append(f"fps {v.get('fps')} != {plan.fps_out}")
    a = info.get("audio") or {}
    if plan.include_audio and a and plan.acodec and not plan.audio_copy:
        if PROBE_TO_CODEC.get(a.get("codec") or "") != plan.acodec:
            problems.append(f"audio codec {a.get('codec')}")
    dur = info.get("duration") or 0
    tol = max(1.0, plan.duration * 0.03) if plan.category != "gif" else max(1.5, plan.duration * 0.1)
    if plan.duration and abs(dur - plan.duration) > tol:
        problems.append(f"duration {dur:.2f}s vs expected {plan.duration:.2f}s")
    if problems:
        raise OmniError("output_invalid", "The converted file failed verification: " + "; ".join(problems) + ".")
    # Decode check: the whole file when reasonably short, otherwise head + tail.
    if dur <= FULL_DECODE_MAX_SECONDS:
        ok = _decode_ok(path)
    else:
        ok = _decode_ok(path, 0, 60) and _decode_ok(path, max(0.0, dur - 60), 60)
    if not ok:
        raise OmniError("output_invalid", "The converted file couldn't be decoded during verification.")
    info["size"] = path.stat().st_size
    return info

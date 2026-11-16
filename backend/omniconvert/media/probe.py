"""FFprobe inspection of untrusted media.

* ``-protocol_whitelist file`` prevents crafted playlists (HLS/concat/etc.) from
  making FFmpeg open network or other local resources (a classic SSRF vector).
* Files are stored without their original extension, so format detection is
  content-based, and the detected demuxer must be in an allowlist.
"""
from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any, Optional

from ..config import get_settings
from ..errors import OmniError

# Demuxers we accept for uploaded / fetched input.
ALLOWED_DEMUXERS = {"mov", "mp4", "m4a", "3gp", "matroska", "webm", "avi", "mp3", "wav", "aac", "flac", "ogg"}

DISPLAY_FORMAT = {
    "mov,mp4,m4a,3gp,3g2,mj2": "MP4/MOV",
    "matroska,webm": "MKV/WEBM",
    "avi": "AVI",
    "mp3": "MP3",
    "wav": "WAV",
    "aac": "AAC",
    "flac": "FLAC",
    "ogg": "OGG",
}


def _fps(stream: dict) -> Optional[float]:
    for key in ("avg_frame_rate", "r_frame_rate"):
        v = stream.get(key)
        if v and v not in ("0/0", "0/1"):
            try:
                f = float(Fraction(v))
                if 0 < f < 1000:
                    return round(f, 3)
            except (ZeroDivisionError, ValueError):
                continue
    return None


def _rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                return int(round(float(sd["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    try:
        return int(stream.get("tags", {}).get("rotate", 0)) % 360
    except (TypeError, ValueError):
        return 0


def _int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def run_ffprobe(path: Path, timeout: int = 60) -> dict:
    s = get_settings()
    args = [
        s.ffprobe_path, "-v", "error", "-protocol_whitelist", "file",
        "-print_format", "json", "-show_format", "-show_streams", "-show_error",
        str(path),
    ]
    try:
        out = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise OmniError("unsupported_input", "Inspecting the file took too long; it may be corrupt.")
    try:
        data = json.loads(out.stdout or b"{}")
    except json.JSONDecodeError:
        data = {}
    if out.returncode != 0 or "format" not in data:
        raise OmniError("unsupported_input")
    return data


def normalize(data: dict) -> dict:
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    v = next((st for st in streams if st.get("codec_type") == "video" and not (st.get("disposition") or {}).get("attached_pic")), None)
    a = next((st for st in streams if st.get("codec_type") == "audio"), None)
    duration = _float(fmt.get("duration"))
    if duration is None:
        for st in (v, a):
            if st and _float(st.get("duration")):
                duration = _float(st.get("duration"))
                break
    info: dict[str, Any] = {
        "format_name": fmt.get("format_name", ""),
        "format": DISPLAY_FORMAT.get(fmt.get("format_name", ""), (fmt.get("format_name", "") or "").split(",")[0].upper()),
        "duration": round(duration, 3) if duration else None,
        "size": _int(fmt.get("size")),
        "bit_rate": _int(fmt.get("bit_rate")),
        "has_video": v is not None,
        "has_audio": a is not None,
        "video_streams": sum(1 for st in streams if st.get("codec_type") == "video"),
        "audio_streams": sum(1 for st in streams if st.get("codec_type") == "audio"),
        "video": None,
        "audio": None,
    }
    if v:
        w, h = _int(v.get("width")) or 0, _int(v.get("height")) or 0
        rot = _rotation(v)
        dw, dh = (h, w) if rot in (90, 270) else (w, h)
        info["video"] = {
            "codec": v.get("codec_name"),
            "profile": v.get("profile"),
            "width": dw,
            "height": dh,
            "coded_width": w,
            "coded_height": h,
            "rotation": rot,
            "fps": _fps(v),
            "pix_fmt": v.get("pix_fmt"),
            "bit_rate": _int(v.get("bit_rate")),
            "frames": _int(v.get("nb_frames")),
        }
    if a:
        info["audio"] = {
            "codec": a.get("codec_name"),
            "sample_rate": _int(a.get("sample_rate")),
            "channels": _int(a.get("channels")),
            "channel_layout": a.get("channel_layout"),
            "bit_rate": _int(a.get("bit_rate")),
            "bits_per_sample": _int(a.get("bits_per_raw_sample")) or _int(a.get("bits_per_sample")),
        }
    return info


def inspect_media(path: Path, *, enforce_limits: bool = True) -> dict:
    """Probe + validate an untrusted input file. Returns normalized media info."""
    from .capabilities import get_capabilities

    data = run_ffprobe(path)
    info = normalize(data)
    demuxers = set(info["format_name"].split(","))
    if not demuxers & ALLOWED_DEMUXERS:
        raise OmniError("unsupported_input")
    if not info["has_video"] and not info["has_audio"]:
        raise OmniError("unsupported_input", "The file contains no video or audio streams.")
    decoders = get_capabilities().decoders
    for kind in ("video", "audio"):
        st = info.get(kind)
        if st and st.get("codec") not in decoders:
            raise OmniError("unsupported_codec", f"The {kind} codec '{st.get('codec')}' isn't supported.")
    if info["has_video"]:
        vid = info["video"]
        if not vid["width"] or not vid["height"]:
            raise OmniError("unsupported_input", "The video stream has no valid dimensions.")
        if max(vid["width"], vid["height"]) > 8192:
            raise OmniError("unsupported_input", "Video dimensions above 8192 pixels aren't supported.")
    if enforce_limits:
        s = get_settings()
        if info["duration"] and info["duration"] > s.max_duration_seconds:
            raise OmniError("duration_too_long", f"Media is limited to {s.max_duration_seconds // 60} minutes.")
    if not info["duration"]:
        raise OmniError("unsupported_input", "Couldn't determine the media duration; the file may be corrupt or a still image.")
    return info

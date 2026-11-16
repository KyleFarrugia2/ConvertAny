"""Runtime capability detection + the format/codec/enhancement matrix.

Nothing here is assumed: encoders and filters are read from the deployed FFmpeg
build, and optional GPU tools are executed to confirm they actually run.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import get_settings

# --------------------------------------------------------------------------
# Codec registry
# --------------------------------------------------------------------------
VIDEO_CODECS = {
    "h264": {"label": "H.264", "encoders": ["libx264"], "note": "Plays on virtually every device and browser."},
    "hevc": {"label": "H.265 / HEVC", "encoders": ["libx265"], "note": "Smaller files than H.264; limited support in some browsers and older devices."},
    "vp9": {"label": "VP9", "encoders": ["libvpx-vp9"], "note": "Open web codec; good browser support, slower to encode."},
    "av1": {"label": "AV1", "encoders": ["libsvtav1", "libaom-av1"], "note": "Smallest files; needs recent devices and is slow to encode."},
    "prores": {"label": "Apple ProRes 422", "encoders": ["prores_ks"], "note": "Editing codec; very large files."},
}
AUDIO_CODECS = {
    "aac": {"label": "AAC", "encoders": ["aac"], "lossy": True},
    "mp3": {"label": "MP3", "encoders": ["libmp3lame"], "lossy": True},
    "opus": {"label": "Opus", "encoders": ["libopus"], "lossy": True},
    "vorbis": {"label": "Vorbis", "encoders": ["libvorbis"], "lossy": True},
    "flac": {"label": "FLAC (lossless)", "encoders": ["flac"], "lossy": False},
    "pcm": {"label": "PCM (uncompressed)", "encoders": ["pcm_s16le"], "lossy": False},
}

# ffprobe codec_name -> our codec id (for stream-copy decisions)
PROBE_TO_CODEC = {
    "h264": "h264", "hevc": "hevc", "vp9": "vp9", "av1": "av1", "prores": "prores",
    "aac": "aac", "mp3": "mp3", "opus": "opus", "vorbis": "vorbis", "flac": "flac",
    "pcm_s16le": "pcm", "pcm_s24le": "pcm",
}

CONTAINERS = {
    # video
    "mp4": {"category": "video", "label": "MP4", "ext": "mp4", "video": ["h264", "hevc", "av1"], "audio": ["aac", "mp3", "opus"], "muxer": "mp4", "recommended": True},
    "webm": {"category": "video", "label": "WEBM", "ext": "webm", "video": ["vp9", "av1"], "audio": ["opus", "vorbis"], "muxer": "webm"},
    "mov": {"category": "video", "label": "MOV", "ext": "mov", "video": ["h264", "hevc", "prores"], "audio": ["aac", "pcm"], "muxer": "mov"},
    "mkv": {"category": "video", "label": "MKV", "ext": "mkv", "video": ["h264", "hevc", "vp9", "av1"], "audio": ["aac", "opus", "flac", "mp3", "vorbis"], "muxer": "matroska"},
    # audio
    "mp3": {"category": "audio", "label": "MP3", "ext": "mp3", "video": [], "audio": ["mp3"], "muxer": "mp3", "recommended": True},
    "m4a": {"category": "audio", "label": "AAC (M4A)", "ext": "m4a", "video": [], "audio": ["aac"], "muxer": "ipod"},
    "wav": {"category": "audio", "label": "WAV", "ext": "wav", "video": [], "audio": ["pcm"], "muxer": "wav"},
    "flac": {"category": "audio", "label": "FLAC", "ext": "flac", "video": [], "audio": ["flac"], "muxer": "flac"},
    "ogg": {"category": "audio", "label": "OGG", "ext": "ogg", "video": [], "audio": ["vorbis", "opus"], "muxer": "ogg"},
    # additional
    "gif": {"category": "gif", "label": "GIF", "ext": "gif", "video": ["gif"], "audio": [], "muxer": "gif"},
}

REQUIRED_FILTERS = {"scale", "crop", "pad", "transpose", "fps", "volume", "afade", "loudnorm", "palettegen", "paletteuse", "aresample"}


@dataclass
class ToolStatus:
    available: bool
    reason: str = ""
    path: str = ""
    version: str = ""


@dataclass
class Capabilities:
    ffmpeg_version: str = ""
    encoders: set[str] = field(default_factory=set)
    decoders: set[str] = field(default_factory=set)
    filters: set[str] = field(default_factory=set)
    rife: ToolStatus = field(default_factory=lambda: ToolStatus(False, "Not configured"))
    realesrgan: ToolStatus = field(default_factory=lambda: ToolStatus(False, "Not configured"))
    gpu_name: str = ""

    def encoder_for(self, codec: str) -> Optional[str]:
        reg = VIDEO_CODECS.get(codec) or AUDIO_CODECS.get(codec)
        if codec == "gif":
            return "gif" if "gif" in self.encoders else None
        if not reg:
            return None
        return next((e for e in reg["encoders"] if e in self.encoders), None)

    @property
    def minterpolate(self) -> bool:
        return "minterpolate" in self.filters


def _run(args: list[str], timeout: int = 20) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, errors="replace").stdout
    except Exception:
        return ""


def _parse_list(output: str, codec_type: str | None) -> set[str]:
    names = set()
    for line in output.splitlines():
        m = re.match(r"^\s*([VASFXBD.]{6})\s+(\S+)", line)
        if m and (codec_type is None or m.group(1)[0] == codec_type or codec_type == "*"):
            names.add(m.group(2))
    return names


def _check_ncnn_tool(path_s: str, name: str, env_name: str) -> ToolStatus:
    if not path_s:
        return ToolStatus(False, f"{name} is not installed on this server (set {env_name}).")
    path = Path(path_s)
    exe = path if path.exists() else (Path(shutil.which(path_s)) if shutil.which(path_s) else None)
    if not exe or not exe.exists():
        return ToolStatus(False, f"{name} executable not found at the configured path.")
    # The ncnn tools print usage with -h; a GPU/Vulkan failure surfaces at runtime,
    # so we also run a 2-frame self-test once at startup (see enhancement.selftest).
    out = subprocess.run([str(exe), "-h"], capture_output=True, text=True, timeout=20, errors="replace")
    text = (out.stdout or "") + (out.stderr or "")
    if "Usage" not in text and "usage" not in text:
        return ToolStatus(False, f"{name} did not run correctly.")
    return ToolStatus(True, "", str(exe))


_caps: Optional[Capabilities] = None
_lock = threading.Lock()


def get_capabilities(refresh: bool = False) -> Capabilities:
    global _caps
    with _lock:
        if _caps is not None and not refresh:
            return _caps
        s = get_settings()
        c = Capabilities()
        ver = _run([s.ffmpeg_path, "-hide_banner", "-version"])
        c.ffmpeg_version = ver.splitlines()[0] if ver else ""
        c.encoders = _parse_list(_run([s.ffmpeg_path, "-hide_banner", "-encoders"]), "*")
        c.decoders = _parse_list(_run([s.ffmpeg_path, "-hide_banner", "-decoders"]), "*")
        for line in _run([s.ffmpeg_path, "-hide_banner", "-filters"]).splitlines():
            m = re.match(r"^\s*[TSC.]{2,3}\s+(\S+)\s+\S+->\S+", line)
            if m:
                c.filters.add(m.group(1))
        c.rife = _check_ncnn_tool(s.rife_ncnn_path, "rife-ncnn-vulkan", "OC_RIFE_NCNN_PATH")
        c.realesrgan = _check_ncnn_tool(s.realesrgan_ncnn_path, "realesrgan-ncnn-vulkan", "OC_REALESRGAN_NCNN_PATH")
        if c.rife.available or c.realesrgan.available:
            from .enhancement import selftest

            selftest(c)
        smi = shutil.which("nvidia-smi")
        if smi:
            names = _run([smi, "--query-gpu=name", "--format=csv,noheader"]).strip().splitlines()
            c.gpu_name = names[0].strip() if names else ""
        _caps = c
        return c


def capability_matrix() -> dict:
    """Public capability description consumed by the frontend."""
    from ..adapters.registry import source_matrix

    s = get_settings()
    c = get_capabilities()
    formats = {}
    for key, ct in CONTAINERS.items():
        vcodecs = [
            {"id": v, "label": VIDEO_CODECS[v]["label"], "note": VIDEO_CODECS[v]["note"], "available": bool(c.encoder_for(v))}
            for v in ct["video"] if v in VIDEO_CODECS
        ]
        acodecs = [
            {"id": a, "label": AUDIO_CODECS[a]["label"], "lossy": AUDIO_CODECS[a]["lossy"], "available": bool(c.encoder_for(a))}
            for a in ct["audio"]
        ]
        primary_ok = (key == "gif" and c.encoder_for("gif")) or any(v["available"] for v in vcodecs) or (ct["category"] == "audio" and any(a["available"] for a in acodecs))
        formats[key] = {
            "id": key, "label": ct["label"], "category": ct["category"], "ext": ct["ext"],
            "recommended": ct.get("recommended", False),
            "available": bool(primary_ok),
            "video_codecs": vcodecs, "audio_codecs": acodecs,
        }
    interp_cpu = c.minterpolate
    return {
        "ffmpeg": c.ffmpeg_version,
        "formats": formats,
        "enhancement": {
            "interpolation": {
                "minterpolate": {
                    "available": interp_cpu,
                    "label": "CPU motion-compensated interpolation (FFmpeg minterpolate)",
                    "reason": "" if interp_cpu else "This FFmpeg build lacks the minterpolate filter.",
                    "speed": "Slow: typically well below real time on CPU, especially at 1080p and 120 FPS.",
                    "max_height": s.interp_cpu_max_height,
                    "max_seconds": {"60": s.interp_cpu_max_seconds_60, "120": s.interp_cpu_max_seconds_120},
                    "scene_detection": True,
                },
                "rife": {
                    "available": c.rife.available,
                    "label": "GPU AI interpolation (RIFE via rife-ncnn-vulkan)",
                    "reason": c.rife.reason,
                    "speed": "Fast on a supported GPU.",
                    "max_height": s.interp_gpu_max_height,
                    "max_seconds": {"60": s.interp_gpu_max_seconds_60, "120": s.interp_gpu_max_seconds_120},
                    "scene_detection": True,
                },
                "fps120_enabled": s.enable_120fps,
            },
            "upscale": {
                "standard": {"available": "scale" in c.filters, "label": "Standard resize (Lanczos)"},
                "ai": {
                    "available": c.realesrgan.available,
                    "label": "AI upscaling (Real-ESRGAN via realesrgan-ncnn-vulkan)",
                    "reason": c.realesrgan.reason,
                    "max_input_height": s.upscale_ai_max_input_height,
                    "max_seconds": s.upscale_ai_max_seconds,
                },
            },
            "loudnorm": "loudnorm" in c.filters,
            "gpu": c.gpu_name,
        },
        "sources": source_matrix(),
        "limits": {
            "max_upload_bytes": s.max_upload_bytes,
            "max_url_download_bytes": s.max_url_download_bytes,
            "max_duration_seconds": s.max_duration_seconds,
            "max_gif_seconds": s.max_gif_seconds,
            "upload_chunk_bytes": s.upload_chunk_bytes,
            "max_files_per_batch": s.max_files_per_batch,
            "max_url_items_per_batch": s.max_url_items_per_batch,
            "file_ttl_seconds": s.file_ttl_seconds,
            "max_output_height": s.max_output_height,
            "preview_seconds": s.preview_seconds,
        },
        "accepted_inputs": ["mp4", "mov", "mkv", "webm", "avi", "m4v", "mp3", "wav", "aac", "m4a", "flac", "ogg"],
        "abuse_contact_email": s.abuse_contact_email,
    }

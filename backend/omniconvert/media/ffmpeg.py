"""FFmpeg command construction and supervised execution.

Commands are always argument arrays (never shell strings). Inputs are opened
with ``-protocol_whitelist file,pipe`` so media files can't make FFmpeg fetch
remote or arbitrary resources.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from ..config import get_settings
from ..errors import OmniError
from .planner import Plan

log = logging.getLogger("omniconvert.ffmpeg")


class Cancelled(Exception):
    pass


def _base(s) -> list[str]:
    return [s.ffmpeg_path, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats"]


def _input(path: Path, start: float, duration: float, trimmed: bool) -> list[str]:
    args = ["-protocol_whitelist", "file,pipe"]
    if trimmed:
        if start > 0:
            args += ["-ss", f"{start:.3f}"]
        args += ["-t", f"{duration:.3f}"]
    return args + ["-i", str(path)]


def _video_chain(plan: Plan, *, with_fps: bool = True, with_scale: bool = True, with_post: bool = True) -> list[str]:
    fps = plan.vf_fps if with_fps else []
    scale = plan.vf_scale if with_scale else []
    mid = (scale + fps) if plan.scale_first else (fps + scale)
    return plan.vf_pre + mid + (plan.vf_post if with_post else [])


def _meta_args(plan: Plan, strip: bool) -> list[str]:
    args: list[str] = []
    if strip:
        args += ["-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact"]
    if plan.muxer in ("mp4", "mov", "ipod"):
        args += ["-movflags", "+faststart"]
    return args


def build_onepass(plan: Plan, src: Path, out: Path, strip_metadata: bool) -> list[str]:
    s = get_settings()
    args = _base(s) + _input(src, plan.start, plan.duration, plan.trimmed)
    if plan.category == "gif":
        chain = "[0:v:0]" + ",".join(plan.vf_pre + plan.vf_scale + [plan.vf_post[0]]) + ";" + ";".join(plan.vf_post[1:]) + "[gout]"
        return args + ["-filter_complex", chain, "-map", "[gout]", *plan.v_args, "-f", "gif", str(out)]

    if plan.include_video:
        args += ["-map", "0:v:0"]
    if plan.include_audio:
        args += ["-map", "0:a:0"]
    if plan.include_video:
        chain = _video_chain(plan)
        if chain and not plan.video_copy:
            args += ["-vf", ",".join(chain)]
        args += plan.v_args
        if plan.fps_mode != "original" and not plan.video_copy and plan.fps_out:
            args += ["-r", f"{plan.fps_out:g}"]
    else:
        args += ["-vn"]
    if plan.include_audio:
        if plan.af and not plan.audio_copy:
            args += ["-af", ",".join(plan.af)]
        args += plan.a_args
    else:
        args += ["-an"]
    args += ["-sn", "-dn", "-max_muxing_queue_size", "4096"]
    args += _meta_args(plan, strip_metadata)
    return args + ["-f", plan.muxer, str(out)]


def build_extract_frames(plan: Plan, src: Path, frames_dir: Path) -> list[str]:
    """Decode (with crop/rotate/downscale) to lossless PNG frames for GPU tools."""
    s = get_settings()
    chain = plan.vf_pre + (plan.vf_scale if plan.scale_first else []) + ["format=rgb24"]
    return _base(s) + _input(src, plan.start, plan.duration, plan.trimmed) + [
        "-map", "0:v:0", "-vf", ",".join(chain), "-fps_mode", "passthrough", "-start_number", "1",
        str(frames_dir / "%08d.png"),
    ]


def build_encode_frames(plan: Plan, frames_dir: Path, fps: float, src: Path, out: Path, strip_metadata: bool) -> list[str]:
    s = get_settings()
    args = _base(s) + ["-protocol_whitelist", "file,pipe", "-framerate", f"{fps:.6f}", "-start_number", "1", "-i", str(frames_dir / "%08d.png")]
    if plan.include_audio:
        args += _input(src, plan.start, plan.duration, plan.trimmed)
    args += ["-map", "0:v:0"]
    if plan.include_audio:
        args += ["-map", "1:a:0"]
    chain = ([] if plan.scale_first else plan.vf_scale) + plan.vf_post
    if chain:
        args += ["-vf", ",".join(chain)]
    args += plan.v_args
    if plan.include_audio:
        if plan.af and not plan.audio_copy:
            args += ["-af", ",".join(plan.af)]
        args += plan.a_args
        args += ["-shortest"]
    else:
        args += ["-an"]
    args += ["-max_muxing_queue_size", "4096"] + _meta_args(plan, strip_metadata)
    return args + ["-f", plan.muxer, str(out)]


def _limits():  # pragma: no cover - POSIX only
    """Runs in the child before exec: resource limits + die with the worker."""
    import resource
    import signal as _signal

    s = get_settings()
    resource.setrlimit(resource.RLIMIT_CPU, (s.worker_cpu_seconds_limit, s.worker_cpu_seconds_limit))
    resource.setrlimit(resource.RLIMIT_AS, (s.worker_memory_limit_bytes, s.worker_memory_limit_bytes))
    if sys.platform.startswith("linux"):
        import ctypes

        PR_SET_PDEATHSIG = 1
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, _signal.SIGKILL)


class _WinJob:  # pragma: no cover - Windows only
    """A Windows Job Object with KILL_ON_JOB_CLOSE: when the worker process dies
    (even via hard kill), Windows terminates every media process assigned to it,
    so orphaned FFmpeg processes can't keep running or lock scratch files."""

    _handle = None
    _lock = threading.Lock()

    @classmethod
    def assign(cls, proc: subprocess.Popen) -> None:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        with cls._lock:
            if cls._handle is None:
                class IO_COUNTERS(ctypes.Structure):
                    _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

                class BASIC(ctypes.Structure):
                    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

                class EXTENDED(ctypes.Structure):
                    _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

                k32.CreateJobObjectW.restype = wintypes.HANDLE
                h = k32.CreateJobObjectW(None, None)
                info = EXTENDED()
                info.BasicLimitInformation.LimitFlags = 0x2000 | 0x100  # KILL_ON_JOB_CLOSE | PROCESS_MEMORY
                info.ProcessMemoryLimit = get_settings().worker_memory_limit_bytes
                k32.SetInformationJobObject(wintypes.HANDLE(h), 9, ctypes.byref(info), ctypes.sizeof(info))
                cls._handle = h
        k32.AssignProcessToJobObject(wintypes.HANDLE(cls._handle), wintypes.HANDLE(int(proc._handle)))


def run_process(
    args: list[str],
    *,
    expected_seconds: Optional[float] = None,
    on_progress: Optional[Callable[[Optional[float]], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    timeout: Optional[float] = None,
    parse_ffmpeg_progress: bool = True,
    poll_progress: Optional[Callable[[], Optional[float]]] = None,
) -> None:
    """Run a media tool with progress reporting, cancellation, and a timeout."""
    s = get_settings()
    timeout = timeout or s.job_timeout_seconds
    stderr_tail: deque[str] = deque(maxlen=40)
    err_file = tempfile.TemporaryFile()
    kwargs: dict = {}
    if sys.platform != "win32":
        kwargs["preexec_fn"] = _limits
    else:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
    proc = subprocess.Popen(args, stdout=subprocess.PIPE if parse_ffmpeg_progress else subprocess.DEVNULL, stderr=err_file, stdin=subprocess.DEVNULL, **kwargs)
    if sys.platform == "win32":
        try:
            _WinJob.assign(proc)
        except Exception:
            log.warning("could not assign media process to job object")
    deadline = time.monotonic() + timeout
    progress_state: dict = {"t": None}

    def reader():
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    us = int(line.split("=", 1)[1])
                    progress_state["t"] = max(0.0, us / 1_000_000)
                except ValueError:
                    pass

    th = None
    if parse_ffmpeg_progress:
        th = threading.Thread(target=reader, daemon=True)
        th.start()
    last_report = 0.0
    try:
        while proc.poll() is None:
            time.sleep(0.25)
            if should_cancel and should_cancel():
                proc.kill()
                proc.wait()
                raise Cancelled()
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                raise OmniError("timeout")
            now = time.monotonic()
            if on_progress and now - last_report > 0.75:
                last_report = now
                frac = None
                if poll_progress:
                    frac = poll_progress()
                elif expected_seconds and progress_state["t"] is not None:
                    frac = min(0.999, progress_state["t"] / expected_seconds)
                on_progress(frac)
        if th:
            th.join(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
        err_file.seek(0)
        for line in err_file.read().decode("utf-8", "replace").splitlines():
            stderr_tail.append(line[:300])
        err_file.close()
    if proc.returncode != 0:
        tail = "\n".join(stderr_tail)
        log.error("media tool failed", extra={"event": "tool_failed", "tool": Path(args[0]).name, "rc": proc.returncode, "stderr": tail[-2000:]})
        low = tail.lower()
        if any(k in low for k in ("malloc of size", "cannot allocate memory", "out of memory", "failed to allocate")):
            raise OmniError("out_of_memory", retryable=False)
        if "no space left" in low:
            raise OmniError("insufficient_disk")
        if "decoder" in low and "not found" in low or "invalid data found" in low:
            raise OmniError("unsupported_codec", retryable=False)
        raise OmniError("processing_failed")

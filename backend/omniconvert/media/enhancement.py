"""GPU enhancement pipeline (RIFE interpolation, Real-ESRGAN upscaling).

Pipeline: decode → PNG frames → [scene-split RIFE] → [Real-ESRGAN] → encode with
original audio. Each tool's availability is verified at startup by a real
self-test on generated frames; if the self-test fails the feature is reported
as unavailable rather than silently falling back to frame duplication.

Scene handling: cuts are detected with FFmpeg's ``scdet`` filter, frames are
interpolated per scene, and no frames are synthesized *across* a cut (the last
frame of a scene is repeated to fill its slot), which avoids ghosted blends
between unrelated shots.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from ..config import get_settings
from ..errors import OmniError
from .ffmpeg import build_encode_frames, build_extract_frames, run_process
from .planner import Plan

log = logging.getLogger("omniconvert.enhance")


def _count(d: Path) -> int:
    try:
        return sum(1 for _ in os.scandir(d))
    except FileNotFoundError:
        return 0


def selftest(caps) -> None:
    """Run each configured GPU tool on tiny generated frames; disable on failure."""
    s = get_settings()
    with tempfile.TemporaryDirectory(dir=s.scratch_root) as td:
        tdp = Path(td)
        src = tdp / "src"
        src.mkdir()
        r = subprocess.run(
            [s.ffmpeg_path, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=2", "-frames:v", "2", "-start_number", "1", str(src / "%08d.png")],
            capture_output=True, timeout=60,
        )
        if r.returncode != 0:
            return
        if caps.rife.available:
            out = tdp / "rife"
            out.mkdir()
            exe = Path(caps.rife.path)
            try:
                rr = subprocess.run([str(exe), "-i", str(src), "-o", str(out), "-n", "4", "-m", str(exe.parent / s.rife_model), "-f", "%08d.png"], capture_output=True, timeout=120)
                ok = rr.returncode == 0 and _count(out) == 4
            except Exception:
                ok = False
            if not ok:
                caps.rife.available = False
                caps.rife.reason = "The RIFE self-test failed (no working Vulkan GPU or missing model files)."
        if caps.realesrgan.available:
            out = tdp / "esr"
            out.mkdir()
            exe = Path(caps.realesrgan.path)
            try:
                rr = subprocess.run([str(exe), "-i", str(src), "-o", str(out), "-n", s.realesrgan_model, "-s", "2", "-f", "png", "-m", str(exe.parent / "models")], capture_output=True, timeout=120)
                ok = rr.returncode == 0 and _count(out) == 2
            except Exception:
                ok = False
            if not ok:
                caps.realesrgan.available = False
                caps.realesrgan.reason = "The Real-ESRGAN self-test failed (no working Vulkan GPU or missing model files)."


def detect_scene_cuts(frames_dir: Path, fps: float, threshold: float = 10.0) -> list[int]:
    """Return 0-based indices of frames that start a new scene."""
    s = get_settings()
    meta = frames_dir.parent / "scenes.txt"
    subprocess.run(
        [s.ffmpeg_path, "-v", "error", "-nostdin", "-framerate", f"{fps:.6f}", "-start_number", "1", "-i", "frames/%08d.png",
         "-vf", f"scdet=threshold={threshold},metadata=print:key=lavfi.scd.time:file=scenes.txt", "-f", "null", "-"],
        cwd=str(frames_dir.parent), capture_output=True, timeout=3600,
    )
    cuts = []
    if meta.exists():
        for line in meta.read_text(errors="replace").splitlines():
            m = re.match(r"frame:(\d+)", line)
            if m and int(m.group(1)) > 0:
                cuts.append(int(m.group(1)))
    return sorted(set(cuts))


def _disk_check(work: Path, frames: int, w: int, h: int, multiplier: float) -> None:
    s = get_settings()
    need = int(frames * w * h * 3 * 0.55 * (1 + multiplier))
    free = shutil.disk_usage(work).free
    if free - need < s.worker_min_free_disk_bytes:
        raise OmniError("insufficient_disk")


def run_external(
    plan: Plan,
    src: Path,
    out: Path,
    work: Path,
    *,
    strip_metadata: bool,
    set_stage: Callable[[str, Optional[float]], None],
    should_cancel: Callable[[], bool],
) -> None:
    from .capabilities import get_capabilities

    s = get_settings()
    caps = get_capabilities()
    frames = work / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    src_fps = float(plan.fps_in or 30.0)
    expected_frames = max(1, int(round(plan.duration * src_fps)))
    w0 = plan.out_width if plan.scale_first else (plan.src_width or plan.out_width)
    h0 = plan.out_height if plan.scale_first else (plan.src_height or plan.out_height)
    ratio = (plan.interp["target_fps"] / src_fps) if plan.interp else 1.0
    up = plan.upscale["factor"] if plan.upscale and plan.upscale.get("mode") == "ai" else 1
    _disk_check(work, expected_frames, w0, h0, ratio * up * up + ratio)

    # 1. decode
    set_stage("converting", 0.0)
    run_process(build_extract_frames(plan, src, frames), expected_seconds=plan.duration,
                on_progress=lambda f: set_stage("converting", f), should_cancel=should_cancel)
    n_in = _count(frames)
    if n_in == 0:
        raise OmniError("processing_failed", "No frames could be decoded from the source.")
    current, fps = frames, src_fps

    # 2. interpolate per scene
    if plan.interp:
        set_stage("interpolating", 0.0)
        target = float(plan.interp["target_fps"])
        exe = Path(caps.rife.path)
        cuts = [0] + detect_scene_cuts(frames, src_fps) + [n_in]
        files = sorted(frames.iterdir())
        total_out = int(round(n_in * target / src_fps))
        interp_dir = work / "interp"
        interp_dir.mkdir()
        produced = 0
        out_index = 1
        for si in range(len(cuts) - 1):
            a, b = cuts[si], cuts[si + 1]
            if b <= a:
                continue
            seg = files[a:b]
            # frames this scene should occupy, keeping cumulative timing exact
            seg_target = int(round(b * target / src_fps)) - int(round(a * target / src_fps))
            seg_target = max(seg_target, 1)
            if len(seg) < 2:
                for _ in range(seg_target):
                    shutil.copyfile(seg[0], interp_dir / f"{out_index:08d}.png")
                    out_index += 1
                continue
            seg_in, seg_out = work / f"seg_in_{si}", work / f"seg_out_{si}"
            seg_in.mkdir()
            seg_out.mkdir()
            for i, f in enumerate(seg, 1):
                try:
                    os.link(f, seg_in / f"{i:08d}.png")
                except OSError:
                    shutil.copyfile(f, seg_in / f"{i:08d}.png")
            base_done = produced
            run_process(
                [str(exe), "-i", str(seg_in), "-o", str(seg_out), "-n", str(seg_target), "-m", str(exe.parent / s.rife_model), "-f", "%08d.png"],
                parse_ffmpeg_progress=False, should_cancel=should_cancel,
                on_progress=lambda f: set_stage("interpolating", f),
                poll_progress=lambda: min(0.999, (base_done + _count(seg_out)) / max(1, total_out)),
            )
            outs = sorted(seg_out.iterdir())
            if len(outs) != seg_target:
                raise OmniError("processing_failed", "Frame interpolation produced an unexpected number of frames.")
            for f in outs:
                os.replace(f, interp_dir / f"{out_index:08d}.png")
                out_index += 1
            produced += seg_target
            shutil.rmtree(seg_in, ignore_errors=True)
            shutil.rmtree(seg_out, ignore_errors=True)
        shutil.rmtree(frames, ignore_errors=True)
        current, fps = interp_dir, target

    # 3. AI upscale
    if up > 1:
        set_stage("enhancing", 0.0)
        exe = Path(caps.realesrgan.path)
        up_dir = work / "upscaled"
        up_dir.mkdir()
        n = _count(current)
        run_process(
            [str(exe), "-i", str(current), "-o", str(up_dir), "-n", s.realesrgan_model, "-s", str(up), "-f", "png", "-m", str(exe.parent / "models")],
            parse_ffmpeg_progress=False, should_cancel=should_cancel,
            on_progress=lambda f: set_stage("enhancing", f),
            poll_progress=lambda: min(0.999, _count(up_dir) / max(1, n)),
        )
        if _count(up_dir) != n:
            raise OmniError("processing_failed", "AI upscaling produced an unexpected number of frames.")
        shutil.rmtree(current, ignore_errors=True)
        current = up_dir
        # final exact-size resize happens in the encode step
        plan.scale_first = False

    # 4. encode with original audio
    set_stage("finalizing", 0.0)
    run_process(build_encode_frames(plan, current, fps, src, out, strip_metadata), expected_seconds=plan.duration,
                on_progress=lambda f: set_stage("finalizing", f), should_cancel=should_cancel)
    shutil.rmtree(current, ignore_errors=True)

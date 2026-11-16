"""Turns (options, source media info) into a validated, executable plan.

The same planner is used by the API (to validate & estimate before a job is
queued, and to show the "before processing" summary) and by the worker (to
re-plan against the actually fetched file).
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from ..config import get_settings
from .capabilities import AUDIO_CODECS, CONTAINERS, PROBE_TO_CODEC, VIDEO_CODECS, Capabilities, get_capabilities
from .options import ConvertOptions

# CRF per codec per quality preset ("original" = visually transparent target when re-encoding).
CRF = {
    "h264": {"original": 17, "high": 20, "balanced": 23, "small": 28},
    "hevc": {"original": 19, "high": 22, "balanced": 26, "small": 30},
    "vp9": {"original": 24, "high": 30, "balanced": 34, "small": 40},
    "av1": {"original": 22, "high": 28, "balanced": 34, "small": 42},
}
PRORES_PROFILE = {"original": 3, "high": 3, "balanced": 2, "small": 1}
AUDIO_DEFAULT_KBPS = {
    "aac": {"original": 256, "high": 192, "balanced": 160, "small": 96},
    "mp3": {"original": 320, "high": 256, "balanced": 192, "small": 128},
    "opus": {"original": 192, "high": 160, "balanced": 128, "small": 64},
    "vorbis": {"original": 256, "high": 192, "balanced": 160, "small": 96},
}
# bits-per-pixel-per-frame heuristics for size estimates (H.264 baseline values)
BPP = {"original": 0.14, "high": 0.10, "balanced": 0.07, "small": 0.04}
CODEC_EFFICIENCY = {"h264": 1.0, "hevc": 0.65, "vp9": 0.7, "av1": 0.5, "prores": 20.0}
OPUS_RATES = {8000, 12000, 16000, 24000, 48000}
MINTERP = "minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1:scd=fdiff:scd_threshold=10"


def even(n: float) -> int:
    """Nearest even integer (yuv420p needs even dimensions)."""
    return max(2, 2 * int(round(n / 2)))


_FILENAME_BAD = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]+')


def sanitize_filename(name: str, ext: str, fallback: str = "omniconvert") -> str:
    name = unicodedata.normalize("NFKC", name or "")
    name = re.sub(r"\.(mp4|webm|mov|mkv|mp3|m4a|wav|flac|ogg|gif|aac|avi|m4v)$", "", name, flags=re.I)
    name = _FILENAME_BAD.sub("_", name).strip(" ._")
    name = re.sub(r"\s+", " ", name)[:150]
    if not name or name.upper() in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}:
        name = fallback
    return f"{name}.{ext}"


@dataclass
class Plan:
    errors: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    category: str = "video"
    container: str = "mp4"
    ext: str = "mp4"
    muxer: str = "mp4"
    vcodec: Optional[str] = None
    acodec: Optional[str] = None
    v_encoder: Optional[str] = None
    a_encoder: Optional[str] = None
    stream_copy: bool = False
    video_copy: bool = False
    audio_copy: bool = False
    include_audio: bool = True
    include_video: bool = True
    start: float = 0.0
    duration: float = 0.0
    trimmed: bool = False
    src_width: int = 0
    src_height: int = 0
    out_width: int = 0
    out_height: int = 0
    src_short: int = 0
    out_short: int = 0
    upscaling: bool = False
    fps_in: Optional[float] = None
    fps_out: Optional[float] = None
    fps_mode: str = "original"
    interp: Optional[dict] = None
    upscale: Optional[dict] = None
    external: bool = False  # needs frame-based GPU pipeline
    scale_first: bool = True
    vf_pre: list[str] = field(default_factory=list)
    vf_scale: list[str] = field(default_factory=list)
    vf_fps: list[str] = field(default_factory=list)
    vf_post: list[str] = field(default_factory=list)
    af: list[str] = field(default_factory=list)
    v_args: list[str] = field(default_factory=list)
    a_args: list[str] = field(default_factory=list)
    out_sample_rate: Optional[int] = None
    out_channels: Optional[int] = None
    audio_kbps: Optional[int] = None
    stages: list[str] = field(default_factory=list)
    queue: str = "cpu"
    pipeline_kind: str = ""
    estimate: dict = field(default_factory=dict)
    filename: str = ""
    summary: dict = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        d = asdict(self)
        d["valid"] = self.valid
        return d


def _resolve_audio(plan: Plan, o: ConvertOptions, media: dict, caps: Capabilities) -> None:
    ct = CONTAINERS[o.output]
    src_a = media.get("audio") or {}
    if not plan.include_audio:
        return
    acodec = o.audio_codec if o.audio_codec != "auto" else None
    src_codec = PROBE_TO_CODEC.get(src_a.get("codec") or "")
    if acodec is None:
        # When preserving quality, keep the source codec if the container accepts it.
        acodec = src_codec if (o.quality == "original" and src_codec in ct["audio"]) else ct["audio"][0]
    if acodec not in ct["audio"]:
        plan.errors["audio_codec"] = f"{AUDIO_CODECS.get(acodec, {}).get('label', acodec)} audio can't be stored in {ct['label']}."
        return
    enc = caps.encoder_for(acodec)
    if not enc:
        plan.errors["audio_codec"] = f"The {AUDIO_CODECS[acodec]['label']} encoder isn't available on this server."
        return
    plan.acodec, plan.a_encoder = acodec, enc
    lossy = AUDIO_CODECS[acodec]["lossy"]

    # filters
    e = o.edit
    if e.volume != 100:
        plan.af.append(f"volume={e.volume / 100:.2f}")
    if e.fade_in > 0:
        plan.af.append(f"afade=t=in:st=0:d={min(e.fade_in, plan.duration):.3f}")
    if e.fade_out > 0:
        d = min(e.fade_out, plan.duration)
        plan.af.append(f"afade=t=out:st={max(0.0, plan.duration - d):.3f}:d={d:.3f}")
    if o.normalize:
        if "loudnorm" not in caps.filters:
            plan.errors["normalize"] = "Loudness normalization isn't available on this server."
        else:
            plan.af.append("loudnorm=I=-16:TP=-1.5:LRA=11")

    # sample rate / channels
    src_sr = src_a.get("sample_rate") or 48000
    sr = o.sample_rate
    if acodec == "opus":
        if sr and sr not in OPUS_RATES:
            plan.errors["sample_rate"] = "Opus supports 48 kHz (or lower telephone rates); choose 48 kHz."
        sr = sr or (src_sr if src_sr in OPUS_RATES else 48000)
    if acodec == "mp3" and sr and sr > 48000:
        plan.errors["sample_rate"] = "MP3 supports sample rates up to 48 kHz."
    if acodec == "mp3" and not sr and src_sr > 48000:
        sr = 48000
    if o.normalize and not sr:
        sr = src_sr if src_sr <= 48000 else 48000  # loudnorm upsamples internally; pin output rate
    plan.out_sample_rate = sr or src_sr
    ch = {"mono": 1, "stereo": 2}.get(o.channels)
    src_ch = src_a.get("channels") or 2
    if acodec == "mp3" and not ch and src_ch > 2:
        ch = 2
    plan.out_channels = ch or src_ch

    unchanged = not plan.af and not o.sample_rate and not ch and (sr in (None, src_sr)) and not plan.trimmed
    plan.audio_copy = bool(
        unchanged and src_codec == acodec and o.quality == "original" and o.audio_bitrate_kbps is None
    )
    if plan.audio_copy:
        plan.a_args = ["-c:a", "copy"]
        plan.audio_kbps = (src_a.get("bit_rate") or 0) // 1000 or None
        return

    args = ["-c:a", enc]
    if lossy:
        kbps = o.audio_bitrate_kbps or AUDIO_DEFAULT_KBPS[acodec][o.quality]
        if acodec == "opus" and kbps > 256:
            kbps = 256
        plan.audio_kbps = kbps
        args += ["-b:a", f"{kbps}k"]
        src_kbps = (src_a.get("bit_rate") or 0) // 1000
        src_lossy = PROBE_TO_CODEC.get(src_a.get("codec") or "") in ("aac", "mp3", "opus", "vorbis")
        if src_lossy and src_kbps and kbps > src_kbps * 1.25:
            plan.notes.append(f"The source audio is about {src_kbps} kbps. A higher bitrate can't restore detail that was already removed.")
    else:
        if acodec == "pcm":
            args = ["-c:a", "pcm_s24le" if o.wav_bit_depth == 24 else "pcm_s16le"]
        elif acodec == "flac":
            args += ["-compression_level", str(o.flac_compression)]
        if PROBE_TO_CODEC.get(src_a.get("codec") or "") in ("aac", "mp3", "opus", "vorbis"):
            plan.notes.append("The source audio is compressed. Converting to a lossless format preserves what's there but can't restore detail that's already been removed.")
    if sr and sr != src_sr or o.sample_rate or o.normalize:
        args += ["-ar", str(plan.out_sample_rate)]
    if ch:
        args += ["-ac", str(ch)]
    plan.a_args = args


def build_plan(
    o: ConvertOptions,
    media: dict,
    *,
    source_type: str = "upload",
    caps: Optional[Capabilities] = None,
    history_speed: Optional[tuple[float, int]] = None,
    worker: bool = False,
    title: str = "",
) -> Plan:
    s = get_settings()
    caps = caps or get_capabilities()
    plan = Plan()
    ct = CONTAINERS[o.output]
    plan.container, plan.ext, plan.muxer, plan.category = o.output, ct["ext"], ct["muxer"], ct["category"]
    e = o.edit
    src_v = media.get("video") or {}
    src_dur = float(media.get("duration") or 0)
    has_v, has_a = bool(media.get("has_video")), bool(media.get("has_audio"))

    # ---------------- source / category compatibility --------------------
    if plan.category in ("video", "gif") and not has_v:
        plan.errors["output"] = "This source has no video track. Choose an audio format instead."
        return plan
    if plan.category == "audio" and not has_a:
        plan.errors["output"] = "This source has no audio track to extract."
        return plan
    plan.include_video = plan.category in ("video", "gif")
    plan.include_audio = has_a and (plan.category == "audio" or (plan.category == "video" and o.include_audio))
    if plan.category == "video" and o.include_audio and not has_a:
        plan.notes.append("The source has no audio, so the output will be silent.")

    # ---------------- trim / duration -----------------------------------
    start = e.trim_start or 0.0
    end = e.trim_end if e.trim_end is not None else src_dur
    if src_dur and end > src_dur + 0.05:
        end = src_dur
    if src_dur and start >= src_dur:
        plan.errors["trim_start"] = "Start time is beyond the end of the media."
    if not src_dur and e.trim_end is None:
        # Some sources (e.g. direct links) don't advertise a duration; the worker
        # re-plans against the fetched file, where it's known.
        end = start
        plan.notes.append("Duration will be confirmed after the source is fetched.")
    elif end <= start:
        plan.errors["trim_end"] = "End time must be after the start time."
    if o.preview:
        end = min(end, start + s.preview_seconds)
        plan.notes.append(f"Preview: only {end - start:.0f} seconds starting at {start:.1f}s will be processed.")
    plan.start = round(start, 3)
    plan.duration = round(max(0.0, end - start), 3)
    plan.trimmed = start > 0.01 or (src_dur and end < src_dur - 0.05) or o.preview
    if plan.duration > s.max_duration_seconds:
        plan.errors["trim_end"] = f"Output is limited to {s.max_duration_seconds // 60} minutes."

    # ---------------- video ---------------------------------------------
    if plan.include_video:
        w, h = int(src_v.get("width") or 0), int(src_v.get("height") or 0)
        plan.src_width, plan.src_height = w, h
        plan.fps_in = src_v.get("fps")
        if e.crop:
            c = e.crop
            if w and h and (c.x + c.w > w or c.y + c.h > h):
                plan.errors["crop"] = f"Crop area must fit inside the {w}×{h} frame."
            else:
                cw, ch_ = even(c.w), even(c.h)
                plan.vf_pre.append(f"crop={cw}:{ch_}:{c.x}:{c.y}")
                w, h = cw, ch_
        if e.rotate == 90:
            plan.vf_pre.append("transpose=1")
        elif e.rotate == 270:
            plan.vf_pre.append("transpose=2")
        elif e.rotate == 180:
            plan.vf_pre.append("transpose=1,transpose=1")
        if e.rotate in (90, 270):
            w, h = h, w
        short = min(w, h) or 0
        plan.src_short = short

        if plan.category == "gif":
            _plan_gif(plan, o, w, h)
        else:
            _plan_video_geometry(plan, o, w, h, short, caps, s, source_type, worker)
            _plan_fps(plan, o, caps, s)
            _plan_video_codec(plan, o, media, caps, s)

    if plan.include_audio:
        _resolve_audio(plan, o, media, caps)

    # ---------------- full stream copy (remux) --------------------------
    if plan.category == "video" and plan.video_copy and (not plan.include_audio or plan.audio_copy):
        plan.stream_copy = True
    if plan.category == "audio" and plan.audio_copy:
        plan.stream_copy = True
    if plan.stream_copy:
        plan.notes.append("No re-encoding needed: streams are copied into the new container, preserving original quality.")

    # ---------------- stages / queue -------------------------------------
    stages = ["queued"]
    if source_type == "url":
        stages += ["analyzing", "fetching"]
    if plan.external:
        stages += ["converting"]
        if plan.interp:
            stages.append("interpolating")
        if plan.upscale and plan.upscale.get("mode") == "ai":
            stages.append("enhancing")
    elif plan.interp:
        stages.append("interpolating")
    else:
        stages.append("converting")
    stages += ["finalizing", "ready"]
    plan.stages = stages
    plan.queue = "gpu" if plan.external else "cpu"

    fps_tag = f"{plan.interp['engine']}{int(plan.fps_out or 0)}" if plan.interp else ("fps" if plan.fps_mode == "standard" else "nofps")
    up_tag = plan.upscale["mode"] if plan.upscale else "none"
    codec_tag = "copy" if plan.stream_copy else (plan.vcodec or plan.acodec or "")
    bucket = 0 if plan.category == "audio" else min((360, 480, 720, 1080, 1440, 2160, 99999), key=lambda b: abs(b - (plan.out_short or 0)))
    plan.pipeline_kind = f"{plan.category}|{codec_tag}|{bucket}|{fps_tag}|{up_tag}"

    base = (o.filename or title or "omniconvert").strip()
    plan.filename = sanitize_filename(base, plan.ext)
    _estimate(plan, o, media, history_speed)
    plan.summary = _summary(plan, o)
    return plan


def _plan_gif(plan: Plan, o: ConvertOptions, w: int, h: int) -> None:
    s = get_settings()
    if plan.duration > s.max_gif_seconds:
        plan.errors["output"] = f"GIFs are limited to {s.max_gif_seconds} seconds. Trim the clip first."
    if "gif" not in get_capabilities().encoders:
        plan.errors["output"] = "GIF encoding isn't available on this server."
    gw = min(o.gif_width, w) if w else o.gif_width
    gw = even(gw)
    gh = even(h * gw / w) if w else gw
    plan.out_width, plan.out_height = gw, gh
    plan.out_short = min(gw, gh)
    plan.fps_out = min(float(o.gif_fps), plan.fps_in or o.gif_fps)
    plan.vcodec, plan.v_encoder = "gif", "gif"
    plan.vf_scale = [f"fps={plan.fps_out:g}", f"scale={gw}:-2:flags=lanczos"]
    plan.vf_post = ["split[g0][g1]", "[g0]palettegen=stats_mode=diff[gp]", "[g1][gp]paletteuse=dither=bayer:bayer_scale=4"]
    plan.v_args = ["-loop", "0"]
    plan.include_audio = False


def _target_dims(short: int, aspect: str, w: int, h: int) -> tuple[int, int]:
    if aspect == "16:9":
        return even(short * 16 / 9), even(short)
    if aspect == "9:16":
        return even(short), even(short * 16 / 9)
    if aspect == "1:1":
        return even(short), even(short)
    if w >= h:
        return even(w * short / h), even(short)
    return even(short), even(h * short / w)


def _plan_video_geometry(plan, o, w, h, short, caps, s, source_type, worker) -> None:
    e = o.edit
    if not (w and h):
        # Unknown until fetched (some direct links): the worker re-plans with real data.
        plan.notes.append("Exact resolution will be confirmed after the source is fetched.")
        if o.upscale != "none":
            plan.warnings.append("Upscaling can only be confirmed once the source resolution is known.")
        return
    target_short = short if o.resolution == "original" else int(o.resolution)
    if target_short > short and short:
        if o.upscale == "none":
            if worker and source_type == "url":
                plan.warnings.append(f"The fetched source is {short}p, below the requested {target_short}p; kept native resolution.")
                target_short = short
            else:
                plan.errors["resolution"] = f"{target_short}p is above the source ({short}p). Enable resolution enhancement to upscale, or choose {short}p or lower."
        else:
            plan.upscaling = True
    elif o.upscale != "none" and o.resolution != "original":
        plan.notes.append("Resolution enhancement is ignored because the target isn't above the source resolution.")
    if target_short > s.max_output_height:
        plan.errors["resolution"] = f"Output resolution is limited to {s.max_output_height}p."

    out_w, out_h = _target_dims(target_short, e.aspect, w, h)
    if max(out_w, out_h) > 7680:
        plan.errors["resolution"] = "Output dimensions exceed 7680 pixels."
    plan.out_width, plan.out_height, plan.out_short = out_w, out_h, min(out_w, out_h)
    plan.scale_first = out_w * out_h <= w * h

    if plan.upscaling:
        mode = o.upscale
        if mode == "ai":
            st = caps.realesrgan
            if not st.available:
                plan.errors["upscale"] = f"AI upscaling isn't available: {st.reason} Choose standard resize instead."
            elif short > s.upscale_ai_max_input_height:
                plan.errors["upscale"] = f"AI upscaling accepts sources up to {s.upscale_ai_max_input_height}p."
            elif plan.duration > s.upscale_ai_max_seconds:
                plan.errors["upscale"] = f"AI upscaling is limited to {s.upscale_ai_max_seconds}s clips. Trim the clip or use standard resize."
            factor = 2 if target_short <= short * 2 else (3 if target_short <= short * 3 else 4)
            plan.upscale = {"mode": "ai", "factor": factor, "label": "AI upscaling (Real-ESRGAN)", "from": short, "to": target_short}
            plan.external = True
        else:
            plan.upscale = {"mode": "standard", "label": "Standard resize (Lanczos)", "from": short, "to": target_short}
        plan.notes.append(f"Output is upscaled from {short}p to {target_short}p. Upscaling can improve appearance but can't recover detail that isn't in the source; this is not native {target_short}p.")

    scale_needed = (out_w, out_h) != (w, h) or e.aspect != "original"
    if scale_needed:
        if e.aspect == "original":
            plan.vf_scale = [f"scale={out_w}:{out_h}:flags=lanczos"]
        elif e.aspect_mode == "fit":
            plan.vf_scale = [f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos", f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black"]
        elif e.aspect_mode == "fill":
            plan.vf_scale = [f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase:flags=lanczos", f"crop={out_w}:{out_h}"]
        else:
            if o.keep_aspect:
                plan.errors["aspect_mode"] = "Stretching requires turning off 'Preserve aspect ratio'."
            plan.vf_scale = [f"scale={out_w}:{out_h}:flags=lanczos"]
            plan.warnings.append("Stretch mode distorts the picture to fill the frame.")


def _plan_fps(plan: Plan, o: ConvertOptions, caps: Capabilities, s) -> None:
    src = plan.fps_in or 30.0
    plan.fps_mode = o.fps_mode
    if o.fps_mode == "original":
        plan.fps_out = plan.fps_in
        return
    if not o.target_fps:
        plan.errors["target_fps"] = "Choose a target frame rate."
        return
    tgt = float(o.target_fps)
    plan.fps_out = tgt
    if o.fps_mode == "standard":
        plan.vf_fps = [f"fps={o.target_fps}"]
        if tgt > src + 0.5:
            plan.notes.append(f"Standard conversion duplicates frames to reach {o.target_fps} FPS; motion won't be smoother.")
        return

    # motion interpolation
    if o.target_fps not in (60, 120):
        plan.errors["target_fps"] = "Motion interpolation supports 60 or 120 FPS targets."
        return
    if tgt <= src + 0.5:
        plan.errors["target_fps"] = f"The source is already {src:g} FPS. Interpolation only applies to higher targets — choose original or standard conversion."
        return
    if o.target_fps == 120 and not s.enable_120fps:
        plan.errors["target_fps"] = "120 FPS interpolation is disabled on this server. Choose 60 FPS or the original frame rate."
        return
    engine = o.interp_engine
    if engine == "auto":
        engine = "rife" if caps.rife.available else "minterpolate"
    if engine == "rife" and not caps.rife.available:
        plan.errors["interp_engine"] = f"GPU AI interpolation is unavailable: {caps.rife.reason} Choose the CPU method or 60 FPS."
        return
    if engine == "minterpolate" and not caps.minterpolate:
        plan.errors["interp_engine"] = "CPU motion interpolation isn't available in this FFmpeg build."
        return
    gpu = engine == "rife"
    max_h = s.interp_gpu_max_height if gpu else s.interp_cpu_max_height
    max_sec = {60: s.interp_gpu_max_seconds_60 if gpu else s.interp_cpu_max_seconds_60,
               120: s.interp_gpu_max_seconds_120 if gpu else s.interp_cpu_max_seconds_120}[o.target_fps]
    work_short = plan.out_short if plan.scale_first else plan.src_short
    if work_short > max_h:
        plan.errors["target_fps"] = f"{'GPU' if gpu else 'CPU'} interpolation is limited to {max_h}p. Choose a lower output resolution or trim."
    if plan.duration > max_sec:
        plan.errors["target_fps"] = f"{o.target_fps} FPS interpolation with this method is limited to {max_sec // 60 if max_sec >= 120 else max_sec}{' minutes' if max_sec >= 120 else ' seconds'} of media. Trim the clip, use the preview, or choose 60 FPS."
    if plan.upscale and plan.upscale.get("mode") == "ai" and not gpu:
        plan.errors["upscale"] = "AI upscaling combined with interpolation requires GPU interpolation, which isn't available. Choose standard resize or disable interpolation."
    if plan.upscale and plan.upscale.get("mode") == "ai" and gpu and o.target_fps == 120:
        plan.errors["target_fps"] = "AI upscaling combined with 120 FPS isn't supported reliably. Choose 60 FPS or standard resize."
    label = "RIFE AI interpolation (GPU)" if gpu else "Motion-compensated interpolation (FFmpeg minterpolate, CPU)"
    plan.interp = {"engine": engine, "label": label, "source_fps": src, "target_fps": o.target_fps, "scene_detection": True, "gpu": gpu}
    if gpu:
        plan.external = True
    else:
        plan.vf_fps = [MINTERP.format(fps=o.target_fps)]


def _plan_video_codec(plan: Plan, o: ConvertOptions, media: dict, caps: Capabilities, s) -> None:
    ct = CONTAINERS[o.output]
    src_codec = PROBE_TO_CODEC.get((media.get("video") or {}).get("codec") or "")
    vcodec = o.video_codec if o.video_codec != "auto" else None
    if vcodec is None:
        vcodec = src_codec if (o.quality == "original" and src_codec in ct["video"]) else ct["video"][0]
    if vcodec not in ct["video"]:
        plan.errors["video_codec"] = f"{VIDEO_CODECS.get(vcodec, {}).get('label', vcodec)} can't be stored in {ct['label']}."
        return
    enc = caps.encoder_for(vcodec)
    if not enc:
        plan.errors["video_codec"] = f"The {VIDEO_CODECS[vcodec]['label']} encoder isn't available on this server."
        return
    plan.vcodec, plan.v_encoder = vcodec, enc
    no_filters = not (plan.vf_pre or plan.vf_scale or plan.vf_fps or plan.external)
    plan.video_copy = bool(no_filters and vcodec == src_codec and o.quality == "original" and not o.video_bitrate_kbps and not plan.trimmed)
    if plan.video_copy:
        plan.v_args = ["-c:v", "copy"]
        if vcodec == "hevc" and o.output in ("mp4", "mov"):
            plan.v_args += ["-tag:v", "hvc1"]
        return

    q = o.quality
    br = o.video_bitrate_kbps
    args = ["-c:v", enc]
    # Encoder memory scales with threads x frames in flight x frame size. Automatic
    # threading on many-core CPUs (e.g. 28 cores -> ~42 x264 threads) exhausts the
    # per-process memory cap at 1440p/4K, so cap threads and shorten lookahead for
    # large frames. Beyond ~16 threads the speed gain is negligible anyway.
    big = plan.out_width * plan.out_height > 1920 * 1088
    huge = plan.out_width * plan.out_height > 2560 * 1600
    threads = 8 if huge else (12 if big else 16)
    if vcodec in ("h264", "hevc", "vp9", "prores"):
        args += ["-threads", str(threads)]
    if vcodec == "h264":
        args += ["-preset", "medium", "-profile:v", "high"]
        if big:
            args += ["-x264-params", f"rc-lookahead={10 if huge else 20}"]
        args += ["-b:v", f"{br}k", "-maxrate", f"{int(br * 1.5)}k", "-bufsize", f"{br * 2}k"] if br else ["-crf", str(CRF["h264"][q])]
        plan.vf_post = ["format=yuv420p"]
    elif vcodec == "hevc":
        x265p = "log-level=error" + (f":pools={threads}:frame-threads={2 if huge else 3}:rc-lookahead={10 if huge else 20}" if big else "")
        args += ["-preset", "medium", "-x265-params", x265p]
        args += ["-b:v", f"{br}k"] if br else ["-crf", str(CRF["hevc"][q])]
        if o.output in ("mp4", "mov"):
            args += ["-tag:v", "hvc1"]
        plan.vf_post = ["format=yuv420p"]
    elif vcodec == "vp9":
        args += ["-deadline", "good", "-cpu-used", "4", "-row-mt", "1"]
        args += ["-b:v", f"{br}k"] if br else ["-crf", str(CRF["vp9"][q]), "-b:v", "0"]
        plan.vf_post = ["format=yuv420p"]
    elif vcodec == "av1":
        if enc == "libsvtav1":
            args += ["-preset", "8"]
            if big:  # SVT-AV1 buffers many 4K frames per logical processor
                args += ["-svtav1-params", f"lp={2 if huge else 4}"]
        else:
            args += ["-cpu-used", "6", "-row-mt", "1"]
        args += ["-b:v", f"{br}k"] if br else ["-crf", str(CRF["av1"][q])] + ([] if enc == "libsvtav1" else ["-b:v", "0"])
        plan.vf_post = ["format=yuv420p"]
    elif vcodec == "prores":
        args += ["-profile:v", str(PRORES_PROFILE[q]), "-vendor", "apl0"]
        plan.vf_post = ["format=yuv422p10le"]
        if br:
            plan.notes.append("ProRes uses a fixed quality profile; the target bitrate is ignored.")
    plan.vf_post.append("setsar=1")
    if o.quality == "original":
        plan.notes.append("'Original/best available' re-encodes at very high quality where copying isn't possible. It is not lossless.")
    plan.v_args = args


def _estimate(plan: Plan, o: ConvertOptions, media: dict, history: Optional[tuple[float, int]]) -> None:
    dur = plan.duration or 0
    src_dur = float(media.get("duration") or 0) or dur
    est_bytes: Optional[float] = None
    if plan.stream_copy and media.get("size") and src_dur:
        est_bytes = media["size"] * (dur / src_dur)
        spread = (0.9, 1.1)
    elif plan.category == "audio":
        if plan.acodec == "pcm":
            est_bytes = dur * (plan.out_sample_rate or 48000) * (plan.out_channels or 2) * (3 if o.wav_bit_depth == 24 else 2)
            spread = (0.98, 1.02)
        elif plan.acodec == "flac":
            est_bytes = dur * (plan.out_sample_rate or 48000) * (plan.out_channels or 2) * 2 * 0.6
            spread = (0.6, 1.3)
        else:
            est_bytes = dur * (plan.audio_kbps or 128) * 1000 / 8
            spread = (0.95, 1.08)
    elif plan.category == "gif":
        est_bytes = plan.out_width * plan.out_height * (plan.fps_out or 12) * dur * 0.12
        spread = (0.3, 2.5)
    elif plan.category == "video":
        fps = plan.fps_out or plan.fps_in or 30
        if o.video_bitrate_kbps:
            vbps = o.video_bitrate_kbps * 1000
            spread = (0.85, 1.15)
        elif plan.vcodec == "prores":
            vbps = plan.out_width * plan.out_height * fps * {3: 3.5, 2: 2.4, 1: 1.6}[PRORES_PROFILE[o.quality]]
            spread = (0.8, 1.2)
        else:
            bpp = BPP[o.quality] * CODEC_EFFICIENCY.get(plan.vcodec or "h264", 1.0)
            # higher frame rates compress better per-frame (smaller deltas)
            fps_factor = (30 / fps) ** 0.5 if fps > 30 else 1.0
            vbps = plan.out_width * plan.out_height * fps * bpp * fps_factor
            src_vbr = (media.get("video") or {}).get("bit_rate") or media.get("bit_rate")
            if src_vbr and not plan.upscaling and not plan.interp and plan.out_short >= plan.src_short:
                vbps = min(vbps, src_vbr * 1.3)
            spread = (0.5, 1.6)
        abps = (plan.audio_kbps or 160) * 1000 if plan.include_audio else 0
        est_bytes = (vbps + abps) * dur / 8
    if est_bytes:
        plan.estimate["size_low"] = int(est_bytes * spread[0])
        plan.estimate["size_high"] = int(est_bytes * spread[1])
    if history and history[1] >= 3 and dur:
        speed = history[0]  # media seconds processed per wall-clock second
        mid = dur / max(speed, 1e-3)
        plan.estimate["seconds_low"] = int(mid * 0.6) + 1
        plan.estimate["seconds_high"] = int(mid * 1.8) + 3
        plan.estimate["basis"] = f"Based on {history[1]} recent similar conversions on this server."
    else:
        plan.estimate["basis"] = "Not enough history on this server to estimate processing time yet."
        if plan.interp and not plan.interp["gpu"]:
            plan.estimate["basis"] += " CPU interpolation is usually much slower than real time."


def _summary(plan: Plan, o: ConvertOptions) -> dict:
    out: dict[str, Any] = {
        "container": CONTAINERS[plan.container]["label"],
        "video_codec": VIDEO_CODECS.get(plan.vcodec or "", {}).get("label") if plan.vcodec != "gif" else "GIF",
        "audio_codec": AUDIO_CODECS.get(plan.acodec or "", {}).get("label") if plan.include_audio else None,
        "resolution": f"{plan.out_width}×{plan.out_height}" if plan.include_video else None,
        "source_resolution": f"{plan.src_width}×{plan.src_height}" if plan.include_video and plan.src_width else None,
        "fps_in": plan.fps_in,
        "fps_out": plan.fps_out,
        "fps_method": {
            "original": "Preserve original frame rate",
            "standard": "Standard conversion (drops/duplicates frames)",
            "interpolate": plan.interp["label"] if plan.interp else "Motion interpolation",
        }[plan.fps_mode] if plan.include_video and plan.category == "video" else None,
        "upscale": plan.upscale["label"] if plan.upscale else None,
        "duration": plan.duration,
        "stream_copy": plan.stream_copy,
        "audio": (f"{plan.audio_kbps} kbps · " if plan.audio_kbps and AUDIO_CODECS.get(plan.acodec or "", {}).get("lossy") else "")
        + (f"{(plan.out_sample_rate or 0) / 1000:g} kHz · " if plan.out_sample_rate else "")
        + ({1: "mono", 2: "stereo"}.get(plan.out_channels or 0, f"{plan.out_channels} ch") if plan.out_channels else "")
        if plan.include_audio else None,
    }
    return out

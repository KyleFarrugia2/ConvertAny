"""Conversion options schema (shared contract with the frontend)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Output = Literal["mp4", "webm", "mov", "mkv", "mp3", "m4a", "wav", "flac", "ogg", "gif"]
Quality = Literal["original", "high", "balanced", "small"]
Resolution = Literal["original", "360", "480", "720", "1080", "1440", "2160"]

AUDIO_BITRATES = (64, 96, 128, 160, 192, 256, 320)
SAMPLE_RATES = (22050, 32000, 44100, 48000, 96000)
FPS_TARGETS = (24, 25, 30, 48, 50, 60, 120)


class Crop(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=16)
    h: int = Field(ge=16)


class EditOptions(BaseModel):
    trim_start: Optional[float] = Field(default=None, ge=0)
    trim_end: Optional[float] = Field(default=None, gt=0)
    crop: Optional[Crop] = None
    rotate: Literal[0, 90, 180, 270] = 0
    aspect: Literal["original", "16:9", "9:16", "1:1"] = "original"
    aspect_mode: Literal["fit", "fill", "stretch"] = "fit"
    volume: int = Field(default=100, ge=0, le=300)
    fade_in: float = Field(default=0, ge=0, le=30)
    fade_out: float = Field(default=0, ge=0, le=30)
    strip_metadata: bool = False


class ConvertOptions(BaseModel):
    output: Output = "mp4"
    quality: Quality = "high"
    video_codec: Literal["auto", "h264", "hevc", "vp9", "av1", "prores"] = "auto"
    audio_codec: Literal["auto", "aac", "mp3", "opus", "vorbis", "flac", "pcm"] = "auto"
    resolution: Resolution = "original"
    video_bitrate_kbps: Optional[int] = Field(default=None, ge=100, le=200_000)
    keep_aspect: bool = True
    include_audio: bool = True

    fps_mode: Literal["original", "standard", "interpolate"] = "original"
    target_fps: Optional[int] = None
    interp_engine: Literal["auto", "minterpolate", "rife"] = "auto"
    upscale: Literal["none", "standard", "ai"] = "none"

    audio_bitrate_kbps: Optional[int] = None
    sample_rate: Optional[int] = None
    channels: Literal["original", "mono", "stereo"] = "original"
    normalize: bool = False
    wav_bit_depth: Literal[16, 24] = 16
    flac_compression: int = Field(default=5, ge=0, le=12)

    gif_fps: int = Field(default=12, ge=5, le=30)
    gif_width: int = Field(default=480, ge=120, le=1080)

    edit: EditOptions = Field(default_factory=EditOptions)
    filename: Optional[str] = Field(default=None, max_length=180)
    preview: bool = False

    @field_validator("audio_bitrate_kbps")
    @classmethod
    def _abr(cls, v):
        if v is not None and v not in AUDIO_BITRATES:
            raise ValueError(f"must be one of {AUDIO_BITRATES}")
        return v

    @field_validator("sample_rate")
    @classmethod
    def _sr(cls, v):
        if v is not None and v not in SAMPLE_RATES:
            raise ValueError(f"must be one of {SAMPLE_RATES}")
        return v

    @field_validator("target_fps")
    @classmethod
    def _fps(cls, v):
        if v is not None and v not in FPS_TARGETS:
            raise ValueError(f"must be one of {FPS_TARGETS}")
        return v
